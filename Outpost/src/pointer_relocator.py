"""Small Windows utility for rebasing a known module-relative address."""

import ctypes
import os
import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

import psutil

from common_components import get_module_base, open_process_for_reading, read_ptr_from_process


APP_TITLE = 'Pointer Relocator'


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


@dataclass(frozen=True)
class ProcessChoice:
    pid: int
    name: str
    executable: str

    @property
    def label(self) -> str:
        return f'{self.name} (PID {self.pid})'


def list_processes() -> list[ProcessChoice]:
    """List user-visible processes without requiring elevated access."""
    processes: list[ProcessChoice] = []
    for process in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            info = process.info
            name = str(info.get('name') or f'PID {info["pid"]}')
            executable = str(info.get('exe') or '')
            processes.append(ProcessChoice(int(info['pid']), name, executable))
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
        self.result_var = tk.StringVar(value='Choose a process, enter the two prior addresses, then relocate.')
        self.detail_var = tk.StringVar(value='')

        self._build_ui()
        self.refresh_processes()

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry('720x390')
        self.root.minsize(620, 360)
        self.root.configure(bg='#f4f7fa')
        self.root.columnconfigure(0, weight=1)

        body = ttk.Frame(self.root, padding=20)
        body.grid(sticky='nsew')
        body.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(body, text=APP_TITLE, font=('Segoe UI', 16, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 4)
        )
        ttk.Label(
            body,
            text='Recalculates a module-relative address in the selected running process.',
            foreground='#53606d',
        ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 18))

        ttk.Label(body, text='Process').grid(row=2, column=0, sticky='w', pady=5)
        self.process_box = ttk.Combobox(body, textvariable=self.process_var, state='readonly')
        self.process_box.grid(row=2, column=1, sticky='ew', padx=(12, 8), pady=5)
        self.process_box.bind('<<ComboboxSelected>>', self._on_process_selected)
        ttk.Button(body, text='Refresh', command=self.refresh_processes).grid(row=2, column=2, pady=5)

        ttk.Label(body, text='Module').grid(row=3, column=0, sticky='w', pady=5)
        ttk.Entry(body, textvariable=self.module_var).grid(row=3, column=1, columnspan=2, sticky='ew', padx=(12, 0), pady=5)

        ttk.Separator(body).grid(row=4, column=0, columnspan=3, sticky='ew', pady=14)

        ttk.Label(body, text='Previous module base').grid(row=5, column=0, sticky='w', pady=5)
        ttk.Entry(body, textvariable=self.previous_base_var, font=('Consolas', 10)).grid(
            row=5, column=1, columnspan=2, sticky='ew', padx=(12, 0), pady=5
        )
        ttk.Label(body, text='Previous address').grid(row=6, column=0, sticky='w', pady=5)
        ttk.Entry(body, textvariable=self.previous_address_var, font=('Consolas', 10)).grid(
            row=6, column=1, columnspan=2, sticky='ew', padx=(12, 0), pady=5
        )

        ttk.Button(body, text='Find New Candidate', command=self.find_candidate).grid(
            row=7, column=1, sticky='w', pady=(16, 10)
        )
        ttk.Label(body, textvariable=self.result_var, font=('Consolas', 11, 'bold'), foreground='#176b3a').grid(
            row=8, column=0, columnspan=3, sticky='w', pady=(4, 3)
        )
        ttk.Label(body, textvariable=self.detail_var, foreground='#53606d', wraplength=660).grid(
            row=9, column=0, columnspan=3, sticky='w'
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
            messagebox.showwarning(APP_TITLE, 'Enter both previous addresses as hexadecimal values, for example 0x7FF600000000.', parent=self.root)
            return

        handle = open_process_for_reading(choice.pid)
        if not handle:
            messagebox.showerror(APP_TITLE, 'Could not read that process. Try running this tool with the same privilege level as the target.', parent=self.root)
            return

        try:
            current_base = get_module_base(handle, module_name)
            if current_base is None:
                messagebox.showerror(APP_TITLE, f'Module not found in the selected process: {module_name}', parent=self.root)
                return
            candidate = rebase_address(previous_address, previous_base, current_base)
            value = read_ptr_from_process(handle, candidate)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

        offset = previous_address - previous_base
        self.result_var.set(f'Candidate address: 0x{candidate:X}')
        if value is None:
            self.detail_var.set(
                f'Module base is 0x{current_base:X}; preserved offset is 0x{offset:X}. The address could not be read.'
            )
        else:
            self.detail_var.set(
                f'Module base is 0x{current_base:X}; preserved offset is 0x{offset:X}. '
                f'Pointer-sized value at candidate: 0x{value:X}.'
            )


def main() -> None:
    if sys.platform != 'win32':
        raise SystemExit('Pointer Relocator runs on Windows only.')
    root = tk.Tk()
    PointerRelocatorApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()