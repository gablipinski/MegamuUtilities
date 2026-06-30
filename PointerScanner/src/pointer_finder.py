"""
Interactive pointer finder for Windows game processes.

Workflow:
1) Attach to a running process.
2) Enter a value currently shown in game.
3) Perform an in-game action that changes the value, then enter the new value.
4) Repeat until a small set of candidate addresses remains.
5) Run a reverse pointer-chain search to find static module-based chains.

This tool outputs pointer definitions compatible with pointer scan address format.
"""

from __future__ import annotations

import ast
import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import struct
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Iterable

try:
    import psutil
except ImportError:
    print("[ERROR] Missing dependency: psutil")
    print("        Install with: pip install psutil")
    raise


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

READABLE_MASKS = (
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x10,  # PAGE_EXECUTE
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
)

MAX_PATH = 260
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class MODULEINFO(ctypes.Structure):
    _fields_ = [
        ("lpBaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", wintypes.DWORD),
        ("EntryPoint", ctypes.c_void_p),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * (255 + 1)),
        ("szExePath", ctypes.c_wchar * MAX_PATH),
    ]


@dataclass
class MemoryRegion:
    base: int
    size: int
    protect: int
    rtype: int


@dataclass
class ModuleEntry:
    name: str
    base: int
    size: int


@dataclass
class ParentRef:
    addr: int
    offset: int


@dataclass
class KnownPointerHint:
    module: str
    base_offset: int
    offsets: list[int]


@dataclass
class PointerListHint:
    name: str
    hint: KnownPointerHint
    description: str


def _is_windows() -> bool:
    return os.name == "nt"


def _max_user_address_for_pointer_size(pointer_size: int) -> int:
    if pointer_size <= 4:
        return 0xFFFFFFFF
    # x64 Windows user-space canonical ceiling.
    return 0x00007FFFFFFFFFFF


def _hex(value: int) -> str:
    return f"0x{value:X}"


def _open_process(pid: int, desired_access: int | None = None) -> int | None:
    access = desired_access if desired_access is not None else (PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)
    handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
    return int(handle) if handle else None


def _close_handle(handle: int) -> None:
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def _read_memory(handle: int, address: int, size: int) -> bytes | None:
    if size <= 0:
        return b""

    max_addr = _max_user_address_for_pointer_size(ctypes.sizeof(ctypes.c_void_p))
    if address < 0 or address > max_addr:
        return None

    buf = (ctypes.c_ubyte * size)()
    bytes_read = ctypes.c_size_t(0)
    try:
        ok = ctypes.windll.kernel32.ReadProcessMemory(
            ctypes.c_void_p(handle),
            ctypes.c_void_p(address),
            ctypes.byref(buf),
            size,
            ctypes.byref(bytes_read),
        )
    except (OverflowError, ValueError):
        return None
    if not ok or bytes_read.value <= 0:
        return None
    return bytes(buf[: bytes_read.value])


def _write_memory(handle: int, address: int, data: bytes) -> bool:
    if not data:
        return False

    max_addr = _max_user_address_for_pointer_size(ctypes.sizeof(ctypes.c_void_p))
    if address < 0 or address > max_addr:
        return False

    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    bytes_written = ctypes.c_size_t(0)
    try:
        ok = ctypes.windll.kernel32.WriteProcessMemory(
            ctypes.c_void_p(handle),
            ctypes.c_void_p(address),
            ctypes.byref(buf),
            len(data),
            ctypes.byref(bytes_written),
        )
    except (OverflowError, ValueError):
        return False

    return bool(ok) and int(bytes_written.value) == len(data)


