import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from app_version import APP_NAME, APP_VERSION
from common_components import (
    diagnose_pointer_chain,
    log_hp_pointer_debug,
    log_sd_pointer_debug,
    open_process_for_reading,
    read_numeric_from_process,
    read_value_pointer,
    read_value_pointer_with_offset_fallback,
)
from scan_addresses import SCAN_ADDRESSES as DEFAULT_SCAN_ADDRESSES


class OutpostUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f'{APP_NAME} v{APP_VERSION}')
        self.root.geometry('760x430')
        self.root.minsize(660, 390)
        self.root.configure(bg='#111418')

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

        self.root.option_add('*Font', ('Segoe UI', 10))
        self._setup_theme()

        self.scan_addresses_path = Path(__file__).resolve().parent.parent / 'configs' / 'scan_addresses.user.json'
        self.saved_scan_addresses = self._load_scan_addresses()

        self._position_popup_at_main_window = self._position_popup_center

        self.pid: int | None = None
        self.handle: int | None = None
        self.hp_module = 'GameAssembly.dll'
        self.hp_base = '0x054A3188'
        self.hp_offsets = ['0xB8', '0x0', '0x210', '0x1B0', '0x28', '0x80', '0x3C']
        self.sd_module = 'GameAssembly.dll'
        self.sd_base = '0x054C6AA0'
        self.sd_offsets = ['0xD0', '0xB8', '0x0', '0x210', '0x1B8', '0x20', '0x4C']

        self.is_test_running = False
        self.test_thread = None
        self.test_start_time = None
        self.last_hp = None
        self.last_sd = None
        self.hp_total_value = 0
        self.sd_total_value = 0
        self.hp_meter_canvas = None
        self.hp_meter_fill_id = None
        self.sd_meter_canvas = None
        self.sd_meter_fill_id = None

        self._build_ui()

    def _setup_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        style.configure(
            'Dark.TEntry',
            fieldbackground=self._colors['input_bg'],
            foreground=self._colors['text'],
            bordercolor=self._colors['border'],
            lightcolor=self._colors['border'],
            darkcolor=self._colors['border'],
            padding=(6, 4),
        )

    def _position_popup_center(self, popup: tk.Misc, size: str | None = None) -> None:
        """Center a popup over the parent root window."""
        self.root.update_idletasks()
        popup.update_idletasks()

        if size:
            try:
                w_str, h_str = size.split('x', 1)
                popup_w = int(w_str)
                popup_h = int(h_str)
            except (TypeError, ValueError):
                popup_w = popup.winfo_reqwidth() or 480
                popup_h = popup.winfo_reqheight() or 340
        else:
            popup_w = popup.winfo_reqwidth() or 480
            popup_h = popup.winfo_reqheight() or 340

        if self.root.winfo_viewable():
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()
            x = root_x + (root_w - popup_w) // 2
            y = root_y + (root_h - popup_h) // 2
        else:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = (screen_w - popup_w) // 2
            y = (screen_h - popup_h) // 2

        x = max(0, x)
        y = max(0, y)

        if size:
            popup.geometry(f'{size}+{x}+{y}')
        else:
            popup.geometry(f'+{x}+{y}')

    def _load_scan_addresses(self):
        try:
            entries = DEFAULT_SCAN_ADDRESSES if isinstance(DEFAULT_SCAN_ADDRESSES, list) else []
            if self.scan_addresses_path.exists():
                payload = json.loads(self.scan_addresses_path.read_text(encoding='utf-8'))
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
                    is_absolute_root = False
                    try:
                        base_value = int(base_offset.replace('0x', '').replace('0X', ''), 16)
                        is_absolute_root = base_value > 0x100000000
                    except ValueError:
                        is_absolute_root = False

                    if (module or is_absolute_root) and offsets:
                        result.append({
                            'name': name,
                            'type': 'pointer',
                            'module': module,
                            'base_offset': base_offset,
                            'offsets': offsets,
                            'description': desc,
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
            self.scan_addresses_path.parent.mkdir(parents=True, exist_ok=True)
            self.scan_addresses_path.write_text(
                json.dumps({'addresses': self.saved_scan_addresses}, indent=2, ensure_ascii=True) + '\n',
                encoding='utf-8',
            )
            return True
        except Exception as exc:
            messagebox.showerror('Save failed', f'Could not save addresses:\n{exc}', parent=self.root)
            return False

    def _pick_scan_address(self, var: tk.StringVar) -> None:
        if not self.saved_scan_addresses:
            messagebox.showinfo('No addresses', 'No saved addresses yet. Use the address button to add some.', parent=self.root)
            return

        dlg = tk.Toplevel(self.root)
        dlg.title('Pick Address')
        self._position_popup_center(dlg, '480x320')
        dlg.minsize(380, 260)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text='Select a saved address:', font=('Segoe UI', 10), bg=self._colors['bg'], fg=self._colors['text']).pack(anchor=tk.W, padx=12, pady=(12, 6))

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        lb = tk.Listbox(
            list_frame,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            activestyle='none',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
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
            var.set(self.saved_scan_addresses[sel[0]]['name'])
            dlg.destroy()

        self._make_button(btn_row, text='Select', width=12, command=_confirm, accent=True).pack(side=tk.LEFT, padx=(0, 8))
        self._make_button(btn_row, text='Cancel', width=10, command=dlg.destroy).pack(side=tk.LEFT)

        lb.bind('<Double-Button-1>', lambda _e: _confirm())
        dlg.bind('<Return>', lambda _e: _confirm())
        dlg.bind('<Escape>', lambda _e: dlg.destroy())
        self.root.wait_window(dlg)

    def _open_scan_address_manager(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('Manage Addresses')
        self._position_popup_center(dlg, '620x520')
        dlg.minsize(520, 440)
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors['bg'])
        dlg.transient(self.root)
        dlg.grab_set()

        list_frame = tk.Frame(dlg, bg=self._colors['bg'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 6))

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        lb = tk.Listbox(
            list_frame,
            bg=self._colors['input_bg'],
            fg=self._colors['text'],
            selectbackground=self._colors['accent'],
            selectforeground='#ffffff',
            activestyle='none',
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors['border'],
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

        type_frame = tk.Frame(dlg, bg=self._colors['bg'])
        type_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        tk.Label(type_frame, text='Type:', bg=self._colors['bg'], fg=self._colors['muted'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
        type_var = tk.StringVar(value='pointer')
        for val, lbl in [('static', 'Static address'), ('pointer', 'Pointer chain')]:
            tk.Radiobutton(
                type_frame,
                text=lbl,
                variable=type_var,
                value=val,
                bg=self._colors['bg'],
                fg=self._colors['text'],
                selectcolor=self._colors['input_bg'],
                activebackground=self._colors['bg'],
                font=('Segoe UI', 9),
            ).pack(side=tk.LEFT, padx=(8, 0))

        fields_frame = tk.Frame(dlg, bg=self._colors['bg'])
        fields_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        name_var = tk.StringVar()
        desc_var = tk.StringVar()
        static_addr_var = tk.StringVar()
        module_var = tk.StringVar()
        base_offset_var = tk.StringVar(value='0x0')
        offsets_var = tk.StringVar()

        def _row(parent):
            f = tk.Frame(parent, bg=self._colors['bg'])
            f.pack(fill=tk.X, pady=2)
            return f

        r_name = _row(fields_frame)
        tk.Label(r_name, text='Name:', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_name, textvariable=name_var, width=28, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_desc = _row(fields_frame)
        tk.Label(r_desc, text='Description:', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_desc, textvariable=desc_var, width=28, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_addr = _row(fields_frame)
        tk.Label(r_addr, text='Address (hex):', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_addr, textvariable=static_addr_var, width=28, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_mod = _row(fields_frame)
        tk.Label(r_mod, text='Module:', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_mod, textvariable=module_var, width=28, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        r_base = _row(fields_frame)
        tk.Label(r_base, text='Base offset:', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_base, textvariable=base_offset_var, width=12, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0))

        r_off = _row(fields_frame)
        tk.Label(r_off, text='Offsets:', bg=self._colors['bg'], fg=self._colors['muted'], width=12, anchor=tk.E).pack(side=tk.LEFT)
        tk.Entry(r_off, textvariable=offsets_var, width=28, bg=self._colors['input_bg'], fg=self._colors['text'], insertbackground=self._colors['text'], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'], highlightcolor=self._colors['accent']).pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        tk.Label(r_off, text='(hex, comma-separated)', bg=self._colors['bg'], fg=self._colors['muted'], font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=(6, 0))

        def _toggle_fields():
            is_ptr = type_var.get() == 'pointer'
            if is_ptr:
                r_addr.pack_forget()
                r_mod.pack(fill=tk.X, pady=2)
                r_base.pack(fill=tk.X, pady=2)
                r_off.pack(fill=tk.X, pady=2)
            else:
                r_mod.pack_forget(); r_base.pack_forget(); r_off.pack_forget()
                r_addr.pack(fill=tk.X, pady=2)

        _toggle_fields()
        type_var.trace_add('write', lambda *_: _toggle_fields())

        def _build_entry():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning('Missing name', 'Name is required.', parent=dlg)
                return None
            if type_var.get() == 'pointer':
                module = module_var.get().strip()
                base_off = base_offset_var.get().strip() or '0x0'
                raw = [o.strip() for o in offsets_var.get().split(',') if o.strip()]
                if not raw:
                    messagebox.showwarning('Missing fields', 'Offsets are required for pointer type.', parent=dlg)
                    return None
                return {'name': name, 'type': 'pointer', 'module': module, 'base_offset': base_off, 'offsets': raw, 'description': desc_var.get().strip()}
            addr = static_addr_var.get().strip()
            if not addr:
                messagebox.showwarning('Missing address', 'Address is required.', parent=dlg)
                return None
            return {'name': name, 'type': 'static', 'address': addr, 'description': desc_var.get().strip()}

        btn_row = tk.Frame(dlg, bg=self._colors['bg'])
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))

        def _add():
            entry = _build_entry()
            if entry is None:
                return
            self.saved_scan_addresses.append(entry)
            if self._save_scan_addresses():
                _refresh()

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

        def _delete():
            sel = lb.curselection()
            if not sel:
                return
            entry = self.saved_scan_addresses[sel[0]]
            if not messagebox.askyesno('Delete', f'Delete "{entry["name"]}"?', parent=dlg):
                return
            del self.saved_scan_addresses[sel[0]]
            if self._save_scan_addresses():
                _refresh()

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
                base_offset_var.set('0x0')
                offsets_var.set('')
            _toggle_fields()

        lb.bind('<<ListboxSelect>>', _on_select)

        self._make_button(btn_row, text='Add', width=8, command=_add).pack(side=tk.LEFT, padx=(0, 8))
        self._make_button(btn_row, text='Update', width=8, command=_update).pack(side=tk.LEFT, padx=(0, 8))
        self._make_button(btn_row, text='Delete', width=8, command=_delete).pack(side=tk.LEFT, padx=(0, 8))
        self._make_button(btn_row, text='Close', width=8, command=dlg.destroy).pack(side=tk.LEFT)

        dlg.bind('<Escape>', lambda _e: dlg.destroy())
        self.root.wait_window(dlg)

    def _build_ui(self):
        self.root.configure(bg=self._colors['bg'])

        outer = tk.Frame(self.root, bg=self._colors['bg'])
        outer.pack(fill='both', expand=True, padx=18, pady=18)

        title = tk.Label(
            outer,
            text='Outpost',
            font=('Segoe UI Semibold', 17),
            bg=self._colors['bg'],
            fg=self._colors['text'],
        )
        title.pack(anchor='w', pady=(0, 10))

        status_panel = tk.Frame(outer, bg=self._colors['panel'], bd=1, relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'])
        status_panel.pack(fill='x', pady=(0, 10))

        status_inner = tk.Frame(status_panel, bg=self._colors['panel'])
        status_inner.pack(fill='x', padx=10, pady=(8, 10))

        process_row = tk.Frame(status_inner, bg=self._colors['panel'])
        process_row.pack(fill='x')

        self.pid_var = tk.StringVar(value='Not attached')
        pid_label = tk.Label(process_row, textvariable=self.pid_var, bg=self._colors['panel'], fg=self._colors['text'], font=('Segoe UI Semibold', 12), anchor='w')
        pid_label.pack(side='left', fill='x', expand=True)

        self.attach_button = tk.Button(
            process_row,
            text='Attach',
            command=self.attach_to_process,
            width=10,
            relief=tk.FLAT,
            bd=0,
            cursor='hand2',
            padx=8,
            pady=5,
            bg='#2a3340',
            fg='#dfe7f3',
            activebackground='#3b4656',
            activeforeground='#ffffff',
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor='#4c5c72',
        )
        self.attach_button.pack(side='right', padx=(8, 0))

        controls_frame = tk.Frame(outer, bg=self._colors['bg'])
        controls_frame.pack(fill='x', pady=(0, 10))

        self.test_button = tk.Button(
            controls_frame,
            text='Start Test',
            command=self.toggle_test,
            width=16,
            relief=tk.FLAT,
            bd=0,
            cursor='hand2',
            padx=8,
            pady=8,
            bg=self._colors['success'],
            fg='#ffffff',
            activebackground='#1f8f58',
            activeforeground='#ffffff',
            highlightthickness=1,
            highlightbackground=self._colors['border'],
            highlightcolor=self._colors['success'],
        )
        self.test_button.pack(anchor='w')
        self._set_test_button_state(False)

        self._create_meter_card_stack(outer)

        self.hp_module_var = tk.StringVar(value='GameAssembly.dll')
        self.hp_base_var = tk.StringVar(value='0x054A3188')
        self.hp_offsets_var = tk.StringVar(value='0xB8, 0x0, 0x210, 0x1B0, 0x28, 0x80, 0x3C')
        self.sd_module_var = tk.StringVar(value='GameAssembly.dll')
        self.sd_base_var = tk.StringVar(value='0x054C6AA0')
        self.sd_offsets_var = tk.StringVar(value='0xD0, 0xB8, 0x0, 0x210, 0x1B8, 0x20, 0x4C')

        self.result_var = tk.StringVar(value='Ready')
        tk.Label(outer, textvariable=self.result_var, bg=self._colors['bg'], fg=self._colors['muted'], justify='left', wraplength=560).pack(anchor='w', pady=(12, 0))

    def _create_meter_card_stack(self, parent):
        values_frame = tk.Frame(parent, bg=self._colors['bg'])
        values_frame.pack(fill='x', pady=(4, 10))
        values_frame.grid_columnconfigure(0, weight=1, uniform='meter_cards')
        values_frame.grid_columnconfigure(1, weight=1, uniform='meter_cards')

        self.hp_var = tk.StringVar(value='0')
        self.hp_total_var = tk.StringVar(value='/ 0')
        self.hp_damage_list_var = tk.StringVar(value='')
        self.sd_var = tk.StringVar(value='0')
        self.sd_total_var = tk.StringVar(value='/ 0')
        self.sd_damage_list_var = tk.StringVar(value='')

        self.hp_stats_labels = {}
        self.sd_stats_labels = {}

        self.hp_meter_canvas, self.hp_meter_fill_id = self._build_meter_card(values_frame, 'HP', '#d94a4a', self.hp_var, self.hp_total_var, self.hp_stats_labels, 0)
        self.sd_meter_canvas, self.sd_meter_fill_id = self._build_meter_card(values_frame, 'SD', '#f4d35e', self.sd_var, self.sd_total_var, self.sd_stats_labels, 1)

        self._set_damage_table_state('hp', {'Bypass': {'total': 0.0, 'dps': 0.0, 'time': 0.0}, 'Direct': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
        self._set_damage_table_state('sd', {'SD': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})

    def _build_meter_card(self, parent, title, accent_color, value_var, total_var, table_store, column=0):
        card = tk.Frame(parent, bg='#1a2129', bd=1, relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors['border'])
        card.grid(row=0, column=column, sticky='nsew', padx=(0, 8) if column == 0 else (0, 0))
        parent.grid_rowconfigure(0, weight=1)

        header = tk.Frame(card, bg=card['bg'])
        header.pack(fill='x', padx=12, pady=(10, 6))
        tk.Label(header, text=title, bg=card['bg'], fg='#ebf0f7', font=('Segoe UI Semibold', 11)).pack(side='left')
        tk.Label(header, textvariable=total_var, bg=card['bg'], fg=self._colors['muted'], font=('Segoe UI', 10)).pack(side='right')

        value_label = tk.Label(card, textvariable=value_var, bg=card['bg'], fg='#f4f7fb', font=('Segoe UI Semibold', 24))
        value_label.pack(anchor='w', padx=12, pady=(0, 6))

        meter = tk.Canvas(card, width=260, height=18, bg='#0d1217', highlightthickness=1, highlightbackground=self._colors['border'])
        meter.pack(fill='x', padx=12, pady=(0, 8))
        meter_fill_id = meter.create_rectangle(0, 0, 0, 18, fill=accent_color, outline='')
        self._update_meter(meter, meter_fill_id, 0, 100)

        stats_table = tk.Frame(card, bg=card['bg'])
        stats_table.pack(fill='x', padx=12, pady=(0, 12))

        header_row = tk.Frame(stats_table, bg=card['bg'])
        header_row.pack(fill='x', pady=(0, 6))
        tk.Label(header_row, text='Type', bg=card['bg'], fg=self._colors['muted'], font=('Segoe UI Semibold', 9), width=12, anchor='w').grid(row=0, column=0, sticky='w')
        tk.Label(header_row, text='Total', bg=card['bg'], fg=self._colors['muted'], font=('Segoe UI Semibold', 9), width=8, anchor='center').grid(row=0, column=1)
        tk.Label(header_row, text='DPS', bg=card['bg'], fg=self._colors['muted'], font=('Segoe UI Semibold', 9), width=8, anchor='center').grid(row=0, column=2)
        tk.Label(header_row, text='Time', bg=card['bg'], fg=self._colors['muted'], font=('Segoe UI Semibold', 9), width=8, anchor='center').grid(row=0, column=3)
        header_row.grid_columnconfigure(0, weight=2)
        header_row.grid_columnconfigure(1, weight=1)
        header_row.grid_columnconfigure(2, weight=1)
        header_row.grid_columnconfigure(3, weight=1)

        rows = {}
        if title == 'HP':
            rows = {'Bypass': {'total': None, 'dps': None, 'time': None}, 'Direct': {'total': None, 'dps': None, 'time': None}}
        else:
            rows = {'SD': {'total': None, 'dps': None, 'time': None}}

        for name in rows.keys():
            row_frame = tk.Frame(stats_table, bg=card['bg'])
            row_frame.pack(fill='x', pady=2)
            tk.Label(row_frame, text=name, bg=card['bg'], fg='#e7edf7', font=('Segoe UI', 9), width=12, anchor='w').grid(row=0, column=0, sticky='w')
            total_label = tk.Label(row_frame, text='0', bg=card['bg'], fg='#f4f7fb', font=('Segoe UI', 9), width=8, anchor='center')
            dps_label = tk.Label(row_frame, text='0.00', bg=card['bg'], fg='#f4f7fb', font=('Segoe UI', 9), width=8, anchor='center')
            time_label = tk.Label(row_frame, text='0.00s', bg=card['bg'], fg='#f4f7fb', font=('Segoe UI', 9), width=8, anchor='center')
            total_label.grid(row=0, column=1)
            dps_label.grid(row=0, column=2)
            time_label.grid(row=0, column=3)
            rows[name] = {'total': total_label, 'dps': dps_label, 'time': time_label}

        table_store.update(rows)
        return meter, meter_fill_id

    def _update_meter(self, canvas, fill_id, current_value, total_value):
        canvas.update_idletasks()
        width = max(1, canvas.winfo_width() - 2)
        if total_value is None or total_value <= 0:
            percent = 0.0
        else:
            percent = max(0.0, min(1.0, float(current_value) / float(total_value)))
        draw_width = max(0, int(width * percent))
        canvas.coords(fill_id, 1, 1, 1 + draw_width, canvas.winfo_height() - 1)

    def attach_to_process(self):
        import psutil

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

        try:
            pid_titles = _get_window_titles_by_pid()
            candidates = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    info = proc.info
                    name = (info.get('name') or '').strip()
                    if not name or 'megamu' not in name.lower():
                        continue
                    pid = int(info.get('pid') or 0)
                    display_name = (pid_titles.get(pid, [])[0] if pid_titles.get(pid) else name)
                    candidates.append((display_name, name, pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            candidates = []

        if not candidates:
            messagebox.showwarning('No processes', 'No MEGAMU processes found to attach to.', parent=self.root)
            return

        sorted_candidates = sorted(candidates, key=lambda item: (item[0].lower(), item[2]))

        choice = tk.Toplevel(self.root)
        choice.title('Select Process')
        choice.configure(bg='#111418')
        choice.transient(self.root)
        choice.grab_set()
        self._position_popup_center(choice, '420x200')

        listbox = tk.Listbox(choice, bg='#0f1318', fg='#e7ecf3', selectbackground='#2f81f7', highlightthickness=0, relief=tk.FLAT, width=42, height=8)
        for display_name, _exe_name, pid in sorted_candidates:
            listbox.insert(tk.END, f'{display_name}  (PID {pid})')
        listbox.pack(fill='both', expand=True, padx=12, pady=(12, 12))

        def _confirm():
            index = listbox.curselection()
            if not index:
                messagebox.showwarning('No selection', 'Choose a process first.', parent=choice)
                return
            display_name, _exe_name, pid = sorted_candidates[index[0]]
            handle = open_process_for_reading(pid)
            if handle is None:
                messagebox.showerror('Attach failed', f'Could not open process {display_name} (PID {pid}).', parent=choice)
                return
            self.pid = pid
            self.handle = handle
            self.pid_var.set(f'{display_name} [{pid}]')
            self.result_var.set('Attached and ready to monitor.')
            choice.destroy()

        listbox.bind('<Double-Button-1>', lambda _e: _confirm())
        listbox.bind('<Return>', lambda _e: _confirm())

    def _read_pointer_value(self, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> int | None:
        if self.handle is None or self.pid is None:
            return None
        return read_value_pointer_with_offset_fallback(self.handle, module_name, base_offset_hex, offsets_hex)

    def _set_test_button_state(self, running: bool) -> None:
        if not hasattr(self, 'test_button') or self.test_button is None:
            return
        if running:
            self.test_button.config(
                text='Stop Test',
                bg='#d94a4a',
                activebackground='#b93a3a',
                highlightcolor='#d94a4a',
                fg='#ffffff',
            )
        else:
            self.test_button.config(
                text='Start Test',
                bg=self._colors['success'],
                activebackground='#1f8f58',
                highlightcolor=self._colors['success'],
                fg='#ffffff',
            )

    def _set_damage_table_state(self, kind, values):
        target = self.hp_stats_labels if kind == 'hp' else self.sd_stats_labels
        for name, metrics in values.items():
            label_group = target.get(name)
            if not isinstance(label_group, dict):
                continue
            total_label = label_group.get('total')
            dps_label = label_group.get('dps')
            time_label = label_group.get('time')
            if isinstance(total_label, tk.Label):
                total_label.config(text=f"{float(metrics.get('total', 0.0)):.0f}")
            if isinstance(dps_label, tk.Label):
                dps_label.config(text=f"{float(metrics.get('dps', 0.0)):.2f}")
            if isinstance(time_label, tk.Label):
                time_label.config(text=f"{float(metrics.get('time', 0.0)):.2f}s")

    def _clear_test_state(self):
        self.is_test_running = False
        self.test_start_time = None
        self.hp_start_total = 0
        self.sd_start_total = 0
        self.hp_total_value = 0
        self.sd_total_value = 0
        self._set_damage_table_state('hp', {'Bypass': {'total': 0.0, 'dps': 0.0, 'time': 0.0}, 'Direct': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
        self._set_damage_table_state('sd', {'SD': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
        self.hp_total_var.set('/ 0')
        self.sd_total_var.set('/ 0')
        self.hp_var.set('0')
        self.sd_var.set('0')
        self.result_var.set('Ready')

    def _refresh_values(self):
        if self.handle is None:
            return

        hp_module = self.hp_module_var.get().strip() or 'GameAssembly.dll'
        hp_base = self.hp_base_var.get().strip() or '0x054A3188'
        hp_offsets = [part.strip() for part in self.hp_offsets_var.get().split(',') if part.strip()]
        if not hp_offsets:
            hp_offsets = ['0xB8', '0x0', '0x210', '0x1B0', '0x28', '0x80', '0x3C']

        sd_module = self.sd_module_var.get().strip() or 'GameAssembly.dll'
        sd_base = self.sd_base_var.get().strip() or '0x054C6AA0'
        sd_offsets = [part.strip() for part in self.sd_offsets_var.get().split(',') if part.strip()]
        if not sd_offsets:
            sd_offsets = ['0xD0', '0xB8', '0x0', '0x210', '0x1B8', '0x20', '0x4C']

        hp_value = self._read_pointer_value(hp_module, hp_base, hp_offsets)
        if hp_value is None:
            print(f"[Outpost UI] HP read failed for module={hp_module} base={hp_base} offsets={hp_offsets}")
            print(f"[Outpost UI] HP diag: {diagnose_pointer_chain(self.handle, hp_module, hp_base, hp_offsets)}")
            log_hp_pointer_debug(self.handle, hp_module, hp_base, hp_offsets)
            hp_value = 0
        sd_value = self._read_pointer_value(sd_module, sd_base, sd_offsets)
        if sd_value is None:
            print(f"[Outpost UI] SD read failed for module={sd_module} base={sd_base} offsets={sd_offsets}")
            print(f"[Outpost UI] SD diag: {diagnose_pointer_chain(self.handle, sd_module, sd_base, sd_offsets)}")
            log_sd_pointer_debug(self.handle, sd_module, sd_base, sd_offsets)
            sd_value = 0

        hp_total = max(0, int(hp_value))
        sd_total = max(0, int(sd_value))

        if self.is_test_running:
            start_hp_total = getattr(self, 'hp_start_total', None)
            start_sd_total = getattr(self, 'sd_start_total', None)
            if start_hp_total is not None:
                hp_total_label = start_hp_total
            else:
                hp_total_label = hp_total
            if start_sd_total is not None:
                sd_total_label = start_sd_total
            else:
                sd_total_label = sd_total
        else:
            hp_total_label = hp_total
            sd_total_label = sd_total

        self.hp_total_value = max(0, int(hp_total_label))
        self.sd_total_value = max(0, int(sd_total_label))

        self.hp_var.set(str(hp_total))
        self.sd_var.set(str(sd_total))
        self.hp_total_var.set(f'/ {hp_total_label}')
        self.sd_total_var.set(f'/ {sd_total_label}')

        hp_bar_total = self.hp_total_value if self.hp_total_value > 0 else max(1, hp_total)
        sd_bar_total = self.sd_total_value if self.sd_total_value > 0 else max(1, sd_total)
        self._update_meter(self.hp_meter_canvas, self.hp_meter_fill_id, max(0, hp_total), hp_bar_total)
        self._update_meter(self.sd_meter_canvas, self.sd_meter_fill_id, max(0, sd_total), sd_bar_total)

        if getattr(self, 'test_start_time', None) is not None and self.is_test_running:
            duration = max(0.001, __import__('time').time() - self.test_start_time)
            hp_dps = max(0.0, max(0, hp_total - int(self.hp_var.get())) / duration) if False else 0.0
            sd_dps = max(0.0, max(0, sd_total - int(self.sd_var.get())) / duration) if False else 0.0
            self._set_damage_table_state('hp', {'Bypass': {'total': 0.0, 'dps': 0.0, 'time': 0.0}, 'Direct': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
            self._set_damage_table_state('sd', {'SD': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
        else:
            self._set_damage_table_state('hp', {'Bypass': {'total': 0.0, 'dps': 0.0, 'time': 0.0}, 'Direct': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})
            self._set_damage_table_state('sd', {'SD': {'total': 0.0, 'dps': 0.0, 'time': 0.0}})

    def toggle_test(self):
        if self.is_test_running:
            self.is_test_running = False
            if self.test_thread is not None and self.test_thread.is_alive():
                self.test_thread.join(timeout=0.2)
            self._clear_test_state()
            self._set_test_button_state(False)
            self.start_test()
            return

        self.start_test()

    def start_test(self):
        if self.handle is None or self.pid is None:
            messagebox.showwarning('Not attached', 'Attach to a process before starting the test.', parent=self.root)
            return

        if self.is_test_running:
            messagebox.showinfo('Test running', 'A test is already running.', parent=self.root)
            return

        self.is_test_running = True
        self._set_test_button_state(True)
        self.result_var.set('Running...')
        self.test_start_time = None
        self.last_hp = None
        self.last_sd = None

        self._refresh_values()
        try:
            self.hp_start_total = max(0, int(self.hp_var.get()))
            self.sd_start_total = max(0, int(self.sd_var.get()))
        except Exception:
            self.hp_start_total = 0
            self.sd_start_total = 0

        self.hp_total_var.set(f'/ {self.hp_start_total}')
        self.sd_total_var.set(f'/ {self.sd_start_total}')

        self.test_thread = __import__('threading').Thread(target=self._run_test_loop, daemon=True)
        self.test_thread.start()

    def _run_test_loop(self):
        import time

        self.test_start_time = time.time()
        start_hp = None
        start_sd = None

        hp_bypass_started = False
        hp_bypass_start_time = None
        hp_bypass_total_damage = 0.0
        last_bypass_dps = 0.0

        sd_started = False
        sd_start_time = None
        sd_total_damage = 0.0
        last_sd_dps = 0.0

        hp_direct_started = False
        hp_direct_start_time = None
        hp_direct_total_damage = 0.0
        last_direct_dps = 0.0

        hp_done = False
        sd_done = False
        last_hp_value = None
        last_sd_value = None

        while self.is_test_running:
            try:
                self._refresh_values()
                hp_value = int(self.hp_var.get())
                sd_value = int(self.sd_var.get())
            except Exception:
                hp_value = 0
                sd_value = 0

            if start_hp is None:
                start_hp = hp_value
                last_hp_value = hp_value
            if start_sd is None:
                start_sd = sd_value
                last_sd_value = sd_value

            if not hp_done and not sd_done and start_hp > 0 and hp_value < last_hp_value:
                delta = max(0, last_hp_value - hp_value)
                if not hp_bypass_started:
                    hp_bypass_started = True
                    hp_bypass_start_time = time.time()
                    hp_bypass_total_damage = 0.0
                hp_bypass_total_damage += delta
                elapsed = max(0.001, time.time() - hp_bypass_start_time)
                last_bypass_dps = hp_bypass_total_damage / elapsed

            if not sd_done and start_sd > 0 and sd_value < last_sd_value:
                delta = max(0, last_sd_value - sd_value)
                if not sd_started:
                    sd_started = True
                    sd_start_time = time.time()
                    sd_total_damage = 0.0
                sd_total_damage += delta
                elapsed = max(0.001, time.time() - sd_start_time)
                last_sd_dps = sd_total_damage / elapsed

            if not sd_done and start_sd > 0 and sd_value <= 0:
                sd_done = True
                if sd_started:
                    last_sd_dps = sd_total_damage / max(0.001, time.time() - (sd_start_time or time.time()))
                if hp_bypass_started:
                    last_bypass_dps = hp_bypass_total_damage / max(0.001, time.time() - (hp_bypass_start_time or time.time()))
                    hp_bypass_started = False
                if not hp_done and hp_value > 0:
                    hp_direct_started = True
                    if hp_direct_start_time is None:
                        hp_direct_start_time = time.time()
                    hp_direct_total_damage = 0.0

            if sd_done and not hp_done and start_hp > 0 and hp_value < last_hp_value:
                delta = max(0, last_hp_value - hp_value)
                if not hp_direct_started:
                    hp_direct_started = True
                    hp_direct_start_time = time.time()
                    hp_direct_total_damage = 0.0
                hp_direct_total_damage += delta
                elapsed = max(0.001, time.time() - hp_direct_start_time)
                last_direct_dps = hp_direct_total_damage / elapsed

            if hp_value <= 0 and start_hp > 0 and not hp_done:
                hp_done = True
                if hp_direct_started:
                    last_direct_dps = hp_direct_total_damage / max(0.001, time.time() - (hp_direct_start_time or time.time()))

            bypass_time = max(0.0, (time.time() - hp_bypass_start_time) if hp_bypass_started and hp_bypass_start_time is not None else 0.0)
            direct_time = max(0.0, (time.time() - hp_direct_start_time) if hp_direct_started and hp_direct_start_time is not None else 0.0)
            sd_time = max(0.0, (time.time() - sd_start_time) if sd_started and sd_start_time is not None else 0.0)

            hp_bypass_total = hp_bypass_total_damage if hp_bypass_started else 0.0
            hp_direct_total = hp_direct_total_damage if hp_direct_started else 0.0
            self._set_damage_table_state('hp', {
                'Bypass': {'total': hp_bypass_total, 'dps': last_bypass_dps, 'time': bypass_time},
                'Direct': {'total': hp_direct_total, 'dps': last_direct_dps, 'time': direct_time},
            })
            self._set_damage_table_state('sd', {'SD': {'total': sd_total_damage, 'dps': last_sd_dps, 'time': sd_time}})

            last_hp_value = hp_value
            last_sd_value = sd_value

            if hp_done and sd_done:
                self.is_test_running = False
                self._set_test_button_state(False)
                self.result_var.set(
                    f'HP reached 0. Bypass: {last_bypass_dps:.2f} | Direct: {last_direct_dps:.2f} | '
                    f'SD reached 0. SD DPS: {last_sd_dps:.2f}'
                )
                return

            time.sleep(0.05)

    def _finish_test(self, resource_name: str, start_value: int, start_time: float | None):
        import time

        try:
            end_value = int(float(self.hp_var.get())) if resource_name == 'HP' else int(float(self.sd_var.get()))
        except Exception:
            end_value = 0

        duration = max(0.001, time.time() - (start_time or time.time()))
        if start_value <= 0:
            dps = 0.0
        else:
            dps = max(0.0, (start_value - end_value) / duration)

        if resource_name == 'HP':
            self.hp_dps_var.set(f'HP DPS: {dps:.2f}')
            label = f'HP reached 0. HP DPS: {dps:.2f}'
        else:
            self.sd_dps_var.set(f'SD DPS: {dps:.2f}')
            label = f'SD reached 0. SD DPS: {dps:.2f}'

        self.is_test_running = False
        self.result_var.set(label)

    def run(self):
        self.root.mainloop()


def main():
    app = OutpostUI()
    app.run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[INFO] Outpost stopped')
    except Exception as exc:
        print(f'[ERROR] {exc}')
        sys.exit(1)
