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

from config import BotConfig
from windows_notifier import WindowsNotifier


class TwitchBot(commands.Cog):
    """Bot that automatically joins giveaways across multiple Twitch channels"""

    def __init__(self, bot: commands.Bot, config: BotConfig):
        self.bot = bot
        self.config = config
        self.notifier = WindowsNotifier(config.notification)
        self.is_shutting_down = False

        # Map channel names to their configs
        self.channels_map = {ch.name: ch for ch in config.channels}
        # Per-channel main trigger counters.
        self.trigger_counters: dict[str, int] = {ch.name: 0 for ch in config.channels}
        # Last trigger hit timestamp per channel (monotonic seconds).
        self.trigger_last_hit: dict[str, float] = {ch.name: 0.0 for ch in config.channels}
        # Temporary extra repetitions required after each trigger-based entry.
        self.trigger_threshold_bonus: dict[str, int] = {ch.name: 0 for ch in config.channels}
        # Timestamp of last won-trigger fire per channel and sender bot name (monotonic seconds)
        self.won_last_triggered: dict[tuple[str, str], float] = {}
        # Cooldown between won-trigger responses (seconds)
        self.WON_COOLDOWN_S: float = self.config.runtime.won_cooldown_s
        # Reset trigger chain if inactive for this duration.
        self.TRIGGER_TIMEOUT_S: float = self.config.runtime.trigger_timeout_s
        # Temporary threshold increase applied after each trigger-based entry.
        self.TRIGGER_THRESHOLD_INCREMENT: int = self.config.runtime.trigger_threshold_increment

        # Setup log files under a single logs directory.
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        self.wins_log_path = logs_dir / "wins.log"
        legacy_wins_log_path = Path(__file__).resolve().parent.parent / "wins.log"
        if legacy_wins_log_path.exists() and not self.wins_log_path.exists():
            legacy_wins_log_path.replace(self.wins_log_path)
        self.wins_log_path.touch(exist_ok=True)

        # Persistent per-channel counters for joins and wins.
        self.stats_log_path = logs_dir / "channel_stats.json"
        self.channel_stats = self._load_channel_stats()

    def _append_win_log(self, timestamp: str, channel_name: str):
        with open(self.wins_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] Won in #{channel_name}\n")

    def _load_channel_stats(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {ch.name: {"joined": 0, "won": 0} for ch in self.config.channels}
        if not self.stats_log_path.exists():
            return stats

        try:
            with open(self.stats_log_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return stats

        channels_payload = payload.get("channels", {}) if isinstance(payload, dict) else {}
        if not isinstance(channels_payload, dict):
            return stats

        for channel_name, values in channels_payload.items():
            if channel_name not in stats or not isinstance(values, dict):
                continue
            joined = int(values.get("joined", 0))
            won = int(values.get("won", 0))
            stats[channel_name]["joined"] = max(0, joined)
            stats[channel_name]["won"] = max(0, won)

        return stats

    def _save_channel_stats(self):
        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channels": self.channel_stats,
        }
        with open(self.stats_log_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _increment_channel_stat(self, channel_name: str, key: str):
        values = self.channel_stats.setdefault(channel_name, {"joined": 0, "won": 0})
        values[key] = max(0, int(values.get(key, 0))) + 1
        self._save_channel_stats()
        print(f"[📈] [{channel_name}] Stats -> joined: {values.get('joined', 0)} | won: {values.get('won', 0)}")

    def _reset_trigger_chain(self, channel_name: str, reason: str | None = None, reset_bonus: bool = False):
        self.trigger_counters[channel_name] = 0
        self.trigger_last_hit[channel_name] = 0.0
        if reset_bonus:
            self.trigger_threshold_bonus[channel_name] = 0
        if reason:
            print(f"[🔄] [{channel_name}] Trigger counter reset ({reason})")

    def _normalize_text(self, text: str) -> str:
        """Normalizes text for resilient trigger matching."""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _contains_trigger(self, message_text: str, trigger_text: str) -> bool:
        """Checks trigger using both raw and normalized forms. Supports * as wildcard."""
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

    def _get_random_delay_s(self, channel_name: str, delay_range_ms: tuple[int, int]) -> float:
        delay_min, delay_max = delay_range_ms
        chosen_ms = random.randint(delay_min, delay_max)
        delay_s = chosen_ms / 1000
        print(f"[⏳] [{channel_name}] Waiting {delay_s}s before replying... (range {delay_min}-{delay_max}ms)")
        return delay_s

    async def graceful_shutdown(self, reason: str = "manual interrupt"):
        """Shuts down the bot gracefully."""
        if self.is_shutting_down:
            return

        self.is_shutting_down = True
        print(f"\n[⏹️] Shutting down gracefully ({reason})...")

        try:
            await self.bot.close()
            print("[✓] Twitch connection closed")
        except Exception as error:
            print(f"[✗] Error closing bot: {error}")

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
        print(f"\n[✓] Bot connected as {self.bot.nick}")
        print(f"[✓] Listening to {len(self.config.channels)} channel(s): {channel_names}")
        print(f"[✓] Winner nickname: {self.config.nickname}")
        for ch in self.config.channels:
            giveaway_trigger = ", ".join(trigger for trigger in ch.giveaway_triggers if trigger) or "(none)"
            won_trigger = ", ".join(trigger for trigger in ch.won_triggers if trigger) or "(none)"
            stats = self.channel_stats.get(ch.name, {"joined": 0, "won": 0})
            print(f"## {ch.name} ##")
            print(f"    - giveaway trigger: {giveaway_trigger}")
            print(f"    - won trigger: {won_trigger}")
            print(f"    - stats: joined={stats.get('joined', 0)} won={stats.get('won', 0)}")
        print()

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

        channel_name = message.channel.name
        now = time.monotonic()

        channel_config = self.channels_map.get(channel_name)
        if not channel_config:
            return

        # Expire trigger chain if inactive for too long.
        timeout_seconds = self.TRIGGER_TIMEOUT_S
        if self.trigger_counters[channel_name] > 0 or self.trigger_threshold_bonus[channel_name] > 0:
            last_hit = self.trigger_last_hit.get(channel_name, 0.0)
            if last_hit > 0 and (now - last_hit) >= timeout_seconds:
                self._reset_trigger_chain(channel_name, "timeout", reset_bonus=True)

        message_lower = message.content.lower()

        # --- Won trigger: notify + reply after delay ---
        for trigger in channel_config.won_triggers:
            if self._contains_trigger(message_lower, trigger):
                sender = message.author.name
                cooldown_key = (channel_name, sender)
                last_won = self.won_last_triggered.get(cooldown_key, 0.0)
                if last_won > 0 and (now - last_won) < self.WON_COOLDOWN_S:
                    remaining = int(self.WON_COOLDOWN_S - (now - last_won))
                    print(f"[⏸] [{channel_name}] Won trigger from {sender} on cooldown ({remaining}s left) - skipping")
                    return

                self.won_last_triggered[cooldown_key] = now
                self._reset_trigger_chain(channel_name, reset_bonus=True)
                print(f"\n[🏆] Won giveaway in #{channel_name}!")
                print(f"[📝] Message: {message.content}")

                # Log the win.
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._append_win_log(timestamp, channel_name)
                self._increment_channel_stat(channel_name, "won")

                if self.config.notification.enabled:
                    self.notifier.send_notification(channel_name, message.content, title="🏆 You won the giveaway!")

                won_reply = f"{channel_config.won_prefix}{self.config.nickname}"
                if won_reply:
                    delay_sec = self._get_random_delay_s(channel_name, channel_config.delay_ms)
                    if delay_sec > 0:
                        await asyncio.sleep(delay_sec)
                    print(f"[📤] Sending: {won_reply}")
                    await message.channel.send(won_reply)
                return

        if (
            channel_config.break_chain_condition
            and self._contains_trigger(message_lower, channel_config.break_chain_condition)
        ):
            if self.trigger_counters[channel_name] > 0:
                self._reset_trigger_chain(channel_name, "break condition matched", reset_bonus=True)
            return

        # --- Main giveaway trigger: count repetitions, then enter ---
        for trigger in channel_config.giveaway_triggers:
            if self._contains_trigger(message_lower, trigger):
                self.trigger_counters[channel_name] += 1
                self.trigger_last_hit[channel_name] = now
                count = self.trigger_counters[channel_name]
                threshold = channel_config.repeat_enter_condition + self.trigger_threshold_bonus[channel_name]
                print(f"[📊] [{channel_name}] Giveaway trigger: {count}/{threshold}")

                if count < threshold:
                    return

                self.trigger_threshold_bonus[channel_name] += self.TRIGGER_THRESHOLD_INCREMENT
                next_threshold = channel_config.repeat_enter_condition + self.trigger_threshold_bonus[channel_name]
                self._reset_trigger_chain(channel_name)
                self.trigger_last_hit[channel_name] = now
                print(f"\n[🎰] Giveaway detected in #{channel_name}")
                print(f"[📝] Streamer: {channel_name}")
                print(f"[📝] User: {message.author.name}")
                print(f"[📖] Message: {message.content}")
                print(f"[⬆] [{channel_name}] Next trigger threshold increased to {next_threshold}")

                if self.config.notification.enabled:
                    self.notifier.send_notification(channel_name, message.content, title="🎰 Giveaway detected!")
            
                delay_sec = self._get_random_delay_s(channel_name, channel_config.delay_ms)
                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)

                print(f"[📤] Sending: {channel_config.giveaway_message}")
                await message.channel.send(channel_config.giveaway_message)
                self._increment_channel_stat(channel_name, "joined")

                return

