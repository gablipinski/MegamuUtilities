from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

"""
Activity monitor overview
=========================

This module implements a lightweight, per-channel activity model used to decide
if the bot should enter a giveaway after a giveaway trigger appears in chat.

How it works at runtime:
1. The bot calls observe_message(...) for every message.
2. The monitor always keeps a rolling baseline window (default 300s):
    - message timestamps
    - author timestamps
3. When the bot detects a giveaway trigger, it calls start_window(...):
    - this opens a short monitor window (default 25s)
4. During the active window, observe_message(...) accumulates:
    - total messages
    - unique users
    - command-like messages (starting with ! or #)
5. The bot periodically calls evaluate_if_ready(...):
    - returns None while the window is still collecting data
    - returns ActivityDecision after the window ends
6. Decision is based on activity score + minimum hard gates:
    - enough message volume
    - enough unique users
    - score above threshold
7. Every final decision is appended to logs/activity_monitor.log as JSONL.

Why this design:
- Replaces static trigger repetition logic with behavior-based detection.
- Keeps decisions explainable through logged metrics.
- Isolates monitoring logic in one module for easier tuning.

Mathematical logic used by the current implementation:

Let:
- Bm = baseline_msg_count
- Bu = baseline_unique
- Wm = window_messages
- Wu = window_unique
- Wc = command_like
- Tb = baseline_window_s
- Tw = elapsed monitor window duration

Derived features:
- baseline_rate = Bm / max(Tb, 1)
- window_rate = Wm / max(Tw, 1)
- rate_ratio = window_rate / max(baseline_rate, 0.2)
- unique_ratio = Wu / max(Bu, 1)
- command_ratio = Wc / max(Wm, 1)

Weighted score:
- score = 0.6 * rate_ratio + 0.3 * unique_ratio + 0.1 * (command_ratio * 5.0)

Hard gates:
- enough_volume = (Wm >= min_messages_in_window)
- enough_unique = (Wu >= min_unique_chatters)

Final decision:
- should_enter = enough_volume and enough_unique and (score >= enter_score_threshold)
"""


@dataclass
class ActivityDecision:
    """Final result for one activity window evaluation."""

    enter: bool
    reason: str
    metrics: dict[str, float]


