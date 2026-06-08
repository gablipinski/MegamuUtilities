"""Quick desktop UI for generating Watchtower licenses."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from generate_license import generate_license


class LicenseGeneratorUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('Watchtower License Generator')
        self.root.resizable(False, False)

        self.machine_id_var = tk.StringVar()
        self.key_name_var = tk.StringVar()
        self.expiry_var = tk.StringVar(value=(date.today() + timedelta(days=365)).isoformat())
        self.output_var = tk.StringVar(value='')

        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky='nsew')

        ttk.Label(frame, text='Machine ID').grid(row=0, column=0, sticky='w', pady=(0, 8))
        ttk.Entry(frame, textvariable=self.machine_id_var, width=40).grid(
            row=0, column=1, sticky='ew', pady=(0, 8)
        )

        ttk.Label(frame, text='Key Name').grid(row=1, column=0, sticky='w', pady=(0, 8))
        ttk.Entry(frame, textvariable=self.key_name_var, width=40).grid(
            row=1, column=1, sticky='ew', pady=(0, 8)
        )

        ttk.Label(frame, text='Expiry Date (YYYY-MM-DD or never)').grid(
            row=2, column=0, sticky='w', pady=(0, 8)
        )
        ttk.Entry(frame, textvariable=self.expiry_var, width=40).grid(
            row=2, column=1, sticky='ew', pady=(0, 8)
        )

        ttk.Label(frame, text='Output (optional)').grid(row=3, column=0, sticky='w', pady=(0, 8))
        ttk.Entry(frame, textvariable=self.output_var, width=40).grid(
            row=3, column=1, sticky='ew', pady=(0, 8)
        )

        ttk.Button(frame, text='Generate License', command=self._on_generate).grid(
            row=4, column=0, columnspan=2, sticky='ew', pady=(8, 0)
        )

        frame.columnconfigure(1, weight=1)

    def _on_generate(self) -> None:
        machine_id = self.machine_id_var.get().strip()
        key_name = self.key_name_var.get().strip()
        expiry_raw = self.expiry_var.get().strip()
        output_raw = self.output_var.get().strip()

        if not machine_id:
            messagebox.showerror('Missing data', 'Machine ID is required.')
            return
        if not key_name:
            messagebox.showerror('Missing data', 'Key Name is required.')
            return

        expiry = '' if expiry_raw.lower() == 'never' else expiry_raw
        output_path = Path(output_raw) if output_raw else None

        try:
            generate_license(
                machine_id=machine_id.upper().strip(),
                issued_to=key_name,
                expiry=expiry,
                output_path=output_path,
            )
        except Exception as exc:
            messagebox.showerror('Generation failed', str(exc))
            return

        messagebox.showinfo('License generated', 'License created successfully for Watchtower.')


def main() -> None:
    root = tk.Tk()
    LicenseGeneratorUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
