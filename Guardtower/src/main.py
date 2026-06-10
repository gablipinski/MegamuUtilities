#!/usr/bin/env python3
"""
Twitch bot that automatically joins giveaways across multiple channels using multiple accounts.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import sys
from twitchio.ext import commands
from app_version import APP_VERSION
from config import load_config, resolve_default_config_path
from bot import TwitchBot
from console_log import log_line
from license_manager import get_license_path, validate_license
from startup_logs import emit_startup_logs

_MAIN_WINDOW_WIDTH = 1220
_MAIN_WINDOW_HEIGHT = 820


def _is_missing_user_info_error(error: Exception) -> bool:
    message = str(error).lower()
    markers = (
        'account missing "username"',
        'account missing "oauth_token"',
        'account missing "nickname"',
        'twitch.username is not configured',
        'twitch.oauth_token is not configured',
        'nickname is not configured',
    )
    return any(marker in message for marker in markers)


def _centered_dialog_geometry(root, width: int, height: int) -> str:
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    # Align dialog center to the same centered position used by the main window.
    anchor_x = max(0, (screen_w - _MAIN_WINDOW_WIDTH) // 2)
    anchor_y = max(0, (screen_h - _MAIN_WINDOW_HEIGHT) // 2)
    x = max(0, anchor_x + (_MAIN_WINDOW_WIDTH - width) // 2)
    y = max(0, anchor_y + (_MAIN_WINDOW_HEIGHT - height) // 2)
    return f'{width}x{height}+{x}+{y}'


def _load_credentials_seed_values() -> tuple[str, str, str]:
    config_path = resolve_default_config_path()
    username = ''
    oauth_token = ''
    nickname = ''

    if not config_path.exists():
        return username, oauth_token, nickname

    try:
        with open(config_path, 'r', encoding='utf-8-sig') as file:
            payload = json.load(file)
    except Exception:
        return username, oauth_token, nickname

    accounts = payload.get('accounts')
    if isinstance(accounts, list) and accounts:
        first = accounts[0] if isinstance(accounts[0], dict) else {}
        username = str(first.get('username', '')).strip()
        oauth_token = str(first.get('oauth_token', '')).strip()
        nickname = str(first.get('nickname', '')).strip()
        return username, oauth_token, nickname

    twitch = payload.get('twitch', {})
    if isinstance(twitch, dict):
        username = str(twitch.get('username', '')).strip()
        oauth_token = str(twitch.get('oauth_token', '')).strip()
    nickname = str(payload.get('nickname', '')).strip()
    return username, oauth_token, nickname


def _save_startup_credentials(username: str, oauth_token: str, nickname: str) -> None:
    config_path = resolve_default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file '{config_path}' not found")

    with open(config_path, 'r', encoding='utf-8-sig') as file:
        payload = json.load(file)

    if 'accounts' in payload and isinstance(payload['accounts'], list):
        if not payload['accounts']:
            payload['accounts'].append({})
        first_account = payload['accounts'][0]
        if not isinstance(first_account, dict):
            first_account = {}
            payload['accounts'][0] = first_account
        first_account['username'] = username
        first_account['oauth_token'] = oauth_token
        first_account['nickname'] = nickname
    else:
        twitch = payload.get('twitch')
        if not isinstance(twitch, dict):
            twitch = {}
            payload['twitch'] = twitch
        twitch['username'] = username
        twitch['oauth_token'] = oauth_token
        payload['nickname'] = nickname

    with open(config_path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def _show_startup_credentials_dialog() -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return False

    username_seed, token_seed, nickname_seed = _load_credentials_seed_values()
    result = {'saved': False}

    colors = {
        'bg': '#111418',
        'panel': '#171b21',
        'panel_alt': '#1d232b',
        'border': '#2b3440',
        'text': '#e7ecf3',
        'muted': '#9aa7b7',
        'accent': '#2f81f7',
        'accent_hover': '#1f6fe0',
        'danger': '#c2494b',
        'danger_hover': '#a6383b',
        'input_bg': '#0f1318',
    }

    root = tk.Tk()
    root.withdraw()

    dialog = tk.Toplevel(root)
    dialog.title('Guardtower - Configure User Info')
    dialog.geometry(_centered_dialog_geometry(root, 640, 360))
    dialog.resizable(False, False)
    dialog.configure(bg=colors['bg'])
    dialog.transient(root)
    dialog.grab_set()

    panel = tk.Frame(
        dialog,
        bg=colors['panel'],
        highlightthickness=1,
        highlightbackground=colors['border'],
        padx=16,
        pady=16,
    )
    panel.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    tk.Label(
        panel,
        text='Complete your account information to start Guardtower',
        bg=colors['panel'],
        fg=colors['text'],
        font=('Segoe UI Semibold', 11),
        anchor='w',
    ).pack(fill=tk.X)

    tk.Label(
        panel,
        text='These values are saved to config.json and used at startup.',
        bg=colors['panel'],
        fg=colors['muted'],
        font=('Segoe UI', 9),
        anchor='w',
    ).pack(fill=tk.X, pady=(4, 12))

    fields = tk.Frame(panel, bg=colors['panel'])
    fields.pack(fill=tk.BOTH, expand=True)

    username_var = tk.StringVar(value=username_seed)
    token_var = tk.StringVar(value=token_seed)
    nickname_var = tk.StringVar(value=nickname_seed)

    def _entry_row(parent, title: str, var: tk.StringVar, show_char: str | None = None):
        row = tk.Frame(parent, bg=colors['panel'])
        row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            row,
            text=title,
            bg=colors['panel'],
            fg=colors['muted'],
            font=('Segoe UI', 9),
            anchor='w',
            width=14,
        ).pack(side=tk.LEFT)

        entry = tk.Entry(
            row,
            textvariable=var,
            bg=colors['input_bg'],
            fg=colors['text'],
            insertbackground=colors['text'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=colors['border'],
            highlightcolor=colors['accent'],
            font=('Segoe UI', 10),
            show=show_char,
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return entry

    username_entry = _entry_row(fields, 'Username', username_var)
    _entry_row(fields, 'OAuth Token', token_var)
    _entry_row(fields, 'Nickname', nickname_var)

    def _make_button(parent, text: str, command, *, accent: bool = False, danger: bool = False):
        bg = colors['panel_alt']
        hover = '#2a313a'
        fg = colors['text']
        if accent:
            bg = colors['accent']
            hover = colors['accent_hover']
            fg = '#ffffff'
        elif danger:
            bg = colors['danger']
            hover = colors['danger_hover']
            fg = '#ffffff'

        return tk.Button(
            parent,
            text=text,
            command=command,
            relief=tk.FLAT,
            bd=0,
            cursor='hand2',
            padx=10,
            pady=6,
            bg=bg,
            fg=fg,
            activebackground=hover,
            activeforeground='#ffffff',
            highlightthickness=1,
            highlightbackground=colors['border'],
            highlightcolor=colors['accent'],
            font=('Segoe UI', 9),
            width=12,
        )

    footer = tk.Frame(panel, bg=colors['panel'])
    footer.pack(fill=tk.X, pady=(4, 0))

    def _cancel() -> None:
        dialog.destroy()

    def _save() -> None:
        username = username_var.get().strip()
        oauth_token = token_var.get().strip()
        nickname = nickname_var.get().strip()

        if not username:
            messagebox.showerror('Missing data', 'Username is required.', parent=dialog)
            return
        if not oauth_token:
            messagebox.showerror('Missing data', 'OAuth token is required.', parent=dialog)
            return
        if not nickname:
            messagebox.showerror('Missing data', 'Nickname is required.', parent=dialog)
            return

        try:
            _save_startup_credentials(username, oauth_token, nickname)
        except Exception as exc:
            messagebox.showerror('Save error', f'Failed to save config.json:\n{exc}', parent=dialog)
            return

        result['saved'] = True
        dialog.destroy()

    _make_button(footer, 'Save', _save, accent=True).pack(side=tk.RIGHT)
    _make_button(footer, 'Cancel', _cancel, danger=True).pack(side=tk.RIGHT, padx=(0, 8))

    username_entry.focus_set()
    dialog.protocol('WM_DELETE_WINDOW', _cancel)
    root.wait_window(dialog)
    root.destroy()
    return result['saved']


def _show_startup_error_dialog(title: str, message: str) -> None:
    """Best-effort error dialog for frozen GUI builds with no console."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except Exception:
        # If Tk is unavailable, keep original behavior (console log only).
        return


