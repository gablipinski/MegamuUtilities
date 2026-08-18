"""Windows utility for recalculating a known module-relative address."""

import ast
import ctypes
import os
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

import psutil


APP_TITLE = 'Pointer Relocator'
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
LIST_MODULES_ALL = 0x03


def parse_hex_address(raw: str) -> int:
    """Parse a non-negative hexadecimal address entered by the user."""
    value = int(raw.strip().lower().removeprefix('0x'), 16)
    if value < 0:
        raise ValueError('Address cannot be negative.')
    return value


def rebase_address(previous_address: int, previous_module_base: int, current_module_base: int) -> int:
    """Apply the previous module-relative offset to the module's current base."""
    if previous_address < previous_module_base:
        raise ValueError('Previous address must be inside or above the previous module base.')
    return current_module_base + (previous_address - previous_module_base)


def open_process_for_reading(pid: int) -> int | None:
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    return int(handle) if handle else None


def get_module_base(handle: int, module_name: str) -> int | None:
    """Return the base address for a loaded module, matched by filename."""
    psapi = ctypes.WinDLL('psapi', use_last_error=True)

    class ModuleInfo(ctypes.Structure):
        _fields_ = [
            ('base_address', ctypes.c_void_p),
            ('image_size', ctypes.c_uint32),
            ('entry_point', ctypes.c_void_p),
        ]

    psapi.EnumProcessModulesEx.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
    ]
    psapi.EnumProcessModulesEx.restype = ctypes.c_int
    psapi.GetModuleBaseNameW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    psapi.GetModuleBaseNameW.restype = ctypes.c_uint32
    psapi.GetModuleInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ModuleInfo), ctypes.c_uint32,
    ]
    psapi.GetModuleInformation.restype = ctypes.c_int

    requested_name = os.path.basename(module_name.strip().strip('"').strip("'")).lower()
    if not requested_name:
        return None

    modules = (ctypes.c_void_p * 1024)()
    bytes_needed = ctypes.c_uint32(0)
    if not psapi.EnumProcessModulesEx(
        ctypes.c_void_p(handle), modules, ctypes.sizeof(modules), ctypes.byref(bytes_needed), LIST_MODULES_ALL
    ):
        return None

    module_count = min(bytes_needed.value // ctypes.sizeof(ctypes.c_void_p), len(modules))
    for index in range(module_count):
        module = modules[index]
        name_buffer = ctypes.create_unicode_buffer(260)
        if not psapi.GetModuleBaseNameW(ctypes.c_void_p(handle), module, name_buffer, len(name_buffer)):
            continue
        if name_buffer.value.lower() != requested_name:
            continue
        module_info = ModuleInfo()
        if psapi.GetModuleInformation(
            ctypes.c_void_p(handle), module, ctypes.byref(module_info), ctypes.sizeof(module_info)
        ):
            return int(module_info.base_address)
    return None


def read_pointer_sized_value(handle: int, address: int) -> int | None:
    """Read an unsigned pointer-sized value from a process address."""
    pointer_type = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
    value = pointer_type()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        ctypes.c_void_p(handle), ctypes.c_void_p(address), ctypes.byref(value),
        ctypes.sizeof(value), ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(value):
        return int(value.value)
    return None


def read_uint32(handle: int, address: int) -> int | None:
    """Read a 32-bit unsigned value, suitable for validating a pointer target."""
    value = ctypes.c_uint32()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        ctypes.c_void_p(handle), ctypes.c_void_p(address), ctypes.byref(value),
        ctypes.sizeof(value), ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(value):
        return int(value.value)
    return None


def nearby_offsets(original_offset: int, search_radius: int, alignment: int = 8) -> list[int]:
    """Return non-negative offsets ordered from closest to the saved offset."""
    if search_radius < 0 or alignment <= 0:
        raise ValueError('Search radius and alignment must be positive.')
    candidates = [original_offset]
    for delta in range(alignment, search_radius + 1, alignment):
        for candidate in (original_offset - delta, original_offset + delta):
            if candidate >= 0:
                candidates.append(candidate)
    return candidates


@dataclass(frozen=True)
class ProcessChoice:
    pid: int
    name: str
    executable: str

    @property
    def label(self) -> str:
        return f'{self.name} (PID {self.pid})'


@dataclass(frozen=True)
class SavedPointer:
    app_name: str
    source_path: Path
    name: str
    module: str
    base_offset: str
    offsets: tuple[str, ...]
    description: str

    @property
    def label(self) -> str:
        return f'{self.app_name}: {self.name}'


def _load_scan_addresses(path: Path) -> list[dict]:
    """Load only the literal SCAN_ADDRESSES assignment from a Python source file."""
    try:
        module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return []

    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == 'SCAN_ADDRESSES' for target in statement.targets):
            continue
        try:
            entries = ast.literal_eval(statement.value)
        except (ValueError, TypeError):
            return []
        return entries if isinstance(entries, list) else []
    return []


