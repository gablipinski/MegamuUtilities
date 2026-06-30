import ctypes
import importlib
import os
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


def get_process_pointer_size(handle: int) -> int:
    """Best-effort pointer size for target process: 4 or 8 bytes."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    is_64_host = ctypes.sizeof(ctypes.c_void_p) == 8

    # Default to host pointer size when detection is unavailable.
    default_size = 8 if is_64_host else 4

    try:
        is_wow64_process_2 = getattr(kernel32, 'IsWow64Process2', None)
        if is_wow64_process_2 is not None:
            process_machine = ctypes.c_ushort(0)
            native_machine = ctypes.c_ushort(0)
            ok = bool(
                is_wow64_process_2(
                    ctypes.c_void_p(handle),
                    ctypes.byref(process_machine),
                    ctypes.byref(native_machine),
                )
            )
            if ok:
                # IMAGE_FILE_MACHINE_UNKNOWN (0) means not WOW64 and same arch as native.
                if process_machine.value != 0:
                    return 4
                return 8 if native_machine.value != 0 and is_64_host else default_size
    except Exception:
        pass

    try:
        is_wow64 = ctypes.c_int(0)
        ok = bool(kernel32.IsWow64Process(ctypes.c_void_p(handle), ctypes.byref(is_wow64)))
        if ok:
            if is_wow64.value:
                return 4
            return 8 if is_64_host else 4
    except Exception:
        pass

    return default_size


def read_ptr_from_process(handle: int, address: int, pointer_size: int | None = None) -> int | None:
    if pointer_size == 8:
        ptr_types = (ctypes.c_uint64,)
    elif pointer_size == 4:
        ptr_types = (ctypes.c_uint32,)
    else:
        # Unknown pointer size: try 64 then 32 as a last resort.
        ptr_types = (ctypes.c_uint64, ctypes.c_uint32)

    for ptr_type in ptr_types:
        buf = ptr_type()
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
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    def _normalize_module_tokens(name: str) -> tuple[str, str]:
        base = os.path.basename(str(name).strip().strip('"').strip("'")).lower()
        if not base:
            return '', ''
        stem = base[:-4] if base.endswith('.dll') else base
        return base, stem

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
    target_base, target_stem = _normalize_module_tokens(module_name)
    for i in range(min(count, 1024)):
        mod = hmod_array[i]
        name_buf = ctypes.create_unicode_buffer(260)
        if psapi.GetModuleBaseNameW(process_handle, mod, name_buf, 260) == 0:
            continue
        mod_base, mod_stem = _normalize_module_tokens(name_buf.value)
        if mod_base in {target_base, target_stem} or mod_stem in {target_base, target_stem}:
            info = MODULEINFO()
            if psapi.GetModuleInformation(process_handle, mod, ctypes.byref(info), ctypes.sizeof(info)):
                return int(info.lpBaseOfDll)
            return int(mod)

    # Fallback path for processes where PSAPI module matching is incomplete.
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_MODULE_NAME32 = 255
    MAX_PATH = 260

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', ctypes.c_uint32),
            ('th32ModuleID', ctypes.c_uint32),
            ('th32ProcessID', ctypes.c_uint32),
            ('GlblcntUsage', ctypes.c_uint32),
            ('ProccntUsage', ctypes.c_uint32),
            ('modBaseAddr', ctypes.c_void_p),
            ('modBaseSize', ctypes.c_uint32),
            ('hModule', ctypes.c_void_p),
            ('szModule', ctypes.c_wchar * (MAX_MODULE_NAME32 + 1)),
            ('szExePath', ctypes.c_wchar * MAX_PATH),
        ]

    kernel32.GetProcessId.argtypes = [ctypes.c_void_p]
    kernel32.GetProcessId.restype = ctypes.c_uint32
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Module32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32FirstW.restype = ctypes.c_int
    kernel32.Module32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    pid = kernel32.GetProcessId(ctypes.c_void_p(handle))
    if not pid:
        return None

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID_HANDLE_VALUE or snap is None:
        return None

    try:
        me32 = MODULEENTRY32W()
        me32.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = kernel32.Module32FirstW(snap, ctypes.byref(me32))
        while ok:
            mod_base, mod_stem = _normalize_module_tokens(me32.szModule)
            if mod_base in {target_base, target_stem} or mod_stem in {target_base, target_stem}:
                return int(me32.modBaseAddr)
            ok = kernel32.Module32NextW(snap, ctypes.byref(me32))
    finally:
        kernel32.CloseHandle(snap)

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

    try:
        parsed_offsets = [int(off_hex.replace('0x', '').replace('0X', ''), 16) for off_hex in offsets_hex]
    except ValueError:
        return None

    def _resolve_with_pointer_size(pointer_size: int) -> int | None:
        ptr = read_ptr_from_process(handle, module_base + base_off, pointer_size=pointer_size)
        if ptr is None:
            return None

        for off in parsed_offsets[:-1]:
            ptr = read_ptr_from_process(handle, ptr + off, pointer_size=pointer_size)
            if ptr is None:
                return None

        return read_numeric_from_process(handle, ptr + parsed_offsets[-1])

    primary_size = get_process_pointer_size(handle)
    value = _resolve_with_pointer_size(primary_size)
    if value is not None:
        return value

    alt_size = 4 if primary_size == 8 else 8
    return _resolve_with_pointer_size(alt_size)
