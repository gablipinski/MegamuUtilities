from __future__ import annotations

import queue
import sys
import ctypes
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

from pynput import keyboard, mouse

from app_version import APP_NAME, APP_VERSION
from config import DEFAULT_CONFIG_PATH, MacroConfig, load_macros, save_macros
from macro_engine import MacroEngine, get_cursor_position


@dataclass
class WorkingMacro:
    name: str
    hotkey: str
    active: bool
    steps: list[dict[str, int | str | bool]]
    repeat_while_held: bool


class MacroUI:
    def __init__(self) -> None:
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
            'input_bg': '#0f1318',
            'warning': '#e3b341',
        }

        self._font_ui = ('Segoe UI', 10)
        self._font_title = ('Segoe UI Semibold', 16)

        self._event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._engine = MacroEngine(self._queue_log)
        self._macros: list[WorkingMacro] = []
        self._selected_macro_idx: int | None = None
        self._tray_icon = None
        self._tray_minimized = False
        self._tray_quick_panel: tk.Toplevel | None = None

        self.root.title(f'{APP_NAME} v{APP_VERSION}')
        self.root.geometry('900x620')
        self.root.minsize(820, 560)
        self.root.configure(bg=self._colors['bg'])
        self.root.option_add('*Font', self._font_ui)
        self._set_window_icon()

        self._build_ui()
        self._load_macros()
        self._start_hotkeys()
        self._setup_tray_support()

        self.root.after(120, self._drain_events)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _setup_tray_support(self) -> None:
        if sys.platform != 'win32':
            return

        try:
            from windows_tray import WindowsTrayIcon
        except Exception as exc:
            self._append_log(f'Tray support unavailable: {exc}', 'ignore')
            return

        icon_path = Path(__file__).resolve().parent.parent / 'icons' / 'siegetower.ico'
        self._tray_icon = WindowsTrayIcon(
            icon_path=icon_path,
            tooltip=f'{APP_NAME} v{APP_VERSION}',
            on_single_click=lambda: self._event_queue.put(('tray_single_click', '')),
            on_double_click=lambda: self._event_queue.put(('tray_double_click', '')),
        )
        self.root.bind('<Unmap>', self._on_root_unmap, add='+')

    def _on_root_unmap(self, _event: tk.Event) -> None:
        if self._tray_minimized:
            return
        if self.root.state() != 'iconic':
            return
        self._minimize_to_tray()

    def _minimize_to_tray(self) -> None:
        if self._tray_icon is None:
            return
        if not self._tray_icon.start():
            self._append_log('Could not start tray icon.', 'ignore')
            return

        self._tray_minimized = True
        self.root.withdraw()
        self._append_log('Minimized to tray. Single-click tray icon for quick macro panel, double-click to restore.', 'notification')

    def _restore_from_tray(self) -> None:
        if not self._tray_minimized:
            return

        self._destroy_tray_quick_panel()
        if self._tray_icon is not None:
            self._tray_icon.stop()
        self._tray_minimized = False

        self.root.deiconify()
        self.root.state('normal')
        self.root.lift()
        self.root.focus_force()
        self._append_log('Restored from tray.', 'notification')

    def _toggle_tray_quick_panel(self) -> None:
        if not self._tray_minimized:
            return
        if self._tray_quick_panel is not None and self._tray_quick_panel.winfo_exists():
            self._destroy_tray_quick_panel()
            return
        self._show_tray_quick_panel()

    def _show_tray_quick_panel(self) -> None:
        panel_width = 360
        panel_height = 390

        panel = tk.Toplevel(self.root)
        panel.title(f'{APP_NAME} Quick Macros')
        panel.configure(bg=self._colors['panel'])
        panel.resizable(False, False)
        panel.attributes('-topmost', True)

        # Align popup to the bottom-right of the Windows work area (above taskbar).
        x = max(0, int(self.root.winfo_screenwidth()) - panel_width)
        y = max(0, int(self.root.winfo_screenheight()) - panel_height)
        if sys.platform == 'win32':
            class RECT(ctypes.Structure):
                _fields_ = [
                    ('left', ctypes.c_long),
                    ('top', ctypes.c_long),
                    ('right', ctypes.c_long),
                    ('bottom', ctypes.c_long),
                ]

            rect = RECT()
            SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                x = max(0, int(rect.right) - panel_width)
                y = max(0, int(rect.bottom) - panel_height)

        panel.geometry(f'{panel_width}x{panel_height}+{x}+{y}')

        self._tray_quick_panel = panel

        container = tk.Frame(
            panel,
            bg=self._colors['panel'],
            padx=10,
            pady=10,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
        )
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text='Quick Macros',
            bg=self._colors['panel'],
            fg=self._colors['text'],
            font=('Segoe UI Semibold', 11),
            anchor='w',
        ).pack(fill=tk.X)

        tk.Label(
            container,
            text='Toggle configured macros:',
            bg=self._colors['panel'],
            fg=self._colors['muted'],
            anchor='w',
        ).pack(fill=tk.X, pady=(2, 6))

        macro_shell = tk.Frame(container, bg=self._colors['panel'])
        macro_shell.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        macro_canvas = tk.Canvas(
            macro_shell,
            bg=self._colors['input_bg'],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        macro_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        macro_scroll = tk.Scrollbar(macro_shell, orient=tk.VERTICAL, command=macro_canvas.yview)
        macro_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        macro_rows = tk.Frame(macro_canvas, bg=self._colors['input_bg'])
        macro_rows_window = macro_canvas.create_window((0, 0), window=macro_rows, anchor='nw')

        macro_rows.bind('<Configure>', lambda _e: macro_canvas.configure(scrollregion=macro_canvas.bbox('all')))
        macro_canvas.bind('<Configure>', lambda e: macro_canvas.itemconfigure(macro_rows_window, width=e.width))
        macro_canvas.configure(yscrollcommand=macro_scroll.set)

        def _toggle_macro_from_tray(idx: int, active_var: tk.BooleanVar) -> None:
            self._set_macro_active(idx, bool(active_var.get()))
            _refresh_tray_checkbox_list()

        def _refresh_tray_checkbox_list() -> None:
            for child in macro_rows.winfo_children():
                child.destroy()

            if not self._macros:
                tk.Label(
                    macro_rows,
                    text='No macros configured.',
                    bg=self._colors['input_bg'],
                    fg=self._colors['muted'],
                    anchor='w',
                ).pack(fill=tk.X, padx=8, pady=(8, 0))
                return

            for idx, macro in enumerate(self._macros):
                row_bg = self._colors['input_bg']
                row = tk.Frame(macro_rows, bg=row_bg)
                row.pack(fill=tk.X)

                active_var = tk.BooleanVar(value=macro.active)
                checkbox = tk.Checkbutton(
                    row,
                    variable=active_var,
                    command=lambda i=idx, v=active_var: _toggle_macro_from_tray(i, v),
                    bg=row_bg,
                    fg=self._colors['text'],
                    activebackground=row_bg,
                    activeforeground=self._colors['text'],
                    selectcolor=self._colors['panel_alt'],
                    highlightthickness=0,
                    bd=0,
                    padx=6,
                    pady=2,
                )
                checkbox.pack(side=tk.LEFT)

                suffix = ' (hold)' if macro.repeat_while_held else ''
                text = f'{idx + 1}. {macro.name} [{macro.hotkey}] - {len(macro.steps)} step(s){suffix}'
                tk.Label(
                    row,
                    text=text,
                    bg=row_bg,
                    fg=self._colors['text'],
                    anchor='w',
                ).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)

        _refresh_tray_checkbox_list()

        actions = tk.Frame(container, bg=self._colors['panel'])
        actions.pack(fill=tk.X)

        tray_hotkeys_button = self._make_button(
            actions,
            text='Stop Hotkeys' if self._engine.running else 'Start Hotkeys',
            width=14,
            command=self._toggle_hotkeys_from_tray,
            danger=self._engine.running,
            success=not self._engine.running,
        )
        tray_hotkeys_button.pack(side=tk.LEFT)

        def _refresh_tray_hotkey_button() -> None:
            if self._engine.running:
                tray_hotkeys_button.configure(
                    text='Stop Hotkeys',
                    bg=self._colors['danger'],
                    activebackground=self._colors['danger_hover'],
                    fg='#ffffff',
                    activeforeground='#ffffff',
                )
            else:
                tray_hotkeys_button.configure(
                    text='Start Hotkeys',
                    bg=self._colors['success'],
                    activebackground='#1f8f58',
                    fg='#ffffff',
                    activeforeground='#ffffff',
                )

        tray_hotkeys_button.configure(command=lambda: self._toggle_hotkeys_from_tray(_refresh_tray_hotkey_button))

        self._make_button(
            actions,
            text='Open App',
            width=10,
            command=self._restore_from_tray,
        ).pack(side=tk.RIGHT)

        panel.bind('<Escape>', lambda _e: self._destroy_tray_quick_panel())
        panel.bind('<FocusOut>', lambda _e: self._destroy_tray_quick_panel())
        panel.protocol('WM_DELETE_WINDOW', self._destroy_tray_quick_panel)
        panel.focus_force()

    def _destroy_tray_quick_panel(self) -> None:
        panel = self._tray_quick_panel
        self._tray_quick_panel = None
        if panel is None:
            return
        try:
            if panel.winfo_exists():
                panel.destroy()
        except Exception:
            pass

    def _toggle_hotkeys_from_tray(self, refresh_button=None) -> None:
        self._toggle_hotkeys()
        if callable(refresh_button):
            try:
                refresh_button()
            except Exception:
                pass

    def _set_window_icon(self) -> None:
        icon_path = Path(__file__).resolve().parent.parent / 'icons' / 'siegetower.png'
        if not icon_path.exists():
            return
        try:
            self._window_icon = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._window_icon)
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()

    def _make_button(
        self,
        parent: tk.Misc,
        text: str,
        *,
        width: int,
        command,
        accent: bool = False,
        danger: bool = False,
        success: bool = False,
    ) -> tk.Button:
        bg = self._colors['panel_alt']
        hover_bg = '#2a313a'
        fg = self._colors['text']

        if accent:
            bg = self._colors['accent']
            hover_bg = self._colors['accent_hover']
            fg = '#ffffff'
        elif danger:
            bg = self._colors['danger']
            hover_bg = self._colors['danger_hover']
            fg = '#ffffff'
        elif success:
            bg = self._colors['success']
            hover_bg = '#1f8f58'
            fg = '#ffffff'

        return tk.Button(
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

    def _normalize_key_name(self, value: str) -> str:
        key = value.strip().lower().replace(' ', '')
        aliases = {
            'control': 'ctrl',
            'ctrl_l': 'ctrl',
            'ctrl_r': 'ctrl',
            'control_l': 'ctrl',
            'control_r': 'ctrl',
            'shift_l': 'shift',
            'shift_r': 'shift',
            'alt_l': 'alt',
            'alt_r': 'alt',
            'cmd': 'win',
            'cmd_l': 'win',
            'cmd_r': 'win',
            'esc': 'esc',
            'escape': 'esc',
            'return': 'enter',
            'space': 'space',
            'page_up': 'pageup',
            'page_down': 'pagedown',
            'caps_lock': 'capslock',
        }
        return aliases.get(key, key)

    def _key_event_name(self, key: keyboard.Key | keyboard.KeyCode) -> str:
        if isinstance(key, keyboard.KeyCode):
            char = key.char
            if char:
                return self._normalize_key_name(char)
            vk = key.vk
            if vk is None:
                return ''
            if 48 <= vk <= 57:
                return chr(vk)
            if 65 <= vk <= 90:
                return chr(vk).lower()
            if 112 <= vk <= 123:
                return f'f{vk - 111}'
            return self._normalize_key_name(str(vk))

        name = str(key).split('.')[-1]
        return self._normalize_key_name(name)

    def _mouse_button_name(self, button: mouse.Button) -> str:
        raw_name = getattr(button, 'name', '') or str(button).split('.')[-1]
        button_name = raw_name.strip().lower()
        aliases = {
            'back': 'x1',
            'forward': 'x2',
            'button4': 'x1',
            'button5': 'x2',
            'xbutton1': 'x1',
            'xbutton2': 'x2',
            'mouse4': 'x1',
            'mouse5': 'x2',
        }
        button_name = aliases.get(button_name, button_name)
        if button_name.startswith('button') and button_name[6:].isdigit():
            if button_name.endswith('4'):
                return 'x1'
            if button_name.endswith('5'):
                return 'x2'
        if button_name in {'left', 'right', 'middle', 'x1', 'x2'}:
            return button_name
        return ''

    def _place_window_at_parent_origin(self, window: tk.Toplevel, parent: tk.Misc) -> None:
        window.update_idletasks()
        parent.update_idletasks()
        window.geometry(f'+{parent.winfo_rootx()}+{parent.winfo_rooty()}')

    def _create_popup(self, parent: tk.Misc, *, title: str, geometry: str, resizable: bool = False) -> tk.Toplevel:
        popup = tk.Toplevel(parent)
        popup.title(title)
        popup.geometry(geometry)
        popup.configure(bg=self._colors['panel'])
        popup.transient(parent)
        popup.grab_set()
        popup.resizable(resizable, resizable)
        self._place_window_at_parent_origin(popup, parent)
        return popup

    def _ask_integer_popup(
        self,
        parent: tk.Misc,
        *,
        title: str,
        prompt: str,
        initial: int,
        min_value: int,
        max_value: int | None = None,
    ) -> int | None:
        popup = self._create_popup(parent, title=title, geometry='420x170', resizable=False)

        body = tk.Frame(popup, bg=self._colors['panel'], padx=12, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(body, text=prompt, bg=self._colors['panel'], fg=self._colors['text'], anchor='w').pack(fill=tk.X)
        entry = tk.Entry(
            body,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            insertbackground=self._colors['text'],
            relief=tk.FLAT,
        )
        entry.pack(fill=tk.X, pady=(8, 0))
        entry.insert(0, str(initial))
        entry.select_range(0, tk.END)
        entry.focus_set()

        status_var = tk.StringVar(value='')
        tk.Label(body, textvariable=status_var, bg=self._colors['panel'], fg=self._colors['warning'], anchor='w').pack(fill=tk.X, pady=(6, 0))

        result: dict[str, int | None] = {'value': None}

        def _save() -> None:
            raw = entry.get().strip()
            try:
                value = int(raw)
            except ValueError:
                status_var.set('Value must be a whole number.')
                return
            if value < min_value:
                status_var.set(f'Value must be >= {min_value}.')
                return
            if max_value is not None and value > max_value:
                status_var.set(f'Value must be <= {max_value}.')
                return
            result['value'] = value
            popup.destroy()

        actions = tk.Frame(body, bg=self._colors['panel'])
        actions.pack(fill=tk.X, pady=(10, 0))
        self._make_button(actions, text='OK', width=10, command=_save, accent=True).pack(side=tk.RIGHT)
        self._make_button(actions, text='Cancel', width=10, command=popup.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        entry.bind('<Return>', lambda _e: _save())
        popup.protocol('WM_DELETE_WINDOW', popup.destroy)
        popup.wait_window()
        return result['value']

    def _build_ui(self) -> None:
        container = tk.Frame(
            self.root,
            padx=14,
            pady=14,
            bg=self._colors['panel'],
            highlightthickness=1,
            highlightbackground=self._colors['border'],
        )
        container.pack(fill=tk.BOTH, expand=True)

        title_row = tk.Frame(container, bg=self._colors['panel'])
        title_row.pack(fill=tk.X)

        tk.Label(
            title_row,
            text=f'{APP_NAME} v{APP_VERSION}',
            bg=self._colors['panel'],
            fg=self._colors['text'],
            font=self._font_title,
        ).pack(side=tk.LEFT)

        self.btn_toggle_hotkeys = self._make_button(
            title_row,
            text='Stop Hotkeys',
            width=14,
            command=self._toggle_hotkeys,
            danger=True,
        )
        self.btn_toggle_hotkeys.pack(side=tk.RIGHT)

        self._make_button(
            title_row,
            text='Edit Macros',
            width=14,
            command=self._open_macro_editor,
            accent=True,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(
            title_row,
            text='Reload Config',
            width=14,
            command=self._load_macros,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(
            title_row,
            text='Open .siege',
            width=14,
            command=self._import_macros_file,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._make_button(
            title_row,
            text='Save .siege',
            width=14,
            command=self._export_macros_file,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        state_row = tk.Frame(container, bg=self._colors['panel'])
        state_row.pack(fill=tk.X, pady=(10, 8))

        self.led = tk.Canvas(state_row, width=18, height=18, highlightthickness=0, bg=self._colors['panel'])
        self.led.pack(side=tk.LEFT)
        self.led_circle = self.led.create_oval(2, 2, 16, 16, fill='#00b050', outline='#1f1f1f')

        self.lbl_state = tk.Label(
            state_row,
            text='Hotkeys: running',
            bg=self._colors['panel'],
            fg=self._colors['text'],
            font=('Segoe UI Semibold', 10),
        )
        self.lbl_state.pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_config = tk.Label(
            container,
            text=f'Config: {DEFAULT_CONFIG_PATH}',
            bg=self._colors['panel'],
            fg=self._colors['muted'],
            anchor='w',
        )
        self.lbl_config.pack(fill=tk.X, pady=(0, 8))

        center = tk.Frame(container, bg=self._colors['panel'])
        center.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(center, bg=self._colors['panel'])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(left, text='Configured macros', bg=self._colors['panel'], fg=self._colors['muted'], anchor='w').pack(fill=tk.X)
        macro_shell = tk.Frame(left, bg=self._colors['panel'])
        macro_shell.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        self.macro_canvas = tk.Canvas(
            macro_shell,
            bg=self._colors['input_bg'],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        self.macro_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        macro_scroll = tk.Scrollbar(macro_shell, orient=tk.VERTICAL, command=self.macro_canvas.yview)
        macro_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.macro_canvas.configure(yscrollcommand=macro_scroll.set)

        self.macro_rows = tk.Frame(self.macro_canvas, bg=self._colors['input_bg'])
        self._macro_rows_window = self.macro_canvas.create_window((0, 0), window=self.macro_rows, anchor='nw')
        self.macro_rows.bind('<Configure>', lambda _e: self.macro_canvas.configure(scrollregion=self.macro_canvas.bbox('all')))
        self.macro_canvas.bind('<Configure>', lambda e: self.macro_canvas.itemconfigure(self._macro_rows_window, width=e.width))

        right = tk.Frame(center, bg=self._colors['panel'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

        tk.Label(right, text='Live log', bg=self._colors['panel'], fg=self._colors['muted'], anchor='w').pack(fill=tk.X)
        self.txt_log = tk.Text(
            right,
            height=20,
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
            padx=8,
            pady=8,
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.txt_log.tag_configure('notification', foreground='#6fe0ff')
        self.txt_log.tag_configure('ignore', foreground='#ff8080')
        self.txt_log.tag_configure('other', foreground=self._colors['text'])

        footer = tk.Frame(container, bg=self._colors['panel'])
        footer.pack(fill=tk.X, pady=(10, 0))

        self._make_button(footer, text='Trigger Selected', width=16, command=self._trigger_selected_macro, success=True).pack(side=tk.LEFT)
        self._make_button(footer, text='Toggle Selected Active', width=20, command=self._toggle_selected_macro_active).pack(side=tk.LEFT, padx=(8, 0))

    def _set_hotkey_state(self, running: bool) -> None:
        if running:
            self.led.itemconfig(self.led_circle, fill='#00b050')
            self.lbl_state.configure(text='Hotkeys: running')
            self.btn_toggle_hotkeys.configure(
                text='Stop Hotkeys',
                bg=self._colors['danger'],
                activebackground=self._colors['danger_hover'],
            )
        else:
            self.led.itemconfig(self.led_circle, fill='#7a7a7a')
            self.lbl_state.configure(text='Hotkeys: stopped')
            self.btn_toggle_hotkeys.configure(
                text='Start Hotkeys',
                bg=self._colors['success'],
                activebackground='#1f8f58',
            )

    def _queue_log(self, message: str, kind: str) -> None:
        self._event_queue.put((kind, message))

    def _append_log(self, message: str, kind: str = 'other') -> None:
        tag = kind if kind in {'notification', 'ignore', 'other'} else 'other'
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.insert(tk.END, f'{message}\n', (tag,))
        self.txt_log.see(tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _drain_events(self) -> None:
        while True:
            try:
                kind, message = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if kind == 'tray_single_click':
                self._toggle_tray_quick_panel()
                continue
            if kind == 'tray_double_click':
                self._restore_from_tray()
                continue
            self._append_log(str(message), str(kind))

        self.root.after(120, self._drain_events)

    def _refresh_macro_list(self) -> None:
        for child in self.macro_rows.winfo_children():
            child.destroy()

        if self._selected_macro_idx is not None:
            if self._selected_macro_idx < 0 or self._selected_macro_idx >= len(self._macros):
                self._selected_macro_idx = None

        for idx, macro in enumerate(self._macros):
            selected = self._selected_macro_idx == idx
            row_bg = self._colors['accent'] if selected else self._colors['input_bg']
            text_fg = '#ffffff' if selected else self._colors['text']
            suffix = ' (hold)' if macro.repeat_while_held else ''
            row = tk.Frame(self.macro_rows, bg=row_bg)
            row.pack(fill=tk.X)

            active_var = tk.BooleanVar(value=macro.active)
            checkbox = tk.Checkbutton(
                row,
                variable=active_var,
                command=lambda i=idx, v=active_var: self._set_macro_active(i, bool(v.get())),
                bg=row_bg,
                fg=text_fg,
                activebackground=row_bg,
                activeforeground=text_fg,
                selectcolor=self._colors['panel_alt'],
                highlightthickness=0,
                bd=0,
                padx=6,
                pady=2,
            )
            checkbox.pack(side=tk.LEFT)

            text = f'{idx + 1}. {macro.name} [{macro.hotkey}] - {len(macro.steps)} step(s){suffix}'
            label = tk.Label(row, text=text, bg=row_bg, fg=text_fg, anchor='w')
            label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)

            row.bind('<Button-1>', lambda _e, i=idx: self._select_macro_idx(i))
            label.bind('<Button-1>', lambda _e, i=idx: self._select_macro_idx(i))

    def _load_macros(self) -> None:
        loaded = load_macros()
        self._macros = [
            WorkingMacro(
                name=item.name,
                hotkey=item.hotkey,
                active=item.active,
                steps=[dict(s) for s in item.steps],
                repeat_while_held=item.repeat_while_held,
            )
            for item in loaded
        ]
        if self._selected_macro_idx is not None and self._selected_macro_idx >= len(self._macros):
            self._selected_macro_idx = None
        self._apply_macros_to_engine()
        self._refresh_macro_list()
        self._append_log(f'Loaded {len(self._macros)} macro(s) from config.', 'notification')

    def _save_macros(self) -> None:
        save_macros([
            MacroConfig(
                name=macro.name,
                hotkey=macro.hotkey,
                active=macro.active,
                repeat_while_held=macro.repeat_while_held,
                steps=macro.steps,
            )
            for macro in self._macros
        ])
        self._append_log(f'Saved {len(self._macros)} macro(s) to config.', 'notification')

    def _apply_macros_to_engine(self) -> None:
        self._engine.set_macros([
            {
                'name': macro.name,
                'hotkey': macro.hotkey,
                'repeat_while_held': macro.repeat_while_held,
                'steps': macro.steps,
            }
            for macro in self._macros
            if macro.active
        ])

    def _import_macros_file(self) -> None:
        selected_path = filedialog.askopenfilename(
            parent=self.root,
            title='Open Macro File',
            initialdir=str(DEFAULT_CONFIG_PATH.parent),
            filetypes=[
                ('Siegetower macro files', '*.siege'),
                ('JSON files', '*.json'),
                ('All files', '*.*'),
            ],
        )
        if not selected_path:
            return

        source_path = Path(selected_path)
        try:
            loaded = load_macros(source_path)
        except Exception as exc:
            messagebox.showerror('Import failed', f'Could not read macro file:\n{source_path}\n\n{exc}', parent=self.root)
            return

        self._macros = [
            WorkingMacro(
                name=item.name,
                hotkey=item.hotkey,
                active=item.active,
                steps=[dict(s) for s in item.steps],
                repeat_while_held=item.repeat_while_held,
            )
            for item in loaded
        ]
        self._selected_macro_idx = None
        self._save_macros()
        self._apply_macros_to_engine()
        self._refresh_macro_list()
        self._append_log(f'Imported {len(self._macros)} macro(s) from {source_path}.', 'notification')

    def _export_macros_file(self) -> None:
        selected_path = filedialog.asksaveasfilename(
            parent=self.root,
            title='Save Macro File',
            initialdir=str(DEFAULT_CONFIG_PATH.parent),
            defaultextension='.siege',
            filetypes=[
                ('Siegetower macro files', '*.siege'),
                ('JSON files', '*.json'),
                ('All files', '*.*'),
            ],
        )
        if not selected_path:
            return

        target_path = Path(selected_path)
        if not target_path.suffix:
            target_path = target_path.with_suffix('.siege')

        try:
            save_macros([
                MacroConfig(
                    name=macro.name,
                    hotkey=macro.hotkey,
                    active=macro.active,
                    repeat_while_held=macro.repeat_while_held,
                    steps=macro.steps,
                )
                for macro in self._macros
            ], config_path=target_path)
        except Exception as exc:
            messagebox.showerror('Export failed', f'Could not save macro file:\n{target_path}\n\n{exc}', parent=self.root)
            return

        self._append_log(f'Exported {len(self._macros)} macro(s) to {target_path}.', 'notification')

    def _toggle_hotkeys(self) -> None:
        if self._engine.running:
            self._engine.stop()
            self._set_hotkey_state(False)
            return
        self._start_hotkeys()

    def _start_hotkeys(self) -> None:
        try:
            self._engine.start()
            self._set_hotkey_state(True)
        except Exception as exc:
            self._set_hotkey_state(False)
            self._append_log(f'Failed to start hotkeys: {exc}', 'ignore')

    def _trigger_selected_macro(self) -> None:
        idx = self._selected_macro_idx
        if idx is None or idx < 0 or idx >= len(self._macros):
            return
        macro = self._macros[idx]
        if not self._engine.trigger_macro(macro.name):
            self._append_log('Could not trigger selected macro.', 'ignore')

    def _select_macro_idx(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._macros):
            return
        self._selected_macro_idx = idx
        self._refresh_macro_list()

    def _set_macro_active(self, idx: int, active: bool) -> None:
        if idx < 0 or idx >= len(self._macros):
            return

        self._selected_macro_idx = idx
        macro = self._macros[idx]
        macro.active = active

        disabled = 0
        if macro.active:
            target_hotkey = macro.hotkey.strip().casefold()
            if target_hotkey:
                for pos, other in enumerate(self._macros):
                    if pos == idx:
                        continue
                    if other.active and other.hotkey.strip().casefold() == target_hotkey:
                        other.active = False
                        disabled += 1

        self._save_macros()
        self._apply_macros_to_engine()
        self._refresh_macro_list()

        state_text = 'active' if macro.active else 'inactive'
        if disabled:
            self._append_log(
                f"Macro '{macro.name}' is now {state_text}. Disabled {disabled} other macro(s) sharing [{macro.hotkey}].",
                'notification',
            )
            return
        self._append_log(f"Macro '{macro.name}' is now {state_text}.", 'notification')

    def _toggle_selected_macro_active(self) -> None:
        idx = self._selected_macro_idx
        if idx is None or idx < 0 or idx >= len(self._macros):
            return
        self._set_macro_active(idx, not self._macros[idx].active)

    def _describe_step(self, index: int, step: dict[str, int | str | bool]) -> str:
        step_type = str(step.get('type', '')).lower()
        if step_type == 'click':
            button = str(step.get('button', 'left')).strip().lower()
            if button not in {'left', 'right'}:
                button = 'left'
            at_origin = bool(step.get('at_origin', False))
            if at_origin:
                label = 'Right Click (Origin)' if button == 'right' else 'Click (Origin)'
                return f'{index}. {label}'
            label = 'Right Click' if button == 'right' else 'Click'
            return f'{index}. {label} ({int(step.get("x", 0))}, {int(step.get("y", 0))})'
        if step_type == 'key':
            action = str(step.get('action', 'tap')).strip().lower()
            key_name = str(step.get('key', '')).strip() or '<empty>'
            if action == 'press':
                return f'{index}. Key Down {key_name}'
            if action == 'release':
                return f'{index}. Key Up {key_name}'
            return f'{index}. Key Tap {key_name}'
        if step_type == 'delay':
            ms = max(0, int(step.get('ms', 0)))
            jitter_pct = max(0, int(step.get('jitter_pct', 0)))
            if jitter_pct:
                return f'{index}. Delay {ms}ms +/-{jitter_pct}%'
            return f'{index}. Delay {ms}ms'
        if step_type == 'return_cursor':
            return f'{index}. Return Cursor'
        return f'{index}. Unknown'

    def _open_macro_editor(self) -> None:
        working = [
            WorkingMacro(
                name=m.name,
                hotkey=m.hotkey,
                active=m.active,
                steps=[dict(s) for s in m.steps],
                repeat_while_held=m.repeat_while_held,
            )
            for m in self._macros
        ]

        def _enforce_hotkey_exclusive(items: list[WorkingMacro], active_index: int) -> None:
            if active_index < 0 or active_index >= len(items):
                return
            owner = items[active_index]
            if not owner.active:
                return
            hotkey = owner.hotkey.strip().casefold()
            if not hotkey:
                return
            for idx, macro in enumerate(items):
                if idx == active_index:
                    continue
                if macro.hotkey.strip().casefold() == hotkey:
                    macro.active = False

        dialog = self._create_popup(self.root, title='Macro Editor', geometry='760x520', resizable=True)

        frame = tk.Frame(dialog, bg=self._colors['panel'], padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text='Build macros with click, key and delay steps. Hotkeys are global while listener is running.',
            bg=self._colors['panel'],
            fg=self._colors['text'],
            anchor='w',
        ).pack(fill=tk.X)

        list_shell = tk.Frame(frame, bg=self._colors['panel'])
        list_shell.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        lst = tk.Listbox(
            list_shell,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroller = tk.Scrollbar(list_shell, orient=tk.VERTICAL, command=lst.yview)
        scroller.pack(side=tk.RIGHT, fill=tk.Y)
        lst.configure(yscrollcommand=scroller.set)

        def _refresh_editor_list() -> None:
            lst.delete(0, tk.END)
            for idx, macro in enumerate(working, start=1):
                active_prefix = '[x]' if macro.active else '[ ]'
                lst.insert(tk.END, f'{idx}. {active_prefix} {macro.name} [{macro.hotkey}] - {len(macro.steps)} step(s)')

        def _selected_idx() -> int | None:
            sel = lst.curselection()
            if not sel:
                return None
            return int(sel[0])

        def _edit_macro(initial: WorkingMacro | None = None) -> WorkingMacro | None:
            macro = WorkingMacro(
                name=initial.name if initial else '',
                hotkey=initial.hotkey if initial else '',
                active=initial.active if initial else True,
                steps=[dict(s) for s in (initial.steps if initial else [])],
                repeat_while_held=initial.repeat_while_held if initial else False,
            )

            editor = self._create_popup(dialog, title='Edit Macro' if initial else 'Add Macro', geometry='620x470', resizable=True)

            body = tk.Frame(editor, bg=self._colors['panel'], padx=12, pady=12)
            body.pack(fill=tk.BOTH, expand=True)

            row_name = tk.Frame(body, bg=self._colors['panel'])
            row_name.pack(fill=tk.X)
            tk.Label(row_name, text='Name', width=12, anchor='w', bg=self._colors['panel'], fg=self._colors['muted']).pack(side=tk.LEFT)
            ent_name = tk.Entry(row_name, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT)
            ent_name.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ent_name.insert(0, macro.name)

            row_hotkey = tk.Frame(body, bg=self._colors['panel'])
            row_hotkey.pack(fill=tk.X, pady=(8, 0))
            tk.Label(row_hotkey, text='Hotkey', width=12, anchor='w', bg=self._colors['panel'], fg=self._colors['muted']).pack(side=tk.LEFT)
            ent_hotkey = tk.Entry(row_hotkey, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT)
            ent_hotkey.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ent_hotkey.insert(0, macro.hotkey)

            def _capture_binding_dialog(*, title: str, prompt: str, initial_value: str = '', allow_mouse: bool = False) -> str | None:
                capture = self._create_popup(editor, title=title, geometry='420x210', resizable=False)

                tk.Label(
                    capture,
                    text=prompt,
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    wraplength=300,
                ).pack(fill=tk.X, padx=12, pady=(14, 10))

                status_var = tk.StringVar(value='Waiting for key press...' if not allow_mouse else 'Waiting for key press or side mouse button...')
                tk.Label(
                    capture,
                    textvariable=status_var,
                    bg=self._colors['panel'],
                    fg=self._colors['muted'],
                ).pack(fill=tk.X, padx=12)

                listener_holder: dict[str, keyboard.Listener | None] = {'listener': None}
                mouse_listener_holder: dict[str, mouse.Listener | None] = {'listener': None}
                pressed_keys: set[str] = set()
                captured_binding: dict[str, str | None] = {'value': initial_value.strip() or None}
                save_state: dict[str, tk.Button | None] = {'button': None}

                def _safe_capture_ui(callback) -> None:
                    try:
                        if capture.winfo_exists():
                            callback()
                    except Exception:
                        pass

                if captured_binding['value']:
                    status_var.set(f"Captured: {captured_binding['value']}")

                def _finish(binding: str | None) -> None:
                    listener = listener_holder.get('listener')
                    listener_holder['listener'] = None
                    if listener is not None:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                    mouse_listener = mouse_listener_holder.get('listener')
                    mouse_listener_holder['listener'] = None
                    if mouse_listener is not None:
                        try:
                            mouse_listener.stop()
                        except Exception:
                            pass
                    if binding:
                        ent_hotkey.delete(0, tk.END)
                        ent_hotkey.insert(0, binding)
                        status_var.set(f'Captured: {binding}')
                    if save_state['button'] is not None:
                        try:
                            if save_state['button'].winfo_exists():
                                save_state['button'].configure(state=tk.NORMAL)
                        except Exception:
                            pass

                def _format_binding() -> str:
                    modifiers = [name for name in ('ctrl', 'shift', 'alt', 'win') if name in pressed_keys]
                    others = [name for name in pressed_keys if name not in {'ctrl', 'shift', 'alt', 'win'}]
                    ordered = modifiers + sorted(others)
                    return '+'.join(ordered)

                def _on_press(key: keyboard.Key | keyboard.KeyCode) -> bool:
                    key_name = self._key_event_name(key)
                    if not key_name:
                        return True

                    pressed_keys.add(key_name)
                    binding = _format_binding()
                    captured_binding['value'] = binding
                    if binding:
                        capture.after(0, lambda b=binding: _safe_capture_ui(lambda: status_var.set(f'Captured: {b}')))
                        capture.after(0, lambda: _safe_capture_ui(lambda: save_state['button'].configure(state=tk.NORMAL) if save_state['button'] is not None else None))
                    return True

                def _on_release(key: keyboard.Key | keyboard.KeyCode) -> bool:
                    key_name = self._key_event_name(key)
                    if not key_name:
                        return True

                    pressed_keys.discard(key_name)
                    return True

                listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
                listener_holder['listener'] = listener
                listener.start()

                if allow_mouse:
                    def _on_mouse_click(_x: int, _y: int, button: mouse.Button, pressed: bool) -> bool:
                        if not pressed:
                            return True

                        button_name = self._mouse_button_name(button)
                        if button_name not in {'x1', 'x2'}:
                            if button_name:
                                capture.after(0, lambda b=button_name: _safe_capture_ui(lambda: status_var.set(f'Use side buttons only (captured {b}, ignored).')))
                            return True

                        binding = f'mouse:{button_name}'
                        captured_binding['value'] = binding
                        capture.after(0, lambda b=binding: _safe_capture_ui(lambda: status_var.set(f'Captured: {b}')))
                        capture.after(0, lambda: _safe_capture_ui(lambda: save_state['button'].configure(state=tk.NORMAL) if save_state['button'] is not None else None))
                        return True

                    mouse_listener = mouse.Listener(on_click=_on_mouse_click)
                    mouse_listener_holder['listener'] = mouse_listener
                    mouse_listener.start()

                def _save() -> None:
                    binding = captured_binding['value'] or ent_hotkey.get().strip()
                    if not binding:
                        status_var.set('Press a key or combo before saving.')
                        return
                    _finish(binding)
                    capture.destroy()

                def _cancel() -> None:
                    _finish(None)
                    capture.destroy()

                actions = tk.Frame(capture, bg=self._colors['panel'])
                actions.pack(fill=tk.X, padx=12, pady=(10, 12))
                save_button = self._make_button(actions, text='Save', width=10, command=_save, accent=True)
                save_button.configure(state=tk.NORMAL if captured_binding['value'] else tk.DISABLED)
                save_button.pack(side=tk.RIGHT)
                save_state['button'] = save_button
                self._make_button(actions, text='Cancel', width=10, command=_cancel).pack(side=tk.RIGHT, padx=(0, 8))

                capture.protocol('WM_DELETE_WINDOW', _cancel)
                capture.wait_window()
                return captured_binding['value']

            def _capture_hotkey() -> None:
                binding = _capture_binding_dialog(
                    title='Capture Hotkey',
                    prompt='Press a hotkey combo or use a side mouse button (x1/x2).',
                    initial_value=macro.hotkey,
                    allow_mouse=True,
                )
                if binding:
                    ent_hotkey.delete(0, tk.END)
                    ent_hotkey.insert(0, binding)

            def _capture_key_step_dialog(*, title: str, initial_step: dict[str, int | str | bool] | None = None) -> dict[str, int | str | bool] | None:
                capture = self._create_popup(editor, title=title, geometry='420x260', resizable=False)

                tk.Label(
                    capture,
                    text='Press the key or combo for this step.',
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    wraplength=330,
                ).pack(fill=tk.X, padx=12, pady=(14, 10))

                status_var = tk.StringVar(value='Waiting for key press...')
                tk.Label(
                    capture,
                    textvariable=status_var,
                    bg=self._colors['panel'],
                    fg=self._colors['muted'],
                ).pack(fill=tk.X, padx=12)

                action_var = tk.StringVar(value=str(initial_step.get('action', 'tap')).strip().lower() if initial_step else 'tap')
                action_row = tk.Frame(capture, bg=self._colors['panel'])
                action_row.pack(fill=tk.X, padx=12, pady=(10, 0))
                tk.Label(action_row, text='Action', bg=self._colors['panel'], fg=self._colors['muted'], width=10, anchor='w').pack(side=tk.LEFT)
                for label, value in (('Tap', 'tap'), ('Press', 'press'), ('Release', 'release')):
                    tk.Radiobutton(
                        action_row,
                        text=label,
                        value=value,
                        variable=action_var,
                        bg=self._colors['panel'],
                        fg=self._colors['text'],
                        selectcolor=self._colors['panel_alt'],
                        activebackground=self._colors['panel'],
                        activeforeground=self._colors['text'],
                        highlightthickness=0,
                    ).pack(side=tk.LEFT, padx=(0, 8))

                listener_holder: dict[str, keyboard.Listener | None] = {'listener': None}
                pressed_keys: set[str] = set()
                captured_binding: dict[str, str | None] = {'value': str(initial_step.get('key', '')).strip() if initial_step else None}
                save_state: dict[str, tk.Button | None] = {'button': None}

                def _safe_capture_ui(callback) -> None:
                    try:
                        if capture.winfo_exists():
                            callback()
                    except Exception:
                        pass

                if captured_binding['value']:
                    status_var.set(f"Captured: {captured_binding['value']}")

                def _finish(binding: str | None) -> None:
                    listener = listener_holder.get('listener')
                    listener_holder['listener'] = None
                    if listener is not None:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                    if binding:
                        captured_binding['value'] = binding
                        status_var.set(f'Captured: {binding}')
                    if save_state['button'] is not None:
                        try:
                            if save_state['button'].winfo_exists():
                                save_state['button'].configure(state=tk.NORMAL)
                        except Exception:
                            pass

                def _format_binding() -> str:
                    modifiers = [name for name in ('ctrl', 'shift', 'alt', 'win') if name in pressed_keys]
                    others = [name for name in pressed_keys if name not in {'ctrl', 'shift', 'alt', 'win'}]
                    ordered = modifiers + sorted(others)
                    return '+'.join(ordered)

                def _on_press(key: keyboard.Key | keyboard.KeyCode) -> bool:
                    key_name = self._key_event_name(key)
                    if not key_name:
                        return True

                    pressed_keys.add(key_name)
                    binding = _format_binding()
                    if binding:
                        captured_binding['value'] = binding
                        capture.after(0, lambda b=binding: _safe_capture_ui(lambda: status_var.set(f'Captured: {b}')))
                        capture.after(0, lambda: _safe_capture_ui(lambda: save_state['button'].configure(state=tk.NORMAL) if save_state['button'] is not None else None))
                    return True

                def _on_release(key: keyboard.Key | keyboard.KeyCode) -> bool:
                    key_name = self._key_event_name(key)
                    if not key_name:
                        return True
                    pressed_keys.discard(key_name)
                    return True

                listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
                listener_holder['listener'] = listener
                listener.start()

                result: dict[str, int | str | bool] = {}

                def _save() -> None:
                    binding = captured_binding['value'] or ''
                    if not binding:
                        status_var.set('Press a key or combo before saving.')
                        return
                    result.update({'type': 'key', 'key': binding, 'action': str(action_var.get()).strip().lower() or 'tap'})
                    capture.destroy()

                def _cancel() -> None:
                    _finish(None)
                    capture.destroy()

                actions = tk.Frame(capture, bg=self._colors['panel'])
                actions.pack(fill=tk.X, padx=12, pady=(12, 12))
                save_button = self._make_button(actions, text='Save', width=10, command=_save, accent=True)
                save_button.configure(state=tk.NORMAL if captured_binding['value'] else tk.DISABLED)
                save_button.pack(side=tk.RIGHT)
                save_state['button'] = save_button
                self._make_button(actions, text='Cancel', width=10, command=_cancel).pack(side=tk.RIGHT, padx=(0, 8))

                capture.protocol('WM_DELETE_WINDOW', _cancel)
                capture.wait_window()

                return result if result.get('type') == 'key' else None

            self._make_button(row_hotkey, text='Capture Hotkey', width=14, command=_capture_hotkey).pack(side=tk.LEFT, padx=(8, 0))

            tk.Label(
                body,
                text='Examples: ctrl+1, ctrl+shift+k, alt+f9',
                bg=self._colors['panel'],
                fg=self._colors['muted'],
                anchor='w',
            ).pack(fill=tk.X, pady=(2, 8))

            repeat_var = tk.BooleanVar(value=macro.repeat_while_held)
            tk.Checkbutton(
                body,
                text='Repeat while held',
                variable=repeat_var,
                bg=self._colors['panel'],
                fg=self._colors['text'],
                selectcolor=self._colors['panel_alt'],
                activebackground=self._colors['panel'],
                activeforeground=self._colors['text'],
                highlightthickness=0,
            ).pack(anchor='w', pady=(0, 8))

            steps = tk.Listbox(
                body,
                bg=self._colors['input_bg'],
                fg=self._colors['text'],
                selectbackground=self._colors['accent'],
                selectforeground='#ffffff',
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=self._colors['border'],
                highlightcolor=self._colors['accent'],
            )
            steps.pack(fill=tk.BOTH, expand=True)

            def _refresh_steps() -> None:
                steps.delete(0, tk.END)
                for idx, step in enumerate(macro.steps, start=1):
                    steps.insert(tk.END, self._describe_step(idx, step))

            def _selected_step_idx() -> int | None:
                sel = steps.curselection()
                if not sel:
                    return None
                return int(sel[0])

            def _open_click_step_dialog(
                *,
                title: str,
                initial_step: dict[str, int | str | bool] | None = None,
            ) -> dict[str, int | str | bool] | None:
                picker = self._create_popup(editor, title=title, geometry='420x260', resizable=False)

                body_picker = tk.Frame(picker, bg=self._colors['panel'], padx=12, pady=12)
                body_picker.pack(fill=tk.BOTH, expand=True)

                tk.Label(
                    body_picker,
                    text='Click anywhere to capture the step.',
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    anchor='w',
                ).pack(fill=tk.X)

                status_var = tk.StringVar(value='Waiting for mouse click...')
                tk.Label(
                    body_picker,
                    textvariable=status_var,
                    bg=self._colors['panel'],
                    fg=self._colors['muted'],
                    anchor='w',
                ).pack(fill=tk.X, pady=(8, 0))

                result: dict[str, int | str | bool] = {
                    'type': 'click',
                    'x': 0,
                    'y': 0,
                    'button': 'left',
                    'at_origin': False,
                }

                listener_holder: dict[str, mouse.Listener | None] = {'listener': None}
                captured_value: dict[str, int | str | bool | None] = {'value': None}
                save_state: dict[str, tk.Button | None] = {'button': None}

                def _safe_picker_ui(callback) -> None:
                    try:
                        if picker.winfo_exists():
                            callback()
                    except Exception:
                        pass

                def _finish(value: dict[str, int | str | bool] | None) -> None:
                    listener = listener_holder.get('listener')
                    listener_holder['listener'] = None
                    if listener is not None:
                        try:
                            listener.stop()
                        except Exception:
                            pass
                    if value is not None:
                        captured_value['value'] = value
                        result.update(value)
                        status_var.set(f"Captured: {value['button']} ({value['x']}, {value['y']})")
                    if save_state['button'] is not None:
                        try:
                            if save_state['button'].winfo_exists():
                                save_state['button'].configure(state=tk.NORMAL)
                        except Exception:
                            pass

                def _on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> bool:
                    if not pressed:
                        return True

                    button_name = self._mouse_button_name(button)
                    if button_name not in {'left', 'right'}:
                        capture_text = button_name or 'unknown'
                        picker.after(0, lambda t=capture_text: _safe_picker_ui(lambda: status_var.set(f'Unsupported button: {t}')))
                        return True

                    picker.after(0, lambda xx=int(x), yy=int(y), bn=button_name: _safe_picker_ui(lambda: _finish({'type': 'click', 'x': xx, 'y': yy, 'button': bn, 'at_origin': False})))
                    return False

                listener = mouse.Listener(on_click=_on_click)
                listener_holder['listener'] = listener
                listener.start()

                def _save_click() -> None:
                    if captured_value['value'] is None:
                        status_var.set('Click somewhere to capture the step first.')
                        return
                    picker.saved = True
                    picker.destroy()

                def _cancel_click() -> None:
                    _finish(None)
                    picker.destroy()

                actions_picker = tk.Frame(body_picker, bg=self._colors['panel'])
                actions_picker.pack(fill=tk.X, pady=(12, 0))
                save_button = self._make_button(actions_picker, text='Save', width=10, command=_save_click, accent=True)
                save_button.configure(state=tk.DISABLED)
                save_button.pack(side=tk.RIGHT)
                save_state['button'] = save_button
                self._make_button(actions_picker, text='Cancel', width=10, command=_cancel_click).pack(side=tk.RIGHT, padx=(0, 6))

                picker.protocol('WM_DELETE_WINDOW', _cancel_click)
                picker.wait_window()

                if getattr(picker, 'saved', False):
                    return result
                return None

            def _add_click() -> None:
                click_step = _open_click_step_dialog(title='Add Click Step')
                if click_step is None:
                    return
                macro.steps.append(click_step)
                _refresh_steps()

            def _add_key() -> None:
                key_step = _capture_key_step_dialog(title='Capture Key Step')
                if key_step is None:
                    return
                macro.steps.append(key_step)
                _refresh_steps()

            def _add_delay() -> None:
                ms = self._ask_integer_popup(
                    editor,
                    title='Delay Step',
                    prompt='Delay in milliseconds:',
                    initial=0,
                    min_value=0,
                )
                if ms is None:
                    return
                jitter_pct = self._ask_integer_popup(
                    editor,
                    title='Delay Jitter',
                    prompt='Optional jitter percentage (0 to disable):',
                    initial=0,
                    min_value=0,
                    max_value=100,
                )
                if jitter_pct is None:
                    return
                delay_step: dict[str, int | str | bool] = {'type': 'delay', 'ms': int(ms)}
                if jitter_pct:
                    delay_step['jitter_pct'] = int(jitter_pct)
                macro.steps.append(delay_step)
                _refresh_steps()

            def _add_return_cursor() -> None:
                macro.steps.append({'type': 'return_cursor'})
                _refresh_steps()

            def _remove_step() -> None:
                idx = _selected_step_idx()
                if idx is None:
                    return
                del macro.steps[idx]
                _refresh_steps()
                if macro.steps:
                    next_idx = min(idx, len(macro.steps) - 1)
                    steps.selection_set(next_idx)

            def _remove_step_key(_event: tk.Event) -> str:
                _remove_step()
                return 'break'

            def _edit_step() -> None:
                idx = _selected_step_idx()
                if idx is None:
                    return

                step = macro.steps[idx]
                step_type = str(step.get('type', '')).strip().lower()

                if step_type == 'click':
                    click_step = _open_click_step_dialog(title='Edit Click Step', initial_step=step)
                    if click_step is None:
                        return
                    macro.steps[idx] = click_step
                elif step_type == 'key':
                    key_step = _capture_key_step_dialog(title='Edit Key Step', initial_step=step)
                    if key_step is None:
                        return
                    macro.steps[idx] = key_step
                elif step_type == 'delay':
                    current_ms = max(0, int(step.get('ms', 0)))
                    ms = self._ask_integer_popup(
                        editor,
                        title='Edit Delay Step',
                        prompt='Delay in milliseconds:',
                        initial=current_ms,
                        min_value=0,
                    )
                    if ms is None:
                        return
                    current_jitter = max(0, int(step.get('jitter_pct', 0)))
                    jitter_pct = self._ask_integer_popup(
                        editor,
                        title='Edit Delay Jitter',
                        prompt='Optional jitter percentage (0 to disable):',
                        initial=current_jitter,
                        min_value=0,
                        max_value=100,
                    )
                    if jitter_pct is None:
                        return
                    updated_step: dict[str, int | str | bool] = {'type': 'delay', 'ms': int(ms)}
                    if jitter_pct:
                        updated_step['jitter_pct'] = int(jitter_pct)
                    macro.steps[idx] = updated_step
                elif step_type == 'return_cursor':
                    macro.steps[idx] = {'type': 'return_cursor'}
                else:
                    messagebox.showerror('Unsupported step', f'Cannot edit unknown step type: {step_type}', parent=editor)
                    return

                _refresh_steps()
                steps.selection_set(idx)

            def _edit_step_event(_event: tk.Event) -> str:
                _edit_step()
                return 'break'

            def _move_step(delta: int) -> None:
                idx = _selected_step_idx()
                if idx is None:
                    return
                new_idx = idx + delta
                if new_idx < 0 or new_idx >= len(macro.steps):
                    return
                macro.steps[idx], macro.steps[new_idx] = macro.steps[new_idx], macro.steps[idx]
                _refresh_steps()
                steps.selection_set(new_idx)

            actions = tk.Frame(body, bg=self._colors['panel'])
            actions.pack(fill=tk.X, pady=(8, 0))
            self._make_button(actions, text='Add Click', width=10, command=_add_click).pack(side=tk.LEFT)
            self._make_button(actions, text='Add Key', width=10, command=_add_key).pack(side=tk.LEFT, padx=(6, 0))
            self._make_button(actions, text='Add Delay', width=10, command=_add_delay).pack(side=tk.LEFT, padx=(6, 0))
            self._make_button(actions, text='Return Cursor', width=12, command=_add_return_cursor).pack(side=tk.LEFT, padx=(6, 0))
            self._make_button(actions, text='Edit Step', width=10, command=_edit_step).pack(side=tk.LEFT, padx=(6, 0))
            self._make_button(actions, text='Remove', width=10, command=_remove_step).pack(side=tk.LEFT, padx=(6, 0))

            reorder = tk.Frame(body, bg=self._colors['panel'])
            reorder.pack(fill=tk.X, pady=(6, 0))
            self._make_button(reorder, text='Move Up', width=10, command=lambda: _move_step(-1)).pack(side=tk.LEFT)
            self._make_button(reorder, text='Move Down', width=10, command=lambda: _move_step(1)).pack(side=tk.LEFT, padx=(6, 0))

            result: dict[str, bool] = {'saved': False}

            def _save_macro() -> None:
                macro.name = ent_name.get().strip()
                macro.hotkey = ent_hotkey.get().strip()
                macro.repeat_while_held = bool(repeat_var.get())
                if not macro.name or not macro.hotkey:
                    messagebox.showerror('Invalid macro', 'Name and hotkey are required.', parent=editor)
                    return
                if not macro.steps:
                    messagebox.showerror('Invalid macro', 'At least one step is required.', parent=editor)
                    return
                result['saved'] = True
                editor.destroy()

            footer = tk.Frame(body, bg=self._colors['panel'])
            footer.pack(fill=tk.X, pady=(10, 0))
            self._make_button(footer, text='Save', width=10, command=_save_macro, accent=True).pack(side=tk.RIGHT)
            self._make_button(footer, text='Cancel', width=10, command=editor.destroy).pack(side=tk.RIGHT, padx=(0, 8))

            steps.bind('<Delete>', _remove_step_key)
            steps.bind('<Double-Button-1>', _edit_step_event)
            _refresh_steps()
            editor.wait_window()
            return macro if result['saved'] else None

        def _add_macro() -> None:
            created = _edit_macro()
            if created is None:
                return
            if any(item.name.casefold() == created.name.casefold() for item in working):
                messagebox.showerror('Duplicate name', 'Macro name already exists.', parent=dialog)
                return
            working.append(created)
            if created.active:
                _enforce_hotkey_exclusive(working, len(working) - 1)
            _refresh_editor_list()

        def _edit_selected() -> None:
            idx = _selected_idx()
            if idx is None:
                return
            edited = _edit_macro(working[idx])
            if edited is None:
                return
            for pos, item in enumerate(working):
                if pos == idx:
                    continue
                if item.name.casefold() == edited.name.casefold():
                    messagebox.showerror('Duplicate name', 'Macro name already exists.', parent=dialog)
                    return
            working[idx] = edited
            if edited.active:
                _enforce_hotkey_exclusive(working, idx)
            _refresh_editor_list()
            lst.selection_set(idx)

        def _edit_selected_event(_event: tk.Event) -> str:
            _edit_selected()
            return 'break'

        def _remove_selected() -> None:
            idx = _selected_idx()
            if idx is None:
                return
            del working[idx]
            _refresh_editor_list()

        def _duplicate_selected() -> None:
            idx = _selected_idx()
            if idx is None:
                return

            source = working[idx]
            existing_names = {item.name.casefold() for item in working}

            base_name = f'{source.name} Copy'
            new_name = base_name
            suffix = 2
            while new_name.casefold() in existing_names:
                new_name = f'{base_name} {suffix}'
                suffix += 1

            duplicated = WorkingMacro(
                name=new_name,
                hotkey=source.hotkey,
                active=source.active,
                steps=[dict(step) for step in source.steps],
                repeat_while_held=source.repeat_while_held,
            )

            working.insert(idx + 1, duplicated)
            _refresh_editor_list()
            lst.selection_clear(0, tk.END)
            lst.selection_set(idx + 1)
            lst.see(idx + 1)

        def _move_selected(delta: int) -> None:
            idx = _selected_idx()
            if idx is None:
                return
            new_idx = idx + delta
            if new_idx < 0 or new_idx >= len(working):
                return
            working[idx], working[new_idx] = working[new_idx], working[idx]
            _refresh_editor_list()
            lst.selection_set(new_idx)

        def _toggle_selected_active() -> None:
            idx = _selected_idx()
            if idx is None:
                return
            working[idx].active = not working[idx].active
            if working[idx].active:
                _enforce_hotkey_exclusive(working, idx)
            _refresh_editor_list()
            lst.selection_set(idx)

        controls = tk.Frame(frame, bg=self._colors['panel'])
        controls.pack(fill=tk.X, pady=(8, 0))
        controls_top = tk.Frame(controls, bg=self._colors['panel'])
        controls_top.pack(fill=tk.X)
        self._make_button(controls_top, text='Add', width=12, command=_add_macro).pack(side=tk.LEFT)
        self._make_button(controls_top, text='Edit', width=12, command=_edit_selected).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls_top, text='Duplicate', width=12, command=_duplicate_selected).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls_top, text='Remove', width=12, command=_remove_selected).pack(side=tk.LEFT, padx=(6, 0))

        controls_bottom = tk.Frame(controls, bg=self._colors['panel'])
        controls_bottom.pack(fill=tk.X, pady=(6, 0))
        self._make_button(controls_bottom, text='Toggle Active', width=14, command=_toggle_selected_active, accent=True).pack(side=tk.LEFT)
        self._make_button(controls_bottom, text='Move Up', width=12, command=lambda: _move_selected(-1)).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls_bottom, text='Move Down', width=12, command=lambda: _move_selected(1)).pack(side=tk.LEFT, padx=(6, 0))

        lst.bind('<Double-Button-1>', _edit_selected_event)

        footer = tk.Frame(frame, bg=self._colors['panel'])
        footer.pack(fill=tk.X, pady=(12, 0))

        def _save_all() -> None:
            self._macros = working
            # Enforce one active macro per hotkey; first active keeps ownership.
            seen_hotkeys: set[str] = set()
            for macro in self._macros:
                if not macro.active:
                    continue
                key = macro.hotkey.strip().casefold()
                if key in seen_hotkeys:
                    macro.active = False
                else:
                    seen_hotkeys.add(key)
            self._save_macros()
            self._engine.set_macros([
                {
                    'name': item.name,
                    'hotkey': item.hotkey,
                    'repeat_while_held': item.repeat_while_held,
                    'steps': item.steps,
                }
                for item in self._macros
                if item.active
            ])
            self._refresh_macro_list()
            dialog.destroy()

        self._make_button(footer, text='Save', width=12, command=_save_all, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text='Cancel', width=12, command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        _refresh_editor_list()
        dialog.wait_window()

    def _on_close(self) -> None:
        self._destroy_tray_quick_panel()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self._engine.stop()
        except Exception:
            pass
        self.root.destroy()