def _format_config_error_message(error: Exception) -> str:
    config_path = resolve_default_config_path()
    return (
        'Startup failed due to invalid configuration.\n\n'
        f'Details: {error}\n\n'
        f'Please update: {config_path}\n\n'
        'Typical fix after installation:\n'
        '- Fill accounts[].username\n'
        '- Fill accounts[].oauth_token\n'
        '- Fill accounts[].nickname\n\n'
        'Then launch the app again.'
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Twitch bot runtime.')
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {APP_VERSION}',
    )
    parser.add_argument(
        '--log',
        action='store_true',
        help='Enable file logging to logs/ while keeping normal bot behavior.',
    )
    parser.add_argument(
        '--log-only',
        action='store_true',
        help='Run in logging-only mode (no giveaway detection and no trigger responses).',
    )
    parser.add_argument(
        '--gui',
        action='store_true',
        default=True,
        help='Launch the Tkinter monitor UI (default when no flags are given).',
    )
    parser.add_argument(
        '--no-gui',
        action='store_true',
        help='Disable the TUI and use plain terminal output.',
    )
    return parser.parse_args()


async def run_bot_account(account, config, args):
    """Run a single bot instance for one account."""
    bot = commands.Bot(
        token=account.oauth_token,
        nick=account.username,
        prefix='§',
        initial_channels=[ch.name for ch in config.channels]
    )
    
    twitch_bot = TwitchBot(
        bot,
        config,
        account_name=account.username,
        account_nickname=account.nickname,
        ignored_usernames=account.ignored_usernames,
        log_only_mode=args.log_only,
        enable_logging=(args.log or args.log_only),
    )
    bot.add_cog(twitch_bot)
    await bot.start()


