from __future__ import annotations

from colorama import Fore, Style, init
from typing import Callable

init(autoreset=True)

# Optional hook: when set, log_line sends to GUI instead of printing to stdout.
_gui_hook: Callable[[str, str, str | None, str | None], None] | None = None


def set_gui_hook(hook: Callable[[str, str, str | None, str | None], None] | None) -> None:
    global _gui_hook
    _gui_hook = hook


_COLOR_BY_KIND = {
    "join": Fore.LIGHTBLACK_EX,
    "monitor_start": Fore.LIGHTBLACK_EX,
    "ignore": Fore.RED,
    "win": Fore.GREEN,
    "notification": Fore.LIGHTCYAN_EX,
    "send": Fore.WHITE,
    "giveaway_active": Fore.MAGENTA,
    "giveaway_inactive": Fore.MAGENTA,
    "decision": Fore.LIGHTBLUE_EX,
    "cooldown": Fore.YELLOW,
    "other": Fore.LIGHTBLACK_EX,
}

_ACCOUNT_COL_WIDTH = 20
_CHANNEL_COL_WIDTH = 22


def log_line(message: str, kind: str = "other", channel: str | None = None, account: str | None = None) -> None:
    if _gui_hook is not None:
        _gui_hook(message, kind, channel, account)
        return
    color = _COLOR_BY_KIND.get(kind, _COLOR_BY_KIND["other"])
    if account or channel:
        account_col = (f"[{account}]" if account else "").ljust(_ACCOUNT_COL_WIDTH)
        channel_col = (f"[{channel}]" if channel else "").ljust(_CHANNEL_COL_WIDTH)
        rendered = f"{account_col} {channel_col} {message}".rstrip()
    else:
        rendered = message
    print(f"{color}{rendered}{Style.RESET_ALL}")
