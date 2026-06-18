import random
import threading
import time

from tkinter import messagebox


def create_process_tower_app():
    from monitor_ui import MonitorUI
    return MonitorUI(initial_mode='PROCESS TOWER')


def find_scan_address_entry(ui, name: str) -> dict | None:
    for entry in ui.saved_scan_addresses:
        if entry['name'] == name:
            return entry
    return None


def find_scan_address_entry_any(ui, names: list[str]) -> dict | None:
    for candidate in names:
        entry = find_scan_address_entry(ui, candidate)
        if entry is not None:
            return entry
    return None


def diagnose_pointer_chain(ui, handle: int, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> str:
    lines = []
    module_base = ui._get_module_base(handle, module_name)
    if module_base is None:
        return f'[DIAG] Module "{module_name}" NOT FOUND in process.'
    lines.append(f'[DIAG] {module_name} base=0x{module_base:X}')
    try:
        base_off = int(base_offset_hex.replace('0x', '').replace('0X', ''), 16)
    except ValueError:
        return '[DIAG] Invalid base_offset hex.'
    try:
        offsets = [int(o.replace('0x', '').replace('0X', ''), 16) for o in offsets_hex]
    except ValueError:
        return '[DIAG] Invalid offsets hex.'
    addr = module_base + base_off
    lines.append(f'[DIAG] Static addr=0x{addr:X}')
    ptr = ui._read_ptr_from_process(handle, addr)
    if ptr is None:
        lines.append(f'[DIAG] FAIL: read_ptr at static addr 0x{addr:X} failed')
        return '\n'.join(lines)
    lines.append(f'[DIAG] P0=0x{ptr:X}')
    if ptr == 0:
        lines.append('[DIAG] FAIL: P0 is null pointer')
        return '\n'.join(lines)
    for i, off in enumerate(offsets[:-1]):
        next_addr = ptr + off
        next_ptr = ui._read_ptr_from_process(handle, next_addr)
        if next_ptr is None:
            lines.append(f'[DIAG] FAIL: offset[{i}]=0x{off:X} read_ptr at 0x{next_addr:X} failed')
            return '\n'.join(lines)
        lines.append(f'[DIAG] P{i + 1}=0x{next_ptr:X} (ptr+0x{off:X}=0x{next_addr:X})')
        if next_ptr == 0:
            lines.append(f'[DIAG] FAIL: null pointer at offset[{i}]=0x{off:X}')
            return '\n'.join(lines)
        ptr = next_ptr
    value_addr = ptr + offsets[-1]
    lines.append(f'[DIAG] Value addr=0x{value_addr:X} (ptr+0x{offsets[-1]:X})')
    value = ui._read_numeric_from_process(handle, value_addr)
    if value is None:
        lines.append(f'[DIAG] FAIL: read_numeric at value addr 0x{value_addr:X} failed')
    else:
        lines.append(f'[DIAG] OK: value={value}')
    return '\n'.join(lines)


def on_process_tower_toggle_scan(ui, idx: int) -> None:
    row = ui._process_tower_rows[idx]
    thread = row.get('scan_thread')
    if thread and thread.is_alive():
        stop_process_tower_scan(ui, idx)
    else:
        start_process_tower_scan(ui, idx)


def start_process_tower_scan(ui, idx: int) -> None:
    row = ui._process_tower_rows[idx]

    handle = row.get('handle')
    if not handle:
        messagebox.showerror('Not attached', 'Attach to a process first.', parent=ui.root)
        return

    entry = find_scan_address_entry(ui, 'SLDetection')
    if entry is None:
        messagebox.showerror(
            'SLDetection not configured',
            'No address named "SLDetection" found in scan_addresses.py.\nUse the "Addresses" button to add it.',
            parent=ui.root,
        )
        return

    try:
        threshold = int(row['threshold_var'].get().strip())
        if threshold < 0:
            raise ValueError
    except ValueError:
        messagebox.showerror('Invalid Max', 'Max must be a positive integer.', parent=ui.root)
        return

    key_combo = row['key_var'].get().strip()
    if not key_combo:
        messagebox.showerror('No trigger key', 'Set a trigger key first using "Set Key".', parent=ui.root)
        return

    pid = row.get('pid')
    stop_ev = row['scan_stop']
    stop_ev.clear()
    is_slayer = bool(row.get('is_slayer_var') and row['is_slayer_var'].get())
    map_entry = find_scan_address_entry_any(ui, ['MapOverlay', 'Map', 'MapVariable', 'MapState']) if is_slayer else None

    thread = threading.Thread(
        target=scan_loop,
        args=(ui, idx, handle, pid, entry, threshold, key_combo, stop_ev, ui._slayer_label(idx), is_slayer, map_entry),
        daemon=True,
    )
    row['scan_thread'] = thread
    row['btn_start'].configure(
        text='Stop',
        bg=ui._colors['danger'],
        activebackground=ui._colors['danger_hover'],
    )
    row['status_var'].set(f'Scanning  •  PID {pid}')
    thread.start()
    ui._log(f'Character #{idx + 1}: scan started (SLDetection, max={threshold}, key={key_combo}).')


def stop_process_tower_scan(ui, idx: int) -> None:
    row = ui._process_tower_rows[idx]
    row['scan_stop'].set()
    thread = row.get('scan_thread')
    if thread:
        thread.join(timeout=2.0)
    reset_process_tower_scan_row(ui, idx)
    ui._log(f'Character #{idx + 1}: scan stopped.')


def reset_process_tower_scan_row(ui, idx: int, status_text: str | None = None) -> None:
    row = ui._process_tower_rows[idx]
    row['scan_thread'] = None
    row['btn_start'].configure(
        text='Start',
        bg=ui._colors['success'],
        activebackground='#1f8f58',
    )
    if status_text is None:
        pid = row.get('pid')
        status_text = f'Attached  •  PID {pid}' if pid else 'Not attached'
    row['status_var'].set(status_text)


def scan_loop(
    ui,
    idx: int,
    handle: int,
    pid: int | None,
    entry: dict,
    threshold: int,
    key_combo: str,
    stop_event: threading.Event,
    slayer_label: str = '',
    is_slayer: bool = False,
    map_entry: dict | None = None,
) -> None:
    last_triggered = 0.0
    cooldown = 3.0
    overlay_open_sent = False
    map_tab_last_sent = 0.0

    while not stop_event.is_set():
        if entry.get('type') == 'pointer':
            value = ui._read_value_pointer(handle, pid, entry['module'], entry['base_offset'], entry['offsets'])
        elif '_resolved' in entry:
            value = ui._read_int_from_process(handle, entry['_resolved'])
        else:
            raw = entry.get('address', '').replace('0x', '').replace('0X', '')
            try:
                value = ui._read_int_from_process(handle, int(raw, 16))
            except ValueError:
                value = None

        now = time.monotonic()
        ui._event_queue.put(('radar_count', {'idx': idx, 'value': value}))

        map_value = None
        if is_slayer and map_entry is not None:
            try:
                if map_entry.get('type') == 'pointer':
                    map_value = ui._read_value_pointer(handle, pid, map_entry['module'], map_entry['base_offset'], map_entry['offsets'])
                elif '_resolved' in map_entry:
                    map_value = ui._read_int_from_process(handle, map_entry['_resolved'])
                else:
                    raw_map = map_entry.get('address', '').replace('0x', '').replace('0X', '')
                    map_value = ui._read_int_from_process(handle, int(raw_map, 16))
            except Exception:
                map_value = None
            if map_value is not None:
                map_value = 1 if int(map_value) != 0 else 0
        ui._event_queue.put(('map_count', {'idx': idx, 'value': map_value}))

        if is_slayer:
            if value == 1:
                overlay_open_sent = False
            else:
                overlay_open_sent = True

            if map_value == 0 and pid:
                if now - map_tab_last_sent >= 1.0:
                    map_tab_last_sent = now
                    try:
                        if ui._send_tab_key_to_pid(pid):
                            ui._event_queue.put(('log', f'Character #{idx + 1}: sent Tab because MapOverlay=0.'))
                        else:
                            ui._event_queue.put(('log', f'Character #{idx + 1}: Tab injection failed for PID {pid}.'))
                    except Exception as exc:
                        ui._event_queue.put(('log', f'Character #{idx + 1}: overlay Tab error: {exc}'))

        if value is not None and value > threshold:
            if now - last_triggered >= cooldown:
                last_triggered = now
                try:
                    if not pid:
                        ui._event_queue.put(('log', f'Character #{idx + 1}: no attached PID; trigger skipped.'))
                        continue
                    time.sleep(random.uniform(0.0, 2.0))
                    ui._focus_process_window(pid)
                    if not ui._send_key_combo_to_pid(pid, key_combo):
                        ui._event_queue.put(('log', f'Character #{idx + 1}: PostMessage failed for PID {pid}; trigger skipped.'))
                        continue
                    ui._event_queue.put(('log', f'Character #{idx + 1}: triggered "{key_combo}" (value={value} > max={threshold}).'))
                except Exception as exc:
                    ui._event_queue.put(('log', f'Character #{idx + 1}: key trigger error: {exc}'))
                if slayer_label:
                    for sub_idx, sub_row in enumerate(ui._process_tower_rows):
                        if sub_row.get('is_slayer_var') and not sub_row['is_slayer_var'].get():
                            if sub_row['radar_var'].get() == slayer_label:
                                sub_key = sub_row['key_var'].get().strip()
                                if sub_key:
                                    sub_pid = sub_row.get('pid')
                                    if not sub_pid:
                                        ui._event_queue.put(('log', f'Character #{sub_idx + 1}: no attached PID; radar trigger skipped.'))
                                        continue
                                    try:
                                        time.sleep(random.uniform(0.0, 2.0))
                                        ui._focus_process_window(sub_pid)
                                        if not ui._send_key_combo_to_pid(sub_pid, sub_key):
                                            ui._event_queue.put(('log', f'Character #{sub_idx + 1}: PostMessage failed for PID {sub_pid}; radar trigger skipped.'))
                                            continue
                                        ui._event_queue.put(('log', f'Character #{sub_idx + 1}: triggered via radar "{slayer_label}" (key={sub_key}).'))
                                    except Exception as exc:
                                        ui._event_queue.put(('log', f'Character #{sub_idx + 1}: radar trigger error: {exc}'))
                ui._event_queue.put(('process_scan_auto_stop', {'idx': idx}))
                stop_event.set()
                break
        stop_event.wait(0.1)