def discover_saved_pointers(workspace_root: Path) -> list[SavedPointer]:
    """Discover pointer entries in sibling applications without executing their code."""
    pointers: list[SavedPointer] = []
    for app_dir in workspace_root.iterdir():
        scan_file = app_dir / 'src' / 'scan_addresses.py'
        if not app_dir.is_dir() or not scan_file.is_file():
            continue
        for entry in _load_scan_addresses(scan_file):
            if not isinstance(entry, dict) or str(entry.get('type', '')).lower() != 'pointer':
                continue
            module = str(entry.get('module', '')).strip()
            base_offset = str(entry.get('base_offset', '')).strip()
            raw_offsets = entry.get('offsets', [])
            offsets = tuple(str(offset).strip() for offset in raw_offsets if str(offset).strip()) if isinstance(raw_offsets, list) else ()
            name = str(entry.get('name', '')).strip()
            if name and module and base_offset and offsets:
                pointers.append(SavedPointer(
                    app_name=app_dir.name,
                    source_path=scan_file,
                    name=name,
                    module=module,
                    base_offset=base_offset,
                    offsets=offsets,
                    description=str(entry.get('description', '')).strip(),
                ))
    return sorted(pointers, key=lambda pointer: (pointer.app_name.lower(), pointer.name.lower()))


