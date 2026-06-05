from __future__ import annotations

import queue
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, simpledialog

from app_version import APP_NAME, APP_VERSION
from config import DEFAULT_CONFIG_PATH, MacroConfig, load_macros, save_macros
from macro_engine import MacroEngine, get_cursor_position


@dataclass
class WorkingMacro:
    name: str
    hotkey: str
    steps: list[dict[str, int | str]]


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

        self._event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._engine = MacroEngine(self._queue_log)
        self._macros: list[WorkingMacro] = []

        self.root.title(f'{APP_NAME} v{APP_VERSION}')
        self.root.geometry('900x620')
        self.root.minsize(820, 560)
        self.root.configure(bg=self._colors['bg'])
        self.root.option_add('*Font', self._font_ui)
        self._set_window_icon()

        self._build_ui()
        self._load_macros()
        self._start_hotkeys()

        self.root.after(120, self._drain_events)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

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
        self.lst_macros = tk.Listbox(
            left,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['accent'],
        )
        self.lst_macros.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

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
            self._append_log(message, kind)

        self.root.after(120, self._drain_events)

    def _refresh_macro_list(self) -> None:
        self.lst_macros.delete(0, tk.END)
        for idx, macro in enumerate(self._macros, start=1):
            self.lst_macros.insert(tk.END, f'{idx}. {macro.name} [{macro.hotkey}] - {len(macro.steps)} step(s)')

    def _load_macros(self) -> None:
        loaded = load_macros()
        self._macros = [WorkingMacro(name=item.name, hotkey=item.hotkey, steps=[dict(s) for s in item.steps]) for item in loaded]
        self._engine.set_macros([
            {'name': macro.name, 'hotkey': macro.hotkey, 'steps': macro.steps}
            for macro in self._macros
        ])
        self._refresh_macro_list()
        self._append_log(f'Loaded {len(self._macros)} macro(s) from config.', 'notification')

    def _save_macros(self) -> None:
        save_macros([
            MacroConfig(name=macro.name, hotkey=macro.hotkey, steps=macro.steps)
            for macro in self._macros
        ])
        self._append_log(f'Saved {len(self._macros)} macro(s) to config.', 'notification')

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
        selected = self.lst_macros.curselection()
        if not selected:
            return
        macro = self._macros[int(selected[0])]
        if not self._engine.trigger_macro(macro.name):
            self._append_log('Could not trigger selected macro.', 'ignore')

    def _describe_step(self, index: int, step: dict[str, int | str]) -> str:
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
            return f'{index}. Key {str(step.get("key", "")).strip() or "<empty>"}'
        if step_type == 'delay':
            return f'{index}. Delay {max(0, int(step.get("ms", 0)))}ms'
        if step_type == 'return_cursor':
            return f'{index}. Return Cursor'
        return f'{index}. Unknown'

    def _open_macro_editor(self) -> None:
        working = [WorkingMacro(name=m.name, hotkey=m.hotkey, steps=[dict(s) for s in m.steps]) for m in self._macros]

        dialog = tk.Toplevel(self.root)
        dialog.title('Macro Editor')
        dialog.geometry('760x520')
        dialog.configure(bg=self._colors['panel'])
        dialog.transient(self.root)
        dialog.grab_set()

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
                lst.insert(tk.END, f'{idx}. {macro.name} [{macro.hotkey}] - {len(macro.steps)} step(s)')

        def _selected_idx() -> int | None:
            sel = lst.curselection()
            if not sel:
                return None
            return int(sel[0])

        def _edit_macro(initial: WorkingMacro | None = None) -> WorkingMacro | None:
            macro = WorkingMacro(
                name=initial.name if initial else '',
                hotkey=initial.hotkey if initial else '',
                steps=[dict(s) for s in (initial.steps if initial else [])],
            )

            editor = tk.Toplevel(dialog)
            editor.title('Edit Macro' if initial else 'Add Macro')
            editor.geometry('620x470')
            editor.configure(bg=self._colors['panel'])
            editor.transient(dialog)
            editor.grab_set()

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

            tk.Label(
                body,
                text='Examples: ctrl+1, ctrl+shift+k, alt+f9',
                bg=self._colors['panel'],
                fg=self._colors['muted'],
                anchor='w',
            ).pack(fill=tk.X, pady=(2, 8))

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
                initial_step: dict[str, int | str] | None = None,
            ) -> dict[str, int | str] | None:
                resolved_button = 'left'
                initial_x = int(initial_step.get('x', 0)) if initial_step else None
                initial_y = int(initial_step.get('y', 0)) if initial_step else None
                initial_at_origin = bool(initial_step.get('at_origin', False)) if initial_step else False
                if initial_step:
                    existing_button = str(initial_step.get('button', resolved_button)).strip().lower()
                    if existing_button in {'left', 'right'}:
                        resolved_button = existing_button

                picker = tk.Toplevel(editor)
                picker.title(title)
                picker.geometry('400x250')
                picker.configure(bg=self._colors['panel'])
                picker.transient(editor)
                picker.grab_set()

                body_picker = tk.Frame(picker, bg=self._colors['panel'], padx=12, pady=12)
                body_picker.pack(fill=tk.BOTH, expand=True)

                tk.Label(
                    body_picker,
                    text='Press F2 to capture current mouse location.',
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    anchor='w',
                ).pack(fill=tk.X)

                row_button = tk.Frame(body_picker, bg=self._colors['panel'])
                row_button.pack(fill=tk.X, pady=(8, 0))
                right_click_var = tk.BooleanVar(value=(resolved_button == 'right'))
                tk.Checkbutton(
                    row_button,
                    text='Right click',
                    variable=right_click_var,
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    selectcolor=self._colors['panel_alt'],
                    activebackground=self._colors['panel'],
                    activeforeground=self._colors['text'],
                    highlightthickness=0,
                ).pack(side=tk.LEFT)

                result: dict[str, int | str | bool] = {
                    'type': 'click',
                    'x': 0,
                    'y': 0,
                    'button': resolved_button,
                    'at_origin': initial_at_origin,
                }

                use_origin_var = tk.BooleanVar(value=initial_at_origin)
                row_origin = tk.Frame(body_picker, bg=self._colors['panel'])
                row_origin.pack(fill=tk.X, pady=(8, 0))
                tk.Checkbutton(
                    row_origin,
                    text='Use original position (trigger position)',
                    variable=use_origin_var,
                    bg=self._colors['panel'],
                    fg=self._colors['text'],
                    selectcolor=self._colors['panel_alt'],
                    activebackground=self._colors['panel'],
                    activeforeground=self._colors['text'],
                    highlightthickness=0,
                ).pack(side=tk.LEFT)

                row_x = tk.Frame(body_picker, bg=self._colors['panel'])
                row_x.pack(fill=tk.X, pady=(10, 0))
                tk.Label(row_x, text='X', width=6, anchor='w', bg=self._colors['panel'], fg=self._colors['muted']).pack(side=tk.LEFT)
                ent_x = tk.Entry(row_x, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT)
                ent_x.pack(side=tk.LEFT, fill=tk.X, expand=True)

                row_y = tk.Frame(body_picker, bg=self._colors['panel'])
                row_y.pack(fill=tk.X, pady=(6, 0))
                tk.Label(row_y, text='Y', width=6, anchor='w', bg=self._colors['panel'], fg=self._colors['muted']).pack(side=tk.LEFT)
                ent_y = tk.Entry(row_y, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT)
                ent_y.pack(side=tk.LEFT, fill=tk.X, expand=True)

                if initial_x is not None:
                    ent_x.insert(0, str(initial_x))
                if initial_y is not None:
                    ent_y.insert(0, str(initial_y))

                def _capture_cursor(_event: tk.Event | None = None) -> str | None:
                    pos = get_cursor_position()
                    if pos is None:
                        messagebox.showerror('Unavailable', 'Could not read cursor position.', parent=picker)
                        return None
                    ent_x.delete(0, tk.END)
                    ent_x.insert(0, str(int(pos[0])))
                    ent_y.delete(0, tk.END)
                    ent_y.insert(0, str(int(pos[1])))
                    return 'break'

                def _save_click() -> None:
                    if use_origin_var.get():
                        x = int(initial_x or 0)
                        y = int(initial_y or 0)
                    else:
                        try:
                            x = int(ent_x.get().strip())
                            y = int(ent_y.get().strip())
                        except ValueError:
                            messagebox.showerror('Invalid click', 'X and Y must be whole numbers.', parent=picker)
                            return
                    result['x'] = x
                    result['y'] = y
                    result['button'] = 'right' if right_click_var.get() else 'left'
                    result['at_origin'] = use_origin_var.get()
                    picker.saved = True
                    picker.destroy()

                actions_picker = tk.Frame(body_picker, bg=self._colors['panel'])
                actions_picker.pack(fill=tk.X, pady=(12, 0))
                self._make_button(actions_picker, text='Capture (F2)', width=12, command=_capture_cursor, accent=True).pack(side=tk.LEFT)
                self._make_button(actions_picker, text='Save', width=10, command=_save_click).pack(side=tk.RIGHT)
                self._make_button(actions_picker, text='Cancel', width=10, command=picker.destroy).pack(side=tk.RIGHT, padx=(0, 6))

                picker.bind('<F2>', _capture_cursor)
                ent_x.bind('<F2>', _capture_cursor)
                ent_y.bind('<F2>', _capture_cursor)
                ent_x.focus_set()
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
                key_name = simpledialog.askstring('Key step', 'Key or combo (example: enter, f1, ctrl+v):', parent=editor)
                if key_name is None:
                    return
                key_name = key_name.strip()
                if not key_name:
                    messagebox.showerror('Invalid key', 'Key cannot be empty.', parent=editor)
                    return
                macro.steps.append({'type': 'key', 'key': key_name})
                _refresh_steps()

            def _add_delay() -> None:
                ms = simpledialog.askinteger('Delay step', 'Delay in milliseconds:', minvalue=0, parent=editor)
                if ms is None:
                    return
                macro.steps.append({'type': 'delay', 'ms': int(ms)})
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
                    current_key = str(step.get('key', '')).strip()
                    key_name = simpledialog.askstring(
                        'Edit key step',
                        'Key or combo (example: enter, f1, ctrl+v):',
                        initialvalue=current_key,
                        parent=editor,
                    )
                    if key_name is None:
                        return
                    key_name = key_name.strip()
                    if not key_name:
                        messagebox.showerror('Invalid key', 'Key cannot be empty.', parent=editor)
                        return
                    macro.steps[idx] = {'type': 'key', 'key': key_name}
                elif step_type == 'delay':
                    current_ms = max(0, int(step.get('ms', 0)))
                    ms = simpledialog.askinteger(
                        'Edit delay step',
                        'Delay in milliseconds:',
                        minvalue=0,
                        initialvalue=current_ms,
                        parent=editor,
                    )
                    if ms is None:
                        return
                    macro.steps[idx] = {'type': 'delay', 'ms': int(ms)}
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
                steps=[dict(step) for step in source.steps],
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

        controls = tk.Frame(frame, bg=self._colors['panel'])
        controls.pack(fill=tk.X, pady=(8, 0))
        self._make_button(controls, text='Add', width=12, command=_add_macro).pack(side=tk.LEFT)
        self._make_button(controls, text='Edit', width=12, command=_edit_selected).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls, text='Duplicate', width=12, command=_duplicate_selected).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls, text='Remove', width=12, command=_remove_selected).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls, text='Move Up', width=12, command=lambda: _move_selected(-1)).pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(controls, text='Move Down', width=12, command=lambda: _move_selected(1)).pack(side=tk.LEFT, padx=(6, 0))

        lst.bind('<Double-Button-1>', _edit_selected_event)

        footer = tk.Frame(frame, bg=self._colors['panel'])
        footer.pack(fill=tk.X, pady=(12, 0))

        def _save_all() -> None:
            self._macros = working
            self._save_macros()
            self._engine.set_macros([
                {'name': item.name, 'hotkey': item.hotkey, 'steps': item.steps}
                for item in self._macros
            ])
            self._refresh_macro_list()
            dialog.destroy()

        self._make_button(footer, text='Save', width=12, command=_save_all, accent=True).pack(side=tk.RIGHT)
        self._make_button(footer, text='Cancel', width=12, command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        _refresh_editor_list()
        dialog.wait_window()

    def _on_close(self) -> None:
        try:
            self._engine.stop()
        except Exception:
            pass
        self.root.destroy()
