import sys
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from app_version import APP_NAME, APP_VERSION
from license_manager import get_license_path, get_machine_id, validate_license
from macro_ui import MacroUI


def _show_activation_dialog(initial_message: str) -> bool:
    root = tk.Tk()
    root.title(f'{APP_NAME} v{APP_VERSION} - Activation Required')
    root.geometry('520x360')
    root.resizable(False, False)
    root.configure(bg='#111418')

    result = {'activated': False}
    machine_id = get_machine_id()

    tk.Label(
        root,
        text=f'{APP_NAME} v{APP_VERSION} - Activation Required',
        font=('Segoe UI Semibold', 13),
        bg='#111418',
        fg='#e7ecf3',
    ).pack(pady=(18, 6))

    tk.Label(
        root,
        text='This software requires a valid license to run.',
        font=('Segoe UI', 10),
        bg='#111418',
        fg='#9aa7b7',
    ).pack()

    tk.Label(
        root,
        text='Your Machine ID:',
        font=('Segoe UI Semibold', 9),
        bg='#111418',
        fg='#e7ecf3',
    ).pack(pady=(14, 2))

    machine_var = tk.StringVar(value=machine_id)
    machine_entry = tk.Entry(
        root,
        textvariable=machine_var,
        state='readonly',
        font=('Consolas', 12),
        justify='center',
        width=28,
        bg='#0f1318',
        fg='#e7ecf3',
        readonlybackground='#0f1318',
        relief=tk.FLAT,
        highlightthickness=1,
        highlightbackground='#2b3440',
        highlightcolor='#2f81f7',
    )
    machine_entry.pack()

    copy_button = tk.Button(
        root,
        text='Copy Machine ID',
        width=20,
        relief=tk.FLAT,
        bd=0,
        cursor='hand2',
        padx=8,
        pady=6,
        bg='#1d232b',
        fg='#e7ecf3',
        activebackground='#2a313a',
        activeforeground='#ffffff',
        highlightthickness=1,
        highlightbackground='#2b3440',
        highlightcolor='#2f81f7',
    )
    copy_button.pack(pady=(8, 0))

    def _copy_id() -> None:
        root.clipboard_clear()
        root.clipboard_append(machine_id)
        copy_button.configure(text='Copied!')
        root.after(1500, lambda: copy_button.configure(text='Copy Machine ID'))

    copy_button.configure(command=_copy_id)

    tk.Label(
        root,
        text='Send this ID to the distributor to receive your license.dat',
        font=('Segoe UI', 8),
        fg='#9aa7b7',
        bg='#111418',
    ).pack(pady=(4, 10))

    status_var = tk.StringVar(value=initial_message)
    tk.Label(
        root,
        textvariable=status_var,
        fg='#ff8080',
        bg='#111418',
        wraplength=470,
        font=('Segoe UI', 9),
        justify='center',
    ).pack(pady=(0, 12))

    def _browse_license() -> None:
        path_str = filedialog.askopenfilename(
            parent=root,
            title='Select license.dat',
            filetypes=[('License file', '*.dat'), ('All files', '*.*')],
        )
        if not path_str:
            return

        selected = Path(path_str)
        valid, msg = validate_license(selected)
        if not valid:
            status_var.set(msg)
            return

        target = get_license_path()
        try:
            shutil.copy(selected, target)
        except Exception as exc:
            status_var.set(f'Could not copy license: {exc}\nCopy manually to: {target}')
            return

        result['activated'] = True
        root.destroy()

    btn_row = tk.Frame(root, bg='#111418')
    btn_row.pack(pady=(0, 18))

    tk.Button(
        btn_row,
        text='Browse for license.dat...',
        width=26,
        command=_browse_license,
        relief=tk.FLAT,
        bd=0,
        cursor='hand2',
        padx=8,
        pady=6,
        bg='#2f81f7',
        fg='#ffffff',
        activebackground='#1f6fe0',
        activeforeground='#ffffff',
        highlightthickness=1,
        highlightbackground='#2b3440',
        highlightcolor='#2f81f7',
    ).pack(side=tk.LEFT, padx=8)

    tk.Button(
        btn_row,
        text='Exit',
        width=10,
        command=root.destroy,
        relief=tk.FLAT,
        bd=0,
        cursor='hand2',
        padx=8,
        pady=6,
        bg='#c2494b',
        fg='#ffffff',
        activebackground='#a6383b',
        activeforeground='#ffffff',
        highlightthickness=1,
        highlightbackground='#2b3440',
        highlightcolor='#2f81f7',
    ).pack(side=tk.LEFT, padx=8)

    root.mainloop()
    return bool(result['activated'])


def main() -> None:
    valid, message = validate_license(get_license_path())
    if not valid:
        if not _show_activation_dialog(message):
            sys.exit(1)

    app = MacroUI()
    app.run()


if __name__ == '__main__':
    main()
