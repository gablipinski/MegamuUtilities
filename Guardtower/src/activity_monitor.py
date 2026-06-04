from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ActivityDecision:
    """Final result for one activity window evaluation."""

    enter: bool
    reason: str
    metrics: dict[str, float]


class ChatActivityMonitor:
    """Per-channel activity monitor that decides giveaway entry from recent chat behavior."""

    # Reference point: a channel with ~20 unique chatters in the baseline window is
    # considered "normally active". Channels below this threshold get a sensitivity
    # boost; channels above get stricter requirements.
    ACTIVITY_REF_UNIQUE: int = 20

    # Fraction of baseline unique users expected to participate in the giveaway window
    # during a "typical" clear giveaway on a reference channel.
    DIVERSITY_REF_FRACTION: float = 0.15

    # Hard safety floors to avoid one-message false positives on very quiet channels.
    ABSOLUTE_MIN_MESSAGES_IN_WINDOW: int = 2
    ABSOLUTE_MIN_UNIQUE_CHATTERS: int = 2

    def __init__(
        self,
        channel_names: list[str],
        logs_dir: Path | None,
        baseline_window_s: float = 300.0,
        monitor_window_s: float = 25.0,
        min_messages_in_window: int = 8,
        min_unique_chatters: int = 4,
        enter_score_threshold: float = 1.6,
        channel_settings: dict[str, dict[str, float | int]] | None = None,
        enable_file_logging: bool = True,
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

        self.enable_file_logging = bool(enable_file_logging)
        self.activity_log_path = (logs_dir / "activity_monitor.log") if (logs_dir is not None and self.enable_file_logging) else None
        if self.activity_log_path is not None:
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

    def reset_channel(self, channel_name: str) -> None:
        """Clear all baseline and active-window state for one channel."""
        if channel_name in self.baseline_messages:
            self.baseline_messages[channel_name].clear()
        if channel_name in self.baseline_users:
            self.baseline_users[channel_name].clear()
        self.active_windows[channel_name] = None

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

    def _compute_adaptive_thresholds(
        self,
        baseline_unique: int,
        window_unique: int,
        min_messages_in_window: int,
        min_unique_chatters: int,
        enter_score_threshold: float,
    ) -> tuple[int, int, float]:
        """
        Derives per-evaluation adaptive thresholds from current channel state.

        activity_scale  - scales ALL gates up/down based on overall channel busyness.
        diversity_factor - adjusts the score threshold based on how many distinct users
                           participated in the giveaway window relative to channel size.
        """
        activity_scale = max(0.25, min(baseline_unique / self.ACTIVITY_REF_UNIQUE, 3.0))

        diversity_ref_count = max(baseline_unique * self.DIVERSITY_REF_FRACTION, 1.0)
        diversity_factor = max(0.75, min(window_unique / diversity_ref_count, 2.0))

        adaptive_min_messages = max(
            self.ABSOLUTE_MIN_MESSAGES_IN_WINDOW,
            round(min_messages_in_window * activity_scale),
        )
        adaptive_min_unique = max(
            self.ABSOLUTE_MIN_UNIQUE_CHATTERS,
            round(min_unique_chatters * activity_scale),
        )

        raw_threshold = (enter_score_threshold * activity_scale) / diversity_factor
        adaptive_threshold = max(
            enter_score_threshold * 0.4,
            min(raw_threshold, enter_score_threshold * 2.5),
        )

        return adaptive_min_messages, adaptive_min_unique, adaptive_threshold

    def _get_setting(self, channel_name: str, key: str) -> float | int:
        override = self.channel_settings.get(channel_name, {})
        if key in override:
            return override[key]
        return getattr(self, key)

    def _append_log(self, channel_name: str, decision: ActivityDecision):
        """Appends one decision row (JSONL) for offline tuning and auditing."""
        if self.activity_log_path is None:
            return
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

        # Adaptive thresholds: scale hard gates and score threshold by channel activity
        # and giveaway window diversity.
        adaptive_min_messages, adaptive_min_unique, adaptive_threshold = (
            self._compute_adaptive_thresholds(
                baseline_unique, window_unique,
                min_messages_in_window, min_unique_chatters, enter_score_threshold,
            )
        )

        activity_scale = max(0.25, min(baseline_unique / self.ACTIVITY_REF_UNIQUE, 3.0))
        diversity_ref_count = max(baseline_unique * self.DIVERSITY_REF_FRACTION, 1.0)
        diversity_factor = max(0.75, min(window_unique / diversity_ref_count, 2.0))

        enough_volume = window_messages >= adaptive_min_messages
        enough_unique = window_unique >= adaptive_min_unique

        should_enter = enough_volume and enough_unique and score >= adaptive_threshold

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
            "activity_scale": round(activity_scale, 3),
            "diversity_factor": round(diversity_factor, 3),
            "adaptive_min_messages": float(adaptive_min_messages),
            "adaptive_min_unique": float(adaptive_min_unique),
            "adaptive_threshold": round(adaptive_threshold, 3),
        }

        if should_enter:
            reason = (
                f"score={metrics['score']} >= threshold={metrics['adaptive_threshold']} "
                f"volume={window_messages}/{adaptive_min_messages} "
                f"unique={window_unique}/{adaptive_min_unique} "
                f"(activity_scale={metrics['activity_scale']} diversity={metrics['diversity_factor']})"
            )
        else:
            failed = []
            if score < adaptive_threshold:
                failed.append(f"score={metrics['score']:.3f}<{metrics['adaptive_threshold']:.3f}")
            if not enough_volume:
                failed.append(f"volume={window_messages}<{adaptive_min_messages}")
            if not enough_unique:
                failed.append(f"unique={window_unique}<{adaptive_min_unique}")
            failed_text = ", ".join(failed) if failed else "unknown"
            reason = (
                f"insufficient activity [{failed_text}] "
                f"(activity_scale={metrics['activity_scale']} diversity={metrics['diversity_factor']})"
            )

        decision = ActivityDecision(enter=should_enter, reason=reason, metrics=metrics)
        self._append_log(channel_name, decision)
        self.active_windows[channel_name] = None
        return decision

    def get_baseline_metrics(self, channel_name: str, now: float | None = None) -> dict[str, float]:
        """Returns current baseline activity metrics for a channel.
        
        This is used by join dedup auto-detection to scale settings based on activity level.
        
        Returns:
        {
            "baseline_msg_count": count of messages in rolling baseline window,
            "baseline_unique": count of unique users in rolling baseline window,
            "baseline_window_s": length of baseline window,
            "msg_rate": messages per second,
        }
        """
        ts = now if now is not None else time.monotonic()
        self._cleanup_baseline(channel_name, ts)
        
        baseline_window_s = float(self._get_setting(channel_name, "baseline_window_s"))
        baseline_msg_count = len(self.baseline_messages[channel_name])
        baseline_unique = self._baseline_unique_count(channel_name)
        
        # Messages per second in the baseline window
        msg_rate = baseline_msg_count / max(baseline_window_s, 1.0) if baseline_msg_count > 0 else 0.0
        
        return {
            "baseline_msg_count": float(baseline_msg_count),
            "baseline_unique": float(baseline_unique),
            "baseline_window_s": baseline_window_s,
            "msg_rate": msg_rate,
        }