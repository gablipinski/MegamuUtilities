"""
Tkinter GUI for the Twitch Giveaway Monitor.

This module ports the monitor from Textual to Tkinter while preserving the
existing layout behavior and runtime features.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import shutil
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as filedialog
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox

from twitchio.ext import commands  # type: ignore[import]

from app_version import APP_NAME, APP_VERSION
from bot import TwitchBot
from config import BotConfig, load_config, resolve_default_config_path
from console_log import set_gui_hook
from license_manager import get_license_path, get_machine_id, validate_license
from startup_logs import emit_startup_logs
from windows_notifier import DesktopNotificationService

GIVEAWAY_SESSION_DURATION_S = 300.0
IDLE_ALERT_THRESHOLD_S = 3600.0

_MAIN_WINDOW_WIDTH = 1220
_MAIN_WINDOW_HEIGHT = 820


def _is_missing_user_info_error(error: Exception) -> bool:
    message = str(error).lower()
    markers = (
        'at least one account is required',
        'account entry must be an object',
        'account missing "username"',
        'account missing "oauth_token"',
        'account missing "nickname"',
        'twitch.username is not configured',
        'twitch.oauth_token is not configured',
        'nickname is not configured',
    )
    return any(marker in message for marker in markers)


def _load_credentials_seed_values() -> tuple[str, str, str]:
    config_path = resolve_default_config_path()
    username, oauth_token, nickname = '', '', ''
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
        return (
            str(first.get('username', '')).strip(),
            str(first.get('oauth_token', '')).strip(),
            str(first.get('nickname', '')).strip(),
        )
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


@dataclass
class ChannelView:
    name: str
    frame: tk.Frame
    header_row: tk.Frame
    header: tk.Label
    led: tk.Canvas
    led_circle: int
    log_widget: tk.Text
    log_lines: list[tuple[str, str]] = field(default_factory=list)
    is_online: bool = False
    status: str = "idle"
    status_since: float = 0.0
    session_giveaways: int = 0
    session_wins: int = 0
    session_win_recorded: bool = False
    win_at: float = 0.0
    idle_alert_active: bool = False


class MonitorUI:
    _ONLINE_POLL_INTERVAL_S = 60.0
    _CHANNEL_LOG_BUFFER_MAX = 500
    _SYSTEM_LOG_BUFFER_MAX = 500

    def __init__(self, args: argparse.Namespace) -> None:
        self._bot_config: BotConfig | None = None
        self._bot_args = args

        self.root = tk.Tk()
        self._colors = {
            "bg": "#111418",
            "panel": "#171b21",
            "panel_alt": "#1d232b",
            "border": "#2b3440",
            "text": "#e7ecf3",
            "muted": "#9aa7b7",
            "accent": "#2f81f7",
            "accent_hover": "#1f6fe0",
            "danger": "#c2494b",
            "danger_hover": "#a6383b",
            "success": "#26a269",
            "input_bg": "#0f1318",
        }
        self._kind_fg = {
            "join": "#9aa7b7",
            "monitor_start": "#9aa7b7",
            "ignore": "#ff6b6b",
            "win": "#26a269",
            "notification": "#6fe0ff",
            "send": "#e7ecf3",
            "giveaway_active": "#7fb2ff",
            "giveaway_inactive": "#82d482",
            "decision": "#7ab0ff",
            "cooldown": "#e3b341",
            "other": "#9aa7b7",
        }
        self._font_ui = ("Segoe UI", 10)
        self._font_title = ("Segoe UI Semibold", 16)
        self._font_title_sm = ("Segoe UI Semibold", 10)

        self._event_queue: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._shutdown_event = threading.Event()
        self._runtime_thread: threading.Thread | None = None
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._bot_instances: list[TwitchBot] = []
        self._bot_clients: list[commands.Bot] = []
        self._online_channels: set[str] = set()

        self._app_icon: tk.PhotoImage | None = None
        self._system_log_lines: list[tuple[str, str]] = []
        self._multitwitch_url: str = "(no channels online)"
        self._channel_views: dict[str, ChannelView] = {}
        self._desktop_notifications = DesktopNotificationService(app_id="Guardtower")
        self._active_confirmation_dialog: tk.Toplevel | None = None
        self._notification_center_window: tk.Toplevel | None = None
        self._notification_listbox: tk.Listbox | None = None
        self._notification_center_ids: list[str] = []
        self._notification_history: list[dict[str, object]] = []
        self._notification_seq = 0
        self._notification_bell_button: tk.Button | None = None

        self._load_app_icon()
        self._apply_app_icon(self.root)
        self.root.withdraw()
        if not self._check_license_startup():
            self.root.destroy()
            sys.exit(0)
        config = self._load_config_with_credential_check()
        if config is None:
            self.root.destroy()
            sys.exit(0)
        self._bot_config = config
        global GIVEAWAY_SESSION_DURATION_S
        GIVEAWAY_SESSION_DURATION_S = config.runtime.giveaway_end_after_win_s
        self._build_ui()
        self._create_channel_cards()
        self._reorder_channel_cards(set())

        self.root.title(f"{APP_NAME} Monitor v{APP_VERSION}")
        self.root.geometry("1220x820")
        self.root.minsize(980, 620)
        self.root.configure(bg=self._colors["bg"])
        self.root.option_add("*Font", self._font_ui)

        set_gui_hook(self._queue_log_event)
        self._start_runtime_thread()

        self.root.after(120, self._drain_events)
        self.root.after(1000, self._tick_statuses)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<FocusIn>", self._on_root_focus)
        self.root.deiconify()

    def run(self) -> None:
        self.root.mainloop()

    def _load_app_icon(self) -> None:
        icons_dirs = []
        if getattr(sys, "frozen", False):
            icons_dirs.append(Path(sys.executable).resolve().parent / "icons")
        icons_dirs.append(Path(__file__).resolve().parent.parent / "icons")

        for icons_dir in icons_dirs:
            for name in ("guardtower.png", "guardtower.webp"):
                icon_path = icons_dir / name
                if not icon_path.exists():
                    continue
                try:
                    self._app_icon = tk.PhotoImage(file=str(icon_path))
                    return
                except Exception:
                    continue
        self._app_icon = None

    def _apply_app_icon(self, window: tk.Misc) -> None:
        if self._app_icon is None:
            return
        try:
            window.iconphoto(True, self._app_icon)
        except Exception:
            pass

    def _check_license_startup(self) -> bool:
        valid, message = validate_license(get_license_path())
        if valid:
            return True
        return self._show_activation_dialog(message)

    def _load_config_with_credential_check(self) -> BotConfig | None:
        try:
            return load_config()
        except ValueError as e:
            if not _is_missing_user_info_error(e):
                raise
        # Missing credentials — show the setup dialog (uses same Tk root, no double-instance)
        if not self._show_startup_user_info_dialog():
            return None
        return load_config()

    def _show_startup_user_info_dialog(self) -> bool:
        result: dict[str, bool] = {"saved": False}
        username_seed, token_seed, nickname_seed = _load_credentials_seed_values()

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        dlg_w, dlg_h = 640, 360
        anchor_x = max(0, (screen_w - _MAIN_WINDOW_WIDTH) // 2)
        anchor_y = max(0, (screen_h - _MAIN_WINDOW_HEIGHT) // 2)
        x = max(0, anchor_x + (_MAIN_WINDOW_WIDTH - dlg_w) // 2)
        y = max(0, anchor_y + (_MAIN_WINDOW_HEIGHT - dlg_h) // 2)

        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} - Configure User Info")
        dialog.geometry(f"{dlg_w}x{dlg_h}+{x}+{y}")
        dialog.resizable(False, False)
        dialog.configure(bg=self._colors["bg"])
        # Root is intentionally withdrawn at startup; transient-to-hidden-parent can
        # keep this dialog out of view on some Windows setups.
        dialog.attributes("-topmost", True)
        dialog.grab_set()
        self._apply_app_icon(dialog)
        dialog.lift()
        dialog.focus_force()
        dialog.after(250, lambda: dialog.attributes("-topmost", False))

        panel = tk.Frame(
            dialog,
            bg=self._colors["panel"],
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=16,
            pady=16,
        )
        panel.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            panel,
            text="Complete your account information to start Guardtower",
            bg=self._colors["panel"],
            fg=self._colors["text"],
            font=("Segoe UI Semibold", 11),
            anchor="w",
        ).pack(fill=tk.X)

        tk.Label(
            panel,
            text="These values are saved to config.json and used at startup.",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 12))

        fields = tk.Frame(panel, bg=self._colors["panel"])
        fields.pack(fill=tk.BOTH, expand=True)

        username_var = tk.StringVar(value=username_seed)
        token_var = tk.StringVar(value=token_seed)
        nickname_var = tk.StringVar(value=nickname_seed)

        def _entry_row(parent: tk.Frame, title: str, var: tk.StringVar) -> tk.Entry:
            row = tk.Frame(parent, bg=self._colors["panel"])
            row.pack(fill=tk.X, pady=(0, 10))
            tk.Label(
                row,
                text=title,
                bg=self._colors["panel"],
                fg=self._colors["muted"],
                font=("Segoe UI", 9),
                anchor="w",
                width=14,
            ).pack(side=tk.LEFT)
            entry = tk.Entry(
                row,
                textvariable=var,
                bg=self._colors["input_bg"],
                fg=self._colors["text"],
                insertbackground=self._colors["text"],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors["border"],
                highlightcolor=self._colors["accent"],
                font=("Segoe UI", 10),
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            return entry

        username_entry = _entry_row(fields, "Username", username_var)
        _entry_row(fields, "OAuth Token", token_var)
        _entry_row(fields, "Nickname", nickname_var)

        footer = tk.Frame(panel, bg=self._colors["panel"])
        footer.pack(fill=tk.X, pady=(4, 0))

        def _cancel() -> None:
            dialog.destroy()

        def _save() -> None:
            username = username_var.get().strip()
            oauth_token = token_var.get().strip()
            nickname = nickname_var.get().strip()
            if not username:
                messagebox.showerror("Missing data", "Username is required.", parent=dialog)
                return
            if not oauth_token:
                messagebox.showerror("Missing data", "OAuth token is required.", parent=dialog)
                return
            if not nickname:
                messagebox.showerror("Missing data", "Nickname is required.", parent=dialog)
                return
            try:
                _save_startup_credentials(username, oauth_token, nickname)
            except Exception as exc:
                messagebox.showerror("Save error", f"Failed to save config.json:\n{exc}", parent=dialog)
                return
            result["saved"] = True
            dialog.destroy()

        self._make_button(footer, text="Save", width=12, command=_save, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text="Cancel", width=12, command=_cancel, danger=True).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        username_entry.focus_set()
        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        self.root.wait_window(dialog)
        return result["saved"]

    def _show_activation_dialog(self, initial_message: str) -> bool:
        result: dict[str, bool] = {"activated": False}
        machine_id = get_machine_id()

        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} v{APP_VERSION} - Activation Required")
        dialog.geometry("480x340")
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        self._apply_app_icon(dialog)
        dialog.configure(bg=self._colors["bg"])
        dialog.lift()
        dialog.focus_force()

        tk.Label(
            dialog,
            text=f"{APP_NAME} v{APP_VERSION} - Activation Required",
            font=self._font_title_sm,
            bg=self._colors["bg"],
            fg=self._colors["text"],
        ).pack(pady=(18, 4))

        tk.Label(
            dialog,
            text="This software requires a valid license to run.",
            font=("Segoe UI", 10),
            bg=self._colors["bg"],
            fg=self._colors["muted"],
        ).pack()

        tk.Label(
            dialog,
            text="Your Machine ID:",
            font=("Segoe UI Semibold", 9),
            bg=self._colors["bg"],
            fg=self._colors["text"],
        ).pack(pady=(14, 2))

        mid_var = tk.StringVar(value=machine_id)
        mid_entry = tk.Entry(
            dialog,
            textvariable=mid_var,
            state="readonly",
            font=("Consolas", 12),
            justify="center",
            width=24,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            readonlybackground=self._colors["input_bg"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        )
        mid_entry.pack()

        def _copy_id() -> None:
            dialog.clipboard_clear()
            dialog.clipboard_append(machine_id)
            btn_copy.configure(text="Copied!")
            dialog.after(1500, lambda: btn_copy.configure(text="Copy Machine ID"))

        btn_copy = self._make_button(dialog, text="Copy Machine ID", width=20, command=_copy_id)
        btn_copy.pack(pady=(5, 0))

        tk.Label(
            dialog,
            text="Send this ID to the distributor to receive your license.dat",
            font=("Segoe UI", 8),
            fg=self._colors["muted"],
            bg=self._colors["bg"],
        ).pack(pady=(3, 10))

        status_var = tk.StringVar(value=initial_message.split("\n\n")[0])
        tk.Label(
            dialog,
            textvariable=status_var,
            fg="#ff8080",
            bg=self._colors["bg"],
            wraplength=440,
            font=("Segoe UI", 8),
            justify="center",
        ).pack(pady=(0, 10))

        def _browse_license() -> None:
            path_str = filedialog.askopenfilename(
                parent=dialog,
                title="Select license.dat",
                filetypes=[("License file", "*.dat"), ("All files", "*.*")],
            )
            if not path_str:
                return
            selected = Path(path_str)
            valid, msg = validate_license(selected)
            if not valid:
                status_var.set(msg.split("\n")[0])
                return
            target = get_license_path()
            try:
                shutil.copy2(selected, target)
            except Exception as exc:
                status_var.set(f"Could not copy license: {exc}\nCopy manually to: {target}")
                return
            open_folder = messagebox.askyesno(
                "License Activated",
                f"License copied successfully to:\n\n{target}\n\nOpen this folder now?",
                parent=dialog,
            )
            if open_folder:
                try:
                    os.startfile(str(target.parent))
                except Exception:
                    pass
            result["activated"] = True
            dialog.destroy()

        def _exit_app() -> None:
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=self._colors["bg"])
        btn_row.pack(pady=(0, 18))
        self._make_button(
            btn_row,
            text="Browse for license.dat...",
            width=26,
            command=_browse_license,
            accent=True,
        ).pack(side=tk.LEFT, padx=8)
        self._make_button(btn_row, text="Exit", width=10, command=_exit_app, danger=True).pack(
            side=tk.LEFT, padx=8
        )

        self.root.wait_window(dialog)
        return result["activated"]

    def _make_button(
        self,
        parent: tk.Misc,
        text: str,
        *,
        width: int,
        command,
        accent: bool = False,
        danger: bool = False,
    ) -> tk.Button:
        bg = self._colors["panel_alt"]
        hover_bg = "#2a313a"
        fg = self._colors["text"]

        if accent:
            bg = self._colors["accent"]
            hover_bg = self._colors["accent_hover"]
            fg = "#ffffff"
        elif danger:
            bg = self._colors["danger"]
            hover_bg = self._colors["danger_hover"]
            fg = "#ffffff"

        return tk.Button(
            parent,
            text=text,
            width=width,
            command=command,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            padx=8,
            pady=6,
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        )

    def _force_window_front(self, window: tk.Misc) -> None:
        """Best-effort foreground/topmost enforcement, with Win32 fallback."""
        try:
            window.update_idletasks()
            window.lift()
            window.focus_force()
        except Exception:
            pass

        if sys.platform != "win32":
            return

        try:
            hwnd = int(window.winfo_id())
            user32 = ctypes.windll.user32

            sw_restore = 9
            hwnd_topmost = -1
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_showwindow = 0x0040

            user32.ShowWindow(hwnd, sw_restore)
            user32.SetWindowPos(
                hwnd,
                hwnd_topmost,
                0,
                0,
                0,
                0,
                swp_nomove | swp_nosize | swp_showwindow,
            )
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _flash_taskbar(self) -> None:
        """Flash the Guardtower taskbar button to attract attention from any workspace."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.root.winfo_id())
            flash_info = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_ulong) * 6)
            # FLASHWINFO: cbSize, hwnd, dwFlags, uCount, dwTimeout
            # FLASHW_ALL | FLASHW_TIMERNOFG = 0x0003 | 0x000C = 0x000F
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(
                type('FLASHWINFO', (ctypes.Structure,), {
                    '_fields_': [
                        ('cbSize', ctypes.c_uint),
                        ('hwnd', ctypes.c_void_p),
                        ('dwFlags', ctypes.c_uint),
                        ('uCount', ctypes.c_uint),
                        ('dwTimeout', ctypes.c_uint),
                    ]
                })(
                    cbSize=ctypes.sizeof(ctypes.c_uint) * 5,
                    hwnd=hwnd,
                    dwFlags=0x0000000F,  # FLASHW_ALL | FLASHW_TIMERNOFG
                    uCount=0,           # flash until foreground
                    dwTimeout=0,
                )
            ))
        except Exception:
            pass

    def _on_root_focus(self, _event: tk.Event | None = None) -> None:
        """Re-raise active confirmation dialog when main window gains focus."""
        dlg = self._active_confirmation_dialog
        if dlg is None:
            return
        try:
            if bool(dlg.winfo_exists()):
                self._force_window_front(dlg)
        except Exception:
            self._active_confirmation_dialog = None

    def _focus_confirmation_window(self, dialog: tk.Misc | None = None) -> None:
        """Bring Guardtower (and optionally its confirmation dialog) to the front."""
        try:
            self.root.deiconify()
        except Exception:
            pass
        self._force_window_front(self.root)

        if dialog is None:
            return
        try:
            if bool(dialog.winfo_exists()):
                self._force_window_front(dialog)
        except Exception:
            return

    def _send_confirmation_attention_notification(
        self,
        *,
        channel_name: str,
        message_text: str,
        dialog: tk.Misc,
    ) -> None:
        """Show a Windows notification and flash taskbar for confirmation."""
        body = message_text if len(message_text) <= 120 else (message_text[:117] + "...")
        ok, err = self._desktop_notifications.send_action(
            f"Approval needed for #{channel_name} — switch to Guardtower",
            body,
            action_label="Open Guardtower",
            action=lambda: self.root.after(0, lambda: self._focus_confirmation_window(dialog)),
        )
        if not ok:
            self._append_system_log(
                f"#{channel_name}: failed to show confirmation notification ({err or 'unknown backend error'})",
                "ignore",
            )
        # Flash taskbar regardless of toast result so app is always visible in taskbar.
        self._flash_taskbar()

    def _next_notification_id(self) -> str:
        self._notification_seq += 1
        return f"n{self._notification_seq}"

    def _add_confirmation_notification_item(
        self,
        *,
        channel_name: str,
        message_text: str,
        open_action,
    ) -> str:
        item_id = self._next_notification_id()
        self._notification_history.insert(
            0,
            {
                "id": item_id,
                "type": "confirmation",
                "channel": channel_name,
                "message": message_text,
                "created_at": time.strftime("%H:%M:%S"),
                "status": "pending",
                "open_action": open_action,
                "closed_reason": "",
            },
        )
        self._notification_history = self._notification_history[:40]
        self._refresh_notification_center_view()
        return item_id

    def _set_confirmation_notification_status(self, item_id: str, status: str, reason: str = "") -> None:
        for item in self._notification_history:
            if str(item.get("id", "")) != item_id:
                continue
            item["status"] = status
            item["closed_reason"] = reason
            if status != "pending":
                item["open_action"] = None
            break
        self._refresh_notification_center_view()

    def _pending_notification_count(self) -> int:
        return sum(1 for item in self._notification_history if str(item.get("status", "")) == "pending")

    def _refresh_notification_center_view(self) -> None:
        pending = self._pending_notification_count()
        if self._notification_bell_button is not None:
            self._notification_bell_button.configure(text=f"🔔 Notifications ({pending})")

        if self._notification_listbox is None or not bool(self._notification_listbox.winfo_exists()):
            return

        self._notification_listbox.delete(0, tk.END)
        self._notification_center_ids = []
        for item in self._notification_history:
            item_id = str(item.get("id", ""))
            status = str(item.get("status", "pending")).upper()
            timestamp = str(item.get("created_at", "--:--:--"))
            channel = str(item.get("channel", "")).strip()
            message = str(item.get("message", "")).strip().replace("\n", " ")
            if len(message) > 70:
                message = message[:67] + "..."
            suffix = ""
            closed_reason = str(item.get("closed_reason", "")).strip()
            if closed_reason:
                suffix = f" [{closed_reason}]"

            label = f"[{status}] {timestamp} #{channel}: {message}{suffix}"
            self._notification_center_ids.append(item_id)
            self._notification_listbox.insert(tk.END, label)

    def _open_notification_center(self) -> None:
        win = self._notification_center_window
        if win is not None:
            try:
                if bool(win.winfo_exists()):
                    self._force_window_front(win)
                    self._refresh_notification_center_view()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        win.title("Recent Notifications")
        win.geometry("760x320")
        win.resizable(True, True)
        win.configure(bg=self._colors["panel"])
        self._apply_app_icon(win)
        self._notification_center_window = win

        panel = tk.Frame(
            win,
            bg=self._colors["panel"],
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=10,
            pady=10,
        )
        panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tk.Label(
            panel,
            text="Pending items can be reopened from here.",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        list_shell = tk.Frame(panel, bg=self._colors["panel"])
        list_shell.pack(fill=tk.BOTH, expand=True)

        listbox = tk.Listbox(
            list_shell,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            selectbackground=self._colors["accent"],
            selectforeground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            activestyle="none",
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_shell, orient=tk.VERTICAL, command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.configure(yscrollcommand=scrollbar.set)

        footer = tk.Frame(panel, bg=self._colors["panel"])
        footer.pack(fill=tk.X, pady=(8, 0))

        def _open_selected() -> None:
            if self._notification_listbox is None:
                return
            selected = self._notification_listbox.curselection()
            if not selected:
                return
            idx = int(selected[0])
            if idx < 0 or idx >= len(self._notification_center_ids):
                return
            item_id = self._notification_center_ids[idx]
            for item in self._notification_history:
                if str(item.get("id", "")) != item_id:
                    continue
                if str(item.get("status", "")) != "pending":
                    self._append_system_log("Selected notification is no longer pending", "other")
                    return
                action = item.get("open_action")
                if callable(action):
                    try:
                        action()
                    except Exception:
                        self._append_system_log("Failed to reopen pending confirmation popup", "ignore")
                return

        self._make_button(
            footer,
            text="Open Selected",
            width=14,
            command=_open_selected,
            accent=True,
        ).pack(side=tk.RIGHT)
        self._make_button(
            footer,
            text="Close",
            width=12,
            command=lambda: _on_close(),
        ).pack(side=tk.RIGHT, padx=(0, 8))

        def _on_double_click(_event: tk.Event) -> None:
            _open_selected()

        listbox.bind("<Double-Button-1>", _on_double_click)
        self._notification_listbox = listbox
        self._refresh_notification_center_view()

        def _on_close() -> None:
            self._notification_center_window = None
            self._notification_listbox = None
            self._notification_center_ids = []
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _send_focus_attention_notification(
        self,
        *,
        channel_name: str,
        message_text: str,
        title: str = "Guardtower alert",
    ) -> None:
        """Show a Windows notification and flash taskbar to attract attention."""
        body = message_text if len(message_text) <= 120 else (message_text[:117] + "...")
        ok, err = self._desktop_notifications.send_action(
            f"{title} — switch to Guardtower",
            body,
            action_label="Open Guardtower",
            action=lambda: self.root.after(0, self._focus_confirmation_window),
        )
        if not ok:
            self._append_system_log(
                f"#{channel_name}: failed to show attention notification ({err or 'unknown backend error'})",
                "ignore",
            )
        # Flash taskbar so Guardtower is always visible regardless of toast delivery.
        self._flash_taskbar()

    def request_send_confirmation(self, payload: dict[str, object]) -> dict[str, object]:
        """Runtime-thread entrypoint: ask user to approve one outgoing chat message."""
        if self._shutdown_event.is_set():
            return {"approved": False, "message_text": ""}

        channel_name = str(payload.get("channel_name", "")).strip().lower()
        message_text = str(payload.get("message_text", ""))
        account_name_obj = payload.get("account_name")
        account_name = str(account_name_obj).strip() if isinstance(account_name_obj, str) else None

        timeout_obj = payload.get("timeout_s")
        timeout_s = 30.0
        if isinstance(timeout_obj, (int, float)):
            timeout_s = float(timeout_obj)

        trigger_obj = payload.get("trigger_message")
        trigger_message = str(trigger_obj) if isinstance(trigger_obj, str) else None

        reply_name_obj = payload.get("default_reply_name")
        default_reply_name = str(reply_name_obj) if isinstance(reply_name_obj, str) else None

        won_prefix_obj = payload.get("won_prefix")
        won_prefix = str(won_prefix_obj) if isinstance(won_prefix_obj, str) else None

        is_won_reply = bool(payload.get("is_won_reply", False))

        result: dict[str, object] = {"approved": False, "message_text": message_text}
        done_event = threading.Event()

        def _show() -> None:
            try:
                result.update(self._show_send_confirmation_dialog(
                    channel_name=channel_name,
                    message_text=message_text,
                    account_name=account_name,
                    timeout_s=timeout_s,
                    trigger_message=trigger_message,
                    default_reply_name=default_reply_name,
                    won_prefix=won_prefix,
                    is_won_reply=is_won_reply,
                ))
            except Exception:
                result["approved"] = False
                result["message_text"] = message_text
            finally:
                done_event.set()

        try:
            self.root.after(0, _show)
        except Exception:
            return {"approved": False, "message_text": message_text}

        done_event.wait(timeout=max(1.0, float(timeout_s) + 2.0))
        if not done_event.is_set():
            return {"approved": False, "message_text": message_text}
        return {
            "approved": bool(result.get("approved", False)),
            "message_text": str(result.get("message_text", message_text)),
        }

    def _show_send_confirmation_dialog(
        self,
        *,
        channel_name: str,
        message_text: str,
        account_name: str | None,
        timeout_s: float,
        trigger_message: str | None = None,
        default_reply_name: str | None = None,
        won_prefix: str | None = None,
        is_won_reply: bool = False,
    ) -> dict[str, object]:
        approved = {"value": False}
        approved_message = {"value": message_text}
        timeout_ms = max(1000, int(timeout_s * 1000))
        remaining = {"sec": max(1, int(timeout_s))}

        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm Chat Send")
        dialog_w = 700 if is_won_reply else 640
        dialog_h = 620 if is_won_reply else 420
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        pos_x = max(0, (screen_w - dialog_w) // 2)
        pos_y = max(0, (screen_h - dialog_h) // 2)
        dialog.geometry(f"{dialog_w}x{dialog_h}+{pos_x}+{pos_y}")
        dialog.resizable(False, False)
        dialog.configure(bg=self._colors["bg"])

        self._active_confirmation_dialog = dialog
        self._apply_app_icon(dialog)
        try:
            dialog.lift()
            dialog.focus_force()
        except Exception:
            pass
        self._send_confirmation_attention_notification(
            channel_name=channel_name,
            message_text=message_text,
            dialog=dialog,
        )

        hidden = {"value": False}

        def _reopen_dialog_from_notification() -> None:
            if not bool(dialog.winfo_exists()):
                return
            if hidden["value"]:
                try:
                    dialog.deiconify()
                except Exception:
                    pass
                hidden["value"] = False
            self._focus_confirmation_window(dialog)

        notif_item_id = self._add_confirmation_notification_item(
            channel_name=channel_name,
            message_text=message_text,
            open_action=lambda: self.root.after(0, _reopen_dialog_from_notification),
        )

        panel = tk.Frame(
            dialog,
            bg=self._colors["panel"],
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=14,
            pady=14,
        )
        panel.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(
            panel,
            text="Approve outgoing chat message",
            bg=self._colors["panel"],
            fg=self._colors["text"],
            font=("Segoe UI Semibold", 12),
            anchor="w",
        ).pack(fill=tk.X)

        tk.Label(
            panel,
            text=f"Destination: #{channel_name}",
            bg=self._colors["panel"],
            fg=self._colors["text"],
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 0))

        account_label = account_name.strip() if account_name else "(unknown account)"
        tk.Label(
            panel,
            text=f"Account: {account_label}",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 8))

        tk.Label(
            panel,
            text="Message:",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill=tk.X)

        preview = tk.Text(
            panel,
            height=5 if is_won_reply else 7,
            wrap=tk.WORD,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=8,
            pady=8,
        )
        preview.pack(fill=tk.X, pady=(4, 8))
        preview.insert("1.0", message_text)
        preview.configure(state=tk.DISABLED)

        if trigger_message:
            tk.Label(
                panel,
                text="Win trigger message:",
                bg=self._colors["panel"],
                fg=self._colors["muted"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill=tk.X, pady=(2, 0))

            trigger_preview = tk.Text(
                panel,
                height=3,
                wrap=tk.WORD,
                bg=self._colors["input_bg"],
                fg=self._colors["text"],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors["border"],
                padx=8,
                pady=8,
            )
            trigger_preview.pack(fill=tk.X, pady=(4, 8))
            trigger_preview.insert("1.0", trigger_message)
            trigger_preview.configure(state=tk.DISABLED)

        name_var: tk.StringVar | None = None
        send_preview_var: tk.StringVar | None = None
        normalized_prefix = won_prefix or ""
        normalized_default_name = (default_reply_name or "").strip()

        if is_won_reply:
            row_name = tk.Frame(panel, bg=self._colors["panel"])
            row_name.pack(fill=tk.X, pady=(0, 6))

            tk.Label(
                row_name,
                text="Reply Username",
                bg=self._colors["panel"],
                fg=self._colors["muted"],
                font=("Segoe UI", 9),
                width=15,
                anchor="w",
            ).pack(side=tk.LEFT)

            name_var = tk.StringVar(value=normalized_default_name)
            name_entry = tk.Entry(
                row_name,
                textvariable=name_var,
                bg=self._colors["input_bg"],
                fg=self._colors["text"],
                insertbackground=self._colors["text"],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors["border"],
                highlightcolor=self._colors["accent"],
            )
            name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self._make_button(
                row_name,
                text="Use Registered",
                width=14,
                command=lambda: name_var.set(normalized_default_name),
            ).pack(side=tk.LEFT, padx=(8, 0))

            send_preview_var = tk.StringVar(value=f"Final send: {normalized_prefix}{normalized_default_name}")
            tk.Label(
                panel,
                textvariable=send_preview_var,
                bg=self._colors["panel"],
                fg=self._colors["text"],
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill=tk.X, pady=(0, 6))

            def _refresh_send_preview(*_args) -> None:
                if name_var is None or send_preview_var is None:
                    return
                chosen_name = name_var.get().strip()
                send_preview_var.set(f"Final send: {normalized_prefix}{chosen_name}")

            name_var.trace_add("write", _refresh_send_preview)
            name_entry.focus_set()

        countdown_var = tk.StringVar(value=f"Auto-cancel in {remaining['sec']}s")
        countdown_label = tk.Label(
            panel,
            textvariable=countdown_var,
            bg=self._colors["panel"],
            fg=self._colors["danger"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        countdown_label.pack(fill=tk.X)

        footer = tk.Frame(panel, bg=self._colors["panel"])
        footer.pack(fill=tk.X, pady=(8, 0))

        timer_handle: dict[str, str | None] = {"id": None}

        def _hide_dialog() -> None:
            if not bool(dialog.winfo_exists()):
                return
            hidden["value"] = True
            dialog.withdraw()
            self._append_system_log(
                f"Confirmation popup for #{channel_name} was hidden. Open it from the bell notifications.",
                "notification",
            )

        def _close(value: bool, reason: str) -> None:
            if value and is_won_reply and name_var is not None:
                chosen_name = name_var.get().strip()
                if not chosen_name:
                    messagebox.showerror("Missing value", "Reply Username is required.", parent=dialog)
                    return
                approved_message["value"] = f"{normalized_prefix}{chosen_name}"
            elif value:
                approved_message["value"] = message_text

            approved["value"] = value
            if timer_handle["id"] is not None:
                try:
                    dialog.after_cancel(timer_handle["id"])
                except Exception:
                    pass
                timer_handle["id"] = None
            self._active_confirmation_dialog = None
            self._set_confirmation_notification_status(notif_item_id, "approved" if value else "closed", reason)
            if dialog.winfo_exists():
                dialog.destroy()

        def _tick() -> None:
            remaining["sec"] -= 1
            if remaining["sec"] <= 0:
                countdown_var.set("Auto-cancel in 0s")
                _close(False, "timed out")
                return
            countdown_var.set(f"Auto-cancel in {remaining['sec']}s")
            timer_handle["id"] = dialog.after(1000, _tick)

        self._make_button(footer, text="Send", width=12, command=lambda: _close(True, "sent"), accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text="Do Not Send", width=14, command=lambda: _close(False, "declined"), danger=True).pack(
            side=tk.RIGHT,
            padx=(0, 8),
        )
        self._make_button(footer, text="Hide", width=10, command=_hide_dialog).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", _hide_dialog)
        timer_handle["id"] = dialog.after(1000, _tick)
        dialog.after(timeout_ms, lambda: _close(False, "timed out"))
        self.root.wait_window(dialog)
        return {
            "approved": bool(approved["value"]),
            "message_text": str(approved_message["value"]),
        }

    def _build_ui(self) -> None:
        container = tk.Frame(
            self.root,
            padx=14,
            pady=14,
            bg=self._colors["panel"],
            highlightthickness=1,
            highlightbackground=self._colors["border"],
        )
        container.pack(fill=tk.BOTH, expand=True)

        title_row = tk.Frame(container, bg=self._colors["panel"])
        title_row.pack(fill=tk.X)

        tk.Label(
            title_row,
            text=f"{APP_NAME} v{APP_VERSION}",
            bg=self._colors["panel"],
            fg=self._colors["text"],
            font=self._font_title,
        ).pack(side=tk.LEFT)

        self._make_button(title_row, text="Refresh All", width=14, command=self._refresh_all, accent=True).pack(
            side=tk.RIGHT
        )

        self._notification_bell_button = self._make_button(
            title_row,
            text="🔔 Notifications (0)",
            width=18,
            command=self._open_notification_center,
        )
        self._notification_bell_button.pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(
            title_row,
            text="Edit User Info",
            width=14,
            command=self._open_user_info_editor,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(
            title_row,
            text="Edit Streamers",
            width=14,
            command=self._open_streamers_editor,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(title_row, text="Open MultiTwitch", width=20, command=self._open_multitwitch_url).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        system_panel = tk.Frame(
            container,
            bg=self._colors["panel"],
            highlightthickness=1,
            highlightbackground=self._colors["border"],
        )
        system_panel.pack(fill=tk.X, pady=(10, 10))

        tk.Label(
            system_panel,
            text="System",
            bg=self._colors["panel_alt"],
            fg=self._colors["text"],
            anchor="w",
            padx=8,
            pady=5,
            font=self._font_title_sm,
        ).pack(fill=tk.X)

        self.lbl_multitwitch = tk.Label(
            system_panel,
            text="MultiTwitch -> (loading...)",
            bg=self._colors["input_bg"],
            fg=self._colors["accent"],
            anchor="w",
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self.lbl_multitwitch.pack(fill=tk.X)
        self.lbl_multitwitch.bind("<Button-1>", lambda _event: self._open_multitwitch_url())

        self.txt_system = tk.Text(
            system_panel,
            height=7,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        self.txt_system.pack(fill=tk.X)
        self.txt_system.bind("<Control-c>", lambda _event: self._copy_system_log())
        for kind, color in self._kind_fg.items():
            self.txt_system.tag_configure(kind, foreground=color)

        channels_shell = tk.Frame(container, bg=self._colors["panel"])
        channels_shell.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(channels_shell, bg=self._colors["panel"], bd=0, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(channels_shell, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.channels_container = tk.Frame(self.canvas, bg=self._colors["panel"])
        self._canvas_window = self.canvas.create_window((0, 0), window=self.channels_container, anchor="nw")

        self.channels_container.bind("<Configure>", self._on_channels_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.bind("<MouseWheel>", self._on_mouse_wheel)

    def _on_channels_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._canvas_window, width=event.width)

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        # Ignore wheel events when Guardtower is not the foreground/focused app.
        if self.root.focus_get() is None:
            return

        widget = getattr(event, "widget", None)
        if widget is None:
            return

        # Do not scroll the main channels canvas while the user is scrolling inside
        # per-streamer text areas (or other text inputs) which have their own wheel behavior.
        if isinstance(widget, (tk.Text, tk.Entry, tk.Listbox, tk.Spinbox)):
            return

        # Ignore wheel events coming from child dialogs or other toplevel windows.
        try:
            if widget.winfo_toplevel() is not self.root:
                return
        except Exception:
            return

        delta = -1 * int(event.delta / 120)
        self.canvas.yview_scroll(delta, "units")

    def _create_channel_cards(self) -> None:
        for channel in self._bot_config.channels:
            view = self._create_channel_view(channel.name)
            self._channel_views[channel.name] = view

    def _create_channel_view(self, channel_name: str) -> ChannelView:
        frame = tk.Frame(
            self.channels_container,
            bg=self._colors["panel"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=0,
            pady=0,
        )

        header_row = tk.Frame(frame, bg=self._colors["panel_alt"])
        header_row.pack(fill=tk.X)

        led = tk.Canvas(
            header_row,
            width=16,
            height=16,
            bg=self._colors["panel_alt"],
            highlightthickness=0,
            bd=0,
        )
        led.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        led_circle = led.create_oval(2, 2, 14, 14, fill=self._colors["danger"], outline="#1f1f1f")

        header = tk.Label(
            header_row,
            text="",
            bg=self._colors["panel_alt"],
            fg=self._colors["muted"],
            anchor="w",
            padx=4,
            pady=4,
            font=self._font_title_sm,
        )
        header.pack(side=tk.LEFT, fill=tk.X, expand=True)

        actions = tk.Frame(frame, bg=self._colors["panel"])
        actions.pack(fill=tk.X, padx=8, pady=(6, 4))

        self._make_button(actions, text="Refresh", width=10, command=lambda ch=channel_name: self._refresh_channel(ch)).pack(
            side=tk.LEFT
        )
        self._make_button(
            actions,
            text="Reload",
            width=10,
            command=lambda ch=channel_name: self._reload_channel_triggers(ch),
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(
            actions,
            text="Edit Triggers",
            width=12,
            command=lambda ch=channel_name: self._open_streamer_triggers_editor(ch),
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(
            actions,
            text="Force Join",
            width=10,
            command=lambda ch=channel_name: self._force_join_channel(ch),
            accent=True,
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(
            actions,
            text="Open Channel",
            width=12,
            command=lambda ch=channel_name: self._open_channel_link(ch),
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(actions, text="Copy Log", width=10, command=lambda ch=channel_name: self._copy_channel_log(ch)).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        log_widget = tk.Text(
            frame,
            height=8,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        log_widget.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        log_widget.bind("<Control-c>", lambda _event, ch=channel_name: self._copy_channel_log(ch))

        for kind, color in self._kind_fg.items():
            log_widget.tag_configure(kind, foreground=color)

        view = ChannelView(
            name=channel_name,
            frame=frame,
            header_row=header_row,
            header=header,
            led=led,
            led_circle=led_circle,
            log_widget=log_widget,
            status_since=time.monotonic(),
        )
        self._refresh_channel_header(view)
        return view

    def _render_channel_header(self, view: ChannelView, now_value: float | None = None) -> str:
        now = now_value if now_value is not None else time.monotonic()
        online_prefix = "ONLINE" if view.is_online else "OFFLINE"
        stats = f"({view.session_giveaways}/{view.session_wins})"
        win_badge = " WIN" if (view.win_at > 0 and (now - view.win_at) < GIVEAWAY_SESSION_DURATION_S) else ""

        if view.status == "joined":
            return f"{online_prefix} {view.name} {stats} | JOINED{win_badge}"
        if view.status == "ongoing":
            remaining = max(0, int(GIVEAWAY_SESSION_DURATION_S - (now - view.status_since)))
            return f"{online_prefix} {view.name} {stats} | ONGOING {remaining:>3}s{win_badge}"
        return f"{online_prefix} {view.name} {stats} | idle{win_badge}"

    def _is_idle_stale(self, view: ChannelView, now_value: float) -> bool:
        return view.status == "idle" and (now_value - view.status_since) >= IDLE_ALERT_THRESHOLD_S

    def _refresh_channel_header(self, view: ChannelView, now_value: float | None = None) -> None:
        now = now_value if now_value is not None else time.monotonic()
        idle_stale = self._is_idle_stale(view, now)
        view.idle_alert_active = idle_stale

        bg = self._colors["panel_alt"]
        fg = self._colors["muted"]
        if view.status == "joined":
            bg = self._colors["accent"]
            fg = "#ffffff"
        elif view.status == "ongoing":
            bg = self._colors["success"]
            fg = "#ffffff"
        elif idle_stale:
            bg = self._colors["danger"]
            fg = "#ffffff"

        led_color = self._colors["success"] if view.is_online else self._colors["danger"]
        view.led.itemconfigure(view.led_circle, fill=led_color)
        view.led.configure(bg=bg)
        view.header_row.configure(bg=bg)

        view.header.configure(text=self._render_channel_header(view, now), bg=bg, fg=fg)

    def _set_channel_status(self, view: ChannelView, status: str) -> None:
        view.status = status
        view.status_since = time.monotonic()
        self._refresh_channel_header(view)

    def _append_channel_log(self, view: ChannelView, message: str, kind: str) -> None:
        view.log_lines.append((message, kind))
        if len(view.log_lines) > self._CHANNEL_LOG_BUFFER_MAX:
            view.log_lines = view.log_lines[-self._CHANNEL_LOG_BUFFER_MAX:]

        self._append_text_line(view.log_widget, message, kind)

        if kind == "giveaway_active":
            if view.status != "joined":
                view.session_giveaways += 1
                view.session_win_recorded = False
            self._set_channel_status(view, "joined")
        elif kind == "giveaway_inactive":
            self._set_channel_status(view, "ongoing")
        elif kind == "win" and not view.session_win_recorded:
            view.session_wins += 1
            view.session_win_recorded = True
            if view.status != "ongoing":
                self._set_channel_status(view, "ongoing")
            view.win_at = time.monotonic()
            self._refresh_channel_header(view)

    def _append_system_log(self, message: str, kind: str) -> None:
        self._system_log_lines.append((message, kind))
        if len(self._system_log_lines) > self._SYSTEM_LOG_BUFFER_MAX:
            self._system_log_lines = self._system_log_lines[-self._SYSTEM_LOG_BUFFER_MAX:]
        self._append_text_line(self.txt_system, message, kind)

    def _append_text_line(self, widget: tk.Text, message: str, kind: str) -> None:
        tag = kind if kind in self._kind_fg else "other"
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, f"{message}\n", (tag,))
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _clear_text_widget(self, widget: tk.Text) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.configure(state=tk.DISABLED)

    def _refresh_channel(self, channel_name: str) -> None:
        view = self._channel_views[channel_name]
        self._reset_channel_view(view)
        self._append_system_log(f"#{channel_name} reset", "notification")
        self._call_runtime_thread(lambda: self._reset_bot_channel_runtime(channel_name))

    def _reload_channel_triggers(self, channel_name: str) -> None:
        def _job() -> None:
            success = False
            for twitch_bot in self._bot_instances:
                if twitch_bot.reload_channel_triggers(channel_name):
                    success = True
            self._event_queue.put(("reload_result", {"channel": channel_name, "success": success}))

        self._call_runtime_thread(_job)

    def _force_join_channel(self, channel_name: str) -> None:
        if self._runtime_loop is None:
            self._append_system_log("Runtime is not ready yet", "ignore")
            return

        self._append_system_log(f"Force join requested for #{channel_name}", "notification")

        def _job() -> None:
            async def _run_force_join() -> None:
                attempted = 0
                sent = 0
                for twitch_bot in self._bot_instances:
                    attempted += 1
                    try:
                        if await twitch_bot.force_join_channel(channel_name):
                            sent += 1
                    except Exception as exc:
                        self._event_queue.put((
                            "runtime_error",
                            f"Force join failed in one bot for #{channel_name}: {exc}",
                        ))
                self._event_queue.put((
                    "force_join_result",
                    {"channel": channel_name, "attempted": attempted, "sent": sent},
                ))

            asyncio.create_task(_run_force_join())

        self._call_runtime_thread(_job)

    def _copy_channel_log(self, channel_name: str) -> None:
        view = self._channel_views[channel_name]
        content = "\n".join(line for line, _kind in view.log_lines)
        if self._copy_to_clipboard(content):
            self._append_system_log(f"Copied {len(view.log_lines)} lines from #{channel_name}", "notification")
        else:
            self._append_system_log("Clipboard copy failed", "ignore")

    def _copy_system_log(self) -> None:
        content = "\n".join(line for line, _kind in self._system_log_lines)
        if self._copy_to_clipboard(content):
            self._append_system_log(f"Copied {len(self._system_log_lines)} system lines", "notification")
        else:
            self._append_system_log("Clipboard copy failed", "ignore")

    def _copy_multitwitch_url(self) -> None:
        if self._multitwitch_url.startswith("https://"):
            if self._copy_to_clipboard(self._multitwitch_url):
                self._append_system_log("MultiTwitch URL copied", "notification")
                return
            self._append_system_log("Clipboard copy failed", "ignore")
            return
        self._append_system_log("MultiTwitch URL not available yet", "other")

    def _open_multitwitch_url(self) -> None:
        if not self._multitwitch_url.startswith("https://"):
            self._append_system_log("MultiTwitch URL not available yet", "other")
            return
        try:
            webbrowser.open_new_tab(self._multitwitch_url)
            self._append_system_log("Opened MultiTwitch URL", "notification")
        except Exception as exc:
            self._append_system_log(f"Failed to open MultiTwitch URL: {exc}", "ignore")

    def _open_channel_link(self, channel_name: str) -> None:
        url = f"https://www.twitch.tv/{channel_name}"
        try:
            webbrowser.open_new_tab(url)
            self._append_system_log(f"Opened channel link for #{channel_name}", "notification")
        except Exception as exc:
            self._append_system_log(f"Failed to open #{channel_name}: {exc}", "ignore")

    def _copy_to_clipboard(self, text: str) -> bool:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            return True
        except Exception:
            return False

    def _config_path(self) -> Path:
        return resolve_default_config_path()

    def _load_raw_config(self) -> tuple[Path, dict] | None:
        config_path = self._config_path()
        if not config_path.exists():
            messagebox.showerror("Config not found", f"Config file not found:\n{config_path}", parent=self.root)
            return None
        try:
            with open(config_path, "r", encoding="utf-8-sig") as file:
                payload = json.load(file)
        except Exception as exc:
            messagebox.showerror("Config error", f"Failed to read config.json:\n{exc}", parent=self.root)
            return None
        if not isinstance(payload, dict):
            messagebox.showerror("Config error", "Invalid config root format.", parent=self.root)
            return None
        return config_path, payload

    def _save_raw_config(self, config_path: Path, payload: dict) -> bool:
        try:
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("Save error", f"Failed to save config.json:\n{exc}", parent=self.root)
            return False
        return True

    def _text_to_lines(self, widget: tk.Text) -> list[str]:
        raw = widget.get("1.0", tk.END)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _open_streamer_triggers_editor(self, channel_name: str) -> None:
        loaded = self._load_raw_config()
        if loaded is None:
            return
        config_path, payload = loaded

        channels = payload.get("channels", [])
        if not isinstance(channels, list):
            messagebox.showerror("Config error", "channels is not a list", parent=self.root)
            return

        target_channel: dict | None = None
        for channel in channels:
            if isinstance(channel, dict) and str(channel.get("name", "")).strip() == channel_name:
                target_channel = channel
                break

        if target_channel is None:
            messagebox.showerror("Not found", f"Streamer '{channel_name}' not found in config.json", parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit Triggers - {channel_name}")
        dialog.geometry("640x560")
        dialog.configure(bg=self._colors["panel"])
        dialog.transient(self.root)
        dialog.grab_set()
        self._apply_app_icon(dialog)

        frame = tk.Frame(dialog, bg=self._colors["panel"], padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text=f"Streamer: {channel_name}", bg=self._colors["panel"], fg=self._colors["text"], anchor="w").pack(fill=tk.X)

        tk.Label(frame, text="Giveaway Triggers (one per line)", bg=self._colors["panel"], fg=self._colors["muted"], anchor="w").pack(fill=tk.X, pady=(10, 2))
        txt_giveaway = tk.Text(frame, height=6, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        txt_giveaway.pack(fill=tk.X)
        txt_giveaway.insert("1.0", "\n".join(str(x) for x in target_channel.get("giveaway_triggers", []) if str(x).strip()))

        tk.Label(frame, text="Giveaway Message", bg=self._colors["panel"], fg=self._colors["muted"], anchor="w").pack(fill=tk.X, pady=(10, 2))
        ent_message = tk.Entry(frame, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        ent_message.pack(fill=tk.X)
        ent_message.insert(0, str(target_channel.get("giveaway_message", "")))

        delay_row = tk.Frame(frame, bg=self._colors["panel"])
        delay_row.pack(fill=tk.X, pady=(10, 2))
        tk.Label(delay_row, text="Delay min ms", bg=self._colors["panel"], fg=self._colors["muted"]).pack(side=tk.LEFT)
        ent_delay_min = tk.Entry(delay_row, width=10, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        ent_delay_min.pack(side=tk.LEFT, padx=(8, 16))
        tk.Label(delay_row, text="Delay max ms", bg=self._colors["panel"], fg=self._colors["muted"]).pack(side=tk.LEFT)
        ent_delay_max = tk.Entry(delay_row, width=10, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        ent_delay_max.pack(side=tk.LEFT, padx=(8, 0))

        delay_value = target_channel.get("delay_ms", [2000, 2000])
        if isinstance(delay_value, list) and len(delay_value) == 2:
            ent_delay_min.insert(0, str(delay_value[0]))
            ent_delay_max.insert(0, str(delay_value[1]))
        else:
            ent_delay_min.insert(0, "2000")
            ent_delay_max.insert(0, "2000")

        tk.Label(frame, text="Won Triggers (one per line)", bg=self._colors["panel"], fg=self._colors["muted"], anchor="w").pack(fill=tk.X, pady=(10, 2))
        txt_won = tk.Text(frame, height=6, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        txt_won.pack(fill=tk.X)
        txt_won.insert("1.0", "\n".join(str(x) for x in target_channel.get("won_triggers", []) if str(x).strip()))

        tk.Label(frame, text="Won Prefix", bg=self._colors["panel"], fg=self._colors["muted"], anchor="w").pack(fill=tk.X, pady=(10, 2))
        ent_won_prefix = tk.Entry(frame, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT)
        ent_won_prefix.pack(fill=tk.X)
        ent_won_prefix.insert(0, str(target_channel.get("won_prefix", "")))

        footer = tk.Frame(frame, bg=self._colors["panel"])
        footer.pack(fill=tk.X, pady=(14, 0))

        def _save() -> None:
            giveaway_triggers = self._text_to_lines(txt_giveaway)
            won_triggers = self._text_to_lines(txt_won)

            try:
                delay_min = int(ent_delay_min.get().strip() or "0")
                delay_max = int(ent_delay_max.get().strip() or "0")
            except ValueError:
                messagebox.showerror("Invalid value", "Delay fields must be integers.", parent=dialog)
                return

            delay_min = max(0, delay_min)
            delay_max = max(0, delay_max)
            if delay_min > delay_max:
                delay_min, delay_max = delay_max, delay_min

            target_channel["giveaway_triggers"] = giveaway_triggers
            target_channel["giveaway_message"] = ent_message.get().strip()
            target_channel["delay_ms"] = [delay_min, delay_max]
            target_channel["won_triggers"] = won_triggers
            target_channel["won_prefix"] = ent_won_prefix.get().strip()

            if not self._save_raw_config(config_path, payload):
                return

            self._append_system_log(f"Config saved for #{channel_name}", "notification")
            self._reload_channel_triggers(channel_name)
            dialog.destroy()

        self._make_button(footer, text="Save", width=12, command=_save, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text="Cancel", width=12, command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def _open_streamers_editor(self) -> None:
        loaded = self._load_raw_config()
        if loaded is None:
            return
        config_path, payload = loaded

        channels = payload.get("channels", [])
        if not isinstance(channels, list):
            messagebox.showerror("Config error", "channels is not a list", parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Streamers")
        dialog.geometry("520x560")
        dialog.configure(bg=self._colors["panel"])
        dialog.transient(self.root)
        dialog.grab_set()
        self._apply_app_icon(dialog)

        frame = tk.Frame(dialog, bg=self._colors["panel"], padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Streamer list (one streamer name per line)",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            anchor="w",
        ).pack(fill=tk.X)

        txt_names = tk.Text(
            frame,
            height=24,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
        )
        txt_names.pack(fill=tk.BOTH, expand=True, pady=(8, 10))
        current_names = [str(ch.get("name", "")).strip() for ch in channels if isinstance(ch, dict) and str(ch.get("name", "")).strip()]
        txt_names.insert("1.0", "\n".join(current_names))

        footer = tk.Frame(frame, bg=self._colors["panel"])
        footer.pack(fill=tk.X)

        def _default_channel_entry(name: str) -> dict:
            return {
                "name": name,
                "giveaway_triggers": [],
                "giveaway_message": "",
                "delay_ms": [5000, 15000],
                "won_triggers": [],
                "won_prefix": "",
            }

        def _save() -> None:
            names = [line.strip() for line in txt_names.get("1.0", tk.END).splitlines() if line.strip()]
            if not names:
                messagebox.showerror("Invalid value", "At least one streamer is required.", parent=dialog)
                return
            if len(set(names)) != len(names):
                messagebox.showerror("Invalid value", "Duplicate streamer names found.", parent=dialog)
                return

            existing_by_name = {
                str(ch.get("name", "")).strip(): ch
                for ch in channels
                if isinstance(ch, dict) and str(ch.get("name", "")).strip()
            }
            payload["channels"] = [existing_by_name.get(name, _default_channel_entry(name)) for name in names]

            if not self._save_raw_config(config_path, payload):
                return

            self._append_system_log("Streamer list updated in config.json", "notification")
            self._append_system_log("Restart the app to rebuild channel cards after streamer list changes.", "other")
            dialog.destroy()

        self._make_button(footer, text="Save", width=12, command=_save, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text="Cancel", width=12, command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def _open_user_info_editor(self) -> None:
        loaded = self._load_raw_config()
        if loaded is None:
            return
        config_path, payload = loaded

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit User Info")
        dialog.geometry("560x460")
        dialog.configure(bg=self._colors["panel"])
        dialog.transient(self.root)
        dialog.grab_set()
        self._apply_app_icon(dialog)

        frame = tk.Frame(dialog, bg=self._colors["panel"], padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        accounts = payload.get("accounts", [])
        using_accounts = isinstance(accounts, list) and len(accounts) > 0 and isinstance(accounts[0], dict)

        selected_index = tk.IntVar(value=0)
        var_username = tk.StringVar()
        var_oauth = tk.StringVar()
        var_nickname = tk.StringVar()

        txt_ignored = tk.Text(
            frame,
            height=6,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
        )

        if using_accounts:
            account_names = [str(acc.get("username", f"account-{idx+1}")) for idx, acc in enumerate(accounts)]
            row_account = tk.Frame(frame, bg=self._colors["panel"])
            row_account.pack(fill=tk.X)
            tk.Label(row_account, text="Account", bg=self._colors["panel"], fg=self._colors["muted"]).pack(side=tk.LEFT)
            account_menu = tk.OptionMenu(row_account, selected_index, *range(len(account_names)))
            account_menu.configure(bg=self._colors["panel_alt"], fg=self._colors["text"], highlightthickness=0, bd=0)
            account_menu.pack(side=tk.LEFT, padx=(8, 0))
            tk.Label(row_account, text="(index)", bg=self._colors["panel"], fg=self._colors["muted"]).pack(side=tk.LEFT, padx=(8, 0))

        def _load_account_fields(index: int) -> None:
            if using_accounts:
                account = accounts[index]
                var_username.set(str(account.get("username", "")))
                var_oauth.set(str(account.get("oauth_token", "")))
                var_nickname.set(str(account.get("nickname", "")))
                ignored = account.get("ignored_usernames", [])
                lines = [str(item).strip() for item in ignored if str(item).strip()]
            else:
                twitch = payload.get("twitch", {}) if isinstance(payload.get("twitch", {}), dict) else {}
                var_username.set(str(twitch.get("username", "")))
                var_oauth.set(str(twitch.get("oauth_token", "")))
                var_nickname.set(str(payload.get("nickname", "")))
                lines = []

            txt_ignored.delete("1.0", tk.END)
            txt_ignored.insert("1.0", "\n".join(lines))

        row_user = tk.Frame(frame, bg=self._colors["panel"])
        row_user.pack(fill=tk.X, pady=(10, 4))
        tk.Label(row_user, text="Username", bg=self._colors["panel"], fg=self._colors["muted"], width=14, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_user, textvariable=var_username, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row_oauth = tk.Frame(frame, bg=self._colors["panel"])
        row_oauth.pack(fill=tk.X, pady=(4, 4))
        tk.Label(row_oauth, text="OAuth Token", bg=self._colors["panel"], fg=self._colors["muted"], width=14, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_oauth, textvariable=var_oauth, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row_nick = tk.Frame(frame, bg=self._colors["panel"])
        row_nick.pack(fill=tk.X, pady=(4, 4))
        tk.Label(row_nick, text="Nickname", bg=self._colors["panel"], fg=self._colors["muted"], width=14, anchor="w").pack(side=tk.LEFT)
        tk.Entry(row_nick, textvariable=var_nickname, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(frame, text="Ignored Usernames (one per line)", bg=self._colors["panel"], fg=self._colors["muted"], anchor="w").pack(fill=tk.X, pady=(8, 2))
        txt_ignored.pack(fill=tk.BOTH, expand=True)

        if using_accounts:
            def _on_account_change(*_args) -> None:
                _load_account_fields(int(selected_index.get()))

            selected_index.trace_add("write", _on_account_change)

        _load_account_fields(int(selected_index.get()))

        footer = tk.Frame(frame, bg=self._colors["panel"])
        footer.pack(fill=tk.X, pady=(12, 0))

        def _save() -> None:
            username = var_username.get().strip()
            oauth_token = var_oauth.get().strip()
            nickname = var_nickname.get().strip()
            ignored = [line.strip() for line in txt_ignored.get("1.0", tk.END).splitlines() if line.strip()]

            if not username or not oauth_token or not nickname:
                messagebox.showerror("Invalid value", "Username, OAuth Token and Nickname are required.", parent=dialog)
                return

            if using_accounts:
                index = int(selected_index.get())
                account = accounts[index]
                account["username"] = username
                account["oauth_token"] = oauth_token
                account["nickname"] = nickname
                account["ignored_usernames"] = ignored
            else:
                twitch = payload.get("twitch", {})
                if not isinstance(twitch, dict):
                    twitch = {}
                    payload["twitch"] = twitch
                twitch["username"] = username
                twitch["oauth_token"] = oauth_token
                payload["nickname"] = nickname

            if not self._save_raw_config(config_path, payload):
                return

            self._append_system_log("User info updated in config.json", "notification")
            self._append_system_log("Restart the app to reload account credentials.", "other")
            dialog.destroy()

        self._make_button(footer, text="Save", width=12, command=_save, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text="Cancel", width=12, command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def _reset_channel_view(self, view: ChannelView) -> None:
        view.log_lines.clear()
        view.session_giveaways = 0
        view.session_wins = 0
        view.session_win_recorded = False
        view.win_at = 0.0
        view.status = "idle"
        view.status_since = time.monotonic()
        view.idle_alert_active = False
        self._clear_text_widget(view.log_widget)
        self._refresh_channel_header(view)

    def _refresh_all(self) -> None:
        self._append_system_log("Refreshing all channels...", "other")
        for view in self._channel_views.values():
            self._reset_channel_view(view)

        self._call_runtime_thread(self._reset_all_bot_channels_runtime)
        threading.Thread(target=self._refresh_online_once_worker, daemon=True).start()

    def _refresh_online_once_worker(self) -> None:
        channel_names = [ch.name for ch in self._bot_config.channels]
        online = self._fetch_online_channels_helix_sync(channel_names)
        self._event_queue.put(("online_update", online))
        self._event_queue.put(("refresh_done", len(online)))

    def _queue_log_event(self, message: str, kind: str, channel: str | None, account: str | None) -> None:
        self._event_queue.put(("log", {"message": message, "kind": kind, "channel": channel, "account": account}))

    def _drain_events(self) -> None:
        while True:
            try:
                event_name, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event_name == "log" and isinstance(payload, dict):
                self._handle_log_event(payload)
            elif event_name == "online_update" and isinstance(payload, set):
                self._apply_online_channels(payload)
            elif event_name == "refresh_done" and isinstance(payload, int):
                total = len(self._bot_config.channels)
                self._append_system_log(f"Refresh complete - {payload}/{total} online", "notification")
            elif event_name == "reload_result" and isinstance(payload, dict):
                channel_name = str(payload.get("channel", ""))
                success = bool(payload.get("success", False))
                if success:
                    self._append_system_log(f"#{channel_name} triggers reloaded", "notification")
                else:
                    self._append_system_log(f"#{channel_name} trigger reload failed", "ignore")
            elif event_name == "force_join_result" and isinstance(payload, dict):
                channel_name = str(payload.get("channel", ""))
                attempted = int(payload.get("attempted", 0))
                sent = int(payload.get("sent", 0))
                if sent > 0:
                    self._append_system_log(
                        f"#{channel_name} force join sent ({sent}/{attempted} bot instance(s))",
                        "notification",
                    )
                else:
                    self._append_system_log(
                        f"#{channel_name} force join not sent (0/{attempted} bot instance(s))",
                        "ignore",
                    )
            elif event_name == "runtime_error" and isinstance(payload, str):
                self._append_system_log(f"Bot error: {payload}", "ignore")

        self.root.after(120, self._drain_events)

    def _handle_log_event(self, payload: dict[str, object]) -> None:
        message = str(payload.get("message", ""))
        kind = str(payload.get("kind", "other"))
        channel = payload.get("channel")
        channel_name = str(channel) if isinstance(channel, str) else None

        if kind == "win" and channel_name and message.strip().lower().startswith("won giveaway"):
            self._send_focus_attention_notification(
                channel_name=channel_name,
                message_text=message,
                title=f"Win detected in #{channel_name}",
            )

        if channel_name and channel_name in self._channel_views:
            self._append_channel_log(self._channel_views[channel_name], message, kind)
            return

        if not channel_name and message.startswith("Multitwitch: "):
            return

        prefix = f"[{channel_name}] " if channel_name else ""
        self._append_system_log(f"{prefix}{message}", kind)

    def _tick_statuses(self) -> None:
        now_value = time.monotonic()
        for view in self._channel_views.values():
            refresh_needed = False
            if view.status == "ongoing":
                if now_value - view.status_since >= GIVEAWAY_SESSION_DURATION_S:
                    self._set_channel_status(view, "idle")
                    continue
                refresh_needed = True
            elif view.status == "idle":
                if (not view.idle_alert_active) and self._is_idle_stale(view, now_value):
                    refresh_needed = True

            if view.win_at > 0 and (now_value - view.win_at) >= GIVEAWAY_SESSION_DURATION_S:
                view.win_at = 0.0
                refresh_needed = True

            if refresh_needed:
                self._refresh_channel_header(view, now_value)

        self.root.after(1000, self._tick_statuses)

    def _sorted_channel_names(self, online_channels: set[str]) -> list[str]:
        return [
            channel.name
            for channel in sorted(
                self._bot_config.channels,
                key=lambda channel: (
                    0 if channel.name.casefold() in online_channels else 1,
                    channel.name.casefold(),
                ),
            )
        ]

    def _reorder_channel_cards(self, online_channels: set[str]) -> None:
        ordered = self._sorted_channel_names(online_channels)
        for name in ordered:
            view = self._channel_views[name]
            view.is_online = name.casefold() in online_channels
            self._refresh_channel_header(view)

        for index, name in enumerate(ordered):
            view = self._channel_views[name]
            row = index // 3
            col = index % 3
            view.frame.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

        for col in range(3):
            self.channels_container.grid_columnconfigure(col, weight=1, uniform="channel-col")

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _update_multitwitch_link(self) -> None:
        online_sorted = sorted(self._online_channels)
        if online_sorted:
            self._multitwitch_url = f"https://multitwitch.tv/{'/'.join(online_sorted)}"
        else:
            self._multitwitch_url = "(no channels online)"
        self.lbl_multitwitch.configure(text=f"MultiTwitch -> {self._multitwitch_url}")

    def _apply_online_channels(self, online_channels: set[str]) -> None:
        previous_online = self._online_channels
        self._online_channels = online_channels

        self._call_runtime_thread(
            lambda: self._set_bot_online_channels_runtime(set(online_channels))
        )

        went_online = online_channels - previous_online
        went_offline = previous_online - online_channels

        for channel_name in sorted(went_online):
            self._append_system_log(f"#{channel_name} went ONLINE", "notification")
        for channel_name in sorted(went_offline):
            self._append_system_log(f"#{channel_name} went OFFLINE", "other")

        self._reorder_channel_cards(online_channels)
        self._update_multitwitch_link()

        total = len(self._bot_config.channels)
        self._append_system_log(f"Layout reordered - {len(online_channels)}/{total} online", "notification")

    def _call_runtime_thread(self, callback) -> None:
        if self._runtime_loop is None:
            return
        self._runtime_loop.call_soon_threadsafe(callback)

    def _reset_bot_channel_runtime(self, channel_name: str) -> None:
        for twitch_bot in self._bot_instances:
            twitch_bot.reset_channel(channel_name)

    def _reset_all_bot_channels_runtime(self) -> None:
        for channel in self._bot_config.channels:
            self._reset_bot_channel_runtime(channel.name)

    def _set_bot_online_channels_runtime(self, online_channels: set[str]) -> None:
        for twitch_bot in self._bot_instances:
            twitch_bot.update_online_channels(online_channels)

    def _start_runtime_thread(self) -> None:
        self._runtime_thread = threading.Thread(target=self._runtime_thread_main, daemon=True)
        self._runtime_thread.start()

    def _runtime_thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._runtime_loop = loop
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._runtime_main())
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    async def _runtime_main(self) -> None:
        poll_task = asyncio.create_task(self._poll_online_status())
        bot_task = asyncio.create_task(self._run_bot())

        try:
            await asyncio.gather(poll_task, bot_task)
        except asyncio.CancelledError:
            raise
        finally:
            for task in (poll_task, bot_task):
                if not task.done():
                    task.cancel()
            await self._shutdown_bots()

    async def _run_bot(self) -> None:
        try:
            emit_startup_logs(self._bot_config, self._bot_args)

            start_tasks: list[asyncio.Task[None]] = []
            for account in self._bot_config.accounts:
                bot = commands.Bot(
                    token=account.oauth_token,
                    nick=account.username,
                    prefix="§",
                    initial_channels=[ch.name for ch in self._bot_config.channels],
                )
                twitch_bot = TwitchBot(
                    bot,
                    self._bot_config,
                    account_name=account.username,
                    account_nickname=account.nickname,
                    ignored_usernames=account.ignored_usernames,
                    log_only_mode=self._bot_args.log_only,
                    enable_logging=(self._bot_args.log or self._bot_args.log_only),
                    send_confirmation_callback=self.request_send_confirmation,
                )
                self._bot_instances.append(twitch_bot)
                self._bot_clients.append(bot)
                twitch_bot.update_online_channels(set(self._online_channels))
                bot.add_cog(twitch_bot)
                start_tasks.append(asyncio.create_task(bot.start()))

            await asyncio.gather(*start_tasks)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._event_queue.put(("runtime_error", str(exc)))

    async def _shutdown_bots(self) -> None:
        close_tasks: list[asyncio.Task[None]] = []
        for bot in self._bot_clients:
            try:
                close_tasks.append(asyncio.create_task(bot.close()))
            except Exception:
                continue

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

    async def _poll_online_status(self) -> None:
        first_pass = True
        while not self._shutdown_event.is_set():
            try:
                channel_names = [ch.name for ch in self._bot_config.channels]
                online = await asyncio.to_thread(self._fetch_online_channels_helix_sync, channel_names)
                if first_pass or online != self._online_channels:
                    self._event_queue.put(("online_update", online))
            except Exception:
                pass

            first_pass = False
            try:
                await asyncio.sleep(self._ONLINE_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break

    def _normalize_bearer_token(self, token: str) -> str:
        token_value = token.strip()
        if token_value.lower().startswith("oauth:"):
            return token_value.split(":", 1)[1].strip()
        return token_value

    def _get_twitch_client_id_sync(self, token: str) -> str | None:
        validate_url = "https://id.twitch.tv/oauth2/validate"
        auth_variants = [f"OAuth {token}", f"Bearer {token}"]

        for authorization in auth_variants:
            request = urllib.request.Request(validate_url, headers={"Authorization": authorization})
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            except Exception:
                continue

            client_id = str(payload.get("client_id", "")).strip()
            if client_id:
                return client_id

        return None

    def _fetch_online_channels_helix_sync(self, channel_names: list[str]) -> set[str]:
        if not self._bot_config.accounts:
            return set()

        raw_token = self._bot_config.accounts[0].oauth_token
        token = self._normalize_bearer_token(raw_token)
        if not token:
            return set()

        client_id = self._get_twitch_client_id_sync(token)
        if not client_id:
            return set()

        online: set[str] = set()
        base_url = "https://api.twitch.tv/helix/streams"

        normalized_names = [name.strip().lower() for name in channel_names if name.strip()]
        for index in range(0, len(normalized_names), 100):
            chunk = normalized_names[index:index + 100]
            if not chunk:
                continue

            query = urllib.parse.urlencode([("user_login", name) for name in chunk])
            request = urllib.request.Request(
                f"{base_url}?{query}",
                headers={
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {token}",
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            except Exception:
                continue

            entries = payload.get("data", []) if isinstance(payload, dict) else []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                login = str(entry.get("user_login", "")).strip().lower()
                if login:
                    online.add(login)

        return online

    def _on_close(self) -> None:
        self._shutdown_event.set()
        set_gui_hook(None)

        if self._runtime_loop is not None:
            self._runtime_loop.call_soon_threadsafe(lambda: None)

        if self._runtime_thread is not None and self._runtime_thread.is_alive():
            self._runtime_thread.join(timeout=3.0)

        self.root.destroy()


def run_gui(args: argparse.Namespace) -> None:
    ui = MonitorUI(args)
    ui.run()
