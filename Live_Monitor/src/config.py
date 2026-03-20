import json
from dataclasses import dataclass
from pathlib import Path
from string import Formatter

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'configs' / 'config.json'
DEFAULT_NOTIFICATION_ENABLED = True
DEFAULT_NOTIFICATION_MESSAGE = 'Giveaway detected in {channel}! Message: {message}'


class SafeFormatDict(dict):
    """Dict that leaves unknown placeholders untouched."""

    def __missing__(self, key):
        return '{' + key + '}'


def format_with_context(value: str, context: dict[str, str]) -> str:
    """Expands placeholders in config text, e.g. @{username}."""
    return value.format_map(SafeFormatDict(context))


def format_list(values: list[str], context: dict[str, str]) -> list[str]:
    return [format_with_context(v, context) for v in values]

@dataclass
class ChannelConfig:
    name: str
    giveaway_triggers: list[str]
    giveaway_message: str
    delay_ms: int
    won_triggers: list[str]
    won_prefix: str
    backup_trigger: str           # message to count; empty = disabled
    repeat_enter_condition: int   # how many hits before entering giveaway
    break_chain_condition: str    # message that resets the counter; empty = disabled
    backup_timeout_ms: int        # reset backup chain if idle for this duration

@dataclass
class TwitchConfig:
    username: str
    oauth_token: str

@dataclass
class NotificationConfig:
    enabled: bool = DEFAULT_NOTIFICATION_ENABLED
    message: str = DEFAULT_NOTIFICATION_MESSAGE

@dataclass
class BotConfig:
    twitch: TwitchConfig
    nickname: str
    channels: list[ChannelConfig]
    notification: NotificationConfig

def load_config(config_file: str | None = None) -> BotConfig:
    """Loads configuration from JSON file"""

    config_path = Path(config_file) if config_file else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Config file '{config_path}' not found")

    with open(config_path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)

    # Basic validation
    if not data.get('twitch', {}).get('username'):
        raise ValueError('twitch.username is not configured')
    if not data.get('twitch', {}).get('oauth_token'):
        raise ValueError('twitch.oauth_token is not configured')
    if not data.get('nickname'):
        raise ValueError('nickname is not configured')
    if not data.get('channels'):
        raise ValueError('No channels configured')

    # Parse Twitch config
    twitch = TwitchConfig(
        username=data['twitch']['username'],
        oauth_token=data['twitch']['oauth_token']
    )

    # Parse channels
    channels = []
    for ch in data['channels']:
        if not ch.get('name'):
            raise ValueError("Channel missing 'name'")

        context = {
            'username': str(data.get('twitch', {}).get('username', '')).strip(),
            'nickname': str(data.get('nickname', '')).strip(),
            'channel': str(ch.get('name', '')).strip(),
        }

        giveaway_triggers = [t for t in ch.get('giveaway_triggers', []) if t.strip()]
        won_triggers = [t for t in ch.get('won_triggers', []) if t.strip()]

        channel = ChannelConfig(
            name=ch['name'],
            giveaway_triggers=[t.lower() for t in format_list(giveaway_triggers, context)],
            giveaway_message=format_with_context(ch.get('giveaway_message', 'Joining!'), context),
            delay_ms=ch.get('delay_ms', 2000),
            won_triggers=[t.lower() for t in format_list(won_triggers, context)],
            won_prefix=format_with_context(ch.get('won_prefix', ch.get('won_message', '')), context),
            backup_trigger=format_with_context(ch.get('backup_trigger', ''), context).strip().lower(),
            repeat_enter_condition=int(ch.get('repeat_enter_condition', 5)),
            break_chain_condition=format_with_context(ch.get('break_chain_condition', ''), context).strip().lower(),
            backup_timeout_ms=int(ch.get('backup_timeout_ms', 600000)),
        )
        channels.append(channel)

    notification = NotificationConfig()

    return BotConfig(
        twitch=twitch,
        nickname=str(data.get('nickname', '')).strip(),
        channels=channels,
        notification=notification,
    )
