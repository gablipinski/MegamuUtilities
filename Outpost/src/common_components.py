import ctypes
import os


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


def _parse_hex_int(raw: str) -> int | None:
    try:
        return int(str(raw).replace('0x', '').replace('0X', '').strip(), 16)
    except (TypeError, ValueError):
        return None


def _read_pointer_chain_with_offsets(
    handle: int,
    module_name: str,
    base_offset_hex: str,
    offsets: list[int],
) -> int | None:
    module_base = get_module_base(handle, module_name)
    if module_base is None or not offsets:
        return None

    base_off = _parse_hex_int(base_offset_hex)
    if base_off is None:
        return None

    ptr = read_ptr_from_process(handle, module_base + base_off)
    if ptr is None:
        return None

    for off in offsets[:-1]:
        ptr = read_ptr_from_process(handle, ptr + off)
        if ptr is None:
            return None

    return read_numeric_from_process(handle, ptr + offsets[-1])


def read_value_pointer_with_offset_fallback(
    handle: int,
    module_name: str,
    base_offset_hex: str,
    offsets_hex: list[str],
) -> int | None:
    """Try the configured chain first, then nearby second-hop offsets like Watchtower."""
    offsets: list[int] = []
    for raw in offsets_hex:
        parsed = _parse_hex_int(raw)
        if parsed is None:
            return read_value_pointer(handle, module_name, base_offset_hex, offsets_hex)
        offsets.append(parsed)

    if len(offsets) < 3:
        return read_value_pointer(handle, module_name, base_offset_hex, offsets_hex)

    value = _read_pointer_chain_with_offsets(handle, module_name, base_offset_hex, offsets)
    if value is not None:
        return value

    second = offsets[1]
    candidates = [second, second - 0x10, second - 0x8, second + 0x8, second + 0x10, second + 0x18, second + 0x20]
    seen: set[int] = set()
    for cand in candidates:
        if cand < 0 or cand in seen:
            continue
        seen.add(cand)

        trial = list(offsets)
        trial[1] = cand
        value = _read_pointer_chain_with_offsets(handle, module_name, base_offset_hex, trial)
        if value is not None:
            return value

    return None


def diagnose_pointer_chain(
    handle: int,
    module_name: str,
    base_offset_hex: str,
    offsets_hex: list[str],
) -> str:
    """Return the same compact diagnosis Watchtower uses for pointer-chain failures."""
    module_base = get_module_base(handle, module_name)
    if module_base is None:
        return f'module-not-found ({module_name})'

    try:
        base_off = int(str(base_offset_hex).replace('0x', '').replace('0X', '').strip(), 16)
    except ValueError:
        return 'invalid-base-offset'

    try:
        offsets = [int(str(o).replace('0x', '').replace('0X', '').strip(), 16) for o in offsets_hex]
    except ValueError:
        return 'invalid-offsets'

    if not offsets:
        return 'no-offsets'

    static_addr = module_base + base_off
    ptr = read_ptr_from_process(handle, static_addr)
    if ptr is None:
        return f'fail@static-read addr=0x{static_addr:X}'
    if ptr == 0:
        return f'fail@static-null addr=0x{static_addr:X}'

    for i, off in enumerate(offsets[:-1]):
        hop_addr = ptr + off
        next_ptr = read_ptr_from_process(handle, hop_addr)
        if next_ptr is None:
            return f'fail@hop{i} off=0x{off:X} addr=0x{hop_addr:X}'
        if next_ptr == 0:
            return f'fail@hop{i}-null off=0x{off:X} addr=0x{hop_addr:X}'
        ptr = next_ptr

    value_addr = ptr + offsets[-1]
    value = read_numeric_from_process(handle, value_addr)
    if value is None:
        return f'fail@value-read addr=0x{value_addr:X}'
    return f'ok value={value} addr=0x{value_addr:X}'


def pointer_offset_fallback_candidates(offsets: list[int]) -> list[list[int]]:
    """Return nearby candidate offset sets to try when the first pointer hop drifts."""
    if not offsets:
        return []

    primary = list(offsets)
    if len(primary) < 2:
        return [primary]

    base_offset = primary[1]
    candidates = [
        base_offset,
        base_offset - 0x20,
        base_offset - 0x18,
        base_offset - 0x10,
        base_offset - 0x8,
        base_offset + 0x8,
        base_offset + 0x10,
        base_offset + 0x18,
        base_offset + 0x20,
    ]

    unique: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for cand in candidates:
        if cand < 0:
            continue
        trial = list(primary)
        trial[1] = cand
        key = tuple(trial)
        if key in seen:
            continue
        seen.add(key)
        unique.append(trial)

    unique.insert(0, primary)
    return unique