def save_pointer_offsets(pointer: SavedPointer) -> None:
    """Replace only the selected pointer's literal offsets list in its source file."""
    source = pointer.source_path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(pointer.source_path))
    lines = source.splitlines(keepends=True)

    def source_index(line_number: int, column: int) -> int:
        return sum(len(line) for line in lines[:line_number - 1]) + column

    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.List):
            continue
        if not any(isinstance(target, ast.Name) and target.id == 'SCAN_ADDRESSES' for target in statement.targets):
            continue
        for entry in statement.value.elts:
            if not isinstance(entry, ast.Dict):
                continue
            fields = {
                key.value: value
                for key, value in zip(entry.keys, entry.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            name_node = fields.get('name')
            module_node = fields.get('module')
            offsets_node = fields.get('offsets')
            if not isinstance(name_node, ast.Constant) or name_node.value != pointer.name:
                continue
            if not isinstance(module_node, ast.Constant) or module_node.value != pointer.module:
                continue
            if not isinstance(offsets_node, ast.List):
                continue

            entry_indent = lines[entry.lineno - 1][:len(lines[entry.lineno - 1]) - len(lines[entry.lineno - 1].lstrip())]
            item_indent = f'{entry_indent}    '
            replacement = '[\n' + ''.join(f'{item_indent}"{offset}",\n' for offset in pointer.offsets) + f'{entry_indent}]'
            start = source_index(offsets_node.lineno, offsets_node.col_offset)
            end = source_index(offsets_node.end_lineno, offsets_node.end_col_offset)
            pointer.source_path.write_text(source[:start] + replacement + source[end:], encoding='utf-8')
            return
    raise ValueError(f'Could not find {pointer.label} in {pointer.source_path}.')


def list_processes() -> list[ProcessChoice]:
    processes: list[ProcessChoice] = []
    for process in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            info = process.info
            processes.append(ProcessChoice(
                pid=int(info['pid']),
                name=str(info.get('name') or f'PID {info["pid"]}'),
                executable=str(info.get('exe') or ''),
            ))
        except (psutil.AccessDenied, psutil.NoSuchProcess, KeyError, TypeError, ValueError):
            continue
    return sorted(processes, key=lambda item: (item.name.lower(), item.pid))


class PointerRelocatorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.processes: dict[str, ProcessChoice] = {}
        self.process_var = tk.StringVar()
        self.module_var = tk.StringVar()
        self.previous_base_var = tk.StringVar()
        self.previous_address_var = tk.StringVar()
        self.saved_pointer_var = tk.StringVar(value='No saved pointer selected.')
        self.expected_value_var = tk.StringVar()
        self.search_radius_var = tk.StringVar(value='0x200')
        self.result_var = tk.StringVar(value='Choose a process, enter the two prior addresses, then relocate.')
        self.detail_var = tk.StringVar(value='')
        self.selected_pointer: SavedPointer | None = None
        self._build_ui()
        self.refresh_processes()

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry('720x500')
        self.root.minsize(620, 460)
        self.root.configure(bg='#f4f7fa')
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        body = ttk.Frame(self.root, padding=20)
        body.grid(sticky='nsew')
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=APP_TITLE, font=('Segoe UI', 16, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 4)
        )
        ttk.Label(
            body, text='Recalculates a module-relative address in a running process.', foreground='#53606d'
        ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 18))

        ttk.Label(body, text='Process').grid(row=2, column=0, sticky='w', pady=5)
        self.process_box = ttk.Combobox(body, textvariable=self.process_var, state='readonly')
        self.process_box.grid(row=2, column=1, sticky='ew', padx=(12, 8), pady=5)
        self.process_box.bind('<<ComboboxSelected>>', self._on_process_selected)
        ttk.Button(body, text='Refresh', command=self.refresh_processes).grid(row=2, column=2, pady=5)

        ttk.Label(body, text='Module').grid(row=3, column=0, sticky='w', pady=5)
        self.module_entry = ttk.Entry(body, textvariable=self.module_var)
        self.module_entry.grid(row=3, column=1, sticky='ew', padx=(12, 8), pady=5)
        ttk.Button(body, text='Saved Pointer...', command=self.open_saved_pointer_picker).grid(row=3, column=2, pady=5)
        ttk.Label(body, textvariable=self.saved_pointer_var, foreground='#53606d').grid(
            row=4, column=1, columnspan=2, sticky='w', padx=(12, 0), pady=(0, 5)
        )

        self.manual_separator = ttk.Separator(body)
        self.manual_separator.grid(row=5, column=0, columnspan=3, sticky='ew', pady=14)

        self.previous_base_label = ttk.Label(body, text='Previous module base')
        self.previous_base_label.grid(row=6, column=0, sticky='w', pady=5)
        self.previous_base_entry = ttk.Entry(body, textvariable=self.previous_base_var, font=('Consolas', 10))
        self.previous_base_entry.grid(
            row=6, column=1, columnspan=2, sticky='ew', padx=(12, 0), pady=5
        )
        self.previous_address_label = ttk.Label(body, text='Previous address')
        self.previous_address_label.grid(row=7, column=0, sticky='w', pady=5)
        self.previous_address_entry = ttk.Entry(body, textvariable=self.previous_address_var, font=('Consolas', 10))
        self.previous_address_entry.grid(
            row=7, column=1, columnspan=2, sticky='ew', padx=(12, 0), pady=5
        )

        self.rebase_button = ttk.Button(body, text='Find New Candidate', command=self.find_candidate)
        self.rebase_button.grid(
            row=8, column=1, sticky='w', pady=(16, 10)
        )
        ttk.Button(body, text='Resolve Selected Chain', command=self.resolve_selected_chain).grid(
            row=8, column=2, sticky='e', pady=(16, 10)
        )
        self.expected_value_label = ttk.Label(body, text='Expected final value')
        self.expected_value_entry = ttk.Entry(body, textvariable=self.expected_value_var, width=14, font=('Consolas', 10))
        self.search_radius_label = ttk.Label(body, text='Search range')
        self.search_radius_entry = ttk.Entry(body, textvariable=self.search_radius_var, width=12, font=('Consolas', 10))
        self.repair_button = ttk.Button(body, text='Repair Loaded Chain', command=self.repair_selected_chain)
        self.save_repaired_button = ttk.Button(body, text='Save Repaired Pointer', command=self.save_repaired_pointer)
        ttk.Label(body, textvariable=self.result_var, font=('Consolas', 11, 'bold'), foreground='#176b3a').grid(
            row=11, column=0, columnspan=3, sticky='w', pady=(4, 3)
        )
        ttk.Label(body, textvariable=self.detail_var, foreground='#53606d', wraplength=660).grid(
            row=12, column=0, columnspan=3, sticky='w'
        )

    def refresh_processes(self) -> None:
        choices = list_processes()
        self.processes = {choice.label: choice for choice in choices}
        self.process_box['values'] = list(self.processes)
        if self.process_var.get() not in self.processes:
            self.process_var.set('')

    def _on_process_selected(self, _event: tk.Event) -> None:
        choice = self.processes.get(self.process_var.get())
        if choice and not self.module_var.get().strip():
            self.module_var.set(os.path.basename(choice.executable) or choice.name)

    def open_saved_pointer_picker(self) -> None:
        pointers = discover_saved_pointers(Path(__file__).resolve().parent.parent)
        if not pointers:
            messagebox.showinfo(APP_TITLE, 'No pointer entries were found in sibling src/scan_addresses.py files.', parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title('Select Saved Pointer')
        dialog.geometry('650x400')
        dialog.minsize(560, 330)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        ttk.Label(dialog, text='Select a pointer from another application:', padding=(16, 16, 16, 8)).grid(
            row=0, column=0, sticky='w'
        )
        frame = ttk.Frame(dialog, padding=(16, 0, 16, 8))
        frame.grid(row=1, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        pointer_list = tk.Listbox(frame, activestyle='none', exportselection=False)
        pointer_list.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=pointer_list.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        pointer_list.configure(yscrollcommand=scrollbar.set)

        for pointer in pointers:
            description = f' - {pointer.description}' if pointer.description else ''
            pointer_list.insert(tk.END, f'{pointer.label}{description}')

        button_row = ttk.Frame(dialog, padding=(16, 0, 16, 16))
        button_row.grid(row=2, column=0, sticky='ew')

        def choose() -> None:
            selected = pointer_list.curselection()
            if not selected:
                return
            self.selected_pointer = pointers[selected[0]]
            self.save_repaired_button.grid_remove()
            self.module_var.set(self.selected_pointer.module)
            self.module_entry.configure(state='readonly')
            self.previous_base_var.set('')
            self.previous_address_var.set('')
            self._set_manual_rebase_visible(False)
            self._set_repair_controls_visible(True)
            self.saved_pointer_var.set(
                f'{self.selected_pointer.label}  |  {self.selected_pointer.module}+{self.selected_pointer.base_offset}'
            )
            self.result_var.set('Choose a process, then resolve the loaded pointer chain.')
            self.detail_var.set('')
            dialog.destroy()

        ttk.Button(button_row, text='Select', command=choose).pack(side='left')
        ttk.Button(button_row, text='Cancel', command=dialog.destroy).pack(side='right')
        pointer_list.bind('<Double-Button-1>', lambda _event: choose())
        pointer_list.bind('<Return>', lambda _event: choose())
        pointer_list.focus_set()

    def _set_manual_rebase_visible(self, visible: bool) -> None:
        widgets = (
            self.manual_separator,
            self.previous_base_label,
            self.previous_base_entry,
            self.previous_address_label,
            self.previous_address_entry,
            self.rebase_button,
        )
        for widget in widgets:
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def _set_repair_controls_visible(self, visible: bool) -> None:
        widgets = (
            self.expected_value_label,
            self.expected_value_entry,
            self.search_radius_label,
            self.search_radius_entry,
            self.repair_button,
        )
        if visible:
            self.expected_value_label.grid(row=9, column=0, sticky='w', pady=(0, 8))
            self.expected_value_entry.grid(row=9, column=1, sticky='w', padx=(12, 8), pady=(0, 8))
            self.search_radius_label.grid(row=9, column=1, sticky='e', padx=(0, 116), pady=(0, 8))
            self.search_radius_entry.grid(row=9, column=1, sticky='e', padx=(0, 8), pady=(0, 8))
            self.repair_button.grid(row=9, column=2, sticky='e', pady=(0, 8))
        else:
            for widget in widgets:
                widget.grid_remove()

    def resolve_selected_chain(self) -> None:
        pointer = self.selected_pointer
        choice = self.processes.get(self.process_var.get())
        if pointer is None:
            messagebox.showwarning(APP_TITLE, 'Select a saved pointer first.', parent=self.root)
            return
        if choice is None:
            messagebox.showwarning(APP_TITLE, 'Choose a running process first.', parent=self.root)
            return

        handle = open_process_for_reading(choice.pid)
        if not handle:
            messagebox.showerror(APP_TITLE, 'Could not read that process. Run this tool with the same privilege level as the target.', parent=self.root)
            return

        try:
            module_base = get_module_base(handle, pointer.module)
            if module_base is None:
                messagebox.showerror(APP_TITLE, f'Module not found in the selected process: {pointer.module}', parent=self.root)
                return
            offsets = [parse_hex_address(offset) for offset in pointer.offsets]
            current = read_pointer_sized_value(handle, module_base + parse_hex_address(pointer.base_offset))
            if current is None or current == 0:
                self._show_chain_failure(pointer, f'root 0x{module_base + parse_hex_address(pointer.base_offset):X}')
                return
            for index, offset in enumerate(offsets[:-1], start=1):
                current = read_pointer_sized_value(handle, current + offset)
                if current is None or current == 0:
                    self._show_chain_failure(pointer, f'hop {index}')
                    return
            final_address = current + offsets[-1]
            value = read_pointer_sized_value(handle, final_address)
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, f'Invalid saved pointer: {exc}', parent=self.root)
            return
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

        self.result_var.set(f'{pointer.label} resolved to: 0x{final_address:X}')
        value_text = f'0x{value:X}' if value is not None else 'unreadable'
        self.detail_var.set(f'Module base: 0x{module_base:X}. Pointer-sized value at final address: {value_text}.')

    def repair_selected_chain(self) -> None:
        pointer = self.selected_pointer
        choice = self.processes.get(self.process_var.get())
        if pointer is None:
            messagebox.showwarning(APP_TITLE, 'Select a saved pointer first.', parent=self.root)
            return
        if choice is None:
            messagebox.showwarning(APP_TITLE, 'Choose a running process first.', parent=self.root)
            return
        try:
            expected_value = int(self.expected_value_var.get().strip(), 0)
            search_radius = parse_hex_address(self.search_radius_var.get())
        except ValueError:
            messagebox.showwarning(
                APP_TITLE,
                'Enter the final value currently visible in the target and a hexadecimal search range, such as 0x200.',
                parent=self.root,
            )
            return

        handle = open_process_for_reading(choice.pid)
        if not handle:
            messagebox.showerror(APP_TITLE, 'Could not read that process. Run this tool with the same privilege level as the target.', parent=self.root)
            return

        try:
            module_base = get_module_base(handle, pointer.module)
            if module_base is None:
                messagebox.showerror(APP_TITLE, f'Module not found in the selected process: {pointer.module}', parent=self.root)
                return
            offsets = [parse_hex_address(offset) for offset in pointer.offsets]
            _, failure_index, current = self._trace_pointer_chain(handle, module_base, pointer.base_offset, offsets)
            if failure_index is None or current is None:
                self.result_var.set(f'{pointer.label} has no repairable broken hop.')
                self.detail_var.set('The chain either already resolves or its root slot no longer resolves.')
                return
            repaired_offset = self._find_repaired_offset(
                handle, current, offsets, failure_index, search_radius, expected_value
            )
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, f'Invalid saved pointer: {exc}', parent=self.root)
            return
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

        if repaired_offset is None:
            self.result_var.set(f'No verified repair found for {pointer.label}.')
            self.detail_var.set(
                f'No nearby offset within 0x{search_radius:X} produced final value {expected_value}. '
                'Check the value or increase the range carefully.'
            )
            return

        offsets[failure_index] = repaired_offset
        self.selected_pointer = SavedPointer(
            app_name=pointer.app_name,
            source_path=pointer.source_path,
            name=pointer.name,
            module=pointer.module,
            base_offset=pointer.base_offset,
            offsets=tuple(f'0x{offset:X}' for offset in offsets),
            description=pointer.description,
        )
        self.saved_pointer_var.set(
            f'{self.selected_pointer.label} repaired | changed hop {failure_index + 1} to 0x{repaired_offset:X}'
        )
        self.result_var.set(f'Verified repaired chain found for {pointer.label}.')
        self.detail_var.set('The repaired chain is loaded for this session. Save it to update the selected app, or resolve it again to show its final address.')
        self.save_repaired_button.grid(row=10, column=2, sticky='e', pady=(0, 8))

    def save_repaired_pointer(self) -> None:
        pointer = self.selected_pointer
        if pointer is None:
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f'Update {pointer.app_name}/src/scan_addresses.py for {pointer.name}?',
            parent=self.root,
        ):
            return
        try:
            save_pointer_offsets(pointer)
        except (OSError, SyntaxError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, f'Could not save repaired pointer:\n{exc}', parent=self.root)
            return
        self.save_repaired_button.grid_remove()
        self.result_var.set(f'Saved repaired {pointer.label} offsets to {pointer.app_name}.')
        self.detail_var.set('Restart the selected application or reload its scan-address configuration to use the saved chain.')

    def _trace_pointer_chain(
        self, handle: int, module_base: int, base_offset: str, offsets: list[int]
    ) -> tuple[int | None, int | None, int | None]:
        current = read_pointer_sized_value(handle, module_base + parse_hex_address(base_offset))
        if current is None or current == 0:
            return None, None, None
        for index, offset in enumerate(offsets[:-1]):
            next_pointer = read_pointer_sized_value(handle, current + offset)
            if next_pointer is None or next_pointer == 0:
                return None, index, current
            current = next_pointer
        return current + offsets[-1], None, current

    def _find_repaired_offset(
        self,
        handle: int,
        current: int,
        offsets: list[int],
        failure_index: int,
        search_radius: int,
        expected_value: int,
    ) -> int | None:
        for candidate_offset in nearby_offsets(offsets[failure_index], search_radius):
            next_pointer = read_pointer_sized_value(handle, current + candidate_offset)
            if next_pointer is None or next_pointer == 0:
                continue
            tail_pointer = next_pointer
            valid_tail = True
            for offset in offsets[failure_index + 1:-1]:
                tail_pointer = read_pointer_sized_value(handle, tail_pointer + offset)
                if tail_pointer is None or tail_pointer == 0:
                    valid_tail = False
                    break
            if valid_tail and read_uint32(handle, tail_pointer + offsets[-1]) == expected_value:
                return candidate_offset
        return None

    def _show_chain_failure(self, pointer: SavedPointer, stage: str) -> None:
        self.result_var.set(f'{pointer.label} could not be resolved.')
        self.detail_var.set(f'The saved pointer chain failed at {stage}; this usually means its offsets changed in the target build.')

    def find_candidate(self) -> None:
        choice = self.processes.get(self.process_var.get())
        if choice is None:
            messagebox.showwarning(APP_TITLE, 'Choose a running process first.', parent=self.root)
            return
        module_name = self.module_var.get().strip()
        if not module_name:
            messagebox.showwarning(APP_TITLE, 'Enter the module that contained the previous address.', parent=self.root)
            return

        try:
            previous_base = parse_hex_address(self.previous_base_var.get())
            previous_address = parse_hex_address(self.previous_address_var.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, 'Enter both previous addresses in hexadecimal, for example 0x7FF600000000.', parent=self.root)
            return

        handle = open_process_for_reading(choice.pid)
        if not handle:
            messagebox.showerror(APP_TITLE, 'Could not read that process. Run this tool with the same privilege level as the target.', parent=self.root)
            return

        try:
            current_base = get_module_base(handle, module_name)
            if current_base is None:
                messagebox.showerror(APP_TITLE, f'Module not found in the selected process: {module_name}', parent=self.root)
                return
            candidate = rebase_address(previous_address, previous_base, current_base)
            value = read_pointer_sized_value(handle, candidate)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

        offset = previous_address - previous_base
        self.result_var.set(f'Candidate address: 0x{candidate:X}')
        if value is None:
            self.detail_var.set(
                f'Module base: 0x{current_base:X}; preserved offset: 0x{offset:X}. The candidate could not be read.'
            )
        else:
            self.detail_var.set(
                f'Module base: 0x{current_base:X}; preserved offset: 0x{offset:X}. '
                f'Pointer-sized value at candidate: 0x{value:X}.'
            )


def main() -> None:
    if sys.platform != 'win32':
        raise SystemExit(f'{APP_TITLE} runs on Windows only.')
    root = tk.Tk()
    PointerRelocatorApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()