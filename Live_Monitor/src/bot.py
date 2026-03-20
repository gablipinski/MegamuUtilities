import asyncio
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
import twitchio
from twitchio.ext import commands
from config import BotConfig, ChannelConfig
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
        # Per-channel backup trigger counters
        self.backup_counters: dict[str, int] = {ch.name: 0 for ch in config.channels}
        # Last backup hit timestamp per channel (monotonic seconds)
        self.backup_last_hit: dict[str, float] = {ch.name: 0.0 for ch in config.channels}
        # Temporary extra repetitions required after each backup-based entry
        self.backup_threshold_bonus: dict[str, int] = {ch.name: 0 for ch in config.channels}
        # Timestamp of last won-trigger fire per sender bot name (monotonic seconds)
        self.won_last_triggered: dict[str, float] = {}
        # Cooldown between won-trigger responses (seconds)
        self.WON_COOLDOWN_S: float = 600.0
        # Temporary threshold increase applied after each backup-based entry
        self.BACKUP_THRESHOLD_INCREMENT: int = 15
        # Setup win logger
        self.wins_log_path = Path(__file__).resolve().parent.parent / 'wins.log'
        self.wins_log_path.touch(exist_ok=True)

    def _reset_backup_chain(self, channel_name: str, reason: str | None = None, reset_bonus: bool = False):
        self.backup_counters[channel_name] = 0
        self.backup_last_hit[channel_name] = 0.0
        if reset_bonus:
            self.backup_threshold_bonus[channel_name] = 0
        if reason:
            print(f'[🔄] [{channel_name}] Backup counter reset ({reason})')

    def _normalize_text(self, text: str) -> str:
        """Normalizes text for resilient trigger matching."""
        if not text:
            return ''
        normalized = unicodedata.normalize('NFKD', text)
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _contains_trigger(self, message_text: str, trigger_text: str) -> bool:
        """Checks trigger using both raw and normalized forms. Supports * as wildcard."""
        if not trigger_text:
            return False
        if '*' not in trigger_text:
            if trigger_text in message_text:
                return True
            return self._normalize_text(trigger_text) in self._normalize_text(message_text)
        # Wildcard mode: normalize each literal segment and build a regex
        norm_message = self._normalize_text(message_text)
        parts = [re.escape(self._normalize_text(p)) for p in trigger_text.split('*')]
        pattern = '.*'.join(parts)
        try:
            return bool(re.search(pattern, norm_message, re.DOTALL))
        except re.error:
            return False

    async def graceful_shutdown(self, reason: str = 'manual interrupt'):
        """Shuts down the bot gracefully."""

        if self.is_shutting_down:
            return

        self.is_shutting_down = True
        print(f'\n[⏹️] Shutting down gracefully ({reason})...')

        try:
            await self.bot.close()
            print('[✓] Twitch connection closed')
        except Exception as error:
            print(f'[✗] Error closing bot: {error}')
    
    @commands.Cog.event()
    async def event_command_error(self, ctx, error):
        """Silently ignore unknown commands (e.g. !sorteio used as a backup trigger)"""
        if isinstance(error, commands.CommandNotFound):
            return
        raise error

    @commands.Cog.event()
    async def event_ready(self):
        """Called when bot is connected and ready"""
        print(f'\n[✓] Bot connected as {self.bot.nick}')
        print(f'[✓] Listening to {len(self.config.channels)} channel(s):')
        print(f'[✓] Winner nickname: {self.config.nickname}')
        for ch in self.config.channels:
            print(f'    - #{ch.name}')
            print(f'      giveaway_triggers: {ch.giveaway_triggers}')
            print(f'      giveaway_message: {ch.giveaway_message}')
            print(f'      won_triggers: {ch.won_triggers}')
            print(f'      won_prefix: {ch.won_prefix}')
            print(f'      delay_ms: {ch.delay_ms}')
            print(f'      backup_trigger: {ch.backup_trigger}')
            print(f'      repeat_enter_condition: {ch.repeat_enter_condition}')
            print(f'      break_chain_condition: {ch.break_chain_condition}')
            print(f'      backup_timeout_ms: {ch.backup_timeout_ms}')
            print(f'      backup_increment_per_hit: {self.BACKUP_THRESHOLD_INCREMENT} (temporary, resets on timeout)')
            print(f'      won_cooldown: {int(self.WON_COOLDOWN_S)}s (prevents duplicate won replies)')
        print()
    
    @commands.Cog.event()
    async def event_message(self, message: twitchio.Message):
        """Called for every chat message received"""

        if self.is_shutting_down:
            return

        # System/echo messages arrive without an author
        if message.author is None:
            return

        # Ignore the bot's own messages
        if message.author.name == self.bot.nick:
            return
        
        channel_name = message.channel.name
        channel_config = self.channels_map.get(channel_name)
        
        if not channel_config:
            return
        
        # Expire backup chain if inactive for too long.
        now = time.monotonic()
        if channel_config.backup_timeout_ms > 0 and (
            self.backup_counters[channel_name] > 0 or self.backup_threshold_bonus[channel_name] > 0
        ):
            timeout_seconds = channel_config.backup_timeout_ms / 1000
            last_hit = self.backup_last_hit.get(channel_name, 0.0)
            if last_hit > 0 and (now - last_hit) >= timeout_seconds:
                self._reset_backup_chain(channel_name, 'timeout', reset_bonus=True)

        message_lower = message.content.lower()

        # --- Won trigger: notify + reply after delay ---
        for trigger in channel_config.won_triggers:
            if self._contains_trigger(message_lower, trigger):
                sender = message.author.name
                last_won = self.won_last_triggered.get(sender, 0.0)
                if last_won > 0 and (now - last_won) < self.WON_COOLDOWN_S:
                    remaining = int(self.WON_COOLDOWN_S - (now - last_won))
                    print(f'[⏸] [{channel_name}] Won trigger from {sender} on cooldown ({remaining}s left) — skipping')
                    return
                self.won_last_triggered[sender] = now
                self._reset_backup_chain(channel_name, reset_bonus=True)
                print(f'\n[🏆] Won giveaway in #{channel_name}!')
                print(f'[📝] Message: {message.content}')
                # Log the win
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(self.wins_log_path, 'a', encoding='utf-8') as f:
                    f.write(f'[{timestamp}] Won in #{channel_name}\n')
                if self.config.notification.enabled:
                    self.notifier.send_notification(channel_name, message.content, title='🏆 You won the giveaway!')
                won_reply = f"{channel_config.won_prefix}{self.config.nickname}"
                if won_reply:
                    if channel_config.delay_ms > 0:
                        delay_sec = channel_config.delay_ms / 1000
                        print(f'[⏳] Waiting {delay_sec}s before replying...')
                        await asyncio.sleep(delay_sec)
                    print(f'[📤] Sending: {won_reply}')
                    await message.channel.send(won_reply)
                return

        # --- Main giveaway trigger: wait delay and enter ---
        for trigger in channel_config.giveaway_triggers:
            if self._contains_trigger(message_lower, trigger):
                self._reset_backup_chain(channel_name, reset_bonus=True)  # reset backup routine
                print(f'\n[🎰] Giveaway detected in #{channel_name}')
                print(f'[📝] Streamer: {message.author.name}')
                print(f'[📖] Message: {message.content}')
                
                if channel_config.delay_ms > 0:
                    delay_sec = channel_config.delay_ms / 1000
                    print(f'[⏳] Waiting {delay_sec}s before replying...')
                    await asyncio.sleep(delay_sec)
                
                print(f'[📤] Sending: {channel_config.giveaway_message}')
                await message.channel.send(channel_config.giveaway_message)
                
                if self.config.notification.enabled:
                    self.notifier.send_notification(channel_name, message.content, title='🎰 Giveaway detected!')
                return

        # --- Backup trigger logic ---
        if channel_config.backup_trigger:
            # Break condition resets the counter
            if (
                channel_config.break_chain_condition
                and self._contains_trigger(message_lower, channel_config.break_chain_condition)
            ):
                if self.backup_counters[channel_name] > 0:
                    self._reset_backup_chain(channel_name, 'break condition matched', reset_bonus=True)
                return

            # Count backup trigger hits
            if self._contains_trigger(message_lower, channel_config.backup_trigger):
                self.backup_counters[channel_name] += 1
                self.backup_last_hit[channel_name] = now
                count = self.backup_counters[channel_name]
                threshold = channel_config.repeat_enter_condition + self.backup_threshold_bonus[channel_name]
                print(f'[📊] [{channel_name}] Backup trigger: {count}/{threshold}')

                if count >= threshold:
                    # Each backup-based entry makes the next one harder by the configured increment.
                    self.backup_threshold_bonus[channel_name] += self.BACKUP_THRESHOLD_INCREMENT
                    next_threshold = channel_config.repeat_enter_condition + self.backup_threshold_bonus[channel_name]
                    self._reset_backup_chain(channel_name)
                    self.backup_last_hit[channel_name] = now
                    print(f'\n[🎰] Backup threshold reached in #{channel_name} — entering giveaway')
                    print(f'[⬆] [{channel_name}] Next backup threshold increased to {next_threshold}')
                    
                    if channel_config.delay_ms > 0:
                        delay_sec = channel_config.delay_ms / 1000
                        print(f'[⏳] Waiting {delay_sec}s before replying...')
                        await asyncio.sleep(delay_sec)
                    
                    print(f'[📤] Sending: {channel_config.giveaway_message}')
                    await message.channel.send(channel_config.giveaway_message)
                    
                    if self.config.notification.enabled:
                        self.notifier.send_notification(channel_name, message.content, title='🎰 Giveaway detected (backup)!')