async def main():
    args = parse_args()

    try:
        valid, message = validate_license(get_license_path())
        if not valid:
            log_line(message, 'ignore')
            sys.exit(1)

        config = load_config()
        emit_startup_logs(config, args)
        print()
        print()

        bot_tasks = []
        for account in config.accounts:
            task = run_bot_account(account, config, args)
            bot_tasks.append(task)

        await asyncio.gather(*bot_tasks, return_exceptions=False)
    except FileNotFoundError as e:
        log_line(f'Erro: {e}', 'other')
        sys.exit(1)
    except ValueError as e:
        log_line(f'Erro de configuracao: {e}', 'other')
        sys.exit(1)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        import traceback
        log_line(f'Erro: {e}', 'other')
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    _args = parse_args()
    if _args.no_gui:
        _args.gui = False
    if _args.gui:
        try:
            from monitor_gui import run_gui
            try:
                _config = load_config()
            except ValueError as e:
                if _is_missing_user_info_error(e):
                    log_line(f'Missing user info at startup: {e}', 'ignore')
                    saved = _show_startup_credentials_dialog()
                    if not saved:
                        _show_startup_error_dialog(
                            'Guardtower - Setup Incomplete',
                            'Startup cancelled because account information was not completed.',
                        )
                        sys.exit(1)
                    _config = load_config()
                else:
                    raise

            run_gui(_config, _args)
        except (FileNotFoundError, ValueError) as e:
            log_line(f'Configuration error at startup: {e}', 'ignore')
            _show_startup_error_dialog('Guardtower - Startup Error', _format_config_error_message(e))
            sys.exit(1)
        except ImportError as e:
            log_line(f'GUI dependency missing: {e}', 'ignore')
            log_line('Tkinter is required and normally bundled with Python on Windows.', 'ignore')
            _show_startup_error_dialog(
                'Guardtower - GUI Dependency Missing',
                f'GUI dependency missing: {e}\n\nTkinter is required and normally bundled with Python on Windows.',
            )
            import traceback
            traceback.print_exc()
            input('Press Enter to exit...')
            sys.exit(1)
        except Exception as e:
            log_line(f'GUI error: {e}', 'ignore')
            _show_startup_error_dialog(
                'Guardtower - Startup Error',
                f'Unexpected startup error:\n{e}\n\nCheck your configuration and try again.',
            )
            import traceback
            traceback.print_exc()
            input('Press Enter to exit...')
            sys.exit(1)
        sys.exit(0)

    _exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_line('Bot encerrado', 'other')
    except SystemExit as e:
        _exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        if getattr(sys, 'frozen', False):
            input('\nPressione Enter para fechar...')
    sys.exit(_exit_code)