def log_pointer_debug(label: str, handle: int, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> None:
    """Print a full pointer-chain debug trace for a named metric (HP/SD)."""
    module_base = get_module_base(handle, module_name)
    if module_base is None:
        print(f'[Outpost {label} Debug] module-not-found ({module_name})')
        return

    try:
        base_off = int(str(base_offset_hex).replace('0x', '').replace('0X', '').strip(), 16)
    except ValueError:
        print(f'[Outpost {label} Debug] invalid base_offset: {base_offset_hex}')
        return

    try:
        offsets = [int(str(o).replace('0x', '').replace('0X', '').strip(), 16) for o in offsets_hex]
    except ValueError:
        print(f'[Outpost {label} Debug] invalid offsets: {offsets_hex}')
        return

    static_addr = module_base + base_off
    print(f'[Outpost {label} Debug] module_base=0x{module_base:X} root=0x{static_addr:X}')
    ptr = read_ptr_from_process(handle, static_addr)
    if ptr is None:
        print(f'[Outpost {label} Debug] fail@static-read addr=0x{static_addr:X}')
        return
    print(f'[Outpost {label} Debug] P0=0x{ptr:X}')

    current_ptr = ptr
    for i, off in enumerate(offsets[:-1]):
        hop_addr = current_ptr + off
        next_ptr = read_ptr_from_process(handle, hop_addr)
        next_value_text = f'0x{next_ptr:X}' if next_ptr is not None else 'None'
        print(f'[Outpost {label} Debug] candidate hop{i} off=0x{off:X} addr=0x{hop_addr:X} -> value={next_value_text}')

        if next_ptr is None:
            print(f'[Outpost {label} Debug] fail@hop{i} off=0x{off:X} addr=0x{hop_addr:X}')
            if i == 0:
                for cand in pointer_offset_fallback_candidates(offsets)[1:]:
                    trial = list(cand)
                    probe_offset = trial[1]
                    trial_addr = current_ptr + probe_offset
                    trial_ptr = read_ptr_from_process(handle, trial_addr)
                    trial_value_text = f'0x{trial_ptr:X}' if trial_ptr is not None else 'None'
                    print(f'[Outpost {label} Debug] probe hop0 off=0x{probe_offset:X} addr=0x{trial_addr:X} -> {trial_value_text}')
            return
        if next_ptr == 0:
            print(f'[Outpost {label} Debug] fail@hop{i}-null off=0x{off:X} addr=0x{hop_addr:X}')
            return
        if next_ptr & 0xFFFF00000000 == 0xFFFF00000000:
            print(f'[Outpost {label} Debug] suspicious hop{i} off=0x{off:X} -> 0x{next_ptr:X} looks invalid (high 16 bits FFFF)')
        current_ptr = next_ptr

    value_addr = current_ptr + offsets[-1]
    value = read_numeric_from_process(handle, value_addr)
    print(f'[Outpost {label} Debug] value_addr=0x{value_addr:X} value={value}')


def log_hp_pointer_debug(handle: int, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> None:
    log_pointer_debug('HP', handle, module_name, base_offset_hex, offsets_hex)


def log_sd_pointer_debug(handle: int, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> None:
    log_pointer_debug('SD', handle, module_name, base_offset_hex, offsets_hex)


def get_process_pointer_size(handle: int) -> int:
    """Best-effort pointer size for target process: 4 or 8 bytes."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    is_64_host = ctypes.sizeof(ctypes.c_void_p) == 8
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
    """Resolve a CE-style pointer chain and return the final numeric value.

    Supports both:
    - module-relative chains: module base + base offset
    - absolute pointer roots: direct pointer values such as 0x21FD7D2F83C
    """
    try:
        base_off = int(base_offset_hex.replace('0x', '').replace('0X', ''), 16)
    except ValueError:
        return None

    try:
        parsed_offsets = [int(off_hex.replace('0x', '').replace('0X', ''), 16) for off_hex in offsets_hex]
    except ValueError:
        return None

    module_base = None
    use_absolute_root = bool(base_offset_hex.strip()) and base_off > 0x100000000
    if not use_absolute_root:
        module_base = get_module_base(handle, module_name)
        if module_base is None or not offsets_hex:
            return None
        root_address = module_base + base_off
    else:
        root_address = base_off

    def _resolve_with_pointer_size(pointer_size: int) -> int | None:
        ptr = read_ptr_from_process(handle, root_address, pointer_size=pointer_size)
        if ptr is None:
            if use_absolute_root:
                return read_numeric_from_process(handle, root_address)
            return None

        current_ptr = ptr
        for off in parsed_offsets[:-1]:
            next_ptr = read_ptr_from_process(handle, current_ptr + off, pointer_size=pointer_size)
            if next_ptr is None:
                return None
            current_ptr = next_ptr

        final_address = current_ptr + parsed_offsets[-1]
        return read_numeric_from_process(handle, final_address)

    primary_size = get_process_pointer_size(handle)
    value = _resolve_with_pointer_size(primary_size)
    if value is not None:
        return value

    alt_size = 4 if primary_size == 8 else 8
    value = _resolve_with_pointer_size(alt_size)
    if value is not None:
        return value

    return None
