import asyncio
import queue
import shutil
import sys
import threading
import tkinter as tk
import tkinter.filedialog as filedialog
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from action_controller import ActionController
from area_selector import capture_virtual_screen, select_area_with_parent, select_points_with_parent
from app_version import APP_NAME, APP_VERSION
from player_monitor import PlayerMonitor
from config import WindowConfig, load_config
from screen_monitor import ScreenMonitor


class MonitorUI:
    def __init__(self):
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
        self.root.minsize(520, 340)

        self.region: tuple[int, int, int, int] | None = None
        self.escape_route: list[dict[str, int | str]] = []
        self.template_path = Path(__file__).resolve().parent.parent / 'configs' / 'spot_template.png'

        self._event_queue: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_loop: asyncio.AbstractEventLoop | None = None
        self._player_monitor: PlayerMonitor | None = None
        self._tower_monitor: ScreenMonitor | None = None
        self._detected_waiting_stop = False
        self._mode_var = tk.StringVar(value='SPOT TOWER')
        self._last_mode_selection = 'SPOT TOWER'

        self._build_ui()
        self._set_state_idle('Idle')
        self.root.after(120, self._drain_events)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
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
        bg = self._colors['panel_alt']
        hover_bg = '#2a313a'
        fg = self._colors['text']

        if accent:
            bg = self._colors['accent']
            hover_bg = self._colors['accent_hover']
            fg = '#ffffff'
        elif success:
            bg = self._colors['success']
            hover_bg = '#1f8f58'
            fg = '#ffffff'
        elif danger:
            bg = self._colors['danger']
            hover_bg = self._colors['danger_hover']
            fg = '#ffffff'

        button = tk.Button(
            parent,
            text=text,
            width=width,
            command=command,
            relief=tk.FLAT,
            bd=0,
            cursor='hand2',
            padx=8,
            pady=6,
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground='#ffffff',
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        return button

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
        dlg.geometry('480x340')
        dlg.resizable(False, False)
        dlg.protocol('WM_DELETE_WINDOW', lambda: None)
        self._apply_app_icon(dlg)
        dlg.configure(bg=self._colors['bg'])
        dlg.lift()
        dlg.focus_force()

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
                shutil.copy(selected, target)
            except Exception as exc:
                status_var.set(f'Could not copy license: {exc}\nCopy manually to: {target}')
                return
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
            values=['SPOT TOWER', 'SAFE TOWER'],
            width=18,
            style='Dark.TCombobox',
        )
        self.cmb_mode.pack(side=tk.LEFT, padx=(8, 0))
        self.cmb_mode.bind('<<ComboboxSelected>>', self._on_mode_changed)

        controls = tk.Frame(container, bg=self._colors['panel'])
        controls.pack(fill=tk.X, pady=(10, 8))

        self.btn_select_area = self._make_button(
            controls,
            text='Select Area',
            width=16,
            command=self._on_select_area,
        )
        self.btn_select_area.grid(row=0, column=0, padx=(0, 8), pady=4)

        self.btn_select_route = self._make_button(
            controls,
            text='Create Escape Route',
            width=20,
            command=self._on_select_route,
        )
        self.btn_select_route.grid(row=0, column=1, padx=(0, 8), pady=4)

        self.btn_capture_template = self._make_button(
            controls,
            text='Capture Template',
            width=16,
            command=self._on_capture_template,
        )
        self.btn_capture_template.grid(row=0, column=2, padx=(0, 8), pady=4)

        self.btn_toggle_scan = self._make_button(
            controls,
            text='Start Scanner',
            width=16,
            command=self._on_toggle_scanner,
            accent=True,
        )
        self.btn_toggle_scan.grid(row=1, column=0, padx=(0, 8), pady=4)

        state_row = tk.Frame(container, bg=self._colors['panel'])
        state_row.pack(fill=tk.X, pady=(6, 6))

        self.led = tk.Canvas(
            state_row,
            width=18,
            height=18,
            highlightthickness=0,
            bg=self._colors['panel'],
            bd=0,
        )
        self.led.pack(side=tk.LEFT)
        self.led_circle = self.led.create_oval(2, 2, 16, 16, fill='#7a7a7a', outline='#1f1f1f')

        self.lbl_state = tk.Label(
            state_row,
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

        self.log = tk.Text(
            container,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            insertbackground=self._colors['text'],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
            padx=10,
            pady=8,
        )
        self.log.pack(fill=tk.BOTH, expand=True)

        self._refresh_mode_ui()

    def run(self):
        self.root.mainloop()

    def _log(self, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = f'[{timestamp}] {message}\n'
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_led(self, color: str):
        self.led.itemconfig(self.led_circle, fill=color)

    def _set_state_idle(self, reason: str):
        self._set_led('#7a7a7a')
        self.lbl_state.configure(text=f'State: Idle ({reason})')
        self.btn_toggle_scan.configure(text='Start Scanner')
        self.btn_toggle_scan.configure(bg=self._colors['success'], activebackground='#1f8f58')

    def _set_state_scanning(self):
        self._set_led('#00b050')
        self.lbl_state.configure(text='State: Scanning')
        self.btn_toggle_scan.configure(text='Stop Scanner')
        self.btn_toggle_scan.configure(bg=self._colors['danger'], activebackground=self._colors['danger_hover'])

    def _set_state_detected(self):
        self._set_led('#d32f2f')
        self.lbl_state.configure(text='State: Detected')
        self.btn_toggle_scan.configure(text='Start Scanner')
        self.btn_toggle_scan.configure(bg=self._colors['success'], activebackground='#1f8f58')

    def _selected_mode(self) -> str:
        return 'safe-tower' if self._mode_var.get() == 'SAFE TOWER' else 'spot-tower'

    def _refresh_mode_ui(self):
        mode = self._selected_mode()
        if mode == 'safe-tower':
            self.btn_select_route.configure(state=tk.DISABLED)
            self.lbl_route.configure(text='Escape route: not required in SAFE TOWER mode')
            self._log('Mode set to SAFE TOWER.')
        else:
            self.btn_select_route.configure(state=tk.NORMAL)
            self._update_route_label()
            self._log('Mode set to SPOT TOWER.')

    def _update_route_label(self):
        if not self.escape_route:
            self.lbl_route.configure(text='Escape route: not selected')
            return

        click_count = sum(1 for item in self.escape_route if str(item.get('type', '')).lower() == 'click')
        key_count = sum(1 for item in self.escape_route if str(item.get('type', '')).lower() == 'key')
        self.lbl_route.configure(
            text=f'Escape route: {len(self.escape_route)} step(s) ({click_count} click, {key_count} key)'
        )

    def _on_mode_changed(self, _event=None):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before changing mode.')
            self._mode_var.set(self._last_mode_selection)
            return
        self._last_mode_selection = self._mode_var.get()
        self._detected_waiting_stop = False
        self._set_state_idle('Mode changed')
        self._refresh_mode_ui()

    def _on_select_area(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before selecting a new area.')
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
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before editing escape route.')
            return

        self._open_escape_route_editor()

    def _on_capture_template(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before capturing a template.')
            return

        selection = select_area_with_parent(
            self.root,
            help_text='Drag exact target image | Enter confirm | Esc cancel',
            min_size=4,
        )
        if selection is None:
            self._log('Template capture cancelled.')
            return

        image, offset_x, offset_y = capture_virtual_screen()
        x1, y1, x2, y2 = selection
        crop_x1 = max(0, x1 - offset_x)
        crop_y1 = max(0, y1 - offset_y)
        crop_x2 = max(crop_x1 + 1, x2 - offset_x)
        crop_y2 = max(crop_y1 + 1, y2 - offset_y)
        template = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))

        self.template_path.parent.mkdir(parents=True, exist_ok=True)
        template.save(self.template_path)
        self._log(f'Template saved to {self.template_path.name} ({crop_x2 - crop_x1}x{crop_y2 - crop_y1}).')

    def _open_escape_route_editor(self):
        working_route = [dict(step) for step in self.escape_route]

        dlg = tk.Toplevel(self.root)
        dlg.title('Escape Route Editor')
        dlg.geometry('520x360')
        dlg.resizable(False, False)
        dlg.configure(bg=self._colors['bg'])
        self._apply_app_icon(dlg)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text='Configure ordered escape actions (clicks and keys).',
            font=('Segoe UI', 10),
            anchor='w',
            bg=self._colors['bg'],
            fg=self._colors['text'],
        ).pack(fill=tk.X, padx=12, pady=(12, 6))

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        steps_list = tk.Listbox(
            list_frame,
            height=10,
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
            key_name = simpledialog.askstring(
                'Add key press',
                'Enter key name for pyautogui.press (examples: f1, esc, 1, enter):',
                parent=dlg,
            )
            if key_name is None:
                return
            key_name = key_name.strip()
            if not key_name:
                messagebox.showwarning('Invalid key', 'Key name cannot be empty.', parent=dlg)
                return
            working_route.append({'type': 'key', 'key': key_name})
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
            self.escape_route = working_route
            self._update_route_label()
            self._log(f'Escape route saved with {len(self.escape_route)} step(s).')
            dlg.destroy()

        buttons = tk.Frame(dlg, bg=self._colors['bg'])
        buttons.pack(fill=tk.X, padx=12, pady=(8, 0))

        self._make_button(buttons, text='Add Click', width=12, command=_add_click_step).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._make_button(buttons, text='Add Key', width=12, command=_add_key_step).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._make_button(buttons, text='Remove', width=12, command=_remove_selected).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._make_button(buttons, text='Move Up', width=12, command=lambda: _move_selected(-1)).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._make_button(buttons, text='Move Down', width=12, command=lambda: _move_selected(1)).pack(
            side=tk.LEFT
        )

        footer = tk.Frame(dlg, bg=self._colors['bg'])
        footer.pack(fill=tk.X, padx=12, pady=(10, 12))
        self._make_button(footer, text='Clear', width=10, command=_clear_all).pack(side=tk.LEFT)
        self._make_button(footer, text='Cancel', width=10, command=dlg.destroy, danger=True).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        self._make_button(footer, text='Save', width=10, command=_save, accent=True).pack(side=tk.RIGHT)

        _refresh_list()
        self.root.wait_window(dlg)

    def _on_toggle_scanner(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_scanner(manual_stop=True)
            return

        self._start_scanner()

    def _start_scanner(self):
        if self.region is None:
            messagebox.showwarning('Missing area', 'Select area before starting scanner.')
            return
        if self._selected_mode() == 'spot-tower' and not self.escape_route:
            messagebox.showwarning('Missing route', 'Create escape route before starting scanner.')
            return

        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._detected_waiting_stop = False
        self._set_state_scanning()
        self._log('Scanner started.')

        self._monitor_thread = threading.Thread(target=self._run_monitor_thread, daemon=True)
        self._monitor_thread.start()

    def _stop_scanner(self, manual_stop: bool):
        if self._monitor_loop is not None:
            if self._player_monitor is not None:
                asyncio.run_coroutine_threadsafe(self._player_monitor.stop(), self._monitor_loop)
            if self._tower_monitor is not None:
                asyncio.run_coroutine_threadsafe(self._tower_monitor.stop_monitoring(), self._monitor_loop)

        if manual_stop:
            self._detected_waiting_stop = False
            self._set_state_idle('Stopped')
            self._log('Scanner stop requested.')

    def _run_monitor_thread(self):
        asyncio.run(self._run_monitor_async())

    async def _run_monitor_async(self):
        self._monitor_loop = asyncio.get_running_loop()
        mode = self._selected_mode()
        triggered = False
        try:
            assert self.region is not None
            if mode == 'spot-tower':
                marker_template = self.template_path if self.template_path.exists() else None
                monitor = PlayerMonitor(
                    self.region,
                    interval_ms=100,
                    confirm_frames=1,
                    min_movement_px=0.0,
                    min_confidence=0.50,
                    template_path=str(marker_template) if marker_template is not None else None,
                    template_match_threshold=0.50,
                    startup_ignore_frames=0,
                    background_ack_frames=1,
                    require_background_ack=False,
                    log_each_poll=True,
                    fast_trigger_on_blue_spike=True,
                    fast_trigger_min_blue_pixels=170,
                    fast_trigger_min_increase=100,
                    fast_trigger_ratio=2.1,
                    fast_trigger_confidence=0.56,
                    fast_trigger_circle_min_area_px=24,
                    fast_trigger_circle_max_area_px=320,
                    fast_trigger_circle_min_circularity=0.68,
                    fast_trigger_circle_min_aspect=0.75,
                    fast_trigger_circle_min_new_pixels=30,
                    debug=False,
                )
                self._player_monitor = monitor
                action_controller = ActionController(actions=self.escape_route, cooldown_seconds=2.0)

                async def on_detection(detection):
                    nonlocal triggered
                    if triggered:
                        return
                    triggered = True
                    self._event_queue.put(('detected', detection))
                    await action_controller.execute_escape_sequence('Player detected')
                    await monitor.stop()

                monitor.detection_callback = on_detection
                await monitor.start()
            else:
                x1, y1, x2, y2 = self.region
                config = load_config()
                config.windows = [
                    WindowConfig(
                        position='ui-safe-region',
                        x=int(x1),
                        y=int(y1),
                        width=int(x2 - x1),
                        height=int(y2 - y1),
                        map_name='UIRegion',
                    )
                ]
                config.scan_region = {
                    'left_pct': 0.0,
                    'top_pct': 0.0,
                    'right_pct': 1.0,
                    'bottom_pct': 1.0,
                }
                tower_monitor = ScreenMonitor(config)
                self._tower_monitor = tower_monitor

                async def on_tower_detection(char_name: str, map_name: str, guild_name: str | None):
                    self._event_queue.put(
                        ('tower_detected', {'char_name': char_name, 'map_name': map_name, 'guild_name': guild_name})
                    )
                    return True

                tower_monitor.detection_callback = on_tower_detection
                await tower_monitor.start_monitoring()
        finally:
            self._player_monitor = None
            self._tower_monitor = None
            self._monitor_loop = None
            self._event_queue.put(('stopped', {'triggered': triggered, 'mode': mode}))

    def _drain_events(self):
        while True:
            try:
                event, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event == 'detected':
                self._detected_waiting_stop = True
                self._set_state_detected()
                self._log('Player detected. Escape route executed. Press Stop to reset.')
            elif event == 'tower_detected':
                info = payload if isinstance(payload, dict) else {}
                char_name = info.get('char_name', 'Unknown')
                map_name = info.get('map_name', 'Unknown')
                guild_name = info.get('guild_name')
                if guild_name:
                    self._log(f'SAFE TOWER detection: {char_name} [{guild_name}] in {map_name}.')
                else:
                    self._log(f'SAFE TOWER detection: {char_name} in {map_name}.')
            elif event == 'stopped':
                info = payload if isinstance(payload, dict) else {}
                mode = info.get('mode', 'spot-tower')
                if mode == 'spot-tower' and info.get('triggered'):
                    self._detected_waiting_stop = True
                    self._set_state_detected()
                else:
                    self._detected_waiting_stop = False
                    self._set_state_idle('Stopped')

        self.root.after(120, self._drain_events)

    def _on_close(self):
        self._stop_scanner(manual_stop=False)
        self.root.destroy()
