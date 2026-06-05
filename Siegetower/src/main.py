import sys
import tkinter as tk
from tkinter import messagebox

from license_manager import get_license_path, validate_license
from macro_ui import MacroUI


def main() -> None:
    valid, message = validate_license(get_license_path())
    if not valid:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('License Error', message)
        root.destroy()
        sys.exit(1)

    app = MacroUI()
    app.run()


if __name__ == '__main__':
    main()