class ChatActivityMonitor:
    """Per-channel activity monitor that decides giveaway entry from recent chat behavior."""

    def __init__(
        self,
        channel_names: list[str],
        logs_dir: Path,
        baseline_window_s: float = 300.0,
        monitor_window_s: float = 25.0,
        min_messages_in_window: int = 8,
        min_unique_chatters: int = 4,
        enter_score_threshold: float = 1.6,
        channel_settings: dict[str, dict[str, float | int]] | None = None,
    ):
        self.baseline_window_s = baseline_window_s
        self.monitor_window_s = monitor_window_s
        self.min_messages_in_window = min_messages_in_window
        self.min_unique_chatters = min_unique_chatters
        self.enter_score_threshold = enter_score_threshold
        self.channel_settings = channel_settings or {}

        # Baseline tracks typical channel behavior over a rolling window.
        self.baseline_messages: dict[str, deque[float]] = {name: deque() for name in channel_names}
        self.baseline_users: dict[str, deque[tuple[float, str]]] = {name: deque() for name in channel_names}
        # Active window exists only after a giveaway trigger and for a short period.
        self.active_windows: dict[str, dict | None] = {name: None for name in channel_names}

        self.activity_log_path = logs_dir / "activity_monitor.log"
        self.activity_log_path.touch(exist_ok=True)

    def _cleanup_baseline(self, channel_name: str, now: float):
        """Drop baseline samples older than the rolling baseline window."""
        baseline_window_s = self._get_setting(channel_name, "baseline_window_s")
        cutoff = now - baseline_window_s
        msgs = self.baseline_messages[channel_name]
        users = self.baseline_users[channel_name]

        while msgs and msgs[0] < cutoff:
            msgs.popleft()
        while users and users[0][0] < cutoff:
            users.popleft()

    def observe_message(self, channel_name: str, author_name: str, content: str, now: float | None = None):
        """Ingests one chat message into baseline and current active window, if any."""
        ts = now if now is not None else time.monotonic()

        self.baseline_messages[channel_name].append(ts)
        self.baseline_users[channel_name].append((ts, author_name))
        self._cleanup_baseline(channel_name, ts)

        window = self.active_windows.get(channel_name)
        if window is None:
            return

        window["messages"] += 1
        window["users"].add(author_name)
        text = content.strip()
        if text.startswith("!") or text.startswith("#"):
            window["command_like"] += 1

    def has_active_window(self, channel_name: str) -> bool:
        return self.active_windows.get(channel_name) is not None

    def start_window(self, channel_name: str, trigger_text: str, now: float | None = None):
        """Starts a short monitoring window after a giveaway trigger is seen."""
        if self.has_active_window(channel_name):
            return
        ts = now if now is not None else time.monotonic()
        self.active_windows[channel_name] = {
            "start": ts,
            "trigger": trigger_text,
            "messages": 0,
            "users": set(),
            "command_like": 0,
        }

    def _baseline_unique_count(self, channel_name: str) -> int:
        return len({author for _, author in self.baseline_users[channel_name]})

    def _get_setting(self, channel_name: str, key: str) -> float | int:
        override = self.channel_settings.get(channel_name, {})
        if key in override:
            return override[key]
        return getattr(self, key)

    def _append_log(self, channel_name: str, decision: ActivityDecision):
        """Appends one decision row (JSONL) for offline tuning and auditing."""
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel_name,
            "enter": decision.enter,
            "reason": decision.reason,
            "metrics": decision.metrics,
        }
        with open(self.activity_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def evaluate_if_ready(self, channel_name: str, now: float | None = None) -> ActivityDecision | None:
        """
        Returns:
        - None: monitor window still active and collecting samples.
        - ActivityDecision: monitor window ended and was evaluated.

        Equations implemented in this function:
        - baseline_rate = baseline_msg_count / max(baseline_window_s, 1)
        - window_rate = window_messages / max(elapsed, 1)
        - rate_ratio = window_rate / max(baseline_rate, 0.2)
        - unique_ratio = window_unique / max(baseline_unique, 1)
        - command_ratio = command_like / max(window_messages, 1)
        - score = 0.6 * rate_ratio + 0.3 * unique_ratio + 0.1 * (command_ratio * 5.0)
        - enter = (window_messages >= min_messages_in_window)
                  and (window_unique >= min_unique_chatters)
                  and (score >= enter_score_threshold)
        """
        window = self.active_windows.get(channel_name)
        if window is None:
            return None

        ts = now if now is not None else time.monotonic()
        monitor_window_s = float(self._get_setting(channel_name, "monitor_window_s"))
        elapsed = ts - float(window["start"])
        if elapsed < monitor_window_s:
            return None

        self._cleanup_baseline(channel_name, ts)
        baseline_window_s = float(self._get_setting(channel_name, "baseline_window_s"))
        min_messages_in_window = int(self._get_setting(channel_name, "min_messages_in_window"))
        min_unique_chatters = int(self._get_setting(channel_name, "min_unique_chatters"))
        enter_score_threshold = float(self._get_setting(channel_name, "enter_score_threshold"))
        baseline_msg_count = len(self.baseline_messages[channel_name])
        baseline_unique = self._baseline_unique_count(channel_name)

        window_messages = int(window["messages"])
        window_unique = len(window["users"])
        command_like = int(window["command_like"])

        # Compare short-window behavior with long-window baseline.
        baseline_rate = baseline_msg_count / max(baseline_window_s, 1.0)
        window_rate = window_messages / max(elapsed, 1.0)
        rate_ratio = window_rate / max(baseline_rate, 0.2)
        unique_ratio = window_unique / max(float(baseline_unique), 1.0)
        command_ratio = command_like / max(float(window_messages), 1.0)

        # Weighted score keeps logic simple while reflecting intensity and diversity.
        score = (0.6 * rate_ratio) + (0.3 * unique_ratio) + (0.1 * (command_ratio * 5.0))

        # Hard gates reduce false positives from tiny/noisy bursts.
        enough_volume = window_messages >= min_messages_in_window
        enough_unique = window_unique >= min_unique_chatters

        should_enter = enough_volume and enough_unique and score >= enter_score_threshold

        metrics = {
            "elapsed_s": round(elapsed, 2),
            "baseline_msg_count": float(baseline_msg_count),
            "baseline_unique": float(baseline_unique),
            "window_messages": float(window_messages),
            "window_unique": float(window_unique),
            "command_ratio": round(command_ratio, 3),
            "rate_ratio": round(rate_ratio, 3),
            "unique_ratio": round(unique_ratio, 3),
            "score": round(score, 3),
        }

        if should_enter:
            reason = f"score={metrics['score']} volume={window_messages} unique={window_unique}"
        else:
            failed = []
            if score < enter_score_threshold:
                failed.append("score")
            if not enough_volume:
                failed.append("volume")
            if not enough_unique:
                failed.append("unique")
            failed_text = ",".join(failed) if failed else "unknown"
            reason = (
                f"insufficient activity [{failed_text}] score={metrics['score']} "
                f"volume={window_messages} unique={window_unique}"
            )

        decision = ActivityDecision(enter=should_enter, reason=reason, metrics=metrics)
        self._append_log(channel_name, decision)
        self.active_windows[channel_name] = None
        return decision