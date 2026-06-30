import asyncio
import json
import os
import random
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as filedialog
from datetime import datetime
import importlib
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

import mss
from PIL import Image, ImageTk

from area_selector import select_area_and_snapshot_with_parent, select_area_with_parent, select_points_with_parent
from app_version import APP_NAME, APP_VERSION
from config import get_runtime_config_path
from scan_addresses import SCAN_ADDRESSES as DEFAULT_SCAN_ADDRESSES
from common_components import (
    get_module_base,
    make_button,
    open_process_for_reading,
    position_popup_at_main_window,
    read_int_from_process,
    read_numeric_from_process,
    read_ptr_from_process,
    read_ubyte_from_process,
    read_uint_from_process,
    read_ushort_from_process,
    read_value_pointer,
)
from process_tower import (
    diagnose_pointer_chain,
    find_scan_address_entry,
    find_scan_address_entry_any,
    on_process_tower_toggle_scan,
    scan_loop,
    reset_process_tower_scan_row,
    start_process_tower_scan,
    stop_process_tower_scan,
)
from spot_tower import run_spot_tower_monitor

if TYPE_CHECKING:
    pass


class MonitorUI:
    def __init__(self, initial_mode: str = 'SPOT TOWER'):
        self.root = tk.Tk()
        self._colors = {
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
            'success': '#26a269',
            'warning': '#e3b341',
            'input_bg': '#0f1318',
        }
        self._font_ui = ('Segoe UI', 10)
        self._font_title = ('Segoe UI Semibold', 17)
        self._font_title_sm = ('Segoe UI Semibold', 13)

        self._app_icon: tk.PhotoImage | None = None
        self._load_app_icon()
        self._apply_app_icon(self.root)
        self._setup_theme()
        self.root.withdraw()  # hidden until license is confirmed

        if not self._check_license_startup():
            self.root.destroy()
            sys.exit(0)

        self.root.title(f'{APP_NAME} Controller v{APP_VERSION}')
        self.root.geometry('560x360')
        self.root.minsize(360, 320)

        self.region: tuple[int, int, int, int] | None = None
        self.escape_route: list[dict[str, int | str]] = []
        self.escape_route_name: str | None = None
        self.escape_routes_config_path = get_runtime_config_path('escape_routes.json')
        self.saved_escape_routes: dict[str, list[dict[str, int | str]]] = self._load_saved_escape_routes()
        if self.saved_escape_routes:
            default_name = sorted(self.saved_escape_routes.keys())[0]
            self.escape_route_name = default_name
            self.escape_route = [dict(step) for step in self.saved_escape_routes.get(default_name, [])]
        self.template_path = get_runtime_config_path('spot_template.png')
        self.scan_addresses_config_path = get_runtime_config_path('scan_addresses.user.json')
        self.saved_scan_addresses: list[dict[str, str]] = self._load_scan_addresses()
        self._toast_notifier = None
        self._setup_windows_notifier()

        self._event_queue: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_loop: asyncio.AbstractEventLoop | None = None
        self._player_monitor: object | None = None
        self._detected_waiting_stop = False
        self._stop_requested = False
        self._stop_requested_at: float | None = None
        self._stop_retry_count = 0
        normalized_mode = str(initial_mode).strip().upper()
        if normalized_mode not in {'SPOT TOWER', 'PROCESS TOWER'}:
            normalized_mode = 'SPOT TOWER'
        self._mode_var = tk.StringVar(value=normalized_mode)
        self._last_mode_selection = normalized_mode
        self._compact_controls: bool | None = None
        self._process_tower_count_var = tk.StringVar(value='1')
        self._process_tower_rows: list[dict] = []
        self._process_handles: list[int] = []  # open HANDLE values to close on exit
        self._snapshot_window: tk.Toplevel | None = None
        self._snapshot_label: tk.Label | None = None
        self._snapshot_info_var: tk.StringVar | None = None
        self._snapshot_photo: ImageTk.PhotoImage | None = None
        self._last_trigger_snapshot: Image.Image | None = None
        self._last_trigger_mode: str = 'Trigger'
        self._last_trigger_time_var = tk.StringVar(value='Last trigger: Never')

        self._build_ui()
        self._set_state_idle('Idle')
        self.root.after(120, self._drain_events)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.bind('<Configure>', self._on_window_configure)
        self.root.deiconify()

    def _setup_theme(self) -> None:
        self.root.configure(bg=self._colors['bg'])
        self.root.option_add('*Font', self._font_ui)

        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        style.configure(
            'Dark.TCombobox',
            fieldbackground=self._colors['input_bg'],
            background=self._colors['panel_alt'],
            foreground=self._colors['text'],
            bordercolor=self._colors['border'],
            lightcolor=self._colors['border'],
            darkcolor=self._colors['border'],
            arrowcolor=self._colors['muted'],
            padding=(8, 5),
        )
        style.map(
            'Dark.TCombobox',
            fieldbackground=[('readonly', self._colors['input_bg'])],
            selectbackground=[('readonly', self._colors['input_bg'])],
            selectforeground=[('readonly', self._colors['text'])],
            background=[('readonly', self._colors['panel_alt'])],
            foreground=[('readonly', self._colors['text'])],
            bordercolor=[('focus', self._colors['accent'])],
            lightcolor=[('focus', self._colors['accent'])],
            darkcolor=[('focus', self._colors['accent'])],
            arrowcolor=[('active', self._colors['text']), ('readonly', self._colors['muted'])],
        )

    def _make_button(
        self,
        parent: tk.Misc,
        text: str,
        *,
        width: int,
        command,
        accent: bool = False,
        success: bool = False,
        danger: bool = False,
    ) -> tk.Button:
        return make_button(
            parent,
            self._colors,
            text,
            width=width,
            command=command,
            accent=accent,
            success=success,
            danger=danger,
        )

    def _load_app_icon(self) -> None:
        icon_path = Path(__file__).resolve().parent.parent / 'icons' / 'watchtower.png'
        if not icon_path.exists():
            return
        try:
            self._app_icon = tk.PhotoImage(file=str(icon_path))
        except Exception:
            self._app_icon = None

    def _apply_app_icon(self, window: tk.Misc) -> None:
        if self._app_icon is None:
            return
        try:
            window.iconphoto(True, self._app_icon)
        except Exception:
            # Keep default icon if this platform/window manager rejects iconphoto.
            pass

    def _position_popup_at_main_window(self, popup: tk.Misc, size: str | None = None) -> None:
        position_popup_at_main_window(self.root, popup, size)

    def _setup_windows_notifier(self) -> None:
        try:
            win10toast = importlib.import_module('win10toast')
            self._toast_notifier = win10toast.ToastNotifier()
        except Exception:
            self._toast_notifier = None

    def _notify_safe_zone(self) -> None:
        if self._toast_notifier is None:
            return
        try:
            self._toast_notifier.show_toast(
                'Watchtower',
                'Character moved to safe zone.',
                duration=5,
                threaded=True,
            )
        except Exception:
            pass

    def _capture_scan_area_snapshot(self) -> Image.Image | None:
        if self.region is None:
            return None
        x1, y1, x2, y2 = self.region
        width = max(1, int(x2 - x1))
        height = max(1, int(y2 - y1))
        try:
            with mss.mss() as sct:
                shot = sct.grab({'left': int(x1), 'top': int(y1), 'width': width, 'height': height})
            return Image.frombytes('RGB', shot.size, shot.rgb)
        except Exception:
            return None

    def _show_trigger_snapshot(self, image: Image.Image, mode_label: str) -> None:
        max_width = 760
        max_height = 460
        view = image.copy()
        view.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(view)

        if self._snapshot_window is None or not self._snapshot_window.winfo_exists():
            win = tk.Toplevel(self.root)
            win.title('Trigger Snapshot')
            win.configure(bg=self._colors['bg'])
            self._apply_app_icon(win)
            win.transient(self.root)
            self._position_popup_at_main_window(win)

            self._snapshot_info_var = tk.StringVar(value='')
            info = tk.Label(
                win,
                textvariable=self._snapshot_info_var,
                bg=self._colors['bg'],
                fg=self._colors['text'],
                anchor='w',
                padx=12,
                pady=10,
                font=('Segoe UI Semibold', 9),
            )
            info.pack(fill=tk.X)

            frame = tk.Frame(
                win,
                bg=self._colors['input_bg'],
                highlightthickness=1,
                highlightbackground=self._colors['border'],
            )
            frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

            self._snapshot_label = tk.Label(frame, bg=self._colors['input_bg'])
            self._snapshot_label.pack(fill=tk.BOTH, expand=True)

            self._snapshot_window = win

        self._snapshot_photo = photo
        if self._snapshot_label is not None:
            self._snapshot_label.configure(image=self._snapshot_photo)

        if self._snapshot_info_var is not None:
            shown_w, shown_h = view.size
            self._snapshot_info_var.set(
                f'{mode_label} trigger | Preview: {shown_w}x{shown_h} (scan area capture)'
            )

        if self._snapshot_window is not None:
            self._position_popup_at_main_window(self._snapshot_window)
            self._snapshot_window.lift()
            self._snapshot_window.focus_force()

    def _open_last_trigger_snapshot(self) -> None:
        if self._last_trigger_snapshot is None:
            messagebox.showinfo('No snapshot', 'No trigger snapshot is available yet.', parent=self.root)
            return
        self._show_trigger_snapshot(self._last_trigger_snapshot, self._last_trigger_mode)

    # ── License check ─────────────────────────────────────────────────────────

    def _check_license_startup(self) -> bool:
        from license_manager import get_license_path, validate_license

        valid, message = validate_license(get_license_path())
        if valid:
            return True
        return self._show_activation_dialog(message)

    def _show_activation_dialog(self, initial_message: str) -> bool:
        from license_manager import get_license_path, get_machine_id, validate_license

        result: dict[str, bool] = {'activated': False}
        machine_id = get_machine_id()

        dlg = tk.Toplevel(self.root)
        dlg.title(f'{APP_NAME} v{APP_VERSION} - Activation Required')
        self._position_popup_at_main_window(dlg, '500x380')
        dlg.resizable(False, False)
        dlg.protocol('WM_DELETE_WINDOW', lambda: None)
        self._apply_app_icon(dlg)
        # Root is intentionally hidden here; transient-to-hidden-parent can keep
        # the activation dialog out of view on some Windows setups.
        if self.root.winfo_viewable():
            dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=self._colors['bg'])
        dlg.attributes('-topmost', True)
        dlg.lift()
        dlg.focus_force()
        dlg.after(250, lambda: dlg.attributes('-topmost', False))

        tk.Label(
            dlg,
            text=f'{APP_NAME} v{APP_VERSION} - Activation Required',
            font=self._font_title_sm,
            bg=self._colors['bg'],
            fg=self._colors['text'],
        ).pack(pady=(18, 4))

        tk.Label(
            dlg,
            text='This software requires a valid license to run.',
            font=('Segoe UI', 10),
            bg=self._colors['bg'],
            fg=self._colors['muted'],
        ).pack()

        tk.Label(
            dlg,
            text='Your Machine ID:',
            font=('Segoe UI Semibold', 9),
            bg=self._colors['bg'],
            fg=self._colors['text'],
        ).pack(pady=(14, 2))

        mid_var = tk.StringVar(value=machine_id)
        mid_entry = tk.Entry(
            dlg,
            textvariable=mid_var,
            state='readonly',
            font=('Consolas', 12),
            justify='center',
            width=24,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            readonlybackground=self._colors['input_bg'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        mid_entry.pack()

        def _copy_id() -> None:
            dlg.clipboard_clear()
            dlg.clipboard_append(machine_id)
            btn_copy.configure(text='Copied!')
            dlg.after(1500, lambda: btn_copy.configure(text='Copy Machine ID'))

        btn_copy = self._make_button(dlg, text='Copy Machine ID', width=20, command=_copy_id)
        btn_copy.pack(pady=(5, 0))

        tk.Label(
            dlg,
            text='Send this ID to the distributor to receive your license.dat',
            font=('Segoe UI', 8),
            fg=self._colors['muted'],
            bg=self._colors['bg'],
        ).pack(pady=(3, 10))

        status_var = tk.StringVar(value=initial_message.split('\n\n')[0])
        tk.Label(
            dlg,
            textvariable=status_var,
            fg='#ff8080',
            bg=self._colors['bg'],
            wraplength=440,
            font=('Segoe UI', 8),
            justify='center',
        ).pack(pady=(0, 10))

        def _browse_license() -> None:
            path_str = filedialog.askopenfilename(
                parent=dlg,
                title='Select license.dat',
                filetypes=[('License file', '*.dat'), ('All files', '*.*')],
            )
            if not path_str:
                return
            selected = Path(path_str)
            valid, msg = validate_license(selected)
            if not valid:
                status_var.set(msg.split('\n')[0])
                return
            target = get_license_path()
            try:
                shutil.copy2(selected, target)
            except Exception as exc:
                status_var.set(f'Could not copy license: {exc}\nCopy manually to: {target}')
                return
            open_folder = messagebox.askyesno(
                'License Activated',
                f'License copied successfully to:\n\n{target}\n\nOpen this folder now?',
                parent=dlg,
            )
            if open_folder:
                try:
                    os.startfile(str(target.parent))
                except Exception:
                    pass
            result['activated'] = True
            dlg.destroy()

        def _exit_app() -> None:
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=self._colors['bg'])
        btn_row.pack(pady=(0, 18))
        self._make_button(
            btn_row,
            text='Browse for license.dat…',
            width=26,
            command=_browse_license,
            accent=True,
        ).pack(
            side=tk.LEFT, padx=8
        )
        self._make_button(btn_row, text='Exit', width=10, command=_exit_app, danger=True).pack(
            side=tk.LEFT, padx=8
        )

        self.root.wait_window(dlg)
        return result['activated']

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        container = tk.Frame(
            self.root,
            padx=14,
            pady=14,
            bg=self._colors['panel'],
            highlightthickness=1,
            highlightbackground=self._colors['border'],
        )
        container.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            container,
            text=f'{APP_NAME} v{APP_VERSION}',
            font=self._font_title,
            bg=self._colors['panel'],
            fg=self._colors['text'],
        )
        title.pack(anchor=tk.W)

        mode_row = tk.Frame(container, bg=self._colors['panel'])
        mode_row.pack(fill=tk.X, pady=(10, 4))

        tk.Label(
            mode_row,
            text='Operation Mode:',
            anchor=tk.W,
            bg=self._colors['panel'],
            fg=self._colors['muted'],
        ).pack(side=tk.LEFT)
        self.cmb_mode = ttk.Combobox(
            mode_row,
            state='readonly',
            textvariable=self._mode_var,
            values=['SPOT TOWER', 'PROCESS TOWER'],
            width=18,
            style='Dark.TCombobox',
        )
        self.cmb_mode.pack(side=tk.LEFT, padx=(8, 0))
        self.cmb_mode.bind('<<ComboboxSelected>>', self._on_mode_changed)

        self.controls = tk.Frame(container, bg=self._colors['panel'])
        self.controls.pack(fill=tk.X, pady=(10, 8))

        self.btn_select_area = self._make_button(
            self.controls,
            text='Select Area',
            width=16,
            command=self._on_select_area,
        )

        self.btn_select_route = self._make_button(
            self.controls,
            text='Create Escape Route',
            width=20,
            command=self._on_select_route,
        )

        self.btn_capture_template = self._make_button(
            self.controls,
            text='Capture Template',
            width=16,
            command=self._on_capture_template,
        )

        self.btn_toggle_scan = self._make_button(
            self.controls,
            text='Start Scanner',
            width=16,
            command=self._on_toggle_scanner,
            accent=True,
        )

        self._relayout_controls(compact=False)

        # ── Process Tower Panel ──────────────────────────────────────────────
        self._process_tower_panel = tk.Frame(container, bg=self._colors['panel'])
        # (not packed until PROCESS TOWER mode is active)

        count_row = tk.Frame(self._process_tower_panel, bg=self._colors['panel'])
        count_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            count_row,
            text='Characters to monitor:',
            anchor=tk.W,
            bg=self._colors['panel'],
            fg=self._colors['muted'],
        ).pack(side=tk.LEFT)
        tk.Entry(
            count_row,
            textvariable=self._process_tower_count_var,
            width=6,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            insertbackground=self._colors['text'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        ).pack(side=tk.LEFT, padx=(8, 8))
        self._make_button(
            count_row,
            text='Apply',
            width=8,
            command=self._on_process_tower_apply_count,
        ).pack(side=tk.LEFT)
        self._make_button(
            count_row,
            text='Addresses',
            width=12,
            command=self._open_scan_address_manager,
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Scrollable rows container
        rows_outer = tk.Frame(self._process_tower_panel, bg=self._colors['panel'])
        rows_outer.pack(fill=tk.BOTH, expand=True)

        self._process_tower_canvas = tk.Canvas(
            rows_outer,
            bg=self._colors['panel'],
            highlightthickness=0,
            bd=0,
            height=150,
        )
        scrollbar = ttk.Scrollbar(rows_outer, orient=tk.VERTICAL, command=self._process_tower_canvas.yview)
        self._process_tower_canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._process_tower_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._process_tower_rows_frame = tk.Frame(self._process_tower_canvas, bg=self._colors['panel'])
        self._process_tower_canvas_window = self._process_tower_canvas.create_window(
            (0, 0), window=self._process_tower_rows_frame, anchor='nw'
        )

        def _on_rows_frame_configure(event):
            self._process_tower_canvas.configure(
                scrollregion=self._process_tower_canvas.bbox('all')
            )

        def _on_canvas_configure(event):
            self._process_tower_canvas.itemconfig(
                self._process_tower_canvas_window, width=event.width
            )

        self._process_tower_rows_frame.bind('<Configure>', _on_rows_frame_configure)
        self._process_tower_canvas.bind('<Configure>', _on_canvas_configure)

        self._rebuild_process_tower_rows(1)
        # ─────────────────────────────────────────────────────────────────────

        self.state_row = tk.Frame(container, bg=self._colors['panel'])
        self.state_row.pack(fill=tk.X, pady=(6, 6))

        self.led = tk.Canvas(
            self.state_row,
            width=18,
            height=18,
            highlightthickness=0,
            bg=self._colors['panel'],
            bd=0,
        )
        self.led.pack(side=tk.LEFT)
        self.led_circle = self.led.create_oval(2, 2, 16, 16, fill='#7a7a7a', outline='#1f1f1f')

        self.lbl_state = tk.Label(
            self.state_row,
            text='State: Idle',
            font=('Segoe UI Semibold', 10),
            bg=self._colors['panel'],
            fg=self._colors['text'],
        )
        self.lbl_state.pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_region = tk.Label(
            container,
            text='Region: not selected',
            anchor=tk.W,
            bg=self._colors['panel'],
            fg=self._colors['muted'],
        )
        self.lbl_region.pack(fill=tk.X)

        self.lbl_route = tk.Label(
            container,
            text='Escape route: not selected',
            anchor=tk.W,
            bg=self._colors['panel'],
            fg=self._colors['muted'],
        )
        self.lbl_route.pack(fill=tk.X, pady=(2, 8))

        self.btn_last_snapshot = self._make_button(
            container,
            text='View Last Trigger Snapshot',
            width=26,
            command=self._open_last_trigger_snapshot,
        )
        self.btn_last_snapshot.pack(fill=tk.X, pady=(0, 8))
        self.btn_last_snapshot.configure(state=tk.DISABLED)

        self.lbl_last_trigger = tk.Label(
            container,
            textvariable=self._last_trigger_time_var,
            anchor=tk.W,
            font=('Segoe UI Semibold', 10),
            bg=self._colors['input_bg'],
            fg=self._colors['accent'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
            padx=10,
            pady=8,
        )
        self.lbl_last_trigger.pack(side=tk.BOTTOM, fill=tk.X)

        self._refresh_mode_ui()

    def _relayout_controls(self, compact: bool) -> None:
        if compact == self._compact_controls:
            return

        self._compact_controls = compact

        self.btn_select_area.grid_forget()
        self.btn_select_route.grid_forget()
        self.btn_capture_template.grid_forget()
        self.btn_toggle_scan.grid_forget()

        if compact:
            for col in range(3):
                self.controls.grid_columnconfigure(col, weight=0)

            self.controls.grid_columnconfigure(0, weight=1)
            self.btn_select_area.grid(row=0, column=0, sticky='ew', padx=0, pady=3)
            self.btn_select_route.grid(row=1, column=0, sticky='ew', padx=0, pady=3)
            self.btn_capture_template.grid(row=2, column=0, sticky='ew', padx=0, pady=3)
            self.btn_toggle_scan.grid(row=3, column=0, sticky='ew', padx=0, pady=(6, 3))
            return

        for col in range(3):
            self.controls.grid_columnconfigure(col, weight=1, uniform='controls')

        self.btn_select_area.grid(row=0, column=0, sticky='ew', padx=(0, 8), pady=4)
        self.btn_select_route.grid(row=0, column=1, sticky='ew', padx=(0, 8), pady=4)
        self.btn_capture_template.grid(row=0, column=2, sticky='ew', padx=(0, 0), pady=4)
        self.btn_toggle_scan.grid(row=1, column=0, columnspan=3, sticky='ew', padx=0, pady=(6, 4))

    def _on_window_configure(self, _event=None) -> None:
        width = self.root.winfo_width()
        self._relayout_controls(compact=width < 540)

    def run(self):
        self.root.mainloop()

    def _log(self, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f'[{timestamp}] {message}')

    def _set_last_trigger_now(self) -> None:
        timestamp = datetime.now().strftime('%H:%M:%S')
        self._last_trigger_time_var.set(f'Last trigger: {timestamp}')

    @staticmethod
    def _normalize_route_step(step: object) -> dict[str, int | str] | None:
        if not isinstance(step, dict):
            return None

        step_type = str(step.get('type', '')).strip().lower()
        if step_type == 'click':
            try:
                x = int(step.get('x', 0))
                y = int(step.get('y', 0))
            except (TypeError, ValueError):
                return None
            return {'type': 'click', 'x': x, 'y': y}

        if step_type == 'key':
            key_name = str(step.get('key', '')).strip()
            if not key_name:
                return None
            return {'type': 'key', 'key': key_name}

        if step_type == 'text':
            text_value = str(step.get('text', ''))
            if not text_value:
                return None
            return {'type': 'text', 'text': text_value}

        return None

    def _normalize_route(self, route: object) -> list[dict[str, int | str]]:
        if not isinstance(route, list):
            return []

        normalized: list[dict[str, int | str]] = []
        for step in route:
            normalized_step = self._normalize_route_step(step)
            if normalized_step is not None:
                normalized.append(normalized_step)
        return normalized

    def _load_saved_escape_routes(self) -> dict[str, list[dict[str, int | str]]]:
        if not self.escape_routes_config_path.exists():
            return {}

        try:
            payload = json.loads(self.escape_routes_config_path.read_text(encoding='utf-8'))
        except Exception:
            return {}

        routes_obj = payload.get('routes', {}) if isinstance(payload, dict) else {}
        if not isinstance(routes_obj, dict):
            return {}

        normalized: dict[str, list[dict[str, int | str]]] = {}
        for name, route in routes_obj.items():
            route_name = str(name).strip()
            if not route_name:
                continue
            normalized_route = self._normalize_route(route)
            if normalized_route:
                normalized[route_name] = normalized_route

        return normalized

    def _persist_saved_escape_routes(self) -> bool:
        routes_payload = {
            name: [dict(step) for step in route]
            for name, route in sorted(self.saved_escape_routes.items(), key=lambda item: item[0].lower())
            if route
        }

        payload = {'routes': routes_payload}
        try:
            self.escape_routes_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.escape_routes_config_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True),
                encoding='utf-8',
            )
            return True
        except Exception as exc:
            messagebox.showerror('Save failed', f'Could not save escape routes:\n{exc}', parent=self.root)
            return False

    def _apply_escape_route(self, route_name: str, route: list[dict[str, int | str]], *, log_change: bool = True) -> None:
        self.escape_route_name = route_name
        self.escape_route = [dict(step) for step in route]
        self._update_route_label()
        if log_change:
            self._log(f'Loaded escape route: {route_name} ({len(self.escape_route)} step(s)).')

    def _set_led(self, color: str):
        self.led.itemconfig(self.led_circle, fill=color)

    def _set_state_idle(self, reason: str):
        self._set_led('#7a7a7a')
        self.lbl_state.configure(text=f'State: Idle ({reason})')
        self.btn_toggle_scan.configure(text='Start Scanner', state=tk.NORMAL)
        self.btn_toggle_scan.configure(bg=self._colors['success'], activebackground='#1f8f58')

    def _set_state_scanning(self):
        self._set_led('#00b050')
        self.lbl_state.configure(text='State: Scanning')
        self.btn_toggle_scan.configure(text='Stop Scanner', state=tk.NORMAL)
        self.btn_toggle_scan.configure(bg=self._colors['danger'], activebackground=self._colors['danger_hover'])

    def _set_state_detected(self):
        self._set_led('#d32f2f')
        self.lbl_state.configure(text='State: Detected')
        self.btn_toggle_scan.configure(text='Stop Scanner', state=tk.NORMAL)
        self.btn_toggle_scan.configure(bg=self._colors['danger'], activebackground=self._colors['danger_hover'])

    def _set_state_stopping(self):
        self._set_led(self._colors['warning'])
        self.lbl_state.configure(text='State: Stopping')
        self.btn_toggle_scan.configure(text='Stopping...', state=tk.DISABLED)
        self.btn_toggle_scan.configure(bg=self._colors['warning'], activebackground=self._colors['warning'])

    def _selected_mode(self) -> str:
        val = self._mode_var.get()
        if val == 'PROCESS TOWER':
            return 'process-tower'
        return 'spot-tower'

    def _refresh_mode_ui(self):
        mode = self._selected_mode()
        if mode == 'process-tower':
            self.controls.pack_forget()
            self.btn_last_snapshot.pack_forget()
            self._process_tower_panel.pack(
                fill=tk.BOTH, expand=True, pady=(10, 4),
                before=self.state_row,
            )
            self.state_row.pack_forget()
            self.lbl_region.pack_forget()
            self.lbl_route.configure(text='')
            self.root.minsize(400, 420)
            self._log('Mode set to PROCESS TOWER.')
        else:
            self._process_tower_panel.pack_forget()
            self.state_row.pack(fill=tk.X, pady=(6, 6))
            self.lbl_region.pack(fill=tk.X)
            self.controls.pack(fill=tk.X, pady=(10, 8), before=self.state_row)
            self.btn_last_snapshot.pack(fill=tk.X, pady=(0, 8), before=self.lbl_last_trigger)
            self.root.minsize(360, 320)
            self.btn_select_route.configure(state=tk.NORMAL)
            self._update_route_label()
            self._log('Mode set to SPOT TOWER.')

    def _update_route_label(self):
        if not self.escape_route:
            self.lbl_route.configure(text='Escape route: not selected')
            return

        click_count = sum(1 for item in self.escape_route if str(item.get('type', '')).lower() == 'click')
        key_count = sum(1 for item in self.escape_route if str(item.get('type', '')).lower() == 'key')
        text_count = sum(1 for item in self.escape_route if str(item.get('type', '')).lower() == 'text')
        route_name_label = self.escape_route_name or 'Unnamed'
        self.lbl_route.configure(
            text=(
                f'Escape route: {route_name_label} - {len(self.escape_route)} step(s) '
                f'({click_count} click, {key_count} key, {text_count} text)'
            )
        )

    def _rebuild_process_tower_rows(self, count: int) -> None:
        # Stop any running scan threads first
        for row in self._process_tower_rows:
            stop_ev = row.get('scan_stop')
            if stop_ev:
                stop_ev.set()

        # Preserve existing values
        saved: list[dict] = []
        for row in self._process_tower_rows:
            saved.append({
                'name': row['name_var'].get(),
                'threshold': row['threshold_var'].get() if row.get('threshold_var') else '0',
                'key': row['key_var'].get() if row.get('key_var') else 'alt+0',
                'escape_order': row['escape_order_var'].get() if row.get('escape_order_var') else '1',
                'escape_delay_min_ms': row['escape_delay_min_ms_var'].get() if row.get('escape_delay_min_ms_var') else '100',
                'escape_delay_max_ms': row['escape_delay_max_ms_var'].get() if row.get('escape_delay_max_ms_var') else '300',
                'ghost_app': row['ghost_app_var'].get() if row.get('ghost_app_var') else False,
                'is_slayer': row['is_slayer_var'].get() if row.get('is_slayer_var') else True,
                'radar': row['radar_var'].get() if row.get('radar_var') else '',
                'process_path': row.get('process_path'),
            })

        for widget in self._process_tower_rows_frame.winfo_children():
            widget.destroy()
        self._process_tower_rows = []

        for i in range(count):
            s = saved[i] if i < len(saved) else {}
            name_var = tk.StringVar(value=s.get('name', ''))
            threshold_var = tk.StringVar(value=s.get('threshold', '0'))
            key_var = tk.StringVar(value=s.get('key', 'alt+0'))
            escape_order_var = tk.StringVar(value=s.get('escape_order', '1'))
            escape_delay_min_ms_var = tk.StringVar(value=s.get('escape_delay_min_ms', '100'))
            escape_delay_max_ms_var = tk.StringVar(value=s.get('escape_delay_max_ms', '300'))
            ghost_app_var = tk.BooleanVar(value=bool(s.get('ghost_app', False)))
            is_slayer_var = tk.BooleanVar(value=s.get('is_slayer', True))
            radar_var = tk.StringVar(value=s.get('radar', ''))
            status_var = tk.StringVar(value='Not attached')
            radar_count_var = tk.StringVar(value='Radar: N/A')
            map_count_var = tk.StringVar(value='Map: 0')
            scan_stop = threading.Event()

            row_frame = tk.Frame(
                self._process_tower_rows_frame,
                bg=self._colors['panel_alt'],
                highlightthickness=1,
                highlightbackground=self._colors['border'],
            )
            row_frame.pack(fill=tk.X, pady=(0, 4))

            # ── Top line: index | name | Attach | status ──────────────────
            top = tk.Frame(row_frame, bg=self._colors['panel_alt'])
            top.pack(fill=tk.X, padx=6, pady=(6, 2))

            tk.Label(
                top, text=f'#{i + 1}', width=3, anchor=tk.CENTER,
                bg=self._colors['panel_alt'], fg=self._colors['muted'],
            ).pack(side=tk.LEFT, padx=(0, 4))

            tk.Entry(
                top, textvariable=name_var, width=14,
                bg=self._colors['input_bg'], fg=self._colors['text'],
                insertbackground=self._colors['text'], relief=tk.FLAT,
                highlightthickness=1, highlightbackground=self._colors['border'],
                highlightcolor=self._colors['accent'],
            ).pack(side=tk.LEFT, padx=(0, 8))

            btn_attach = self._make_button(
                top, text='Attach', width=8,
                command=lambda idx=i: self._on_process_tower_attach_process(idx),
            )
            btn_attach.pack(side=tk.LEFT, padx=(0, 8))
            tk.Checkbutton(
                top, text='Slayer',
                variable=is_slayer_var,
                bg=self._colors['panel_alt'], fg=self._colors['text'],
                selectcolor=self._colors['input_bg'],
                activebackground=self._colors['panel_alt'],
                activeforeground=self._colors['text'],
                font=('Segoe UI', 9),
                command=lambda idx=i: self._on_slayer_toggle(idx),
            ).pack(side=tk.LEFT, padx=(0, 6))


            tk.Label(
                top, textvariable=status_var, anchor=tk.W,
                bg=self._colors['panel_alt'], fg=self._colors['muted'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT)

            count_frame = tk.Frame(top, bg=self._colors['panel_alt'])
            count_frame.pack(side=tk.RIGHT)
            radar_count_label = tk.Label(
                count_frame, textvariable=radar_count_var, anchor=tk.W,
                bg=self._colors['panel_alt'], fg=self._colors['accent'],
                font=('Consolas', 9),
            )
            radar_count_label.pack(anchor=tk.E)
            map_count_label = tk.Label(
                count_frame, textvariable=map_count_var, anchor=tk.W,
                bg=self._colors['panel_alt'], fg=self._colors['muted'],
                font=('Consolas', 9),
            )
            map_count_label.pack(anchor=tk.E)

            # ── Bottom line: dynamic (slayer vs radar) ────────────────────
            bot = tk.Frame(row_frame, bg=self._colors['panel_alt'])
            bot.pack(fill=tk.X, padx=6, pady=(0, 6))

            # --- Slayer controls ---
            slayer_frame = tk.Frame(bot, bg=self._colors['panel_alt'])

            tk.Label(slayer_frame, text='Max:', bg=self._colors['panel_alt'],
                     fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
            tk.Entry(slayer_frame, textvariable=threshold_var, width=5,
                     bg=self._colors['input_bg'], fg=self._colors['text'],
                     insertbackground=self._colors['text'], relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=self._colors['border'],
                     highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(4, 10))
            tk.Label(slayer_frame, text='Trigger:', bg=self._colors['panel_alt'],
                     fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
            tk.Label(slayer_frame, textvariable=key_var, width=8, anchor=tk.W,
                     bg=self._colors['input_bg'], fg=self._colors['accent'],
                     font=('Consolas', 9), relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=self._colors['border'],
                     padx=4).pack(side=tk.LEFT, padx=(4, 4))
            self._make_button(slayer_frame, text='Set Key', width=7,
                              command=lambda idx=i: self._on_process_tower_set_key(idx),
                              ).pack(side=tk.LEFT, padx=(0, 8))
            btn_start = self._make_button(slayer_frame, text='Start', width=7, accent=True,
                                          command=lambda idx=i: self._on_process_tower_toggle_scan(idx))
            btn_start.pack(side=tk.LEFT)
            tk.Checkbutton(
                slayer_frame,
                text='Ghost app',
                variable=ghost_app_var,
                bg=self._colors['panel_alt'],
                fg=self._colors['text'],
                selectcolor=self._colors['input_bg'],
                activebackground=self._colors['panel_alt'],
                activeforeground=self._colors['text'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT, padx=(8, 0))

            # --- Radar controls ---
            radar_frame = tk.Frame(bot, bg=self._colors['panel_alt'])

            tk.Label(radar_frame, text='Radar (Slayer):', bg=self._colors['panel_alt'],
                     fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
            radar_combo = ttk.Combobox(radar_frame, textvariable=radar_var,
                                       state='readonly', width=16, style='Dark.TCombobox')
            radar_combo.pack(side=tk.LEFT, padx=(6, 10))
            radar_combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_escape_order_combos())
            tk.Label(radar_frame, text='Trigger:', bg=self._colors['panel_alt'],
                     fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
            tk.Label(radar_frame, textvariable=key_var, width=8, anchor=tk.W,
                     bg=self._colors['input_bg'], fg=self._colors['accent'],
                     font=('Consolas', 9), relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=self._colors['border'],
                     padx=4).pack(side=tk.LEFT, padx=(4, 4))
            self._make_button(radar_frame, text='Set Key', width=7,
                              command=lambda idx=i: self._on_process_tower_set_key(idx),
                              ).pack(side=tk.LEFT)

            def _show_bot_frame(idx=i):
                row = self._process_tower_rows[idx]
                sf = row['slayer_frame']
                rf = row['radar_frame']
                radar_label = row.get('radar_count_label')
                map_label = row.get('map_count_label')
                if row['is_slayer_var'].get():
                    rf.pack_forget()
                    sf.pack(fill=tk.X)
                    if radar_label is not None and not radar_label.winfo_manager():
                        radar_label.pack(anchor=tk.E)
                    if map_label is not None and not map_label.winfo_manager():
                        map_label.pack(anchor=tk.E)
                    radar_var = row.get('radar_count_var')
                    if radar_var is not None and not str(radar_var.get()).strip():
                        radar_var.set('Radar: N/A')
                    map_var = row.get('map_count_var')
                    if map_var is not None and not str(map_var.get()).strip():
                        map_var.set('Map: 0')
                else:
                    sf.pack_forget()
                    rf.pack(fill=tk.X)
                    if radar_label is not None and radar_label.winfo_manager():
                        radar_label.pack_forget()
                    if map_label is not None and map_label.winfo_manager():
                        map_label.pack_forget()
                    if map_label is not None and not map_label.winfo_manager():
                        map_label.pack(anchor=tk.E)
                    radar_var = row.get('radar_count_var')
                    if radar_var is not None:
                        radar_var.set('')
                    map_var = row.get('map_count_var')
                    if map_var is not None:
                        if not str(map_var.get()).startswith('Map: '):
                            map_var.set('Map: 0')

            # --- Shared escape controls (apply to slayer and radar rows) ---
            escape_frame = tk.Frame(row_frame, bg=self._colors['panel_alt'])
            escape_frame.pack(fill=tk.X, padx=6, pady=(0, 6))

            tk.Label(
                escape_frame,
                text='Escape order:',
                bg=self._colors['panel_alt'],
                fg=self._colors['muted'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT)
            escape_order_combo = ttk.Combobox(
                escape_frame,
                textvariable=escape_order_var,
                state='readonly',
                width=4,
                style='Dark.TCombobox',
            )
            escape_order_combo.pack(side=tk.LEFT, padx=(6, 14))
            escape_order_combo.bind('<<ComboboxSelected>>', lambda _e, idx=i: self._on_escape_order_selected(idx))

            tk.Label(
                escape_frame,
                text='Delay ms:',
                bg=self._colors['panel_alt'],
                fg=self._colors['muted'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT)
            tk.Entry(
                escape_frame,
                textvariable=escape_delay_min_ms_var,
                width=6,
                bg=self._colors['input_bg'],
                fg=self._colors['text'],
                insertbackground=self._colors['text'],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors['border'],
                highlightcolor=self._colors['accent'],
            ).pack(side=tk.LEFT, padx=(6, 4))
            tk.Label(
                escape_frame,
                text='to',
                bg=self._colors['panel_alt'],
                fg=self._colors['muted'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT)
            tk.Entry(
                escape_frame,
                textvariable=escape_delay_max_ms_var,
                width=6,
                bg=self._colors['input_bg'],
                fg=self._colors['text'],
                insertbackground=self._colors['text'],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors['border'],
                highlightcolor=self._colors['accent'],
            ).pack(side=tk.LEFT, padx=(4, 0))

            retry_status_var = tk.StringVar(value='Retry: idle')
            tk.Label(
                row_frame,
                textvariable=retry_status_var,
                anchor=tk.W,
                bg=self._colors['panel_alt'],
                fg=self._colors['muted'],
                font=('Consolas', 8),
            ).pack(fill=tk.X, padx=6, pady=(0, 6))

            row_data: dict = {
                'name_var': name_var,
                'threshold_var': threshold_var,
                'key_var': key_var,
                'escape_order_var': escape_order_var,
                'escape_order_combo': escape_order_combo,
                'escape_delay_min_ms_var': escape_delay_min_ms_var,
                'escape_delay_max_ms_var': escape_delay_max_ms_var,
                'ghost_app_var': ghost_app_var,
                'retry_status_var': retry_status_var,
                'is_slayer_var': is_slayer_var,
                'radar_var': radar_var,
                'radar_combo': radar_combo,
                'slayer_frame': slayer_frame,
                'radar_frame': radar_frame,
                'show_bot': _show_bot_frame,
                'process_path': s.get('process_path'),
                'pid': None,
                'handle': None,
                'status_var': status_var,
                'radar_count_var': radar_count_var,
                'radar_count_label': radar_count_label,
                'map_count_var': map_count_var,
                'map_count_label': map_count_label,
                'btn': btn_attach,
                'btn_start': btn_start,
                'scan_stop': scan_stop,
                'scan_thread': None,
            }
            name_var.trace_add('write', lambda *_: self._refresh_radar_combos())
            radar_var.trace_add('write', lambda *_: self._refresh_escape_order_combos())
            self._process_tower_rows.append(row_data)

        # Show correct bottom frame and populate radar combos
        for row in self._process_tower_rows:
            row['show_bot']()
        self._refresh_radar_combos()

        # Resize canvas: ~124px per 3-line row, cap at 4 visible
        row_h = 124
        canvas_h = max(row_h, min(count * row_h, 4 * row_h))
        self._process_tower_canvas.configure(height=canvas_h)

    def _slayer_label(self, idx: int) -> str:
        row = self._process_tower_rows[idx]
        name = row['name_var'].get().strip()
        return name if name else f'#{idx + 1}'

    def _refresh_radar_combos(self) -> None:
        """Repopulate all non-slayer radar comboboxes with current slayer labels."""
        slayer_labels = [
            self._slayer_label(i)
            for i, row in enumerate(self._process_tower_rows)
            if row['is_slayer_var'].get()
        ]
        for row in self._process_tower_rows:
            if not row['is_slayer_var'].get():
                combo = row['radar_combo']
                combo['values'] = slayer_labels
                if row['radar_var'].get() not in slayer_labels:
                    row['radar_var'].set(slayer_labels[0] if slayer_labels else '')
        self._refresh_escape_order_combos()

    def _refresh_escape_order_combos(self) -> None:
        self._refresh_escape_order_combos_with_priority(None)

    def _on_escape_order_selected(self, idx: int) -> None:
        self._refresh_escape_order_combos_with_priority(idx)

    def _refresh_escape_order_combos_with_priority(self, preferred_idx: int | None) -> None:
        group_counts: dict[str, int] = {}
        group_rows: dict[str, list[int]] = {}

        def _row_group_label(row_idx: int) -> str:
            row = self._process_tower_rows[row_idx]
            if row['is_slayer_var'].get():
                return self._slayer_label(row_idx)
            return row['radar_var'].get().strip()

        for i, row in enumerate(self._process_tower_rows):
            if not row['is_slayer_var'].get():
                continue
            label = self._slayer_label(i)
            if label:
                group_counts[label] = 1
                group_rows.setdefault(label, []).append(i)

        for i, row in enumerate(self._process_tower_rows):
            if row['is_slayer_var'].get():
                continue
            label = row['radar_var'].get().strip()
            if label:
                group_counts[label] = group_counts.get(label, 0) + 1
                group_rows.setdefault(label, []).append(i)

        for i, row in enumerate(self._process_tower_rows):
            label = _row_group_label(i)

            group_size = max(1, group_counts.get(label, 1))
            values = [str(n) for n in range(1, group_size + 1)]

            combo = row.get('escape_order_combo')
            if combo is not None:
                combo['values'] = values

        for label, indices in group_rows.items():
            group_size = max(1, group_counts.get(label, 1))
            assignment_order = list(indices)
            if preferred_idx is not None and preferred_idx in assignment_order:
                assignment_order = [preferred_idx] + [idx for idx in assignment_order if idx != preferred_idx]

            taken: set[int] = set()
            assigned: dict[int, int] = {}
            for idx in assignment_order:
                raw = self._process_tower_rows[idx]['escape_order_var'].get().strip()
                try:
                    candidate = int(raw)
                except ValueError:
                    candidate = 0

                if candidate < 1 or candidate > group_size or candidate in taken:
                    for fallback in range(1, group_size + 1):
                        if fallback not in taken:
                            candidate = fallback
                            break

                taken.add(candidate)
                assigned[idx] = candidate

            for idx in indices:
                self._process_tower_rows[idx]['escape_order_var'].set(str(assigned[idx]))

    def _on_slayer_toggle(self, idx: int) -> None:
        row = self._process_tower_rows[idx]
        if not row['is_slayer_var'].get():
            stop_ev = row.get('scan_stop')
            if stop_ev:
                stop_ev.set()
        row['show_bot']()
        self._refresh_radar_combos()


    # ── Scan address module helpers ───────────────────────────────────────────

    def _load_scan_addresses(self) -> list[dict]:
        try:
            # Defaults are embedded in src/scan_addresses.py so they are always
            # available in the packaged executable.
            entries = DEFAULT_SCAN_ADDRESSES if isinstance(DEFAULT_SCAN_ADDRESSES, list) else []
            if self.scan_addresses_config_path.exists():
                payload = json.loads(self.scan_addresses_config_path.read_text(encoding='utf-8'))
                if isinstance(payload, dict):
                    entries = payload.get('addresses', entries)
                elif isinstance(payload, list):
                    entries = payload

            result = []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                name = str(e.get('name', '')).strip()
                if not name:
                    continue
                entry_type = str(e.get('type', 'static')).strip().lower()
                if entry_type == 'pointer':
                    module = str(e.get('module', '')).strip()
                    base_offset = str(e.get('base_offset', '0x0')).strip()
                    raw_offsets = e.get('offsets', [])
                    offsets = [str(o).strip() for o in raw_offsets if str(o).strip()]
                    desc = str(e.get('description', '')).strip()
                    if module and offsets:
                        result.append({
                            'name': name, 'type': 'pointer',
                            'module': module, 'base_offset': base_offset,
                            'offsets': offsets, 'description': desc,
                        })
                else:
                    addr = str(e.get('address', '')).strip()
                    desc = str(e.get('description', '')).strip()
                    if addr:
                        result.append({'name': name, 'type': 'static', 'address': addr, 'description': desc})
            return result
        except Exception:
            return []

    def _save_scan_addresses(self) -> bool:
        try:
            self.scan_addresses_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.scan_addresses_config_path.write_text(
                json.dumps({'addresses': self.saved_scan_addresses}, indent=2, ensure_ascii=True) + '\n',
                encoding='utf-8',
            )
            return True
        except Exception as exc:
            messagebox.showerror('Save failed', f'Could not save addresses:\n{exc}', parent=self.root)
            return False

    def _pick_scan_address(self, addr_var: tk.StringVar) -> None:
        """Open a picker dialog; on select, writes the entry NAME into addr_var."""
        if not self.saved_scan_addresses:
            messagebox.showinfo(
                'No addresses',
                'No saved addresses yet.\nUse the "Addresses" button to add some.',
                parent=self.root,
            )
            return

        dlg = tk.Toplevel(self.root)
        dlg.title('Pick Address')
        self._position_popup_at_main_window(dlg, '480x320')
        dlg.minsize(380, 260)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg, text='Select a saved address:',
            font=('Segoe UI', 10), bg=self._colors['bg'], fg=self._colors['text'],
        ).pack(anchor=tk.W, padx=12, pady=(12, 6))

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        lb = tk.Listbox(
            list_frame, bg=self._colors['input_bg'], fg=self._colors['text'],
            selectbackground=self._colors['accent'], selectforeground='#ffffff',
            activestyle='none', relief=tk.FLAT,
            highlightthickness=1, highlightbackground=self._colors['border'],
            yscrollcommand=sb.set,
        )
        sb.configure(command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for entry in self.saved_scan_addresses:
            tag = '[PTR]' if entry.get('type') == 'pointer' else '[ADDR]'
            label = f'{tag}  {entry["name"]}'
            if entry.get('description'):
                label += f'  —  {entry["description"]}'
            lb.insert(tk.END, label)

        btn_row = tk.Frame(dlg, bg=self._colors['bg'])
        btn_row.pack(fill=tk.X, padx=12, pady=(8, 12))

        def _confirm():
            sel = lb.curselection()
            if not sel:
                return
            # Store the entry name so scan can look up the full definition
            addr_var.set(self.saved_scan_addresses[sel[0]]['name'])
            dlg.destroy()

        self._make_button(btn_row, text='Select', width=12, command=_confirm, accent=True).pack(side=tk.LEFT, padx=(0, 8))
        self._make_button(btn_row, text='Cancel', width=10, command=dlg.destroy).pack(side=tk.LEFT)

        lb.bind('<Double-Button-1>', lambda _e: _confirm())
        dlg.bind('<Return>', lambda _e: _confirm())
        dlg.bind('<Escape>', lambda _e: dlg.destroy())
        self.root.wait_window(dlg)

    def _open_scan_address_manager(self) -> None:
        """Dialog to add / edit / delete saved scan addresses (static or pointer)."""
        dlg = tk.Toplevel(self.root)
        dlg.title('Manage Scan Addresses')
        self._position_popup_at_main_window(dlg, '620x520')
        dlg.minsize(520, 440)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(self.root)
        dlg.grab_set()

        # ── List ─────────────────────────────────────────────────────────────
        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 6))

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        lb = tk.Listbox(
            list_frame, bg=self._colors['input_bg'], fg=self._colors['text'],
            selectbackground=self._colors['accent'], selectforeground='#ffffff',
            activestyle='none', relief=tk.FLAT,
            highlightthickness=1, highlightbackground=self._colors['border'],
            yscrollcommand=sb.set,
        )
        sb.configure(command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _refresh():
            lb.delete(0, tk.END)
            for e in self.saved_scan_addresses:
                tag = '[PTR]' if e.get('type') == 'pointer' else '[ADDR]'
                lb.insert(tk.END, f'{tag}  {e["name"]}')

        _refresh()

        # ── Type selector ─────────────────────────────────────────────────────
        type_frame = tk.Frame(dlg, bg=self._colors['bg'])
        type_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        tk.Label(type_frame, text='Type:', bg=self._colors['bg'],
                 fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
        type_var = tk.StringVar(value='pointer')
        for val, lbl in [('static', 'Static address'), ('pointer', 'Pointer chain')]:
            tk.Radiobutton(
                type_frame, text=lbl, variable=type_var, value=val,
                bg=self._colors['bg'], fg=self._colors['text'],
                selectcolor=self._colors['input_bg'], activebackground=self._colors['bg'],
                font=('Segoe UI', 9),
                command=lambda: _toggle_fields(),
            ).pack(side=tk.LEFT, padx=(8, 0))

        # ── Edit fields ───────────────────────────────────────────────────────
        fields_frame = tk.Frame(dlg, bg=self._colors['bg'])
        fields_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        def _lbl(parent, text, w=10):
            return tk.Label(parent, text=text, bg=self._colors['bg'],
                            fg=self._colors['muted'], font=('Segoe UI', 9), width=w, anchor=tk.E)

        def _ent(parent, var, w=28, mono=False):
            kw = dict(
                textvariable=var, width=w,
                bg=self._colors['input_bg'], fg=self._colors['text'],
                insertbackground=self._colors['text'], relief=tk.FLAT,
                highlightthickness=1, highlightbackground=self._colors['border'],
                highlightcolor=self._colors['accent'],
            )
            if mono:
                kw['font'] = ('Consolas', 9)
            return tk.Entry(parent, **kw)

        name_var = tk.StringVar()
        desc_var = tk.StringVar()
        # Static fields
        static_addr_var = tk.StringVar()
        # Pointer fields
        module_var = tk.StringVar()
        base_offset_var = tk.StringVar()
        offsets_var = tk.StringVar()   # comma-separated

        def _row(parent):
            f = tk.Frame(parent, bg=self._colors['bg'])
            f.pack(fill=tk.X, pady=2)
            return f

        r_name = _row(fields_frame)
        _lbl(r_name, 'Name:').pack(side=tk.LEFT)
        _ent(r_name, name_var).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_desc = _row(fields_frame)
        _lbl(r_desc, 'Description:').pack(side=tk.LEFT)
        _ent(r_desc, desc_var).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        # Static-only row
        r_addr = _row(fields_frame)
        _lbl(r_addr, 'Address (hex):').pack(side=tk.LEFT)
        _ent(r_addr, static_addr_var, mono=True).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        # Pointer-only rows
        r_mod = _row(fields_frame)
        _lbl(r_mod, 'Module:').pack(side=tk.LEFT)
        _ent(r_mod, module_var).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_base = _row(fields_frame)
        _lbl(r_base, 'Base offset:').pack(side=tk.LEFT)
        _ent(r_base, base_offset_var, w=12, mono=True).pack(side=tk.LEFT, padx=(6, 0))

        r_off = _row(fields_frame)
        _lbl(r_off, 'Offsets:').pack(side=tk.LEFT)
        _ent(r_off, offsets_var, mono=True).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        tk.Label(r_off, text='(hex, comma-separated)', bg=self._colors['bg'],
                 fg=self._colors['muted'], font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=(6, 0))

        def _toggle_fields():
            is_ptr = type_var.get() == 'pointer'
            for w in r_addr.winfo_children():
                w.pack_forget() if is_ptr else None
            r_addr.pack_forget() if is_ptr else r_addr.pack(fill=tk.X, pady=2, after=r_desc)
            for r in (r_mod, r_base, r_off):
                r.pack_forget() if not is_ptr else None
                if is_ptr:
                    r.pack(fill=tk.X, pady=2)

        _toggle_fields()

        def _on_select(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            e = self.saved_scan_addresses[sel[0]]
            name_var.set(e['name'])
            desc_var.set(e.get('description', ''))
            if e.get('type') == 'pointer':
                type_var.set('pointer')
                module_var.set(e.get('module', ''))
                base_offset_var.set(e.get('base_offset', '0x0'))
                offsets_var.set(', '.join(e.get('offsets', [])))
                static_addr_var.set('')
            else:
                type_var.set('static')
                static_addr_var.set(e.get('address', ''))
                module_var.set('')
                base_offset_var.set('')
                offsets_var.set('')
            _toggle_fields()

        lb.bind('<<ListboxSelect>>', _on_select)

        def _build_entry() -> dict | None:
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning('Missing name', 'Name is required.', parent=dlg)
                return None
            if type_var.get() == 'pointer':
                module = module_var.get().strip()
                base_off = base_offset_var.get().strip() or '0x0'
                raw = [o.strip() for o in offsets_var.get().split(',') if o.strip()]
                if not module or not raw:
                    messagebox.showwarning('Missing fields', 'Module and Offsets are required for pointer type.', parent=dlg)
                    return None
                return {'name': name, 'type': 'pointer', 'module': module,
                        'base_offset': base_off, 'offsets': raw,
                        'description': desc_var.get().strip()}
            else:
                addr = static_addr_var.get().strip()
                if not addr:
                    messagebox.showwarning('Missing address', 'Address is required.', parent=dlg)
                    return None
                return {'name': name, 'type': 'static', 'address': addr,
                        'description': desc_var.get().strip()}

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = tk.Frame(dlg, bg=self._colors['bg'])
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))

        def _add():
            entry = _build_entry()
            if entry is None:
                return
            self.saved_scan_addresses.append(entry)
            if self._save_scan_addresses():
                _refresh()
                self._log(f'Address saved: {entry["name"]}')

        def _update():
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning('Nothing selected', 'Select an entry to update.', parent=dlg)
                return
            entry = _build_entry()
            if entry is None:
                return
            self.saved_scan_addresses[sel[0]] = entry
            if self._save_scan_addresses():
                _refresh()
                self._log(f'Address updated: {entry["name"]}')

        def _delete():
            sel = lb.curselection()
            if not sel:
                return
            entry = self.saved_scan_addresses[sel[0]]
            if not messagebox.askyesno('Delete', f'Delete "{entry["name"]}"?', parent=dlg):
                return
            del self.saved_scan_addresses[sel[0]]
            if self._save_scan_addresses():
                name_var.set('')
                desc_var.set('')
                static_addr_var.set('')
                module_var.set('')
                base_offset_var.set('')
                offsets_var.set('')
                _refresh()
                self._log(f'Address deleted: {entry["name"]}')

        self._make_button(btn_row, text='Add', width=9, command=_add, accent=True).grid(row=0, column=0, sticky='ew', padx=(0, 6))
        self._make_button(btn_row, text='Update', width=9, command=_update).grid(row=0, column=1, sticky='ew', padx=(0, 6))
        self._make_button(btn_row, text='Delete', width=9, command=_delete, danger=True).grid(row=0, column=2, sticky='ew', padx=(0, 6))
        self._make_button(btn_row, text='Close', width=9, command=dlg.destroy).grid(row=0, column=3, sticky='ew')
        for c in range(4):
            btn_row.grid_columnconfigure(c, weight=1)

        self.root.wait_window(dlg)

    def _on_process_tower_apply_count(self) -> None:
        try:
            count = int(self._process_tower_count_var.get())
            if count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                'Invalid input',
                'Please enter a positive integer for the number of characters.',
                parent=self.root,
            )
            return
        self._rebuild_process_tower_rows(count)

    def _on_process_tower_attach_process(self, idx: int) -> None:
        chosen = self._pick_running_process()
        if chosen is None:
            return
        pid, exe_path, display_name = chosen
        row = self._process_tower_rows[idx]

        # Close previous handle for this slot if any
        prev_handle = row.get('handle')
        if prev_handle:
            self._close_process_handle(prev_handle)
            if prev_handle in self._process_handles:
                self._process_handles.remove(prev_handle)

        handle = self._open_process_for_reading(pid)
        row['pid'] = pid
        row['process_path'] = exe_path
        row['handle'] = handle

        if handle:
            self._process_handles.append(handle)
            row['status_var'].set(f'Attached  •  PID {pid}')
            row['btn'].configure(fg='#26a269')
            self._log(f'Character #{idx + 1}: attached to "{display_name}" (PID {pid}).')
        else:
            row['status_var'].set('Attach failed')
            row['btn'].configure(fg=self._colors['danger'])
            self._log(f'Character #{idx + 1}: failed to attach to "{display_name}" (PID {pid}).')
            messagebox.showerror(
                'Attach failed',
                f'Could not open process (PID {pid}) for reading.\nTry running as Administrator.',
                parent=self.root,
            )

    @staticmethod
    def _open_process_for_reading(pid: int) -> int | None:
        return open_process_for_reading(pid)

    @staticmethod
    def _close_process_handle(handle: int) -> None:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(handle)

    # ── Process Tower: key capture ────────────────────────────────────────────

    def _on_process_tower_set_key(self, idx: int) -> None:
        key = self._capture_hotkey_for_scan(self.root)
        if key:
            self._process_tower_rows[idx]['key_var'].set(key)

    def _capture_hotkey_for_scan(self, parent: tk.Misc) -> str | None:
        """Show a simple key capture dialog. Returns combo like 'alt+2' or None."""
        result: dict = {'key': None}

        dlg = tk.Toplevel(parent)
        dlg.title('Set Trigger Key')
        self._position_popup_at_main_window(dlg, '380x200')
        dlg.resizable(False, False)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(parent)
        dlg.grab_set()

        modifier_map = {
            'Control_L': 'ctrl', 'Control_R': 'ctrl',
            'Alt_L': 'alt', 'Alt_R': 'alt',
            'Shift_L': 'shift', 'Shift_R': 'shift',
            'Win_L': 'win', 'Win_R': 'win',
        }
        key_aliases = {
            'Return': 'enter',
            'Escape': 'esc',
            'BackSpace': 'backspace',
            'Tab': 'tab',
            'space': 'space',
            'Delete': 'delete',
            'Insert': 'insert',
            'Home': 'home',
            'End': 'end',
            'Prior': 'pageup',
            'Next': 'pagedown',
            'Up': 'up',
            'Down': 'down',
            'Left': 'left',
            'Right': 'right',
            'KP_Add': 'kp_add',
            'KP_Subtract': 'kp_subtract',
            'KP_Multiply': 'kp_multiply',
            'KP_Divide': 'kp_divide',
            'KP_Decimal': 'kp_decimal',
        }
        modifiers: set[str] = set()
        main_key: list[str] = []

        combo_var = tk.StringVar(value='<none>')
        hint_var = tk.StringVar(value='Hold modifiers and press your key.')

        def _format() -> str:
            parts = [m for m in ['ctrl', 'alt', 'shift', 'win'] if m in modifiers]
            if main_key:
                parts.append(main_key[0])
            return '+'.join(parts) if parts else '<none>'

        def _refresh():
            combo_var.set(_format())

        def _on_key(event: tk.Event):
            ks = str(event.keysym)
            if ks in modifier_map:
                modifiers.add(modifier_map[ks])
            else:
                mapped = key_aliases.get(ks, ks.lower())
                if mapped.startswith('kp_') and len(mapped) == 4 and mapped[-1].isdigit():
                    mapped = f'num{mapped[-1]}'
                if mapped not in ('??', ''):
                    main_key.clear()
                    main_key.append(mapped)
            _refresh()
            hint_var.set('Combo captured. Click OK to confirm.')
            return 'break'

        def _clear():
            modifiers.clear()
            main_key.clear()
            _refresh()
            hint_var.set('Cleared. Press your combination.')

        def _confirm():
            combo = _format()
            if combo == '<none>':
                messagebox.showwarning('No key', 'Press at least one key first.', parent=dlg)
                return
            result['key'] = combo
            dlg.destroy()

        tk.Label(
            dlg, text='Press your desired key combination, then click OK.',
            font=('Segoe UI', 10), bg=self._colors['bg'], fg=self._colors['text'],
            wraplength=340, justify='left',
        ).pack(fill=tk.X, padx=16, pady=(16, 6))

        tk.Label(
            dlg, textvariable=combo_var,
            font=('Consolas', 14), bg=self._colors['bg'], fg=self._colors['accent'],
        ).pack(pady=(0, 4))

        tk.Label(
            dlg, textvariable=hint_var,
            font=('Segoe UI', 9), bg=self._colors['bg'], fg=self._colors['muted'],
        ).pack(pady=(0, 10))

        btn_row = tk.Frame(dlg, bg=self._colors['bg'])
        btn_row.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._make_button(btn_row, text='Clear', width=8, command=_clear).grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self._make_button(btn_row, text='OK', width=8, command=_confirm, accent=True).grid(row=0, column=1, sticky='ew', padx=(0, 8))
        self._make_button(btn_row, text='Cancel', width=8, command=dlg.destroy).grid(row=0, column=2, sticky='ew')
        for c in range(3):
            btn_row.grid_columnconfigure(c, weight=1)

        dlg.bind('<KeyPress>', _on_key)
        dlg.focus_force()
        self.root.wait_window(dlg)
        return result['key']

    # ── Process Tower: scanning ───────────────────────────────────────────────

    def _find_scan_address_entry(self, name: str) -> dict | None:
        return find_scan_address_entry(self, name)

    def _on_process_tower_toggle_scan(self, idx: int) -> None:
        on_process_tower_toggle_scan(self, idx)

    def _start_process_tower_scan(self, idx: int) -> None:
        start_process_tower_scan(self, idx)

    def _stop_process_tower_scan(self, idx: int) -> None:
        stop_process_tower_scan(self, idx)

    def _reset_process_tower_scan_row(self, idx: int, status_text: str | None = None) -> None:
        reset_process_tower_scan_row(self, idx, status_text)

    def _scan_loop(
        self,
        idx: int,
        handle: int,
        pid: int | None,
        entry: dict,
        threshold: int,
        key_combo: str,
        stop_event: threading.Event,
        slayer_label: str = '',
        is_slayer: bool = False,
        map_entry: dict | None = None,
    ) -> None:
        scan_loop(self, idx, handle, pid, entry, threshold, key_combo, stop_event, slayer_label, is_slayer, map_entry)

    @staticmethod
    def _find_process_windows(pid: int) -> list[int]:
        """Return candidate top-level and child window handles for a PID."""
        win32gui = importlib.import_module('win32gui')
        win32process = importlib.import_module('win32process')

        target_hwnds: list[int] = []

        def _enum_cb(hwnd, _lparam):
            nonlocal target_hwnds
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid != pid:
                return True
            target_hwnds.append(hwnd)

            def _enum_child_cb(child_hwnd, _child_lparam):
                _, child_pid = win32process.GetWindowThreadProcessId(child_hwnd)
                if child_pid == pid:
                    target_hwnds.append(child_hwnd)
                return True

            try:
                win32gui.EnumChildWindows(hwnd, _enum_child_cb, None)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_cb, None)
        # Preserve order but remove duplicates.
        deduped: list[int] = []
        seen: set[int] = set()
        for hwnd in target_hwnds:
            if hwnd not in seen:
                deduped.append(hwnd)
                seen.add(hwnd)
        return deduped

    @staticmethod
    def _find_primary_process_window(pid: int) -> int | None:
        """Return the first visible top-level window for a PID, or a fallback HWND."""
        win32gui = importlib.import_module('win32gui')
        win32process = importlib.import_module('win32process')

        fallback_hwnd = None

        def _enum_cb(hwnd, _lparam):
            nonlocal fallback_hwnd
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid != pid:
                return True
            if fallback_hwnd is None:
                fallback_hwnd = hwnd
            if win32gui.GetParent(hwnd) == 0 and win32gui.IsWindowVisible(hwnd):
                fallback_hwnd = hwnd
                return False
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass
        return fallback_hwnd

    @staticmethod
    def _focus_process_window(pid: int) -> bool:
        """Bring the first window for the PID to the foreground if possible."""
        win32gui = importlib.import_module('win32gui')
        win32con = importlib.import_module('win32con')
        win32process = importlib.import_module('win32process')
        win32api = importlib.import_module('win32api')

        primary_hwnd = None
        fallback_hwnd = None

        for hwnd in MonitorUI._find_process_windows(pid):
            try:
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                if window_pid != pid:
                    continue
                if fallback_hwnd is None:
                    fallback_hwnd = hwnd
                if win32gui.GetParent(hwnd) == 0:
                    primary_hwnd = hwnd
                    break
            except Exception:
                continue

        hwnd = primary_hwnd or fallback_hwnd
        if not hwnd:
            return False

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            if not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            current_thread = win32api.GetCurrentThreadId()
            attached = False
            try:
                if target_thread and current_thread and target_thread != current_thread:
                    attached = bool(win32api.AttachThreadInput(current_thread, target_thread, True))
            except Exception:
                attached = False

            try:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.SetActiveWindow(hwnd)
                win32gui.SetFocus(hwnd)
            finally:
                if attached:
                    try:
                        win32api.AttachThreadInput(current_thread, target_thread, False)
                    except Exception:
                        pass
        except Exception:
            return False

        return True

    @staticmethod
    def _prepare_process_window_for_input(pid: int) -> bool:
        """Attempt to give the process keyboard focus without forcing it to the foreground."""
        win32gui = importlib.import_module('win32gui')
        win32process = importlib.import_module('win32process')
        win32api = importlib.import_module('win32api')

        hwnds = MonitorUI._find_process_windows(pid)
        if not hwnds:
            return False

        hwnd = hwnds[0]
        try:
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            current_thread = win32api.GetCurrentThreadId()
            attached = False
            try:
                if target_thread and current_thread and target_thread != current_thread:
                    attached = bool(win32api.AttachThreadInput(current_thread, target_thread, True))
                win32gui.SetFocus(hwnd)
            finally:
                if attached:
                    try:
                        win32api.AttachThreadInput(current_thread, target_thread, False)
                    except Exception:
                        pass
        except Exception:
            return False

        return True

    @staticmethod
    def _vk_for_token(token: str) -> int | None:
        """Map hotkey token to VK code."""
        t = token.strip().lower()
        alias_map = {
            'control': 'ctrl',
            'return': 'enter',
            'escape': 'esc',
            'spacebar': 'space',
            'prior': 'pageup',
            'next': 'pagedown',
            'pgup': 'pageup',
            'pgdn': 'pagedown',
            'ins': 'insert',
            'del': 'delete',
            'bksp': 'backspace',
        }
        t = alias_map.get(t, t)

        if t.startswith('kp_') and len(t) == 4 and t[-1].isdigit():
            t = f'num{t[-1]}'
        if t.startswith('numpad') and len(t) == 7 and t[-1].isdigit():
            t = f'num{t[-1]}'

        vk_map = {
            'ctrl': 0x11, 'control': 0x11,
            'alt': 0x12,
            'shift': 0x10,
            'win': 0x5B,
            'enter': 0x0D, 'return': 0x0D,
            'esc': 0x1B, 'escape': 0x1B,
            'tab': 0x09,
            'space': 0x20,
            'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
            'pageup': 0x21, 'pagedown': 0x22,
            'home': 0x24, 'end': 0x23,
            'insert': 0x2D, 'delete': 0x2E,
            'backspace': 0x08,
            'num0': 0x60, 'num1': 0x61, 'num2': 0x62, 'num3': 0x63, 'num4': 0x64,
            'num5': 0x65, 'num6': 0x66, 'num7': 0x67, 'num8': 0x68, 'num9': 0x69,
            'kp_add': 0x6B, 'kp_subtract': 0x6D, 'kp_multiply': 0x6A, 'kp_divide': 0x6F, 'kp_decimal': 0x6E,
            'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
            'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
            'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
        }
        if t in vk_map:
            return vk_map[t]
        if len(t) == 1:
            ch = t.upper()
            if 'A' <= ch <= 'Z' or '0' <= ch <= '9':
                return ord(ch)
        return None

    def _send_key_combo_to_pid(self, pid: int, key_combo: str) -> bool:
        """Send combo to process window via PostMessage using pywin32."""
        win32gui = importlib.import_module('win32gui')
        win32con = importlib.import_module('win32con')

        try:
            self._focus_process_window(pid)
        except Exception:
            pass
        try:
            self._prepare_process_window_for_input(pid)
        except Exception:
            pass

        hwnds = self._find_process_windows(pid)
        if not hwnds:
            return False

        primary_hwnd = self._find_primary_process_window(pid)
        if primary_hwnd in hwnds:
            hwnds = [primary_hwnd] + [h for h in hwnds if h != primary_hwnd]

        parts = [p.strip().lower() for p in key_combo.split('+') if p.strip()]
        if not parts:
            return False

        mod_tokens = []
        main_tokens = []
        for p in parts:
            pl = p.lower()
            if pl in {'ctrl', 'control', 'alt', 'shift', 'win'}:
                mod_tokens.append(pl)
            else:
                main_tokens.append(pl)

        if not main_tokens:
            main_tokens = [mod_tokens[-1]] if mod_tokens else []
            mod_tokens = mod_tokens[:-1]
        if not main_tokens:
            return False

        dedup_mod_tokens = []
        for token in mod_tokens:
            if token not in dedup_mod_tokens:
                dedup_mod_tokens.append(token)
        mod_tokens = dedup_mod_tokens

        mod_vks = [self._vk_for_token(m) for m in mod_tokens]
        if any(v is None for v in mod_vks):
            return False
        main_vk = self._vk_for_token(main_tokens[-1])
        if main_vk is None:
            return False

        # Use the exact Alt+number sequence when possible; it is the most stable
        # for the escape-route hotkey pattern currently used in the UI.
        if len(mod_tokens) == 1 and mod_tokens[0] == 'alt' and len(main_tokens) == 1:
            digit = main_tokens[0]
            if len(digit) == 1 and digit.isdigit():
                return self._send_alt_number_sequence(hwnds, digit)

        has_alt = any(m in {'alt'} for m in mod_tokens)
        down_msg = win32con.WM_SYSKEYDOWN if has_alt else win32con.WM_KEYDOWN
        up_msg = win32con.WM_SYSKEYUP if has_alt else win32con.WM_KEYUP

        lparam_down = 0x00000001
        lparam_up = 0xC0000001

        def _send_message(hwnd: int, msg: int, vk: int, lparam: int, use_timeout: bool) -> None:
            if use_timeout:
                win32gui.SendMessageTimeout(
                    hwnd,
                    msg,
                    vk,
                    lparam,
                    win32con.SMTO_ABORTIFHUNG,
                    60,
                )
            else:
                win32gui.PostMessage(hwnd, msg, vk, lparam)

        for use_timeout in (False, True):
            sent_any = False
            for hwnd in hwnds:
                try:
                    _send_message(hwnd, win32con.WM_SYSKEYUP, win32con.VK_MENU, lparam_up, use_timeout)

                    for vk in mod_vks:
                        _send_message(hwnd, down_msg, vk, lparam_down, use_timeout)

                    _send_message(hwnd, down_msg, main_vk, lparam_down, use_timeout)
                    _send_message(hwnd, up_msg, main_vk, lparam_up, use_timeout)

                    for vk in reversed(mod_vks):
                        _send_message(hwnd, up_msg, vk, lparam_up, use_timeout)

                    if has_alt:
                        _send_message(hwnd, win32con.WM_SYSKEYUP, win32con.VK_MENU, lparam_up, use_timeout)

                    sent_any = True
                    break
                except Exception:
                    continue

            if sent_any:
                return True
            time.sleep(0.02)

        return False

    def _send_tab_key_to_pid(self, pid: int) -> bool:
        """Bring target to foreground and send a single TAB key press."""
        win32gui = importlib.import_module('win32gui')
        win32con = importlib.import_module('win32con')

        hwnd = self._find_primary_process_window(pid)
        if not hwnd:
            return False

        tab_vk = win32con.VK_TAB
        lparam_down = 0x00000001
        lparam_up = 0xC0000001

        for use_timeout in (False, True):
            try:
                try:
                    self._focus_process_window(pid)
                except Exception:
                    pass

                if use_timeout:
                    win32gui.SendMessageTimeout(
                        hwnd,
                        win32con.WM_KEYDOWN,
                        tab_vk,
                        lparam_down,
                        win32con.SMTO_ABORTIFHUNG,
                        60,
                    )
                    win32gui.SendMessageTimeout(
                        hwnd,
                        win32con.WM_KEYUP,
                        tab_vk,
                        lparam_up,
                        win32con.SMTO_ABORTIFHUNG,
                        60,
                    )
                else:
                    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, tab_vk, lparam_down)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, tab_vk, lparam_up)
                return True
            except Exception:
                time.sleep(0.02)
                continue
        return False

    def _close_process_app(self, pid: int) -> bool:
        """Request graceful close for process windows belonging to PID."""
        win32gui = importlib.import_module('win32gui')
        win32con = importlib.import_module('win32con')

        hwnds = self._find_process_windows(pid)
        if not hwnds:
            return False

        sent_any = False
        for hwnd in hwnds:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                sent_any = True
            except Exception:
                continue

        if not sent_any:
            return False

        deadline = time.monotonic() + 1.6
        while time.monotonic() < deadline:
            if not self._find_process_windows(pid):
                return True
            time.sleep(0.08)

        return True

    @staticmethod
    def _send_alt_number_sequence(hwnds: list[int], num: str) -> bool:
        """Send Alt+number using the posted message sequence from the user's snippet."""
        try:
            win32gui = importlib.import_module('win32gui')
            win32con = importlib.import_module('win32con')
            vk = 0x30 + int(num)

            lparam_down = 0x00000001
            lparam_up = 0xC0000001

            def _send_message(hwnd: int, msg: int, key: int, lparam: int, use_timeout: bool) -> None:
                if use_timeout:
                    win32gui.SendMessageTimeout(
                        hwnd,
                        msg,
                        key,
                        lparam,
                        win32con.SMTO_ABORTIFHUNG,
                        60,
                    )
                else:
                    win32gui.PostMessage(hwnd, msg, key, lparam)

            for use_timeout in (False, True):
                sent_any = False
                for hwnd in hwnds:
                    try:
                        _send_message(hwnd, win32con.WM_SYSKEYUP, win32con.VK_MENU, lparam_up, use_timeout)
                        _send_message(hwnd, win32con.WM_SYSKEYDOWN, win32con.VK_MENU, lparam_down, use_timeout)
                        _send_message(hwnd, win32con.WM_SYSKEYDOWN, vk, lparam_down, use_timeout)
                        _send_message(hwnd, win32con.WM_SYSKEYUP, vk, lparam_up, use_timeout)
                        _send_message(hwnd, win32con.WM_SYSKEYUP, win32con.VK_MENU, lparam_up, use_timeout)
                        _send_message(hwnd, win32con.WM_SYSKEYUP, win32con.VK_MENU, lparam_up, use_timeout)
                        sent_any = True
                        break
                    except Exception:
                        continue

                if sent_any:
                    return True
                time.sleep(0.02)

            return False
        except Exception:
            return False

    @staticmethod
    def _read_int_from_process(handle: int, address: int) -> int | None:
        return read_int_from_process(handle, address)

    @staticmethod
    def _read_uint_from_process(handle: int, address: int) -> int | None:
        return read_uint_from_process(handle, address)

    @staticmethod
    def _read_ushort_from_process(handle: int, address: int) -> int | None:
        return read_ushort_from_process(handle, address)

    @staticmethod
    def _read_ubyte_from_process(handle: int, address: int) -> int | None:
        return read_ubyte_from_process(handle, address)

    def _read_numeric_from_process(self, handle: int, address: int) -> int | None:
        return read_numeric_from_process(handle, address)

    @staticmethod
    def _read_ptr_from_process(handle: int, address: int) -> int | None:
        return read_ptr_from_process(handle, address)

    @staticmethod
    def _get_module_base(handle: int, module_name: str) -> int | None:
        return get_module_base(handle, module_name)

    def _read_value_pointer(
        self,
        handle: int,
        pid: int | None,
        module_name: str,
        base_offset_hex: str,
        offsets_hex: list[str],
    ) -> int | None:
        return read_value_pointer(handle, module_name, base_offset_hex, offsets_hex)

    def _pick_running_process(self) -> tuple[int, str | None, str] | None:
        """Open a dialog listing running processes; returns (pid, exe_path_or_None, display_name) or None if cancelled."""
        try:
            import psutil
        except ImportError:
            messagebox.showerror(
                'Missing dependency',
                'psutil is required.\nRun: pip install psutil',
                parent=self.root,
            )
            return None

        def _get_window_titles_by_pid() -> dict[int, list[str]]:
            import ctypes
            import ctypes.wintypes

            pid_to_titles: dict[int, list[str]] = {}
            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowTextW = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

            def _enum_cb(hwnd, _lparam):
                if not IsWindowVisible(hwnd):
                    return True
                length = GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if not title:
                    return True
                pid = ctypes.wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pid_to_titles.setdefault(pid.value, []).append(title)
                return True

            EnumWindows(WNDENUMPROC(_enum_cb), 0)
            return pid_to_titles

        def _collect():
            pid_titles = _get_window_titles_by_pid()
            # entries: (process_name, pid, exe_path, window_titles)
            entries: list[tuple[str, int, str | None, list[str]]] = []
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    info = proc.info
                    process_name = info['name'] or ''
                    if 'megamu' not in process_name.lower():
                        continue
                    pid = info['pid']
                    entries.append((
                        process_name,
                        pid,
                        info.get('exe'),
                        pid_titles.get(pid, []),
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return sorted(entries, key=lambda e: e[0].lower())

        all_procs = _collect()

        dlg = tk.Toplevel(self.root)
        dlg.title('Select Running Process')
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        dlg.transient(self.root)
        dlg.grab_set()
        self._apply_app_icon(dlg)
        self._position_popup_at_main_window(dlg, '540x460')
        dlg.minsize(420, 360)

        result: dict = {'value': None}

        # Search row
        search_frame = tk.Frame(dlg, bg=self._colors['bg'], padx=10, pady=(10))
        search_frame.pack(fill=tk.X)
        tk.Label(
            search_frame,
            text='Filter:',
            bg=self._colors['bg'],
            fg=self._colors['muted'],
        ).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        tk.Entry(
            search_frame,
            textvariable=search_var,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            insertbackground=self._colors['text'],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        # Listbox
        list_frame = tk.Frame(dlg, bg=self._colors['bg'], padx=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        listbox = tk.Listbox(
            list_frame,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            activestyle='none',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        visible_procs: list[tuple[str, int, str | None, list[str]]] = []

        def _refresh(filter_text: str = '') -> None:
            nonlocal visible_procs
            ft = filter_text.strip().lower()
            if ft:
                visible_procs = [
                    p for p in all_procs
                    if ft in p[0].lower() or any(ft in t.lower() for t in p[3])
                ]
            else:
                visible_procs = list(all_procs)
            listbox.delete(0, tk.END)
            for name, pid, _exe, titles in visible_procs:
                if titles:
                    label = f'{name}  —  {titles[0]}  (PID {pid})'
                else:
                    label = f'{name}  (PID {pid})'
                listbox.insert(tk.END, label)

        _refresh()
        search_var.trace_add('write', lambda *_: _refresh(search_var.get()))

        # Buttons
        btn_frame = tk.Frame(dlg, bg=self._colors['bg'], padx=10, pady=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        def _confirm() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            name, pid, exe, titles = visible_procs[sel[0]]
            display = titles[0] if titles else name
            result['value'] = (pid, exe, display)
            dlg.destroy()

        def _cancel() -> None:
            dlg.destroy()

        btn_select = self._make_button(btn_frame, text='Select', width=12, command=_confirm, accent=True)
        btn_cancel_pick = self._make_button(btn_frame, text='Cancel', width=10, command=_cancel)
        btn_select.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        btn_cancel_pick.grid(row=0, column=1, sticky='ew')
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        listbox.bind('<Double-Button-1>', lambda _e: _confirm())
        dlg.bind('<Return>', lambda _e: _confirm())
        dlg.bind('<Escape>', lambda _e: _cancel())

        self.root.wait_window(dlg)
        return result['value']

    def _on_mode_changed(self, _event=None):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before changing mode.', parent=self.root)
            self._mode_var.set(self._last_mode_selection)
            return
        self._last_mode_selection = self._mode_var.get()
        self._detected_waiting_stop = False
        self._set_state_idle('Mode changed')
        self._refresh_mode_ui()

    def _on_select_area(self):
        if self._stop_requested:
            messagebox.showinfo('Stopping scanner', 'Scanner is stopping. Please wait a moment.', parent=self.root)
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before selecting a new area.', parent=self.root)
            return

        selection = select_area_with_parent(
            self.root,
            help_text='Select monitor area | Enter confirm | Esc cancel',
        )
        if selection is None:
            self._log('Area selection cancelled.')
            return

        self.region = selection
        x1, y1, x2, y2 = selection
        self.lbl_region.configure(text=f'Region: ({x1}, {y1}) {x2 - x1}x{y2 - y1}')
        self._log('Area selected.')

    def _on_select_route(self):
        if self._selected_mode() != 'spot-tower':
            return
        if self._stop_requested:
            messagebox.showinfo('Stopping scanner', 'Scanner is stopping. Please wait a moment.', parent=self.root)
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before editing escape route.', parent=self.root)
            return

        if not self.saved_escape_routes:
            self._open_escape_route_editor(route_name=None, initial_route=[])
            return

        self._open_escape_route_picker()

    def _open_escape_route_picker(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title('Select Escape Route')
        self._position_popup_at_main_window(dlg, '500x360')
        dlg.minsize(460, 340)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text='Select an existing escape route or create a new one.',
            font=('Segoe UI', 10),
            anchor='w',
            bg=self._colors['bg'],
            fg=self._colors['text'],
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        route_list = tk.Listbox(
            list_frame,
            activestyle='dotbox',
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        route_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=route_list.yview,
            bg=self._colors['panel_alt'],
            troughcolor=self._colors['input_bg'],
            activebackground=self._colors['accent'],
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        route_list.configure(yscrollcommand=scrollbar.set)

        route_names: list[str] = []

        def _refresh_route_list() -> None:
            nonlocal route_names
            route_names = sorted(self.saved_escape_routes.keys(), key=str.lower)
            route_list.delete(0, tk.END)
            for name in route_names:
                route_list.insert(tk.END, name)

            if not route_names:
                dlg.destroy()
                self._open_escape_route_editor(route_name=None, initial_route=[])
                return

            preferred = self.escape_route_name if self.escape_route_name in route_names else route_names[0]
            select_index = route_names.index(preferred)
            route_list.selection_set(select_index)
            route_list.activate(select_index)

        def _selected_route_name() -> str | None:
            selected = route_list.curselection()
            if not selected:
                return None
            idx = int(selected[0])
            if idx < 0 or idx >= len(route_names):
                return None
            return route_names[idx]

        def _use_selected() -> None:
            selected_name = _selected_route_name()
            if not selected_name:
                return
            route = self.saved_escape_routes.get(selected_name, [])
            self._apply_escape_route(selected_name, route)
            dlg.destroy()

        def _edit_selected() -> None:
            selected_name = _selected_route_name()
            if not selected_name:
                return
            route = self.saved_escape_routes.get(selected_name, [])
            dlg.destroy()
            self._open_escape_route_editor(route_name=selected_name, initial_route=route)

        def _create_new() -> None:
            dlg.destroy()
            self._open_escape_route_editor(route_name=None, initial_route=[])

        def _delete_selected() -> None:
            selected_name = _selected_route_name()
            if not selected_name:
                return
            if not messagebox.askyesno(
                'Delete route',
                f'Delete escape route "{selected_name}"?',
                parent=dlg,
            ):
                return
            self.saved_escape_routes.pop(selected_name, None)
            if self.escape_route_name == selected_name:
                self.escape_route_name = None
                self.escape_route = []
                self._update_route_label()
            if not self._persist_saved_escape_routes():
                return
            self._log(f'Escape route deleted: {selected_name}.')
            _refresh_route_list()

        actions = tk.Frame(dlg, bg=self._colors['bg'])
        actions.pack(fill=tk.X, padx=12, pady=(10, 12))

        btn_use = self._make_button(actions, text='Use Selected', width=12, command=_use_selected, accent=True)
        btn_edit = self._make_button(actions, text='Edit', width=10, command=_edit_selected)
        btn_new = self._make_button(actions, text='New', width=10, command=_create_new)
        btn_delete = self._make_button(actions, text='Delete', width=10, command=_delete_selected, danger=True)
        btn_cancel = self._make_button(actions, text='Cancel', width=10, command=dlg.destroy)

        btn_use.grid(row=0, column=0, padx=(0, 6), sticky='ew')
        btn_edit.grid(row=0, column=1, padx=(0, 6), sticky='ew')
        btn_new.grid(row=0, column=2, padx=(0, 6), sticky='ew')
        btn_delete.grid(row=0, column=3, padx=(0, 6), sticky='ew')
        btn_cancel.grid(row=0, column=4, padx=(0, 0), sticky='ew')

        for col in range(5):
            actions.grid_columnconfigure(col, weight=1)

        route_list.bind('<Double-Button-1>', lambda _event: _use_selected())

        _refresh_route_list()
        self.root.wait_window(dlg)

    def _on_capture_template(self):
        if self._stop_requested:
            messagebox.showinfo('Stopping scanner', 'Scanner is stopping. Please wait a moment.', parent=self.root)
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before capturing a template.', parent=self.root)
            return

        selection, image, offset_x, offset_y = select_area_and_snapshot_with_parent(
            self.root,
            help_text='Drag exact target image | Enter confirm | Esc cancel',
            min_size=4,
        )
        if selection is None:
            self._log('Template capture cancelled.')
            return

        x1, y1, x2, y2 = selection
        crop_x1 = max(0, x1 - offset_x)
        crop_y1 = max(0, y1 - offset_y)
        crop_x2 = max(crop_x1 + 1, x2 - offset_x)
        crop_y2 = max(crop_y1 + 1, y2 - offset_y)
        template = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))

        self.template_path.parent.mkdir(parents=True, exist_ok=True)
        template.save(self.template_path)
        self._log(f'Template saved to {self.template_path.name} ({crop_x2 - crop_x1}x{crop_y2 - crop_y1}).')

    def _open_escape_route_editor(
        self,
        route_name: str | None = None,
        initial_route: list[dict[str, int | str]] | None = None,
    ):
        current_route_name = route_name
        if initial_route is None:
            working_route = [dict(step) for step in self.escape_route]
        else:
            working_route = [dict(step) for step in initial_route]

        dlg = tk.Toplevel(self.root)
        dlg.title('Escape Route Editor')
        self._position_popup_at_main_window(dlg, '600x420')
        dlg.minsize(520, 400)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text='Configure ordered escape actions (clicks, keys, and text).',
            font=('Segoe UI', 10),
            anchor='w',
            bg=self._colors['bg'],
            fg=self._colors['text'],
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        steps_list = tk.Listbox(
            list_frame,
            height=8,
            activestyle='dotbox',
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        steps_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=steps_list.yview,
            bg=self._colors['panel_alt'],
            troughcolor=self._colors['input_bg'],
            activebackground=self._colors['accent'],
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        steps_list.configure(yscrollcommand=scrollbar.set)

        def _describe_step(index: int, step: dict[str, int | str]) -> str:
            step_type = str(step.get('type', '')).lower()
            if step_type == 'click':
                x = int(step.get('x', 0))
                y = int(step.get('y', 0))
                return f'{index}. Click at ({x}, {y})'
            if step_type == 'key':
                key = str(step.get('key', '')).strip() or '<empty>'
                return f'{index}. Press key: {key}'
            if step_type == 'text':
                text = str(step.get('text', ''))
                return f'{index}. Type text: {text if text else "<empty>"}'
            return f'{index}. Unknown step'

        def _refresh_list():
            steps_list.delete(0, tk.END)
            for idx, step in enumerate(working_route, start=1):
                steps_list.insert(tk.END, _describe_step(idx, step))

        def _selected_index() -> int | None:
            selected = steps_list.curselection()
            if not selected:
                return None
            return int(selected[0])

        def _add_click_step() -> None:
            points = select_points_with_parent(
                dlg,
                count=1,
                help_text='Select click point | Enter confirm | Esc cancel',
            )
            if len(points) != 1:
                return
            x, y = points[0]
            working_route.append({'type': 'click', 'x': int(x), 'y': int(y)})
            _refresh_list()
            steps_list.selection_clear(0, tk.END)
            steps_list.selection_set(tk.END)

        def _add_key_step() -> None:
            key_aliases = {
                'Return': 'enter',
                'Escape': 'esc',
                'BackSpace': 'backspace',
                'Tab': 'tab',
                'space': 'space',
                'Delete': 'delete',
                'Insert': 'insert',
                'Home': 'home',
                'End': 'end',
                'Prior': 'pageup',
                'Next': 'pagedown',
                'Up': 'up',
                'Down': 'down',
                'Left': 'left',
                'Right': 'right',
                'Print': 'printscreen',
                'Scroll_Lock': 'scrolllock',
                'Pause': 'pause',
                'Caps_Lock': 'capslock',
                'Num_Lock': 'numlock',
                'Shift_L': 'shift',
                'Shift_R': 'shift',
                'Control_L': 'ctrl',
                'Control_R': 'ctrl',
                'Alt_L': 'alt',
                'Alt_R': 'alt',
                'Win_L': 'win',
                'Win_R': 'win',
                'KP_Add': 'kp_add',
                'KP_Subtract': 'kp_subtract',
                'KP_Multiply': 'kp_multiply',
                'KP_Divide': 'kp_divide',
                'KP_Decimal': 'kp_decimal',
            }

            modifier_order = ['ctrl', 'alt', 'shift', 'win']
            selected_modifiers: set[str] = set()
            selected_key: str | None = None
            captured_key: str | None = None
            capture_dlg = tk.Toplevel(dlg)
            capture_dlg.title('Capture key press')
            self._position_popup_at_main_window(capture_dlg, '440x240')
            capture_dlg.resizable(False, False)
            capture_dlg.minsize(360, 220)
            capture_dlg.configure(bg=self._colors['bg'])
            self._apply_app_icon(capture_dlg)
            capture_dlg.transient(dlg)
            capture_dlg.grab_set()

            tk.Label(
                capture_dlg,
                text='Press your combination, then click OK.',
                font=('Segoe UI', 10),
                bg=self._colors['bg'],
                fg=self._colors['text'],
            ).pack(fill=tk.X, padx=12, pady=(16, 6))

            combo_var = tk.StringVar(value='Current combo: <none>')
            tk.Label(
                capture_dlg,
                textvariable=combo_var,
                font=('Segoe UI', 9),
                bg=self._colors['bg'],
                fg=self._colors['text'],
            ).pack(fill=tk.X, padx=12, pady=(2, 6))

            hint_var = tk.StringVar(value='Tip: Hold Alt/Ctrl/Shift and press a key like 2, then click OK.')
            tk.Label(
                capture_dlg,
                textvariable=hint_var,
                font=('Segoe UI', 9),
                bg=self._colors['bg'],
                fg=self._colors['muted'],
            ).pack(fill=tk.X, padx=12, pady=(0, 10))

            def _format_combo() -> str:
                parts: list[str] = [mod for mod in modifier_order if mod in selected_modifiers]
                if selected_key:
                    parts.append(selected_key)
                if not parts:
                    return '<none>'
                return '+'.join(parts)

            def _refresh_combo_label() -> None:
                combo_var.set(f'Current combo: {_format_combo()}')

            def _map_tk_key(event: tk.Event) -> str:
                keysym = str(getattr(event, 'keysym', '') or '')
                char = str(getattr(event, 'char', '') or '')

                if keysym in key_aliases:
                    return key_aliases[keysym]

                if keysym.startswith('KP_') and len(keysym) == 4 and keysym[-1].isdigit():
                    return f'num{keysym[-1]}'

                if len(char) == 1 and char.isprintable() and char != ' ':
                    return char.lower()

                if keysym.startswith('F') and keysym[1:].isdigit():
                    return keysym.lower()

                lowered = keysym.lower()
                if lowered and lowered != '??':
                    return lowered

                return ''

            def _on_key_press(event: tk.Event) -> None:
                nonlocal selected_key
                mapped = _map_tk_key(event)
                if not mapped:
                    hint_var.set('Unsupported key. Try another key.')
                    return 'break'

                if event.state & 0x0004:
                    selected_modifiers.add('ctrl')
                if event.state & 0x0008:
                    selected_modifiers.add('alt')
                if event.state & 0x0001:
                    selected_modifiers.add('shift')

                if mapped in {'ctrl', 'alt', 'shift', 'win'}:
                    selected_modifiers.add(mapped)
                else:
                    selected_key = mapped

                _refresh_combo_label()
                hint_var.set('Combo captured. Press more keys to adjust, then click OK.')
                return 'break'

            def _clear_combo() -> None:
                nonlocal selected_key
                selected_modifiers.clear()
                selected_key = None
                _refresh_combo_label()
                hint_var.set('Cleared. Press your combination again.')

            def _confirm_combo() -> None:
                nonlocal captured_key
                combo = _format_combo()
                if combo == '<none>':
                    messagebox.showwarning('No key captured', 'Press at least one key before clicking OK.', parent=capture_dlg)
                    return
                captured_key = combo
                capture_dlg.destroy()

            def _cancel_capture() -> None:
                capture_dlg.destroy()

            buttons_frame = tk.Frame(capture_dlg, bg=self._colors['bg'])
            buttons_frame.pack(fill=tk.X, padx=12, pady=(8, 12))

            btn_clear_combo = self._make_button(buttons_frame, text='Clear', width=10, command=_clear_combo)
            btn_ok_combo = self._make_button(buttons_frame, text='OK', width=10, command=_confirm_combo, accent=True)
            btn_cancel_combo = self._make_button(
                buttons_frame,
                text='Cancel',
                width=10,
                command=_cancel_capture,
                danger=True,
            )

            btn_clear_combo.grid(row=0, column=0, padx=(0, 6), sticky='ew')
            btn_ok_combo.grid(row=0, column=1, padx=(0, 6), sticky='ew')
            btn_cancel_combo.grid(row=0, column=2, padx=(0, 0), sticky='ew')
            buttons_frame.grid_columnconfigure(0, weight=1)
            buttons_frame.grid_columnconfigure(1, weight=1)
            buttons_frame.grid_columnconfigure(2, weight=1)

            capture_dlg.bind('<KeyPress>', _on_key_press)
            capture_dlg.protocol('WM_DELETE_WINDOW', _cancel_capture)
            capture_dlg.focus_force()
            capture_dlg.wait_window()

            if not captured_key:
                return

            key_name = captured_key
            working_route.append({'type': 'key', 'key': key_name})
            _refresh_list()
            steps_list.selection_clear(0, tk.END)
            steps_list.selection_set(tk.END)

        def _add_text_step() -> None:
            typed_text = simpledialog.askstring(
                'Add text typing',
                'Enter text to type (example: /m coliseum):',
                parent=dlg,
            )
            if typed_text is None:
                return
            if not typed_text:
                messagebox.showwarning('Invalid text', 'Text cannot be empty.', parent=dlg)
                return
            working_route.append({'type': 'text', 'text': typed_text})
            _refresh_list()
            steps_list.selection_clear(0, tk.END)
            steps_list.selection_set(tk.END)

        def _remove_selected() -> None:
            idx = _selected_index()
            if idx is None:
                return
            del working_route[idx]
            _refresh_list()

        def _move_selected(delta: int) -> None:
            idx = _selected_index()
            if idx is None:
                return
            new_idx = idx + delta
            if new_idx < 0 or new_idx >= len(working_route):
                return
            working_route[idx], working_route[new_idx] = working_route[new_idx], working_route[idx]
            _refresh_list()
            steps_list.selection_set(new_idx)

        def _clear_all() -> None:
            if not working_route:
                return
            if not messagebox.askyesno('Clear route', 'Remove all escape steps?', parent=dlg):
                return
            working_route.clear()
            _refresh_list()

        def _save() -> None:
            nonlocal current_route_name
            if not working_route:
                messagebox.showwarning('Empty route', 'Add at least one step before saving.', parent=dlg)
                return

            suggested_name = current_route_name or self.escape_route_name or 'Route 1'
            route_name_input = simpledialog.askstring(
                'Save escape route',
                'Route name:',
                initialvalue=suggested_name,
                parent=dlg,
            )
            if route_name_input is None:
                return

            route_name_input = route_name_input.strip()
            if not route_name_input:
                messagebox.showwarning('Invalid name', 'Route name cannot be empty.', parent=dlg)
                return

            if route_name_input in self.saved_escape_routes and route_name_input != current_route_name:
                if not messagebox.askyesno(
                    'Overwrite route',
                    f'Route "{route_name_input}" already exists. Overwrite it?',
                    parent=dlg,
                ):
                    return

            self.saved_escape_routes[route_name_input] = [dict(step) for step in working_route]
            if current_route_name and current_route_name != route_name_input:
                self.saved_escape_routes.pop(current_route_name, None)

            if not self._persist_saved_escape_routes():
                return

            current_route_name = route_name_input
            self._apply_escape_route(route_name_input, working_route, log_change=False)
            self._log(f'Escape route saved: {route_name_input} ({len(self.escape_route)} step(s)).')
            dlg.destroy()

        buttons = tk.Frame(dlg, bg=self._colors['bg'])
        buttons.pack(fill=tk.X, padx=12, pady=(8, 0))

        btn_add_click = self._make_button(buttons, text='Add Click', width=12, command=_add_click_step)
        btn_add_key = self._make_button(buttons, text='Add Key', width=12, command=_add_key_step)
        btn_add_text = self._make_button(buttons, text='Add Text', width=12, command=_add_text_step)
        btn_remove = self._make_button(buttons, text='Remove', width=12, command=_remove_selected)
        btn_move_up = self._make_button(buttons, text='Move Up', width=12, command=lambda: _move_selected(-1))
        btn_move_down = self._make_button(buttons, text='Move Down', width=12, command=lambda: _move_selected(1))

        btn_add_click.grid(row=0, column=0, padx=(0, 6), pady=(0, 4), sticky='ew')
        btn_add_key.grid(row=0, column=1, padx=(0, 6), pady=(0, 4), sticky='ew')
        btn_add_text.grid(row=0, column=2, padx=(0, 6), pady=(0, 4), sticky='ew')
        btn_remove.grid(row=0, column=3, padx=(0, 6), pady=(0, 4), sticky='ew')
        btn_move_up.grid(row=0, column=4, padx=(0, 6), pady=(0, 4), sticky='ew')
        btn_move_down.grid(row=0, column=5, padx=(0, 0), pady=(0, 4), sticky='ew')

        for col in range(6):
            buttons.grid_columnconfigure(col, weight=1, uniform='route_actions')

        footer = tk.Frame(dlg, bg=self._colors['bg'])
        footer.pack(fill=tk.X, padx=12, pady=(10, 12))

        btn_clear = self._make_button(footer, text='Clear', width=10, command=_clear_all)
        btn_save = self._make_button(footer, text='Save', width=10, command=_save, accent=True)
        btn_cancel = self._make_button(footer, text='Cancel', width=10, command=dlg.destroy, danger=True)

        def _relayout_route_editor(_event=None) -> None:
            width = dlg.winfo_width()

            btn_add_click.grid_forget()
            btn_add_key.grid_forget()
            btn_add_text.grid_forget()
            btn_remove.grid_forget()
            btn_move_up.grid_forget()
            btn_move_down.grid_forget()
            btn_clear.grid_forget()
            btn_save.grid_forget()
            btn_cancel.grid_forget()

            compact = width < 560

            if compact:
                btn_add_click.grid(row=0, column=0, columnspan=2, padx=(0, 6), pady=(0, 4), sticky='ew')
                btn_add_key.grid(row=0, column=2, columnspan=2, padx=(0, 6), pady=(0, 4), sticky='ew')
                btn_add_text.grid(row=0, column=4, columnspan=2, padx=(0, 0), pady=(0, 4), sticky='ew')
                btn_remove.grid(row=1, column=0, columnspan=2, padx=(0, 6), pady=(0, 4), sticky='ew')
                btn_move_up.grid(row=1, column=2, columnspan=2, padx=(0, 6), pady=(0, 4), sticky='ew')
                btn_move_down.grid(row=1, column=4, columnspan=2, padx=(0, 0), pady=(0, 4), sticky='ew')

                for col in range(6):
                    buttons.grid_columnconfigure(col, weight=1, uniform='route_actions_compact')
                for col in range(6, 8):
                    buttons.grid_columnconfigure(col, weight=0, uniform='')

                btn_clear.grid(row=0, column=0, padx=(0, 6), pady=0, sticky='ew')
                btn_save.grid(row=0, column=1, padx=(0, 6), pady=0, sticky='ew')
                btn_cancel.grid(row=0, column=2, padx=(0, 0), pady=0, sticky='ew')
                footer.grid_columnconfigure(0, weight=1, uniform='footer_actions')
                footer.grid_columnconfigure(1, weight=1, uniform='footer_actions')
                footer.grid_columnconfigure(2, weight=1, uniform='footer_actions')
                return

            btn_add_click.grid(row=0, column=0, padx=(0, 6), pady=(0, 4), sticky='ew')
            btn_add_key.grid(row=0, column=1, padx=(0, 6), pady=(0, 4), sticky='ew')
            btn_add_text.grid(row=0, column=2, padx=(0, 6), pady=(0, 4), sticky='ew')
            btn_remove.grid(row=0, column=3, padx=(0, 6), pady=(0, 4), sticky='ew')
            btn_move_up.grid(row=0, column=4, padx=(0, 6), pady=(0, 4), sticky='ew')
            btn_move_down.grid(row=0, column=5, padx=(0, 0), pady=(0, 4), sticky='ew')

            for col in range(6):
                buttons.grid_columnconfigure(col, weight=1, uniform='route_actions')
            buttons.grid_columnconfigure(6, weight=0, uniform='')

            btn_clear.grid(row=0, column=0, padx=(0, 0), pady=0, sticky='w')
            btn_save.grid(row=0, column=1, padx=(0, 6), pady=0, sticky='e')
            btn_cancel.grid(row=0, column=2, padx=(0, 0), pady=0, sticky='e')
            footer.grid_columnconfigure(0, weight=1)
            footer.grid_columnconfigure(1, weight=0)
            footer.grid_columnconfigure(2, weight=0)

        dlg.bind('<Configure>', _relayout_route_editor)
        _relayout_route_editor()
        dlg.after_idle(_relayout_route_editor)

        _refresh_list()
        self.root.wait_window(dlg)

    def _on_toggle_scanner(self):
        if self._stop_requested:
            self._set_state_stopping()
            self._log('Scanner is still stopping. Please wait.')
            return

        if self._detected_waiting_stop:
            self._stop_scanner(manual_stop=True)
            return

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_scanner(manual_stop=True)
            return

        self._start_scanner()

    def _start_scanner(self):
        if self.region is None:
            messagebox.showwarning('Missing area', 'Select area before starting scanner.', parent=self.root)
            return
        if self._selected_mode() == 'spot-tower' and not self.escape_route:
            messagebox.showwarning('Missing route', 'Create escape route before starting scanner.', parent=self.root)
            return

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_requested = True
            if self._stop_requested_at is None:
                self._stop_requested_at = time.monotonic()
            self._set_state_stopping()
            self._log('Previous scanner is still shutting down. Please wait.')
            return

        self._stop_requested = False
        self._stop_requested_at = None
        self._stop_retry_count = 0
        self._detected_waiting_stop = False
        self._set_state_scanning()
        self._log('Scanner started.')

        self._monitor_thread = threading.Thread(target=self._run_monitor_thread, daemon=True)
        self._monitor_thread.start()

    def _stop_scanner(self, manual_stop: bool):
        self._stop_requested = True
        self._stop_requested_at = time.monotonic()
        self._stop_retry_count = 0

        self._request_monitor_shutdown()

        if manual_stop:
            self._detected_waiting_stop = False
            if self._monitor_thread and self._monitor_thread.is_alive():
                self._set_state_stopping()
                self._log('Scanner stop requested. Waiting for shutdown...')
            else:
                self._stop_requested = False
                self._stop_requested_at = None
                self._set_state_idle('Stopped')
                self._log('Scanner stop requested.')

    def _request_monitor_shutdown(self) -> None:
        # Ask async monitors to stop and also flip their running flags directly as a fallback.
        if self._player_monitor is not None:
            self._player_monitor._running = False
            self._player_monitor._active = False

        if self._monitor_loop is not None:
            if self._player_monitor is not None:
                asyncio.run_coroutine_threadsafe(self._player_monitor.stop(), self._monitor_loop)

    def _run_monitor_thread(self):
        asyncio.run(self._run_monitor_async())

    async def _run_monitor_async(self):
        self._monitor_loop = asyncio.get_running_loop()
        mode = self._selected_mode()
        triggered = False
        try:
            if self._stop_requested:
                return
            assert self.region is not None
            if mode == 'spot-tower':
                await run_spot_tower_monitor(self)
        except Exception as exc:
            self._event_queue.put(('error', f'Scanner runtime error: {exc}'))
        finally:
            self._player_monitor = None
            self._monitor_loop = None
            self._event_queue.put(('stopped', {'triggered': triggered, 'mode': mode}))

    def _drain_events(self):
        while True:
            try:
                event, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event == 'detected':
                self._set_state_detected()
                self._set_last_trigger_now()
                self._log('Player detected. Escape route executed.')
            elif event == 'safe_zone':
                self._notify_safe_zone()
                self._log('Character moved to safe zone.')
            elif event == 'error':
                message = str(payload) if payload is not None else 'Unknown scanner error.'
                self._log(message)
            elif event == 'trigger_snapshot':
                info = payload if isinstance(payload, dict) else {}
                image = info.get('image')
                mode_label = str(info.get('mode', 'Trigger'))
                if isinstance(image, Image.Image):
                    self._last_trigger_snapshot = image.copy()
                    self._last_trigger_mode = mode_label
                    self.btn_last_snapshot.configure(
                        state=tk.NORMAL,
                        text=f'View Last Trigger Snapshot ({mode_label})',
                    )
                    self._log(f'{mode_label} snapshot captured. Use "View Last Trigger Snapshot" to open it.')
            elif event == 'radar_count':
                info = payload if isinstance(payload, dict) else {}
                idx = info.get('idx')
                value = info.get('value')
                if isinstance(idx, int) and 0 <= idx < len(self._process_tower_rows):
                    row = self._process_tower_rows[idx]
                    count_var = row.get('radar_count_var')
                    if count_var is not None:
                        shown = str(value) if value is not None else 'N/A'
                        count_var.set(f'Radar: {shown}')
            elif event == 'map_count':
                info = payload if isinstance(payload, dict) else {}
                idx = info.get('idx')
                value = info.get('value')
                reason = str(info.get('reason', 'ok'))
                if isinstance(idx, int) and 0 <= idx < len(self._process_tower_rows):
                    row = self._process_tower_rows[idx]
                    count_var = row.get('map_count_var')
                    if count_var is not None:
                        if value is not None:
                            shown = str(value)
                        else:
                            shown = '0'
                        count_var.set(f'Map: {shown}')
            elif event == 'retry_status':
                info = payload if isinstance(payload, dict) else {}
                idx = info.get('idx')
                text = str(info.get('text', '')).strip()
                if isinstance(idx, int) and 0 <= idx < len(self._process_tower_rows):
                    row = self._process_tower_rows[idx]
                    status_var = row.get('retry_status_var')
                    if status_var is not None and text:
                        status_var.set(text)
            elif event == 'process_scan_auto_stop':
                info = payload if isinstance(payload, dict) else {}
                idx = info.get('idx')
                if isinstance(idx, int) and 0 <= idx < len(self._process_tower_rows):
                    self._reset_process_tower_scan_row(idx, 'Attached  •  Triggered')
                    self._set_last_trigger_now()
                    self._log(f'Character #{idx + 1}: scan auto-stopped after trigger.')
            elif event == 'triggered':
                self._set_last_trigger_now()
            elif event == 'log':
                self._log(str(payload))
            elif event == 'stopped':
                info = payload if isinstance(payload, dict) else {}
                was_triggered = bool(info.get('triggered'))
                self._stop_requested = False
                self._stop_requested_at = None
                self._monitor_thread = None
                self._detected_waiting_stop = False
                if was_triggered:
                    self._set_state_idle('Stopped (triggered)')
                else:
                    self._set_state_idle('Stopped')

        if self._stop_requested:
            thread_alive = bool(self._monitor_thread and self._monitor_thread.is_alive())
            if not thread_alive:
                self._stop_requested = False
                self._stop_requested_at = None
                self._stop_retry_count = 0
                self._monitor_thread = None
                self._detected_waiting_stop = False
                self._set_state_idle('Stopped')
                self._log('Scanner stopped.')
            elif self._stop_requested_at is not None and (time.monotonic() - self._stop_requested_at) >= 4.0:
                # Retry stop signals but keep UI in stopping until thread is actually down.
                self._stop_requested_at = time.monotonic()
                self._stop_retry_count += 1
                self._request_monitor_shutdown()
                self._set_state_stopping()
                if self._stop_retry_count >= 3:
                    # Last-resort UI recovery. Monitoring flags have already been forced down.
                    self._stop_requested = False
                    self._stop_requested_at = None
                    self._stop_retry_count = 0
                    self._monitor_thread = None
                    self._monitor_loop = None
                    self._player_monitor = None
                    self._detected_waiting_stop = False
                    self._set_state_idle('Stopped (forced)')
                    self._log('Scanner stop did not finish in time. Forced reset applied.')
                else:
                    self._log('Scanner is taking longer to stop. Retrying shutdown...')

        self.root.after(120, self._drain_events)

    def _on_close(self):
        self._stop_scanner(manual_stop=False)
        # Stop all process tower scan threads
        for i in range(len(self._process_tower_rows)):
            row = self._process_tower_rows[i]
            stop_ev = row.get('scan_stop')
            if stop_ev:
                stop_ev.set()
        for row in self._process_tower_rows:
            t = row.get('scan_thread')
            if t and t.is_alive():
                t.join(timeout=1.0)
        for handle in self._process_handles:
            self._close_process_handle(handle)
        self._process_handles.clear()
        self.root.destroy()
