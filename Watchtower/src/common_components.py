import ctypes
import importlib
import tkinter as tk
from tkinter import messagebox


def make_button(
    parent: tk.Misc,
    colors: dict[str, str],
    text: str,
    *,
    width: int,
    command,
    accent: bool = False,
    success: bool = False,
    danger: bool = False,
) -> tk.Button:
    bg = colors['panel_alt']
    hover_bg = '#2a313a'
    fg = colors['text']

    if accent:
        bg = colors['accent']
        hover_bg = colors['accent_hover']
        fg = '#ffffff'
    elif success:
        bg = colors['success']
        hover_bg = '#1f8f58'
        fg = '#ffffff'
    elif danger:
        bg = colors['danger']
        hover_bg = colors['danger_hover']
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
        highlightbackground=colors['border'],
        highlightcolor=colors['accent'],
    )
    return button


def position_popup_at_main_window(root: tk.Misc, popup: tk.Misc, size: str | None = None) -> None:
    """Center popup over the main window, or over the screen when the root is hidden."""
    root.update_idletasks()
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

    if root.winfo_viewable():
        root_x = root.winfo_rootx()
        root_y = root.winfo_rooty()
        root_w = root.winfo_width()
        root_h = root.winfo_height()
        x = root_x + (root_w - popup_w) // 2
        y = root_y + (root_h - popup_h) // 2
    else:
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = (screen_w - popup_w) // 2
        y = (screen_h - popup_h) // 2

    x = max(0, x)
    y = max(0, y)

    if size:
        popup.geometry(f'{size}+{x}+{y}')
    else:
        popup.geometry(f'+{x}+{y}')


def open_process_for_reading(pid: int) -> int | None:
    """Open a Windows process handle with VM_READ + QUERY_INFORMATION. Returns handle or None."""
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    return int(handle) if handle else None


def read_int_from_process(handle: int, address: int) -> int | None:
    buf = ctypes.c_int32()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buf),
        ctypes.sizeof(buf),
        ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(buf):
        return buf.value
    return None


def read_uint_from_process(handle: int, address: int) -> int | None:
    buf = ctypes.c_uint32()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buf),
        ctypes.sizeof(buf),
        ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(buf):
        return int(buf.value)
    return None


def read_ushort_from_process(handle: int, address: int) -> int | None:
    buf = ctypes.c_uint16()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buf),
        ctypes.sizeof(buf),
        ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(buf):
        return int(buf.value)
    return None


def read_ubyte_from_process(handle: int, address: int) -> int | None:
    buf = ctypes.c_uint8()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buf),
        ctypes.sizeof(buf),
        ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(buf):
        return int(buf.value)
    return None


def read_numeric_from_process(handle: int, address: int) -> int | None:
    for reader in (read_uint_from_process, read_int_from_process, read_ushort_from_process, read_ubyte_from_process):
        value = reader(handle, address)
        if value is not None:
            return value
    return None


def read_ptr_from_process(handle: int, address: int) -> int | None:
    buf = ctypes.c_uint64()
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buf),
        ctypes.sizeof(buf),
        ctypes.byref(bytes_read),
    )
    if ok and bytes_read.value == ctypes.sizeof(buf):
        return int(buf.value)
    return None


def get_module_base(handle: int, module_name: str) -> int | None:
    """Return the base address of a module loaded in the target process."""
    psapi = ctypes.WinDLL('psapi', use_last_error=True)

    class MODULEINFO(ctypes.Structure):
        _fields_ = [
            ('lpBaseOfDll', ctypes.c_void_p),
            ('SizeOfImage', ctypes.c_uint32),
            ('EntryPoint', ctypes.c_void_p),
        ]

    psapi.EnumProcessModulesEx.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_uint32,
    ]
    psapi.EnumProcessModulesEx.restype = ctypes.c_int
    psapi.GetModuleBaseNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    ]
    psapi.GetModuleBaseNameW.restype = ctypes.c_uint32
    psapi.GetModuleInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(MODULEINFO),
        ctypes.c_uint32,
    ]
    psapi.GetModuleInformation.restype = ctypes.c_int

    process_handle = ctypes.c_void_p(handle)
    hmod_array = (ctypes.c_void_p * 1024)()
    bytes_needed = ctypes.c_uint32(0)
    if not psapi.EnumProcessModulesEx(
        process_handle,
        hmod_array,
        ctypes.sizeof(hmod_array),
        ctypes.byref(bytes_needed),
        0x03,
    ):
        return None

    count = bytes_needed.value // ctypes.sizeof(ctypes.c_void_p)
    target = module_name.lower()
    for i in range(min(count, 1024)):
        mod = hmod_array[i]
        name_buf = ctypes.create_unicode_buffer(260)
        if psapi.GetModuleBaseNameW(process_handle, mod, name_buf, 260) == 0:
            continue
        if name_buf.value.lower() == target:
            info = MODULEINFO()
            if psapi.GetModuleInformation(process_handle, mod, ctypes.byref(info), ctypes.sizeof(info)):
                return int(info.lpBaseOfDll)
            return int(mod)
    return None


def read_value_pointer(
    handle: int,
    module_name: str,
    base_offset_hex: str,
    offsets_hex: list[str],
) -> int | None:
    """Resolve a CE-style pointer chain and return the final numeric value."""
    module_base = get_module_base(handle, module_name)
    if module_base is None or not offsets_hex:
        return None

    try:
        base_off = int(base_offset_hex.replace('0x', '').replace('0X', ''), 16)
    except ValueError:
        return None

    ptr = read_ptr_from_process(handle, module_base + base_off)
    if ptr is None:
        return None

    for off_hex in offsets_hex[:-1]:
        try:
            off = int(off_hex.replace('0x', '').replace('0X', ''), 16)
        except ValueError:
            return None
        ptr = read_ptr_from_process(handle, ptr + off)
        if ptr is None:
            return None

    try:
        final_off = int(offsets_hex[-1].replace('0x', '').replace('0X', ''), 16)
    except ValueError:
        return None
    return read_numeric_from_process(handle, ptr + final_off)
