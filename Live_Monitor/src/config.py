import json
import sys
from dataclasses import dataclass
from pathlib import Path

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
    delay_ms: tuple[int, int]
    won_triggers: list[str]
    won_prefix: str
    activity_monitor: dict[str, float | int] | None = None


def parse_delay_range_ms(value) -> tuple[int, int]:
    """Parses delay_ms from either int or [min, max] into a normalized tuple."""
    if isinstance(value, int):
        delay = max(0, int(value))
        return (delay, delay)

    if isinstance(value, list) and len(value) == 2:
        delay_min = max(0, int(value[0]))
        delay_max = max(0, int(value[1]))
        if delay_min > delay_max:
            delay_min, delay_max = delay_max, delay_min
        return (delay_min, delay_max)

    return (2000, 2000)

@dataclass
class AccountConfig:
    username: str
    oauth_token: str
    nickname: str
    ignored_usernames: list[str]


@dataclass
class TwitchConfig:
    username: str
    oauth_token: str


@dataclass
class NotificationConfig:
    enabled: bool = DEFAULT_NOTIFICATION_ENABLED
    message: str = DEFAULT_NOTIFICATION_MESSAGE


@dataclass
class RuntimeConfig:
    won_cooldown_s: float = 600.0


@dataclass
class ActivityMonitorConfig:
    baseline_window_s: float = 300.0
    monitor_window_s: float = 25.0
    min_messages_in_window: int = 8
    min_unique_chatters: int = 4
    enter_score_threshold: float = 1.6

@dataclass
class BotConfig:
    accounts: list[AccountConfig]
    channels: list[ChannelConfig]
    notification: NotificationConfig
    runtime: RuntimeConfig
    activity_monitor: ActivityMonitorConfig
    # Legacy support: keep twitch and nickname for backward compatibility
    twitch: TwitchConfig | None = None
    nickname: str | None = None


def parse_activity_monitor_config(value: dict | None, defaults: ActivityMonitorConfig | None = None) -> ActivityMonitorConfig:
    base = defaults or ActivityMonitorConfig()
    payload = value if isinstance(value, dict) else {}
    return ActivityMonitorConfig(
        baseline_window_s=float(payload.get('baseline_window_s', base.baseline_window_s)),
        monitor_window_s=float(payload.get('monitor_window_s', base.monitor_window_s)),
        min_messages_in_window=int(payload.get('min_messages_in_window', base.min_messages_in_window)),
        min_unique_chatters=int(payload.get('min_unique_chatters', base.min_unique_chatters)),
        enter_score_threshold=float(payload.get('enter_score_threshold', base.enter_score_threshold)),
    )


def resolve_default_config_path() -> Path:
    """Finds the default config path, preferring external editable configs."""
    candidates: list[Path] = []

    # PyInstaller onefile/onedir executable location
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / 'configs' / 'config.json')

    # Current working directory (useful when launched from project root)
    cwd = Path.cwd().resolve()
    candidates.append(cwd / 'configs' / 'config.json')

    # Source tree fallback
    candidates.append(DEFAULT_CONFIG_PATH)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]

def load_config(config_file: str | None = None) -> BotConfig:
    """Loads configuration from JSON file. Supports both old (single account) and new (multiple accounts) format."""

    config_path = Path(config_file) if config_file else resolve_default_config_path()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file '{config_path}' not found")

    with open(config_path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)

    if not data.get('channels'):
        raise ValueError('No channels configured')

    activity_monitor_defaults = parse_activity_monitor_config(data.get('activity_monitor'))

    # Parse accounts - support both new format (accounts array) and old format (single twitch account)
    accounts: list[AccountConfig] = []
    twitch_legacy: TwitchConfig | None = None
    nickname_legacy: str | None = None

    if 'accounts' in data and isinstance(data['accounts'], list):
        # New format: multiple accounts
        for acc in data['accounts']:
            if not acc.get('username'):
                raise ValueError('Account missing "username"')
            if not acc.get('oauth_token'):
                raise ValueError('Account missing "oauth_token"')
            if not acc.get('nickname'):
                raise ValueError('Account missing "nickname"')
            
            accounts.append(AccountConfig(
                username=acc['username'],
                oauth_token=acc['oauth_token'],
                nickname=acc['nickname'],
                ignored_usernames=[
                    str(value).strip()
                    for value in acc.get('ignored_usernames', [])
                    if str(value).strip()
                ],
            ))
    else:
        # Legacy format: single account (twitch + nickname)
        if not data.get('twitch', {}).get('username'):
            raise ValueError('twitch.username is not configured')
        if not data.get('twitch', {}).get('oauth_token'):
            raise ValueError('twitch.oauth_token is not configured')
        if not data.get('nickname'):
            raise ValueError('nickname is not configured')
        
        username = data['twitch']['username']
        oauth_token = data['twitch']['oauth_token']
        nickname = str(data.get('nickname', '')).strip()
        
        accounts.append(AccountConfig(
            username=username,
            oauth_token=oauth_token,
            nickname=nickname,
            ignored_usernames=[],
        ))
        
        # Keep for backward compatibility
        twitch_legacy = TwitchConfig(username=username, oauth_token=oauth_token)
        nickname_legacy = nickname

    # Parse channels (using first account for context in old format)
    channels = []
    first_account = accounts[0] if accounts else None
    
    for ch in data['channels']:
        if not ch.get('name'):
            raise ValueError("Channel missing 'name'")

        context = {
            'username': str(first_account.username if first_account else '').strip(),
            'nickname': str(first_account.nickname if first_account else '').strip(),
            'channel': str(ch.get('name', '')).strip(),
        }
        won_context = {
            'channel': str(ch.get('name', '')).strip(),
        }

        giveaway_triggers = [t for t in ch.get('giveaway_triggers', []) if t.strip()]
        won_triggers = [t for t in ch.get('won_triggers', []) if t.strip()]

        channel = ChannelConfig(
            name=ch['name'],
            giveaway_triggers=[t.lower() for t in format_list(giveaway_triggers, context)],
            giveaway_message=format_with_context(ch.get('giveaway_message', 'Joining!'), context),
            delay_ms=parse_delay_range_ms(ch.get('delay_ms', 2000)),
            won_triggers=[t.lower() for t in format_list(won_triggers, won_context)],
            won_prefix=format_with_context(ch.get('won_prefix', ch.get('won_message', '')), context),
            activity_monitor=parse_activity_monitor_config(
                ch.get('activity_monitor'),
                defaults=activity_monitor_defaults,
            ).__dict__ if ch.get('activity_monitor') else None,
        )
        channels.append(channel)

    notification = NotificationConfig()
    runtime = RuntimeConfig(
        won_cooldown_s=float(data.get('won_cooldown_s', 600.0)),
    )

    return BotConfig(
        accounts=accounts,
        channels=channels,
        notification=notification,
        runtime=runtime,
        activity_monitor=activity_monitor_defaults,
        twitch=twitch_legacy,
        nickname=nickname_legacy,
    )