def _iter_regions(handle: int, *, min_size: int = 4) -> Iterable[MemoryRegion]:
    kernel32 = ctypes.windll.kernel32
    mbi = MEMORY_BASIC_INFORMATION()
    address = 0
    max_address = _max_user_address_for_pointer_size(ctypes.sizeof(ctypes.c_void_p))

    while address < max_address:
        try:
            result = kernel32.VirtualQueryEx(
                ctypes.c_void_p(handle),
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
        except (OverflowError, ValueError):
            break
        if result == 0:
            break

        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        protect = int(mbi.Protect)
        state = int(mbi.State)
        rtype = int(mbi.Type)

        readable = any((protect & mask) == mask for mask in READABLE_MASKS)
        guarded = bool(protect & PAGE_GUARD)
        noaccess = bool(protect & PAGE_NOACCESS)

        if state == MEM_COMMIT and readable and not guarded and not noaccess and size >= min_size:
            yield MemoryRegion(base=base, size=size, protect=protect, rtype=rtype)

        next_addr = base + max(size, 0x1000)
        if next_addr <= address:
            next_addr = address + 0x1000
        if next_addr > max_address:
            break
        address = next_addr


def _get_process_pointer_size(handle: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    is_64_host = ctypes.sizeof(ctypes.c_void_p) == 8
    default_size = 8 if is_64_host else 4

    try:
        is_wow64_process_2 = getattr(kernel32, "IsWow64Process2", None)
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


def _find_all(data: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + 1
    return out


def _scan_regions_for_value(handle: int, regions: list[MemoryRegion], packed_value: bytes) -> list[int]:
    matches: list[int] = []
    chunk_size = 1 << 20
    overlap = max(0, len(packed_value) - 1)

    for region in regions:
        region_end = region.base + region.size
        cursor = region.base
        while cursor < region_end:
            to_read = min(chunk_size, region_end - cursor)
            blob = _read_memory(handle, cursor, to_read)
            if blob:
                for rel in _find_all(blob, packed_value):
                    matches.append(cursor + rel)

            if to_read <= overlap:
                break
            cursor += to_read - overlap

    return matches


def _read_exact(handle: int, address: int, size: int) -> bytes | None:
    blob = _read_memory(handle, address, size)
    if blob is None or len(blob) != size:
        return None
    return blob


def _filter_candidates_by_value(handle: int, candidates: list[int], packed_value: bytes) -> list[int]:
    size = len(packed_value)
    kept: list[int] = []
    for addr in candidates:
        blob = _read_exact(handle, addr, size)
        if blob == packed_value:
            kept.append(addr)
    return kept


def _get_window_titles_by_pid() -> dict[int, list[str]]:
    pid_to_titles: dict[int, list[str]] = {}

    enum_windows = ctypes.windll.user32.EnumWindows
    get_window_text = ctypes.windll.user32.GetWindowTextW
    get_window_text_length = ctypes.windll.user32.GetWindowTextLengthW
    get_window_thread_process_id = ctypes.windll.user32.GetWindowThreadProcessId
    is_window_visible = ctypes.windll.user32.IsWindowVisible

    wnd_enum_proc = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _enum_cb(hwnd, _lparam):
        if not is_window_visible(hwnd):
            return True
        length = get_window_text_length(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        get_window_text(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = ctypes.wintypes.DWORD()
        get_window_thread_process_id(hwnd, ctypes.byref(pid))
        pid_to_titles.setdefault(int(pid.value), []).append(title)
        return True

    enum_windows(wnd_enum_proc(_enum_cb), 0)
    return pid_to_titles


def _collect_running_processes(megamu_only: bool = True) -> list[tuple[str, int, str | None, list[str]]]:
    pid_titles = _get_window_titles_by_pid()
    entries: list[tuple[str, int, str | None, list[str]]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info
            process_name = str(info.get("name") or "")
            if megamu_only and "megamu" not in process_name.lower():
                continue
            pid = int(info["pid"])
            entries.append(
                (
                    process_name or "<unknown>",
                    pid,
                    info.get("exe"),
                    pid_titles.get(pid, []),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue

    entries.sort(key=lambda e: (e[0].lower(), e[1]))
    return entries


def _choose_process() -> tuple[int, str]:
    entries = _collect_running_processes(megamu_only=True)
    if not entries:
        entries = _collect_running_processes(megamu_only=False)

    print("\nRunning processes (first 100):")
    for i, (name, pid, _exe, titles) in enumerate(entries[:100], start=1):
        if titles:
            print(f"  [{i:02d}] PID {pid:>6}  {name}  -  {titles[0]}")
        else:
            print(f"  [{i:02d}] PID {pid:>6}  {name}")

    while True:
        raw = input("\nEnter PID or list index: ").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            print("[WARN] Please enter a number.")
            continue

        for name, pid, _exe, _titles in entries:
            if pid == value:
                return pid, name

        if 1 <= value <= len(entries[:100]):
            name, pid, _exe, _titles = entries[value - 1]
            return pid, name

        print("[WARN] PID/index not found in list.")


def _parse_value_type(value_type: str) -> tuple[str, int]:
    key = value_type.strip().lower()
    mapping = {
        "i32": ("<i", 4),
        "u32": ("<I", 4),
        "i16": ("<h", 2),
        "u16": ("<H", 2),
        "i8": ("<b", 1),
        "u8": ("<B", 1),
        "f32": ("<f", 4),
    }
    if key not in mapping:
        raise ValueError(f"Unsupported value type: {value_type}")
    return mapping[key]


def _pack_value(raw_value: str, fmt: str) -> bytes:
    if fmt == "<f":
        return struct.pack(fmt, float(raw_value.strip()))
    return struct.pack(fmt, int(raw_value.strip(), 10))


def _parse_hex_int(raw: str) -> int:
    text = str(raw).strip()
    if not text:
        raise ValueError("hex/int value is empty")
    return int(text, 0)


def _parse_offsets_text(raw: str) -> list[int]:
    text = str(raw).strip()
    if not text:
        return []

    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("offsets JSON must be a list")
        values = [int(str(v), 0) for v in data]
    else:
        normalized = text.replace(";", ",").replace("->", ",")
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        values = [int(p, 0) for p in parts]

    return [max(0, int(v)) for v in values]


def _build_known_pointer_hint(module: str, base_offset: str, offsets: str) -> KnownPointerHint | None:
    mod = str(module).strip()
    if not mod:
        return None

    base = _parse_hex_int(base_offset)
    offs = _parse_offsets_text(offsets)
    if not offs:
        raise ValueError("known offsets are required when known module is set")
    return KnownPointerHint(module=mod, base_offset=max(0, int(base)), offsets=offs)


def _load_scan_addresses_from_python_file(path: str) -> list[dict]:
    source_path = Path(path)
    if not source_path.exists() or not source_path.is_file():
        raise ValueError(f"Pointer list file was not found: {path}")

    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Pointer list file is not valid UTF-8: {path}") from exc

    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax in pointer list file: {exc}") from exc

    scan_addresses = None
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCAN_ADDRESSES":
                    value_node = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "SCAN_ADDRESSES":
                value_node = node.value

        if value_node is not None:
            try:
                scan_addresses = ast.literal_eval(value_node)
            except Exception as exc:
                raise ValueError("SCAN_ADDRESSES must be a Python literal list") from exc

    if scan_addresses is None:
        raise ValueError("SCAN_ADDRESSES assignment was not found in the selected file")
    if not isinstance(scan_addresses, list):
        raise ValueError("SCAN_ADDRESSES must be a list")
    return scan_addresses


def _load_pointer_list_hints(path: str) -> list[PointerListHint]:
    raw_entries = _load_scan_addresses_from_python_file(path)
    hints: list[PointerListHint] = []

    for idx, entry in enumerate(raw_entries, start=1):
        if not isinstance(entry, dict):
            continue

        entry_type = str(entry.get("type", "pointer")).strip().lower()
        if entry_type and entry_type != "pointer":
            continue

        name = str(entry.get("name") or f"Pointer{idx}").strip() or f"Pointer{idx}"
        module = str(entry.get("module") or "").strip()
        base_offset = str(entry.get("base_offset") or "").strip()
        offsets_obj = entry.get("offsets", [])
        offsets_text = json.dumps(offsets_obj) if isinstance(offsets_obj, list) else str(offsets_obj)
        description = str(entry.get("description") or "").strip()

        try:
            hint = _build_known_pointer_hint(module, base_offset, offsets_text)
        except Exception:
            continue

        if hint is not None:
            hints.append(PointerListHint(name=name, hint=hint, description=description))

    if not hints:
        raise ValueError("No valid pointer entries were found in SCAN_ADDRESSES")
    return hints


def _normalize_module_name(name: str) -> tuple[str, str]:
    base = os.path.basename(str(name).strip().strip('"').strip("'")).lower()
    stem = base[:-4] if base.endswith(".dll") else base
    return base, stem


def _find_module_by_name(modules: list[ModuleEntry], module_name: str) -> ModuleEntry | None:
    target_base, target_stem = _normalize_module_name(module_name)
    for mod in modules:
        mod_base, mod_stem = _normalize_module_name(mod.name)
        if mod_base in {target_base, target_stem} or mod_stem in {target_base, target_stem}:
            return mod
    return None


def _read_pointer_value(handle: int, address: int, pointer_size: int) -> int | None:
    if pointer_size == 8:
        blob = _read_exact(handle, address, 8)
        if blob is None:
            return None
        return int(struct.unpack("<Q", blob)[0])
    blob = _read_exact(handle, address, 4)
    if blob is None:
        return None
    return int(struct.unpack("<I", blob)[0])


def _is_reasonable_user_address(address: int, pointer_size: int) -> bool:
    if address <= 0:
        return False
    return address <= _max_user_address_for_pointer_size(pointer_size)


def _resolve_pointer_target_address(
    handle: int,
    module_base: int,
    base_offset: int,
    offsets: list[int],
    pointer_size: int,
) -> int | None:
    if not offsets:
        return None

    root_addr = module_base + base_offset
    if not _is_reasonable_user_address(root_addr, pointer_size):
        return None

    ptr = _read_pointer_value(handle, root_addr, pointer_size)
    if ptr is None:
        return None
    if not _is_reasonable_user_address(ptr, pointer_size):
        return None

    for off in offsets[:-1]:
        next_addr = ptr + off
        if not _is_reasonable_user_address(next_addr, pointer_size):
            return None

        ptr = _read_pointer_value(handle, next_addr, pointer_size)
        if ptr is None:
            return None
        if not _is_reasonable_user_address(ptr, pointer_size):
            return None

    final_addr = int(ptr + offsets[-1])
    if not _is_reasonable_user_address(final_addr, pointer_size):
        return None
    return final_addr


def _build_guided_variants(base_offset: int, offsets: list[int]) -> list[tuple[int, list[int]]]:
    deltas = [0, -0x20, -0x18, -0x10, -0x8, 0x8, 0x10, 0x18, 0x20]
    variants: list[tuple[int, list[int]]] = [(base_offset, list(offsets))]

    for delta in deltas[1:]:
        trial_base = base_offset + delta
        if trial_base >= 0:
            variants.append((trial_base, list(offsets)))

    for idx in range(len(offsets)):
        for delta in deltas[1:]:
            trial_offsets = list(offsets)
            trial_value = trial_offsets[idx] + delta
            if trial_value < 0:
                continue
            trial_offsets[idx] = trial_value
            variants.append((base_offset, trial_offsets))

    unique: list[tuple[int, list[int]]] = []
    seen: set[tuple[int, tuple[int, ...]]] = set()
    for base, offs in variants:
        key = (int(base), tuple(int(o) for o in offs))
        if key in seen:
            continue
        seen.add(key)
        unique.append((int(base), [int(o) for o in offs]))
    return unique


def _guided_candidates_from_known_pointer(
    handle: int,
    modules: list[ModuleEntry],
    hint: KnownPointerHint,
    packed_value: bytes,
    pointer_size: int,
) -> tuple[list[int], int]:
    module = _find_module_by_name(modules, hint.module)
    if module is None:
        return [], 0

    attempts = 0
    matches: list[int] = []
    variants = _build_guided_variants(hint.base_offset, hint.offsets)

    for base_offset, offsets in variants:
        attempts += 1
        target_addr = _resolve_pointer_target_address(
            handle,
            module.base,
            base_offset,
            offsets,
            pointer_size,
        )
        if target_addr is None:
            continue

        blob = _read_exact(handle, target_addr, len(packed_value))
        if blob == packed_value:
            matches.append(int(target_addr))

    deduped = sorted(set(matches))
    return deduped, attempts


def _guided_candidates_from_pointer_list(
    handle: int,
    modules: list[ModuleEntry],
    pointer_hints: list[PointerListHint],
    packed_value: bytes,
    pointer_size: int,
) -> tuple[list[int], int, int]:
    all_matches: list[int] = []
    attempts = 0
    matched_entries = 0

    for entry in pointer_hints:
        matches, entry_attempts = _guided_candidates_from_known_pointer(
            handle,
            modules,
            entry.hint,
            packed_value,
            pointer_size,
        )
        attempts += entry_attempts
        if matches:
            matched_entries += 1
            all_matches.extend(matches)

    deduped = sorted(set(all_matches))
    return deduped, attempts, matched_entries


def _enum_modules(handle: int, pid: int) -> list[ModuleEntry]:
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    modules: list[ModuleEntry] = []

    hmods = (ctypes.c_void_p * 2048)()
    needed = wintypes.DWORD(0)

    enum_ok = bool(
        psapi.EnumProcessModulesEx(
            ctypes.c_void_p(handle),
            ctypes.byref(hmods),
            ctypes.sizeof(hmods),
            ctypes.byref(needed),
            0x03,
        )
    )

    if enum_ok:
        count = int(needed.value // ctypes.sizeof(ctypes.c_void_p))
        ptr_mask = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        for i in range(min(count, 2048)):
            raw_mod = hmods[i]
            try:
                mod_value = int(raw_mod or 0)
            except (TypeError, ValueError, OverflowError):
                continue

            if mod_value <= 0:
                continue

            mod_value &= ptr_mask
            mod = ctypes.c_void_p(mod_value)
            name_buf = ctypes.create_unicode_buffer(MAX_PATH)
            try:
                if psapi.GetModuleBaseNameW(ctypes.c_void_p(handle), mod, name_buf, MAX_PATH) == 0:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue

            info = MODULEINFO()
            try:
                if not psapi.GetModuleInformation(
                    ctypes.c_void_p(handle),
                    mod,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue

            base_addr = int(info.lpBaseOfDll or 0)
            size_img = int(info.SizeOfImage or 0)
            if base_addr <= 0 or size_img <= 0:
                continue
            modules.append(
                ModuleEntry(
                    name=str(name_buf.value),
                    base=base_addr,
                    size=size_img,
                )
            )

    if modules:
        return modules

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, wintypes.DWORD(pid))
    if snap == INVALID_HANDLE_VALUE or snap is None:
        return modules

    try:
        me32 = MODULEENTRY32W()
        me32.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = bool(kernel32.Module32FirstW(snap, ctypes.byref(me32)))
        while ok:
            modules.append(
                ModuleEntry(
                    name=str(me32.szModule),
                    base=int(me32.modBaseAddr),
                    size=int(me32.modBaseSize),
                )
            )
            ok = bool(kernel32.Module32NextW(snap, ctypes.byref(me32)))
    finally:
        kernel32.CloseHandle(snap)

    return modules


def _find_module_for_address(modules: list[ModuleEntry], address: int) -> ModuleEntry | None:
    for mod in modules:
        if mod.base <= address < (mod.base + mod.size):
            return mod
    return None


def _iter_pointer_values(blob: bytes, pointer_size: int) -> Iterable[tuple[int, int]]:
    if pointer_size == 8:
        unpack = struct.unpack_from
        for i in range(0, len(blob) - 7, 8):
            yield i, unpack("<Q", blob, i)[0]
    else:
        unpack = struct.unpack_from
        for i in range(0, len(blob) - 3, 4):
            yield i, unpack("<I", blob, i)[0]


def _find_parent_refs(
    handle: int,
    regions: list[MemoryRegion],
    target_addr: int,
    pointer_size: int,
    *,
    max_offset: int,
    alignment: int,
    max_parents: int,
) -> list[ParentRef]:
    found: list[ParentRef] = []

    for region in regions:
        chunk_size = 1 << 20
        cursor = region.base
        end = region.base + region.size

        while cursor < end:
            to_read = min(chunk_size, end - cursor)
            blob = _read_memory(handle, cursor, to_read)
            if blob:
                for rel, ptr_value in _iter_pointer_values(blob, pointer_size):
                    delta = target_addr - int(ptr_value)
                    if delta < 0 or delta > max_offset:
                        continue
                    if alignment > 1 and (delta % alignment) != 0:
                        continue
                    found.append(ParentRef(addr=cursor + rel, offset=int(delta)))
                    if len(found) >= max_parents:
                        return found

            cursor += max(pointer_size, to_read)

    return found


def _search_pointer_chains(
    handle: int,
    regions: list[MemoryRegion],
    modules: list[ModuleEntry],
    value_addr: int,
    pointer_size: int,
    *,
    max_depth: int,
    max_offset: int,
    alignment: int,
    branch_limit: int,
    max_states: int,
) -> list[dict]:
    chains: list[dict] = []
    queue: list[tuple[int, list[int], tuple[int, ...]]] = [(value_addr, [], (value_addr,))]
    seen_states: set[tuple[int, tuple[int, ...]]] = set()

    states_expanded = 0

    while queue and states_expanded < max_states:
        current_addr, offsets, path = queue.pop(0)
        state_key = (current_addr, tuple(offsets))
        if state_key in seen_states:
            continue
        seen_states.add(state_key)

        if len(offsets) >= max_depth:
            continue

        states_expanded += 1
        parents = _find_parent_refs(
            handle,
            regions,
            current_addr,
            pointer_size,
            max_offset=max_offset,
            alignment=alignment,
            max_parents=branch_limit * 8,
        )

        if not parents:
            continue

        # Stable-looking refs first: lower offsets and lower addresses.
        parents.sort(key=lambda p: (p.offset, p.addr))
        parents = parents[:branch_limit]

        for parent in parents:
            new_offsets = [parent.offset] + offsets
            if parent.addr in path:
                continue

            mod = _find_module_for_address(modules, parent.addr)
            if mod is not None:
                chains.append(
                    {
                        "module": mod.name,
                        "base_offset": parent.addr - mod.base,
                        "offsets": new_offsets,
                        "root_address": parent.addr,
                        "depth": len(new_offsets),
                    }
                )
                continue

            queue.append((parent.addr, new_offsets, path + (parent.addr,)))

    # Deduplicate equivalent module/base/offset combinations.
    unique: dict[tuple[str, int, tuple[int, ...]], dict] = {}
    for chain in chains:
        key = (chain["module"], chain["base_offset"], tuple(chain["offsets"]))
        if key not in unique:
            unique[key] = chain

    ordered = list(unique.values())
    ordered.sort(key=lambda c: (c["depth"], c["module"].lower(), c["base_offset"]))
    return ordered


def _format_watchtower_entry(name: str, chain: dict, description: str = "Auto-detected pointer") -> str:
    offsets_hex = [f"0x{off:X}" for off in chain["offsets"]]
    return (
        "{\n"
        f"  \"name\": \"{name}\",\n"
        "  \"type\": \"pointer\",\n"
        f"  \"module\": \"{chain['module']}\",\n"
        f"  \"base_offset\": \"0x{chain['base_offset']:X}\",\n"
        "  \"offsets\": [\n"
        + "\n".join(f"    \"{off}\"," for off in offsets_hex)
        + "\n  ],\n"
        f"  \"description\": \"{description}\"\n"
        "}"
    )


class PointerFinderGUI:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = tk.Tk()
        self.root.title("PointerScanner")
        self.root.geometry("1120x780")
        self.root.minsize(900, 620)

        self._colors = {
            "bg": "#111418",
            "panel": "#171b21",
            "panel_alt": "#1d232b",
            "border": "#2b3440",
            "text": "#e7ecf3",
            "muted": "#9aa7b7",
            "accent": "#2f81f7",
            "accent_hover": "#1f6fe0",
            "danger": "#c2494b",
            "danger_hover": "#a6383b",
            "success": "#26a269",
            "input_bg": "#0f1318",
        }
        self._font_ui = ("Segoe UI", 10)
        self._font_title = ("Segoe UI Semibold", 16)

        self._setup_theme()
        self._load_icon()
        self._apply_icon(self.root)

        self.process_pid: int | None = None
        self.process_name: str = ""
        self.process_exe: str | None = None
        self.handle: int | None = None
        self.can_write_memory = False
        self.pointer_size: int | None = None
        self.regions: list[MemoryRegion] = []
        self.region_min_size: int = 4
        self.modules: list[ModuleEntry] = []

        self.candidates: list[int] = []
        self.history_values: list[str] = []
        self.latest_chains: list[dict] = []
        self.pointer_list_path: str | None = None
        self.pointer_list_hints: list[PointerListHint] = []
        self.pointer_list_choice_map: dict[str, list[PointerListHint]] = {}
        self.candidate_view_addresses: list[int] = []

        self.busy = False
        self.action_buttons: list[tk.Button] = []

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_theme(self) -> None:
        self.root.configure(bg=self._colors["bg"])
        self.root.option_add("*Font", self._font_ui)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Dark.TCombobox",
            fieldbackground=self._colors["input_bg"],
            background=self._colors["panel_alt"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            lightcolor=self._colors["border"],
            darkcolor=self._colors["border"],
            arrowcolor=self._colors["muted"],
            padding=(8, 5),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self._colors["input_bg"])],
            selectbackground=[("readonly", self._colors["input_bg"])],
            selectforeground=[("readonly", self._colors["text"])],
            background=[("readonly", self._colors["panel_alt"])],
            foreground=[("readonly", self._colors["text"])],
            bordercolor=[("focus", self._colors["accent"])],
            lightcolor=[("focus", self._colors["accent"])],
            darkcolor=[("focus", self._colors["accent"])],
        )

        style.configure(
            "Dark.Treeview",
            background=self._colors["input_bg"],
            fieldbackground=self._colors["input_bg"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            relief="flat",
            rowheight=24,
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", self._colors["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=self._colors["panel_alt"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            relief="flat",
            padding=(8, 5),
        )
        style.map(
            "Dark.Treeview.Heading",
            background=[("active", self._colors["panel_alt"])],
            foreground=[("active", self._colors["text"])],
        )

    def _load_icon(self) -> None:
        self._app_icon: tk.PhotoImage | None = None
        root_dir = Path(__file__).resolve().parent.parent
        icon_candidates = [
            root_dir / "icons" / "pointer_scanner.png",
            root_dir / "icons" / "watchtower.png",
        ]
        for icon_path in icon_candidates:
            if not icon_path.exists():
                continue
            try:
                self._app_icon = tk.PhotoImage(file=str(icon_path))
                return
            except Exception:
                self._app_icon = None

    def _apply_icon(self, window: tk.Misc) -> None:
        if self._app_icon is None:
            return
        try:
            window.iconphoto(True, self._app_icon)
        except Exception:
            pass

    def _center_window_on_parent(self, window: tk.Toplevel, parent: tk.Misc | None = None) -> None:
        anchor = parent or self.root
        try:
            anchor.update_idletasks()
            window.update_idletasks()

            parent_x = int(anchor.winfo_rootx())
            parent_y = int(anchor.winfo_rooty())
            parent_w = int(anchor.winfo_width())
            parent_h = int(anchor.winfo_height())

            win_w = int(window.winfo_width())
            win_h = int(window.winfo_height())

            x = parent_x + max(0, (parent_w - win_w) // 2)
            y = parent_y + max(0, (parent_h - win_h) // 2)
            window.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception:
            pass

    def _make_button(
        self,
        parent: tk.Misc,
        text: str,
        *,
        width: int,
        command,
        accent: bool = False,
        danger: bool = False,
    ) -> tk.Button:
        bg = self._colors["panel_alt"]
        hover_bg = "#2a313a"
        fg = self._colors["text"]
        if accent:
            bg = self._colors["accent"]
            hover_bg = self._colors["accent_hover"]
            fg = "#ffffff"
        elif danger:
            bg = self._colors["danger"]
            hover_bg = self._colors["danger_hover"]
            fg = "#ffffff"

        return tk.Button(
            parent,
            text=text,
            width=width,
            command=command,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            padx=8,
            pady=6,
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        )

    def _build_ui(self) -> None:
        root_wrap = tk.Frame(self.root, bg=self._colors["bg"])
        root_wrap.pack(fill=tk.BOTH, expand=True)

        root_scroll = ttk.Scrollbar(root_wrap, orient=tk.VERTICAL)
        root_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        root_canvas = tk.Canvas(
            root_wrap,
            bg=self._colors["bg"],
            highlightthickness=0,
            bd=0,
            yscrollcommand=root_scroll.set,
        )
        root_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        root_scroll.configure(command=root_canvas.yview)

        outer = tk.Frame(root_canvas, bg=self._colors["bg"], padx=12, pady=12)
        outer_window = root_canvas.create_window((0, 0), window=outer, anchor="nw")

        def _sync_root_scroll_region(_event=None) -> None:
            root_canvas.configure(scrollregion=root_canvas.bbox("all"))

        def _sync_outer_width(event: tk.Event) -> None:
            root_canvas.itemconfigure(outer_window, width=event.width)

        outer.bind("<Configure>", _sync_root_scroll_region)
        root_canvas.bind("<Configure>", _sync_outer_width)

        header = tk.Label(
            outer,
            text="PointerScanner",
            font=self._font_title,
            bg=self._colors["bg"],
            fg=self._colors["text"],
            anchor="w",
        )
        header.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Not attached")
        self.proc_var = tk.StringVar(value="Process: none")

        top = tk.Frame(outer, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        top.pack(fill=tk.X, pady=(10, 10))

        top_inner = tk.Frame(top, bg=self._colors["panel"], padx=10, pady=10)
        top_inner.pack(fill=tk.X)

        tk.Label(top_inner, textvariable=self.proc_var, bg=self._colors["panel"], fg=self._colors["text"]).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(top_inner, textvariable=self.status_var, bg=self._colors["panel"], fg=self._colors["muted"]).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        self.btn_pick = self._make_button(top_inner, text="Attach Process", width=14, command=self._on_pick_process, accent=True)
        self.btn_pick.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        top_inner.grid_columnconfigure(0, weight=1)

        settings = tk.Frame(outer, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        settings.pack(fill=tk.X)
        settings_inner = tk.Frame(settings, bg=self._colors["panel"], padx=10, pady=10)
        settings_inner.pack(fill=tk.X)

        self.value_type_var = tk.StringVar(value=self.args.value_type)
        self.entry_name_var = tk.StringVar(value=self.args.entry_name)
        self.current_value_var = tk.StringVar()
        self.refine_value_var = tk.StringVar()
        self.test_write_value_var = tk.StringVar()
        self.max_depth_var = tk.StringVar(value=str(self.args.max_depth))
        self.max_offset_var = tk.StringVar(value=hex(self.args.max_offset))
        self.alignment_var = tk.StringVar(value=str(self.args.alignment))
        self.branch_limit_var = tk.StringVar(value=str(self.args.chain_branch_limit))
        self.max_states_var = tk.StringVar(value=str(self.args.chain_max_states))
        self.known_module_var = tk.StringVar(value=str(getattr(self.args, "known_module", "") or ""))
        self.known_base_offset_var = tk.StringVar(value=str(getattr(self.args, "known_base_offset", "") or ""))
        self.known_offsets_var = tk.StringVar(value=str(getattr(self.args, "known_offsets", "") or ""))
        self.pointer_list_choice_var = tk.StringVar(value="(all loaded pointers)")
        self.pointer_list_status_var = tk.StringVar(value="Pointer list: not loaded")

        tk.Label(settings_inner, text="Value type", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            settings_inner,
            textvariable=self.value_type_var,
            state="readonly",
            values=["i32", "u32", "i16", "u16", "i8", "u8", "f32"],
            style="Dark.TCombobox",
            width=8,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Entry name", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=0, column=1, sticky="w")
        tk.Entry(
            settings_inner,
            textvariable=self.entry_name_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Current value", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=0, column=2, sticky="w")
        tk.Entry(
            settings_inner,
            textvariable=self.current_value_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=1, column=2, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Refine value", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=0, column=3, sticky="w")
        tk.Entry(
            settings_inner,
            textvariable=self.refine_value_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=1, column=3, sticky="ew")

        tk.Label(settings_inner, text="Max depth", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Entry(settings_inner, textvariable=self.max_depth_var, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors["border"], highlightcolor=self._colors["accent"]).grid(row=3, column=0, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Max offset", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=2, column=1, sticky="w", pady=(10, 0))
        tk.Entry(settings_inner, textvariable=self.max_offset_var, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors["border"], highlightcolor=self._colors["accent"]).grid(row=3, column=1, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Alignment", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=2, column=2, sticky="w", pady=(10, 0))
        tk.Entry(settings_inner, textvariable=self.alignment_var, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors["border"], highlightcolor=self._colors["accent"]).grid(row=3, column=2, sticky="ew", padx=(0, 8))

        tk.Label(settings_inner, text="Branch limit", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=2, column=3, sticky="w", pady=(10, 0))
        tk.Entry(settings_inner, textvariable=self.branch_limit_var, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors["border"], highlightcolor=self._colors["accent"]).grid(row=3, column=3, sticky="ew")

        tk.Label(settings_inner, text="Max states", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=4, column=0, sticky="w", pady=(10, 0))
        tk.Entry(settings_inner, textvariable=self.max_states_var, bg=self._colors["input_bg"], fg=self._colors["text"], insertbackground=self._colors["text"], relief=tk.FLAT, highlightthickness=1, highlightbackground=self._colors["border"], highlightcolor=self._colors["accent"]).grid(row=5, column=0, sticky="ew", padx=(0, 8))

        self.btn_scan = self._make_button(settings_inner, text="Initial Scan", width=12, command=self._on_initial_scan, accent=True)
        self.btn_scan.grid(row=5, column=2, sticky="ew", padx=(0, 8), pady=(10, 0))
        self.btn_refine = self._make_button(settings_inner, text="Refine", width=10, command=self._on_refine)
        self.btn_refine.grid(row=5, column=3, sticky="ew", pady=(10, 0))

        for col in range(4):
            settings_inner.grid_columnconfigure(col, weight=1)

        known = tk.Frame(outer, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        known.pack(fill=tk.X, pady=(10, 0))
        known_inner = tk.Frame(known, bg=self._colors["panel"], padx=10, pady=10)
        known_inner.pack(fill=tk.X)

        tk.Label(
            known_inner,
            text="Known pointer (optional, for faster scan)",
            bg=self._colors["panel"],
            fg=self._colors["text"],
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        tk.Label(known_inner, text="Module", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Label(known_inner, text="Base offset", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=1, column=1, sticky="w", pady=(8, 0))
        tk.Label(known_inner, text="Offsets", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=1, column=2, sticky="w", pady=(8, 0))

        tk.Entry(
            known_inner,
            textvariable=self.known_module_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=2, column=0, sticky="ew", padx=(0, 8))

        tk.Entry(
            known_inner,
            textvariable=self.known_base_offset_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=2, column=1, sticky="ew", padx=(0, 8))

        tk.Entry(
            known_inner,
            textvariable=self.known_offsets_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=2, column=2, sticky="ew")

        tk.Label(
            known_inner,
            text="Example offsets: 0x160,0x80,0x1E8 or [\"0x160\",\"0x80\"]",
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            anchor="w",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.btn_load_pointer_list = self._make_button(
            known_inner,
            text="Open Pointer List (.py)",
            width=18,
            command=self._on_load_pointer_list,
        )
        self.btn_load_pointer_list.grid(row=4, column=0, sticky="w", pady=(10, 0))

        self.pointer_list_combo = ttk.Combobox(
            known_inner,
            textvariable=self.pointer_list_choice_var,
            state="readonly",
            values=["(all loaded pointers)"],
            style="Dark.TCombobox",
        )
        self.pointer_list_combo.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(10, 0))

        tk.Label(
            known_inner,
            textvariable=self.pointer_list_status_var,
            bg=self._colors["panel"],
            fg=self._colors["muted"],
            anchor="w",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        for col in range(3):
            known_inner.grid_columnconfigure(col, weight=1)

        content = tk.Frame(outer, bg=self._colors["bg"])
        content.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        body = tk.PanedWindow(content, orient=tk.HORIZONTAL, sashwidth=6, bg=self._colors["bg"], bd=0, relief=tk.FLAT)
        body.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(body, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        right = tk.Frame(body, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        body.add(left, minsize=260)
        body.add(right, minsize=360)

        tk.Label(left, text="Candidate addresses", bg=self._colors["panel"], fg=self._colors["text"], anchor="w", padx=10, pady=8).pack(fill=tk.X)
        list_frame = tk.Frame(left, bg=self._colors["panel"], padx=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.candidate_tree = ttk.Treeview(
            list_frame,
            columns=("address", "value"),
            show="headings",
            style="Dark.Treeview",
            yscrollcommand=sb.set,
        )
        self.candidate_tree.heading("address", text="Address")
        self.candidate_tree.heading("value", text="Current Value")
        self.candidate_tree.column("address", width=160, anchor="w")
        self.candidate_tree.column("value", width=120, anchor="w")

        sb.configure(command=self.candidate_tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.candidate_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.candidates_count_var = tk.StringVar(value="Candidates: 0")
        tk.Label(left, textvariable=self.candidates_count_var, bg=self._colors["panel"], fg=self._colors["muted"], anchor="w", padx=10).pack(fill=tk.X, pady=(0, 10))

        self.btn_refresh_values = self._make_button(
            left,
            text="Refresh Values",
            width=14,
            command=self._on_refresh_candidate_values,
        )
        self.btn_refresh_values.pack(fill=tk.X, padx=10, pady=(0, 10))

        force_row = tk.Frame(left, bg=self._colors["panel"], padx=10, pady=0)
        force_row.pack(fill=tk.X, pady=(0, 10))

        tk.Label(force_row, text="Force value", bg=self._colors["panel"], fg=self._colors["muted"]).grid(row=0, column=0, sticky="w")
        tk.Entry(
            force_row,
            textvariable=self.test_write_value_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        self.btn_test_write = self._make_button(force_row, text="Force Write", width=12, command=self._on_test_write)
        self.btn_test_write.grid(row=1, column=1, sticky="ew")

        force_row.grid_columnconfigure(0, weight=1)

        tk.Label(right, text="Pointer chains and output", bg=self._colors["panel"], fg=self._colors["text"], anchor="w", padx=10, pady=8).pack(fill=tk.X)
        self.output_text = tk.Text(
            right,
            wrap="word",
            height=12,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=8,
            pady=8,
        )
        self.output_text.pack(fill=tk.BOTH, expand=True, padx=10)

        btn_row = tk.Frame(right, bg=self._colors["panel"], padx=10, pady=10)
        btn_row.pack(fill=tk.X)
        self.btn_find_chains = self._make_button(btn_row, text="Find Pointer Chains", width=16, command=self._on_find_chains, accent=True)
        self.btn_copy_json = self._make_button(btn_row, text="Copy Best JSON", width=13, command=self._on_copy_json)
        self.btn_clear = self._make_button(btn_row, text="Clear", width=9, command=self._on_clear, danger=True)
        self.btn_find_chains.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.btn_copy_json.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.btn_clear.grid(row=0, column=2, sticky="ew")
        for col in range(3):
            btn_row.grid_columnconfigure(col, weight=1)

        log_box = tk.Frame(content, bg=self._colors["panel"], highlightthickness=1, highlightbackground=self._colors["border"])
        tk.Label(log_box, text="Log", bg=self._colors["panel"], fg=self._colors["text"], anchor="w", padx=10, pady=8).pack(fill=tk.X)
        self.log_text = tk.Text(
            log_box,
            wrap="word",
            height=6,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        log_box.pack(fill=tk.BOTH, expand=False, pady=(10, 0))

        self.action_buttons = [
            self.btn_pick,
            self.btn_scan,
            self.btn_refine,
            self.btn_test_write,
            self.btn_refresh_values,
            self.btn_find_chains,
            self.btn_copy_json,
            self.btn_clear,
            self.btn_load_pointer_list,
        ]

    def _update_pointer_list_choices(self) -> None:
        options = ["(all loaded pointers)"]
        self.pointer_list_choice_map = {"(all loaded pointers)": list(self.pointer_list_hints)}

        for idx, entry in enumerate(self.pointer_list_hints, start=1):
            label = f"{idx:02d} - {entry.name}"
            options.append(label)
            self.pointer_list_choice_map[label] = [entry]

        self.pointer_list_combo["values"] = options
        if self.pointer_list_choice_var.get() not in self.pointer_list_choice_map:
            self.pointer_list_choice_var.set("(all loaded pointers)")

    def _on_load_pointer_list(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open Pointer List",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            hints = _load_pointer_list_hints(path)
        except Exception as exc:
            messagebox.showerror("Pointer list error", str(exc), parent=self.root)
            return

        self.pointer_list_path = path
        self.pointer_list_hints = hints
        self.pointer_list_status_var.set(f"Pointer list: {os.path.basename(path)} ({len(hints)} entries)")
        self._update_pointer_list_choices()
        self._log(f"Loaded pointer list from {path} with {len(hints)} entries")

    def _log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {message}\n")
        self.log_text.see(tk.END)

    def _append_output(self, text: str, clear: bool = False) -> None:
        if clear:
            self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text + "\n")
        self.output_text.see(tk.END)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in self.action_buttons:
            btn.configure(state=state)
        if busy:
            self.status_var.set("Working...")
        elif self.process_pid:
            self.status_var.set(f"Attached - PID {self.process_pid}")
        else:
            self.status_var.set("Not attached")

    def _run_background(self, task_name: str, fn) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._log(f"{task_name} started")

        def _worker() -> None:
            error: str | None = None
            error_detail: str | None = None
            try:
                fn()
            except Exception as exc:
                error = str(exc)
                error_detail = traceback.format_exc()

            def _done() -> None:
                self._set_busy(False)
                if error:
                    self._log(f"{task_name} failed: {error}")
                    if error_detail:
                        self._log(error_detail.strip())
                    messagebox.showerror("Pointer Finder", error, parent=self.root)
                else:
                    self._log(f"{task_name} completed")

            self.root.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _pick_running_process(self) -> tuple[int, str | None, str] | None:
        all_procs = _collect_running_processes(megamu_only=True)
        if not all_procs:
            all_procs = _collect_running_processes(megamu_only=False)

        dlg = tk.Toplevel(self.root)
        dlg.title("Select Running Process")
        dlg.resizable(True, True)
        dlg.configure(bg=self._colors["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        self._apply_icon(dlg)
        dlg.geometry("540x460")
        dlg.minsize(420, 360)
        self._center_window_on_parent(dlg, self.root)

        result: dict[str, tuple[int, str | None, str] | None] = {"value": None}

        search_frame = tk.Frame(dlg, bg=self._colors["bg"], padx=10, pady=10)
        search_frame.pack(fill=tk.X)
        tk.Label(search_frame, text="Filter:", bg=self._colors["bg"], fg=self._colors["muted"]).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        tk.Entry(
            search_frame,
            textvariable=search_var,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"],
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        list_frame = tk.Frame(dlg, bg=self._colors["bg"], padx=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        listbox = tk.Listbox(
            list_frame,
            bg=self._colors["input_bg"],
            fg=self._colors["text"],
            selectbackground=self._colors["accent"],
            selectforeground="#ffffff",
            activestyle="none",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self._colors["border"],
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        visible_procs: list[tuple[str, int, str | None, list[str]]] = []

        def _refresh(filter_text: str = "") -> None:
            nonlocal visible_procs
            ft = filter_text.strip().lower()
            if ft:
                visible_procs = [
                    p for p in all_procs
                    if ft in p[0].lower() or any(ft in t.lower() for t in p[3])
                ]
            else:
                visible_procs = list(all_procs)
            listbox.delete(0, tk.END)
            for name, pid, _exe, titles in visible_procs:
                if titles:
                    label = f"{name}  -  {titles[0]}  (PID {pid})"
                else:
                    label = f"{name}  (PID {pid})"
                listbox.insert(tk.END, label)

        _refresh()
        search_var.trace_add("write", lambda *_: _refresh(search_var.get()))

        btn_frame = tk.Frame(dlg, bg=self._colors["bg"], padx=10, pady=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        def _confirm() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            name, pid, exe, titles = visible_procs[sel[0]]
            display = titles[0] if titles else name
            result["value"] = (pid, exe, display)
            dlg.destroy()

        def _cancel() -> None:
            dlg.destroy()

        self._make_button(btn_frame, text="Select", width=12, command=_confirm, accent=True).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._make_button(btn_frame, text="Cancel", width=10, command=_cancel).grid(row=0, column=1, sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        listbox.bind("<Double-Button-1>", lambda _e: _confirm())
        dlg.bind("<Return>", lambda _e: _confirm())
        dlg.bind("<Escape>", lambda _e: _cancel())

        self.root.wait_window(dlg)
        return result["value"]

    def _on_pick_process(self) -> None:
        chosen = self._pick_running_process()
        if chosen is None:
            return
        pid, exe_path, display_name = chosen

        if self.handle:
            _close_handle(self.handle)
            self.handle = None

        desired_access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_WRITE
        handle = _open_process(pid, desired_access=desired_access)
        can_write = True
        if not handle:
            handle = _open_process(pid)
            can_write = False
        if not handle:
            messagebox.showerror(
                "Attach failed",
                f"Could not open process (PID {pid}) for reading.\nTry running as Administrator.",
                parent=self.root,
            )
            return

        self.process_pid = pid
        self.process_name = display_name
        self.process_exe = exe_path
        self.handle = handle
        self.can_write_memory = can_write
        self.pointer_size = _get_process_pointer_size(handle)
        self.regions = []
        self.modules = []
        self.candidates = []
        self.history_values = []
        self.latest_chains = []
        self._clear_candidates_view()
        self.candidates_count_var.set("Candidates: 0")
        self.proc_var.set(f"Process: {display_name} (PID {pid})")
        self.status_var.set(f"Attached - PID {pid}")
        self._append_output("", clear=True)
        self._log(f"Attached to {display_name} (PID {pid}), pointer size guess: {self.pointer_size * 8}-bit")
        if not self.can_write_memory:
            self._log("Write access not available. Test Write may fail unless app runs as Administrator.")

    def _parse_scan_params(self) -> tuple[str, int]:
        fmt, value_size = _parse_value_type(self.value_type_var.get())
        return fmt, value_size

    def _get_known_pointer_hint(self) -> KnownPointerHint | None:
        module = self.known_module_var.get().strip()
        base_offset = self.known_base_offset_var.get().strip()
        offsets = self.known_offsets_var.get().strip()
        if not module and not base_offset and not offsets:
            return None
        if not (module and base_offset and offsets):
            raise RuntimeError("Known pointer requires module, base offset, and offsets.")
        try:
            return _build_known_pointer_hint(module, base_offset, offsets)
        except Exception as exc:
            raise RuntimeError(f"Invalid known pointer input: {exc}") from exc

    def _get_pointer_list_hints_for_scan(self) -> list[PointerListHint]:
        if not self.pointer_list_hints:
            return []
        choice = self.pointer_list_choice_var.get().strip()
        selected = self.pointer_list_choice_map.get(choice)
        if selected is None:
            raise RuntimeError("Selected pointer list entry is invalid. Reload the list and try again.")
        return selected

    def _ensure_regions(self, value_size: int) -> None:
        if self.handle is None:
            raise RuntimeError("No process attached.")
        if not self.regions or self.region_min_size != value_size:
            self.regions = list(_iter_regions(self.handle, min_size=value_size))
            self.region_min_size = value_size
            if not self.regions:
                raise RuntimeError("No readable memory regions found.")

    def _on_initial_scan(self) -> None:
        if self.handle is None:
            messagebox.showwarning("No process", "Attach to a process first.", parent=self.root)
            return

        def _task() -> None:
            fmt, value_size = self._parse_scan_params()
            raw = self.current_value_var.get().strip()
            if not raw:
                raise RuntimeError("Current value is required.")
            packed = _pack_value(raw, fmt)
            candidates: list[int] = []
            elapsed = 0.0
            scan_note = ""

            pointer_list_hints = self._get_pointer_list_hints_for_scan()
            if pointer_list_hints:
                if self.pointer_size is None:
                    self.pointer_size = _get_process_pointer_size(self.handle)
                if not self.modules:
                    self.modules = _enum_modules(self.handle, int(self.process_pid or 0))

                t_guided = time.time()
                guided_matches, attempts, matched_entries = _guided_candidates_from_pointer_list(
                    self.handle,
                    self.modules,
                    pointer_list_hints,
                    packed,
                    int(self.pointer_size),
                )
                guided_elapsed = time.time() - t_guided
                if guided_matches:
                    candidates = guided_matches
                    elapsed = guided_elapsed
                    scan_note = (
                        f"Guided scan matched {len(candidates)} candidate(s) in {guided_elapsed:.2f}s "
                        f"from {len(pointer_list_hints)} pointer entry(s), {attempts} variants, "
                        f"{matched_entries} entry match(es)"
                    )
                else:
                    scan_note = (
                        f"Guided scan found no match from {len(pointer_list_hints)} pointer entry(s) "
                        f"after {attempts} variants; falling back to full scan"
                    )
            else:
                hint = self._get_known_pointer_hint()
                if hint is not None:
                    if self.pointer_size is None:
                        self.pointer_size = _get_process_pointer_size(self.handle)
                    if not self.modules:
                        self.modules = _enum_modules(self.handle, int(self.process_pid or 0))

                    t_guided = time.time()
                    guided_matches, attempts = _guided_candidates_from_known_pointer(
                        self.handle,
                        self.modules,
                        hint,
                        packed,
                        int(self.pointer_size),
                    )
                    guided_elapsed = time.time() - t_guided
                    if guided_matches:
                        candidates = guided_matches
                        elapsed = guided_elapsed
                        scan_note = f"Guided scan matched {len(candidates)} candidate(s) in {guided_elapsed:.2f}s ({attempts} variants)"
                    else:
                        scan_note = f"Guided scan found no match after {attempts} variants; falling back to full scan"

            if not candidates:
                self._ensure_regions(value_size)
                t0 = time.time()
                candidates = _scan_regions_for_value(self.handle, self.regions, packed)
                elapsed = time.time() - t0
                if scan_note:
                    scan_note += f"\nFull scan found {len(candidates)} candidate(s) in {elapsed:.1f}s"
                else:
                    scan_note = f"Initial scan found {len(candidates)} candidate(s) in {elapsed:.1f}s"

            self.candidates = candidates
            self.history_values = [raw]
            self.latest_chains = []

            def _ui() -> None:
                self._refresh_candidates_list()
                self._append_output("", clear=True)
                self._append_output(scan_note)

            self.root.after(0, _ui)

        self._run_background("Initial scan", _task)

    def _on_refine(self) -> None:
        if self.handle is None:
            messagebox.showwarning("No process", "Attach to a process first.", parent=self.root)
            return
        if not self.candidates:
            messagebox.showwarning("No candidates", "Run initial scan first.", parent=self.root)
            return

        def _task() -> None:
            fmt, _value_size = self._parse_scan_params()
            raw = self.refine_value_var.get().strip()
            if not raw:
                raise RuntimeError("Refine value is required.")
            packed = _pack_value(raw, fmt)

            filtered = _filter_candidates_by_value(self.handle, self.candidates, packed)
            self.candidates = filtered
            self.history_values.append(raw)

            def _ui() -> None:
                self._refresh_candidates_list()
                self._append_output(f"Refine step: {len(filtered)} candidates remaining")

            self.root.after(0, _ui)

        self._run_background("Refine", _task)

    def _on_test_write(self) -> None:
        if self.handle is None:
            messagebox.showwarning("No process", "Attach to a process first.", parent=self.root)
            return
        if not self.can_write_memory:
            messagebox.showwarning(
                "Write access unavailable",
                "Process handle does not have write access. Run PointerScanner as Administrator and re-attach.",
                parent=self.root,
            )
            return

        target_addr = self._selected_candidate_address()
        if target_addr is None:
            messagebox.showwarning("No candidate", "Run initial scan and select a candidate address first.", parent=self.root)
            return

        raw = self.test_write_value_var.get().strip()
        if not raw:
            raw = self.refine_value_var.get().strip() or self.current_value_var.get().strip()
        if not raw:
            messagebox.showwarning(
                "Missing value",
                "Enter a test write value (or fill refine/current value).",
                parent=self.root,
            )
            return

        def _task() -> None:
            fmt, _value_size = self._parse_scan_params()
            packed = _pack_value(raw, fmt)
            ok = _write_memory(self.handle, target_addr, packed)
            if not ok:
                raise RuntimeError("WriteProcessMemory failed for the selected candidate address.")

            verify = _read_exact(self.handle, target_addr, len(packed))
            verified = verify == packed

            def _ui() -> None:
                self._append_output(
                    f"Test write to {_hex(target_addr)}: value {raw} ({len(packed)} bytes)"
                    + (" [verified]" if verified else " [write ok, verify mismatch]")
                )
                self._log(
                    f"Test write succeeded at {_hex(target_addr)} with value {raw}"
                    + (" (verified)" if verified else " (verify mismatch)")
                )

            self.root.after(0, _ui)

        self._run_background("Test write", _task)

    def _selected_candidate_address(self) -> int | None:
        if not self.candidate_view_addresses:
            return None

        selected = self.candidate_tree.selection()
        if selected:
            item_id = selected[0]
            try:
                idx = int(item_id)
                if 0 <= idx < len(self.candidate_view_addresses):
                    return self.candidate_view_addresses[idx]
            except (ValueError, TypeError):
                pass

        return self.candidate_view_addresses[0]

    def _on_find_chains(self) -> None:
        if self.handle is None:
            messagebox.showwarning("No process", "Attach to a process first.", parent=self.root)
            return
        value_addr = self._selected_candidate_address()
        if value_addr is None:
            messagebox.showwarning("No candidate", "Run initial scan and choose an address first.", parent=self.root)
            return

        def _task() -> None:
            if self.pointer_size is None:
                self.pointer_size = _get_process_pointer_size(self.handle)
            if not self.regions:
                self._ensure_regions(4)
            if not self.modules:
                self.modules = _enum_modules(self.handle, int(self.process_pid or 0))

            max_depth = max(1, int(self.max_depth_var.get().strip()))
            max_offset = max(0, int(self.max_offset_var.get().strip(), 0))
            alignment = max(1, int(self.alignment_var.get().strip()))
            branch_limit = max(1, int(self.branch_limit_var.get().strip()))
            max_states = max(1, int(self.max_states_var.get().strip()))

            chains = _search_pointer_chains(
                self.handle,
                self.regions,
                self.modules,
                value_addr,
                self.pointer_size,
                max_depth=max_depth,
                max_offset=max_offset,
                alignment=alignment,
                branch_limit=branch_limit,
                max_states=max_states,
            )
            self.latest_chains = chains

            def _ui() -> None:
                self._append_output("", clear=True)
                self._append_output(f"Chosen candidate: {_hex(value_addr)}")
                if not chains:
                    self._append_output("No module-root pointer chain found with current limits.")
                    return
                self._append_output(f"Found {len(chains)} candidate chain(s). Top 5:")
                for i, chain in enumerate(chains[:5], start=1):
                    offsets_txt = " -> ".join(f"0x{off:X}" for off in chain["offsets"])
                    self._append_output(
                        f"[{i}] {chain['module']} + 0x{chain['base_offset']:X} | offsets: {offsets_txt}"
                    )
                entry_name = self.entry_name_var.get().strip() or "AutoPointer"
                self._append_output("\nPointer entry JSON snippet:")
                self._append_output(_format_watchtower_entry(entry_name, chains[0]))
                self._append_output("\nRefinement values: " + " -> ".join(self.history_values))

            self.root.after(0, _ui)

        self._run_background("Find pointer chains", _task)

    def _on_copy_json(self) -> None:
        if not self.latest_chains:
            messagebox.showinfo("No chain", "Run 'Find Pointer Chains' first.", parent=self.root)
            return
        entry_name = self.entry_name_var.get().strip() or "AutoPointer"
        payload = _format_watchtower_entry(entry_name, self.latest_chains[0])
        self.root.clipboard_clear()
        self.root.clipboard_append(payload)
        self._log("Best chain JSON copied to clipboard")

    def _refresh_candidates_list(self) -> None:
        selected_before = self._selected_candidate_address()
        self._clear_candidates_view()

        shown_candidates = self.candidates[:2000]
        self.candidate_view_addresses = list(shown_candidates)

        value_type = self.value_type_var.get().strip().lower()
        for idx, addr in enumerate(shown_candidates):
            current_value = self._read_candidate_value_text(addr, value_type)
            self.candidate_tree.insert("", tk.END, iid=str(idx), values=(_hex(addr), current_value))

        if selected_before is not None and selected_before in self.candidate_view_addresses:
            idx = self.candidate_view_addresses.index(selected_before)
            self.candidate_tree.selection_set(str(idx))
            self.candidate_tree.focus(str(idx))
        elif self.candidate_view_addresses:
            self.candidate_tree.selection_set("0")
            self.candidate_tree.focus("0")

        suffix = ""
        if len(self.candidates) > 2000:
            suffix = " (showing first 2000)"
        self.candidates_count_var.set(f"Candidates: {len(self.candidates)}{suffix}")

    def _clear_candidates_view(self) -> None:
        self.candidate_view_addresses = []
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)

    def _read_candidate_value_text(self, address: int, value_type: str) -> str:
        if self.handle is None:
            return "<no process>"

        mapping = {
            "i32": ("<i", 4),
            "u32": ("<I", 4),
            "i16": ("<h", 2),
            "u16": ("<H", 2),
            "i8": ("<b", 1),
            "u8": ("<B", 1),
            "f32": ("<f", 4),
        }
        fmt_size = mapping.get(value_type)
        if fmt_size is None:
            return "<bad type>"

        fmt, size = fmt_size
        blob = _read_exact(self.handle, address, size)
        if blob is None:
            return "<unreadable>"

        try:
            value = struct.unpack(fmt, blob)[0]
        except Exception:
            return "<decode err>"

        if fmt == "<f":
            return f"{float(value):.4f}"
        return str(int(value))

    def _on_refresh_candidate_values(self) -> None:
        if not self.candidates:
            return
        self._refresh_candidates_list()
        self._log("Candidate values refreshed")

    def _on_clear(self) -> None:
        self.candidates = []
        self.history_values = []
        self.latest_chains = []
        self.current_value_var.set("")
        self.refine_value_var.set("")
        self._clear_candidates_view()
        self.candidates_count_var.set("Candidates: 0")
        self._append_output("", clear=True)
        self._log("Cleared scan state")

    def _on_close(self) -> None:
        if self.handle:
            _close_handle(self.handle)
            self.handle = None
        self.can_write_memory = False
        self.root.destroy()

    def run(self) -> int:
        if self.args.pid is not None:
            pid = int(self.args.pid)
            handle = _open_process(pid)
            if handle:
                self.process_pid = pid
                self.process_name = str(pid)
                self.handle = handle
                self.can_write_memory = False
                self.pointer_size = _get_process_pointer_size(handle)
                self.proc_var.set(f"Process: PID {pid}")
                self.status_var.set(f"Attached - PID {pid}")
                self._log(f"Attached directly to PID {pid}")
            else:
                self._log(f"Failed to open PID {pid}")
        self.root.mainloop()
        return 0


def run_cli(args: argparse.Namespace) -> int:
    try:
        fmt, value_size = _parse_value_type(args.value_type)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1

    if args.pid is None:
        pid, proc_name = _choose_process()
    else:
        pid = int(args.pid)
        try:
            proc_name = psutil.Process(pid).name()
        except Exception:
            proc_name = "<unknown>"

    handle = _open_process(pid)
    if not handle:
        print(f"[ERROR] Failed to open PID {pid}. Try running terminal as Administrator.")
        return 1

    print(f"\nAttached to PID {pid} ({proc_name})")

    try:
        pointer_size = _get_process_pointer_size(handle)
        print(f"Pointer size guess: {pointer_size * 8}-bit")

        raw = input("\nEnter the CURRENT in-game value to scan for: ").strip()
        target_value = _pack_value(raw, fmt)

        candidates: list[int] = []
        regions: list[MemoryRegion] = []
        known_hint: KnownPointerHint | None = None
        pointer_list_hints: list[PointerListHint] = []

        if args.pointer_list_file:
            try:
                pointer_list_hints = _load_pointer_list_hints(args.pointer_list_file)
                if args.pointer_name:
                    filtered = [h for h in pointer_list_hints if h.name.lower() == args.pointer_name.lower()]
                    if filtered:
                        pointer_list_hints = filtered
                    else:
                        print(
                            f"[WARN] Pointer name '{args.pointer_name}' was not found in the list; "
                            "using all loaded entries instead."
                        )
                print(
                    f"Loaded pointer list: {args.pointer_list_file} "
                    f"({len(pointer_list_hints)} usable pointer entry/entries)"
                )
            except Exception as exc:
                print(f"[WARN] Pointer list ignored: {exc}")

        if (not pointer_list_hints) and args.known_module and args.known_base_offset and args.known_offsets:
            try:
                known_hint = _build_known_pointer_hint(args.known_module, args.known_base_offset, args.known_offsets)
            except Exception as exc:
                print(f"[WARN] Known pointer hint ignored: {exc}")

        if pointer_list_hints:
            modules = _enum_modules(handle, pid)
            t_guided = time.time()
            guided_matches, attempts, matched_entries = _guided_candidates_from_pointer_list(
                handle,
                modules,
                pointer_list_hints,
                target_value,
                pointer_size,
            )
            guided_elapsed = time.time() - t_guided
            if guided_matches:
                candidates = guided_matches
                print(
                    f"Guided scan matched {len(candidates)} candidate(s) in {guided_elapsed:.2f}s "
                    f"from {len(pointer_list_hints)} pointer entry/entries, {attempts} variants, "
                    f"{matched_entries} entry match(es)"
                )
            else:
                print(
                    f"Guided scan found no match from {len(pointer_list_hints)} pointer entry/entries "
                    f"after {attempts} variants; falling back to full scan"
                )
        elif known_hint is not None:
            modules = _enum_modules(handle, pid)
            t_guided = time.time()
            guided_matches, attempts = _guided_candidates_from_known_pointer(
                handle,
                modules,
                known_hint,
                target_value,
                pointer_size,
            )
            guided_elapsed = time.time() - t_guided
            if guided_matches:
                candidates = guided_matches
                print(
                    f"Guided scan matched {len(candidates)} candidate(s) in {guided_elapsed:.2f}s "
                    f"from {attempts} variants"
                )
            else:
                print(f"Guided scan found no match after {attempts} variants; falling back to full scan")

        if not candidates:
            regions = list(_iter_regions(handle, min_size=value_size))
            if not regions:
                print("[ERROR] No readable memory regions found.")
                return 1
            print(f"Readable committed regions: {len(regions)}")
            t0 = time.time()
            candidates = _scan_regions_for_value(handle, regions, target_value)
            elapsed = time.time() - t0
            print(f"Initial scan found {len(candidates)} addresses in {elapsed:.1f}s")
        else:
            regions = list(_iter_regions(handle, min_size=value_size))
        if not candidates:
            print("[INFO] No candidates found. Try another value type or value.")
            return 0

        history_values = [raw]

        for i in range(args.max_iterations):
            if len(candidates) <= 6:
                break

            print(f"\nRound {i + 1}: perform an in-game action that changes the value.")
            raw_next = input("Enter new value (or q to stop refining): ").strip()
            if raw_next.lower() in {"q", "quit", "exit"}:
                break

            try:
                packed_next = _pack_value(raw_next, fmt)
            except ValueError:
                print("[WARN] Invalid number. Try again.")
                continue

            candidates = _filter_candidates_by_value(handle, candidates, packed_next)
            history_values.append(raw_next)
            print(f"Candidates remaining: {len(candidates)}")

            if not candidates:
                print("[INFO] Candidate list is empty. Restart and try again.")
                return 0

        print("\nCandidate addresses (up to 20 shown):")
        for addr in candidates[:20]:
            print(f"  {_hex(addr)}")

        chosen_addr = candidates[0]
        if len(candidates) > 1:
            chosen_text = input("\nPick address index (1-based, Enter for #1): ").strip()
            if chosen_text:
                try:
                    idx = max(1, int(chosen_text))
                    if idx <= len(candidates):
                        chosen_addr = candidates[idx - 1]
                except ValueError:
                    pass

        print(f"\nUsing candidate address: {_hex(chosen_addr)}")
        print("Running reverse pointer chain search...")

        modules = _enum_modules(handle, pid)
        chains = _search_pointer_chains(
            handle,
            regions,
            modules,
            chosen_addr,
            pointer_size,
            max_depth=max(1, int(args.max_depth)),
            max_offset=max(0, int(args.max_offset)),
            alignment=max(1, int(args.alignment)),
            branch_limit=max(1, int(args.chain_branch_limit)),
            max_states=max(1, int(args.chain_max_states)),
        )

        if not chains:
            print("[INFO] No module-root pointer chain found with current limits.")
            print("       Try increasing --max-depth, --max-offset, or repeat with cleaner candidate narrowing.")
            return 0

        print(f"\nFound {len(chains)} candidate pointer chain(s). Top 5:")
        for i, chain in enumerate(chains[:5], start=1):
            offsets_txt = " -> ".join(f"0x{off:X}" for off in chain["offsets"])
            print(f"  [{i}] {chain['module']} + 0x{chain['base_offset']:X} | offsets: {offsets_txt}")

        best = chains[0]
        print("\nPointer entry JSON snippet:")
        print(_format_watchtower_entry(args.entry_name, best))

        print("\n[INFO] Refinement values used:")
        print("  " + " -> ".join(history_values))
        print("[INFO] Validate this pointer across game restarts before saving.")
        return 0
    finally:
        _close_handle(handle)


def run_gui(args: argparse.Namespace) -> int:
    app = PointerFinderGUI(args)
    return app.run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive pointer finder for Windows processes")
    parser.add_argument("--pid", type=int, default=None, help="PID to attach directly")
    parser.add_argument("--value-type", default="u32", help="Value type: i32, u32, i16, u16, i8, u8, f32")
    parser.add_argument("--max-iterations", type=int, default=8, help="Maximum value-refine rounds")
    parser.add_argument("--max-depth", type=int, default=5, help="Max pointer depth for reverse search")
    parser.add_argument("--max-offset", type=lambda s: int(s, 0), default=0x300, help="Max pointer offset per hop")
    parser.add_argument("--alignment", type=int, default=8, help="Offset alignment filter (4 or 8 recommended)")
    parser.add_argument("--chain-branch-limit", type=int, default=4, help="Parents kept per BFS node")
    parser.add_argument("--chain-max-states", type=int, default=32, help="Max BFS states in chain search")
    parser.add_argument("--entry-name", default="AutoPointer", help="Name used in generated pointer entry")
    parser.add_argument("--known-module", default="", help="Optional known pointer module (e.g. UnityPlayer.dll)")
    parser.add_argument("--known-base-offset", default="", help="Optional known pointer base offset (e.g. 0x01D1C1F0)")
    parser.add_argument(
        "--known-offsets",
        default="",
        help="Optional known pointer offsets, comma format (0x160,0x80) or JSON list",
    )
    parser.add_argument(
        "--pointer-list-file",
        default="",
        help="Optional Python file containing SCAN_ADDRESSES list",
    )
    parser.add_argument(
        "--pointer-name",
        default="",
        help="Optional SCAN_ADDRESSES name filter when --pointer-list-file is used",
    )
    parser.add_argument("--cli", action="store_true", help="Use terminal workflow instead of GUI")
    parser.add_argument("--gui", action="store_true", help="Force GUI mode")
    args = parser.parse_args()

    if not _is_windows():
        print("[ERROR] This tool supports Windows only.")
        return 1

    if args.cli and args.gui:
        print("[ERROR] Use either --cli or --gui, not both.")
        return 1

    if args.cli:
        return run_cli(args)
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
