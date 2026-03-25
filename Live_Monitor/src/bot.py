import asyncio
import json
import random
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import twitchio
from twitchio.ext import commands

from activity_monitor import ChatActivityMonitor
from chat_monitor import ChatMonitorLogger
from console_log import log_line
from config import BotConfig
from windows_notifier import WindowsNotifier


class TwitchBot(commands.Cog):
    """Bot that automatically joins giveaways across multiple Twitch channels"""

    def __init__(
        self,
        bot: commands.Bot,
        config: BotConfig,
        account_name: str,
        account_nickname: str,
        ignored_usernames: list[str] | None = None,
        log_only_mode: bool = False,
        enable_logging: bool = False,
    ):
        self.bot = bot
        self.config = config
        self.account_name = account_name
        self.account_nickname = account_nickname
        self.ignored_usernames = [
            name.strip()
            for name in (ignored_usernames or [])
            if name and name.strip()
        ]
        self.ignored_usernames_set = {
            self._normalize_text(name)
            for name in self.ignored_usernames
        }
        self.log_only_mode = bool(log_only_mode)
        self.enable_logging = bool(enable_logging)
        self.notifier = WindowsNotifier(config.notification)
        self.is_shutting_down = False

        # Map channel names to their configs
        self.channels_map = {ch.name: ch for ch in config.channels}
        # Timestamp of last won-trigger fire per sender across all channels (monotonic seconds).
        # This prevents duplicate replies when multiple channels share chat and the same
        # streamer bot/system account mirrors the same win message into different lives.
        self.won_last_triggered: dict[str, tuple[float, str]] = {}
        # Cooldown between won-trigger responses (seconds)
        self.WON_COOLDOWN_S: float = self.config.runtime.won_cooldown_s
        # Giveaway stays active for 5 minutes after the first winner announcement is seen.
        self.GIVEAWAY_END_AFTER_FIRST_WIN_S: float = 300.0

        # Tracks the currently active giveaway session in each channel.
        # A session begins on the first successful join, stays active until 5 minutes after the
        # first winner announcement is observed, and only allows rejoin if the new trigger score
        # grows geometrically by 1.5x over the last successful join in the same giveaway.
        self.channel_active_giveaway: dict[str, dict[str, float | str | bool | int]] = {}

        # Logs root and global wins.log are always enabled.
        self.logs_root = Path(__file__).resolve().parent.parent / "logs"
        self.wins_log_path = self.logs_root / "wins.log"

        # Setup file logging only when explicitly enabled.
        self.logs_dir = Path(__file__).resolve().parent.parent / "logs" if self.enable_logging else None
        if self.logs_dir is not None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)

        legacy_wins_log_path = Path(__file__).resolve().parent.parent / "wins.log"
        if legacy_wins_log_path.exists() and not self.wins_log_path.exists():
            self.wins_log_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_wins_log_path.replace(self.wins_log_path)

        # Persistent per-channel counters in one JSON file for all channels.
        self.stats_log_path = self.logs_root / "channel_stats.json"
        self.channel_stats = self._load_channel_stats()
        self._save_channel_stats()

        # Chat activity monitor (replaces static repetition logic).
        self.activity_monitor = ChatActivityMonitor(
            channel_names=[ch.name for ch in config.channels],
            logs_dir=self.logs_dir,
            baseline_window_s=config.activity_monitor.baseline_window_s,
            monitor_window_s=config.activity_monitor.monitor_window_s,
            min_messages_in_window=config.activity_monitor.min_messages_in_window,
            min_unique_chatters=config.activity_monitor.min_unique_chatters,
            enter_score_threshold=config.activity_monitor.enter_score_threshold,
            channel_settings={
                ch.name: ch.activity_monitor
                for ch in config.channels
                if ch.activity_monitor is not None
            },
            enable_file_logging=self.enable_logging,
        )
        self.chat_monitor = ChatMonitorLogger(logs_dir=self.logs_dir) if self.logs_dir is not None else None

    def _append_win_log(self, timestamp: str, channel_name: str):
        self.wins_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.wins_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{self.account_name}] [{timestamp}] Won in #{channel_name}\n")

    def _load_channel_stats(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {ch.name: {"giveaways": 0, "wins": 0} for ch in self.config.channels}
        if not self.stats_log_path.exists():
            return stats

        try:
            with open(self.stats_log_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return stats

        # New format: list[{"channel": str, "giveaways": int, "wins": int}]
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                channel_name = str(entry.get("channel", "")).strip()
                if not channel_name:
                    continue
                values = stats.setdefault(channel_name, {"giveaways": 0, "wins": 0})
                values["giveaways"] = max(0, int(entry.get("giveaways", 0)))
                values["wins"] = max(0, int(entry.get("wins", 0)))
            return stats

        # Legacy format compatibility: {"channels": {name: {joined/won or giveaways/wins}}}
        channels_payload = payload.get("channels", {}) if isinstance(payload, dict) else {}
        if isinstance(channels_payload, dict):
            for channel_name, values_payload in channels_payload.items():
                if not isinstance(values_payload, dict):
                    continue
                values = stats.setdefault(channel_name, {"giveaways": 0, "wins": 0})
                giveaways = values_payload.get("giveaways", values_payload.get("joined", 0))
                wins = values_payload.get("wins", values_payload.get("won", 0))
                values["giveaways"] = max(0, int(giveaways))
                values["wins"] = max(0, int(wins))

        return stats

    def _save_channel_stats(self):
        payload = [
            {
                "channel": channel_name,
                "giveaways": max(0, int(values.get("giveaways", 0))),
                "wins": max(0, int(values.get("wins", 0))),
            }
            for channel_name, values in sorted(self.channel_stats.items())
        ]
        self.stats_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.stats_log_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _increment_channel_stat(self, channel_name: str, key: str):
        values = self.channel_stats.setdefault(channel_name, {"giveaways": 0, "wins": 0})
        values[key] = max(0, int(values.get(key, 0))) + 1
        self._save_channel_stats()
        pass

    def _normalize_text(self, text: str) -> str:
        """Normalizes text for resilient trigger matching."""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _contains_trigger(
        self,
        message_text: str,
        trigger_text: str,
        *,
        allow_username_wildcard: bool = True,
    ) -> bool:
        """Checks trigger using both raw and normalized forms. Supports * as wildcard."""
        if not trigger_text:
            return False

        trigger_candidates = [trigger_text]
        if "{username}" in trigger_text:
            trigger_candidates.append(trigger_text.replace("@{username}", f"@{self.account_name}"))
            trigger_candidates.append(trigger_text.replace("{username}", self.account_name))
            if allow_username_wildcard:
                trigger_candidates.append(trigger_text.replace("@{username}", "*"))
                trigger_candidates.append(trigger_text.replace("{username}", "*"))

        for candidate in trigger_candidates:
            if self._contains_trigger_candidate(message_text, candidate):
                return True
        return False

    def _contains_trigger_candidate(self, message_text: str, trigger_text: str) -> bool:
        """Internal single-trigger matcher with optional wildcard support."""
        if not trigger_text:
            return False
        if "*" not in trigger_text:
            if trigger_text in message_text:
                return True
            return self._normalize_text(trigger_text) in self._normalize_text(message_text)

        # Wildcard mode: normalize each literal segment and build a regex.
        norm_message = self._normalize_text(message_text)
        parts = [re.escape(self._normalize_text(p)) for p in trigger_text.split("*")]
        pattern = ".*".join(parts)
        try:
            return bool(re.search(pattern, norm_message, re.DOTALL))
        except re.error:
            return False

    def _should_ignore_author(self, author_name: str | None) -> bool:
        if not author_name:
            return False
        return self._normalize_text(author_name) in self.ignored_usernames_set

    def _get_random_delay_s(self, channel_name: str, delay_range_ms: tuple[int, int]) -> float:
        delay_min, delay_max = delay_range_ms
        chosen_ms = random.randint(delay_min, delay_max)
        delay_s = chosen_ms / 1000
        log_line(
            f"Waiting {delay_s}s before replying (range {delay_min}-{delay_max}ms)",
            "other",
            channel_name,
            account=self.account_name,
        )
        return delay_s

    def _build_giveaway_signature(self, channel_name: str, matched_trigger: str | None, giveaway_message: str) -> str:
        trigger_part = self._normalize_text(matched_trigger or "")
        command_part = self._normalize_text(giveaway_message)
        return f"{channel_name}|{trigger_part}|{command_part}"

    def _get_active_giveaway_session(self, channel_name: str, now: float) -> dict[str, float | str | bool | int] | None:
        session = self.channel_active_giveaway.get(channel_name)
        if session is None:
            return None

        winner_seen_at = float(session.get("winner_seen_at", 0.0))
        if winner_seen_at > 0.0 and (now - winner_seen_at) >= self.GIVEAWAY_END_AFTER_FIRST_WIN_S:
            self.channel_active_giveaway.pop(channel_name, None)
            return None

        return session

    def _mark_giveaway_trigger_seen(self, channel_name: str, giveaway_signature: str, now: float) -> dict[str, float | str | bool | int] | None:
        session = self._get_active_giveaway_session(channel_name, now)
        if session is None:
            return None
        if str(session.get("signature", "")) != giveaway_signature:
            return None
        session["last_trigger_seen_at"] = now
        return session

    def _mark_first_winner_seen(self, channel_name: str, now: float) -> dict[str, float | str | bool | int] | None:
        session = self._get_active_giveaway_session(channel_name, now)
        if session is None:
            return None
        if float(session.get("winner_seen_at", 0.0)) <= 0.0:
            session["winner_seen_at"] = now
            log_line(
                f"Winner announcement seen - giveaway will close in {int(self.GIVEAWAY_END_AFTER_FIRST_WIN_S)}s",
                "giveaway_inactive",
                channel_name,
                account=self.account_name,
            )
        return session

    def _record_successful_join(
        self,
        channel_name: str,
        giveaway_signature: str,
        now: float,
        score: float,
    ) -> None:
        existing = self._get_active_giveaway_session(channel_name, now)
        join_count = 1
        active_since = now
        winner_seen_at = 0.0
        if existing is not None and str(existing.get("signature", "")) == giveaway_signature:
            join_count = int(existing.get("join_count", 0)) + 1
            active_since = float(existing.get("active_since", now))
            winner_seen_at = float(existing.get("winner_seen_at", 0.0))

        self.channel_active_giveaway[channel_name] = {
            "signature": giveaway_signature,
            "active_since": active_since,
            "last_trigger_seen_at": now,
            "last_join_at": now,
            "last_score": score,
            "next_join_score": score * 1.5,
            "winner_seen_at": winner_seen_at,
            "join_count": join_count,
        }
        if join_count == 1:
            log_line(
                f"Giveaway ACTIVE (score={score:.3f}, next_threshold={score * 1.5:.3f})",
                "giveaway_active",
                channel_name,
                account=self.account_name,
            )

    def _find_won_trigger_match(self, message_text: str, triggers: list[str], *, allow_username_wildcard: bool) -> str | None:
        for trigger in triggers:
            if self._contains_trigger(message_text, trigger, allow_username_wildcard=allow_username_wildcard):
                return trigger
        return None

    def _parse_irc_tags(self, tags_raw: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        for pair in tags_raw.split(";"):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            tags[key] = value
        return tags

    async def _handle_won_trigger(
        self,
        *,
        channel_name: str,
        sender: str,
        message_text: str,
        channel_config,
        send_channel,
    ):
        now = time.monotonic()
        sender_key = self._normalize_text(sender) or sender.lower()
        last_won = self.won_last_triggered.get(sender_key)
        if last_won is not None:
            last_won_ts, last_channel = last_won
        else:
            last_won_ts, last_channel = 0.0, ""

        if last_won_ts > 0 and (now - last_won_ts) < self.WON_COOLDOWN_S:
            remaining = int(self.WON_COOLDOWN_S - (now - last_won_ts))
            shared_chat_note = f" (last seen in #{last_channel})" if last_channel else ""
            log_line(
                f"Won trigger from {sender} on cooldown ({remaining}s left){shared_chat_note} - skipping",
                "cooldown",
                channel_name,
                account=self.account_name,
            )
            return

        self.won_last_triggered[sender_key] = (now, channel_name)
        log_line(f"Won giveaway", "win", channel_name, account=self.account_name)
        log_line(f"Message: {message_text}", "win", channel_name, account=self.account_name)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append_win_log(timestamp, channel_name)
        self._increment_channel_stat(channel_name, "wins")

        if self.config.notification.enabled:
            self.notifier.send_notification(
                channel_name,
                message_text,
                title="You won the giveaway!",
                account=self.account_name,
            )

        won_reply = f"{channel_config.won_prefix}{self.account_nickname}"
        if won_reply and send_channel is not None:
            delay_sec = self._get_random_delay_s(channel_name, channel_config.delay_ms)
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)
            log_line(f"Sending: {won_reply}", "send", channel_name, account=self.account_name)
            try:
                await send_channel.send(won_reply)
            except Exception as send_error:
                log_line(
                    f"Send failed: {send_error}",
                    "ignore",
                    channel_name,
                    account=self.account_name,
                )

    async def graceful_shutdown(self, reason: str = "manual interrupt"):
        """Shuts down the bot gracefully."""
        if self.is_shutting_down:
            return

        self.is_shutting_down = True
        log_line(f"Shutting down gracefully ({reason})", "other")

        try:
            await self.bot.close()
            log_line("Twitch connection closed", "other")
        except Exception as error:
            log_line(f"Error closing bot: {error}", "other")

    @commands.Cog.event()
    async def event_command_error(self, ctx, error):
        """Silently ignore unknown commands (e.g. !sorteio used as a giveaway trigger)."""
        if isinstance(error, commands.CommandNotFound):
            return
        raise error

    @commands.Cog.event()
    async def event_ready(self):
        """Called when bot is connected and ready."""
        channel_names = ", ".join(f"#{ch.name}" for ch in self.config.channels)
        log_line(f"Bot connected as {self.bot.nick}", "other", account=self.account_name)
        log_line(f"Listening to {len(self.config.channels)} channel(s): {channel_names}", "other", account=self.account_name)
        if self.ignored_usernames:
            log_line(
                f"Ignoring authors: {', '.join(self.ignored_usernames)}",
                "other",
                account=self.account_name,
            )
        if self.log_only_mode:
            log_line("Runtime mode: logging-only (detection and replies disabled)", "other", account=self.account_name)
        else:
            log_line("Giveaway mode: activity monitor", "other", account=self.account_name)
        print()

    @commands.Cog.event()
    async def event_raw_data(self, data: str):
        """Captures USERNOTICE announcements that may not arrive through event_message."""
        if self.is_shutting_down:
            return
        if not data.startswith("@"):
            return

        # Twitch can return message send rejections as NOTICE lines (e.g. slow/followers-only,
        # account restrictions, channel moderation rules) without raising send() exceptions.
        if " NOTICE #" in data:
            try:
                tags_part, rest = data[1:].split(" ", 1)
            except ValueError:
                return

            channel_match = re.search(r" NOTICE #([^\s]+)", rest)
            if channel_match is None:
                return

            channel_name = channel_match.group(1).strip().lower()
            tags = self._parse_irc_tags(tags_part)
            msg_id = tags.get("msg-id", "")

            notice_text = ""
            if " :" in rest:
                notice_text = rest.split(" :", 1)[1].strip()

            if msg_id and notice_text:
                detail = f"{msg_id}: {notice_text}"
            elif msg_id:
                detail = msg_id
            elif notice_text:
                detail = notice_text
            else:
                detail = "unknown notice"

            log_line(
                f"Twitch NOTICE: {detail}",
                "ignore",
                channel_name,
                account=self.account_name,
            )
            return

        if " USERNOTICE #" not in data:
            return

        try:
            tags_part, rest = data[1:].split(" ", 1)
        except ValueError:
            return

        channel_match = re.search(r" USERNOTICE #([^\s]+)", rest)
        if channel_match is None:
            return

        channel_name = channel_match.group(1).strip().lower()
        channel_config = self.channels_map.get(channel_name)
        if channel_config is None:
            return

        message_text = ""
        if " :" in rest:
            message_text = rest.split(" :", 1)[1]
        message_text = message_text.strip()
        if not message_text:
            return

        tags = self._parse_irc_tags(tags_part)
        msg_id = tags.get("msg-id", "")
        author_name = tags.get("display-name") or tags.get("login") or "system"
        if self._should_ignore_author(author_name):
            return

        classes: set[str] = {"chat_message", "raw_usernotice"}
        metadata: dict[str, str] = {}
        if msg_id:
            metadata["msg_id"] = msg_id
        if msg_id == "announcement":
            classes.add("announcement")
        if self.log_only_mode:
            metadata["runtime_mode"] = "log_only"

        matched_any_won_trigger = None
        matched_won_trigger = None
        if not self.log_only_mode:
            message_lower = message_text.lower()
            matched_any_won_trigger = self._find_won_trigger_match(
                message_lower,
                channel_config.won_triggers,
                allow_username_wildcard=True,
            )
            matched_won_trigger = self._find_won_trigger_match(
                message_lower,
                channel_config.won_triggers,
                allow_username_wildcard=False,
            )
        if matched_any_won_trigger is not None:
            classes.add("won_trigger")
            metadata["matched_won_trigger"] = matched_any_won_trigger

        if self.chat_monitor is not None:
            self.chat_monitor.log_message(
                channel_name=channel_name,
                author_name=author_name,
                message_text=message_text,
                classes=classes,
                metadata=metadata,
            )

        if matched_any_won_trigger is not None:
            self._mark_first_winner_seen(channel_name, time.monotonic())

        if matched_won_trigger is None:
            return

        send_channel = self.bot.get_channel(channel_name)
        await self._handle_won_trigger(
            channel_name=channel_name,
            sender=author_name,
            message_text=message_text,
            channel_config=channel_config,
            send_channel=send_channel,
        )

    @commands.Cog.event()
    async def event_message(self, message: twitchio.Message):
        """Called for every chat message received."""
        if self.is_shutting_down:
            return

        # System/echo messages arrive without an author.
        if message.author is None:
            return

        # Ignore the bot's own messages.
        if message.author.name == self.bot.nick:
            return
        if self._should_ignore_author(message.author.name):
            return

        channel_name = message.channel.name
        now = time.monotonic()

        channel_config = self.channels_map.get(channel_name)
        if not channel_config:
            return

        classes: set[str] = {"chat_message"}
        stripped = message.content.strip()
        if stripped.startswith("!") or stripped.startswith("#"):
            classes.add("command_like")
            classes.add("possible_giveaway_command")

        metadata = {}
        if self.log_only_mode:
            metadata["runtime_mode"] = "log_only"
            if self.chat_monitor is not None:
                self.chat_monitor.log_message(
                    channel_name=channel_name,
                    author_name=message.author.name,
                    message_text=message.content,
                    classes=classes,
                    metadata=metadata,
                )
            return

        self.activity_monitor.observe_message(channel_name, message.author.name, message.content, now)

        message_lower = message.content.lower()

        matched_any_won_trigger = self._find_won_trigger_match(
            message_lower,
            channel_config.won_triggers,
            allow_username_wildcard=True,
        )
        matched_won_trigger = self._find_won_trigger_match(
            message_lower,
            channel_config.won_triggers,
            allow_username_wildcard=False,
        )

        matched_giveaway_trigger = None
        for trigger in channel_config.giveaway_triggers:
            if self._contains_trigger(message_lower, trigger):
                matched_giveaway_trigger = trigger
                break

        if matched_giveaway_trigger is not None:
            classes.add("giveaway_trigger")
        if matched_any_won_trigger is not None:
            classes.add("won_trigger")

        if matched_giveaway_trigger is not None:
            metadata["matched_giveaway_trigger"] = matched_giveaway_trigger
        if matched_any_won_trigger is not None:
            metadata["matched_won_trigger"] = matched_any_won_trigger

        giveaway_signature = None
        if matched_giveaway_trigger is not None:
            giveaway_signature = self._build_giveaway_signature(
                channel_name,
                matched_giveaway_trigger,
                channel_config.giveaway_message,
            )
            self._mark_giveaway_trigger_seen(channel_name, giveaway_signature, now)

        if self.chat_monitor is not None:
            self.chat_monitor.log_message(
                channel_name=channel_name,
                author_name=message.author.name,
                message_text=message.content,
                classes=classes,
                metadata=metadata,
            )

        # --- Won trigger: notify + reply after delay ---
        if matched_any_won_trigger is not None:
            self._mark_first_winner_seen(channel_name, now)

        if matched_won_trigger is not None:
            await self._handle_won_trigger(
                channel_name=channel_name,
                sender=message.author.name,
                message_text=message.content,
                channel_config=channel_config,
                send_channel=message.channel,
            )
            return

        if matched_giveaway_trigger and not self.activity_monitor.has_active_window(channel_name):
            self.activity_monitor.start_window(channel_name, matched_giveaway_trigger, now)
            log_line(
                f"Activity monitor started after trigger: {matched_giveaway_trigger}",
                "monitor_start",
                channel_name,
                account=self.account_name,
            )

        decision = self.activity_monitor.evaluate_if_ready(channel_name, now)
        if decision is None:
            return

        if not decision.enter:
            log_line(f"Giveaway ignored: {decision.reason}", "ignore", channel_name, account=self.account_name)
            return



        # --- Giveaway session guard ---
        current_score: float = float(decision.metrics.get("score", 0.0))

        active_session = self._get_active_giveaway_session(channel_name, now)

        # If evaluate_if_ready fired on a non-trigger message, matched_giveaway_trigger is None
        # and giveaway_signature would be built with an empty trigger part — different from the
        # stored session signature. Reuse the session's own signature in that case so the guard
        # is always applied while the session is alive.
        if giveaway_signature is None:
            if active_session is not None:
                giveaway_signature = str(active_session.get("signature", ""))
            else:
                giveaway_signature = self._build_giveaway_signature(
                    channel_name,
                    matched_giveaway_trigger,
                    channel_config.giveaway_message,
                )

        if active_session is not None:
            active_session["last_trigger_seen_at"] = now
            winner_seen_at = float(active_session.get("winner_seen_at", 0.0))
            if winner_seen_at > 0.0:
                remaining = int(max(0.0, self.GIVEAWAY_END_AFTER_FIRST_WIN_S - (now - winner_seen_at)))
                log_line(
                    f"Giveaway trigger ignored - session ending after winner ({remaining}s until reset)",
                    "ignore",
                    channel_name,
                    account=self.account_name,
                )
                return

            required_score = float(active_session.get("next_join_score", 0.0))
            last_score = float(active_session.get("last_score", 0.0))
            if current_score < required_score:
                log_line(
                    f"Active giveaway blocked (score {current_score:.3f} < next required {required_score:.3f})",
                    "ignore",
                    channel_name,
                    account=self.account_name,
                )
                return
            log_line(
                f"Active giveaway rejoin allowed ({current_score:.3f} >= {required_score:.3f}, last join score {last_score:.3f})",
                "join",
                channel_name,
                account=self.account_name,
            )

        if self.config.notification.enabled:
            self.notifier.send_notification(
                channel_name,
                message.content,
                title="Giveaway detected!",
                account=self.account_name,
            )

        log_line(f"Decision: {decision.reason}", "decision", channel_name, account=self.account_name)

        # Reserve the session BEFORE sleeping so any concurrent trigger sees the active
        # session and gets blocked by the score guard during the delay window.
        self._record_successful_join(channel_name, giveaway_signature, now, current_score)

        delay_sec = self._get_random_delay_s(channel_name, channel_config.delay_ms)
        if delay_sec > 0:
            await asyncio.sleep(delay_sec)

        log_line(f"Sending: {channel_config.giveaway_message}", "send", channel_name, account=self.account_name)
        try:
            await message.channel.send(channel_config.giveaway_message)
        except Exception as send_error:
            log_line(
                f"Send failed: {send_error}",
                "ignore",
                channel_name,
                account=self.account_name,
            )
            return
        self._increment_channel_stat(channel_name, "giveaways")

