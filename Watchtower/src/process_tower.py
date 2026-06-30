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


def find_scan_address_entries_any(ui, names: list[str]) -> list[dict]:
    by_name = {
        str(entry.get('name', '')): entry
        for entry in ui.saved_scan_addresses
        if isinstance(entry, dict)
    }
    result: list[dict] = []
    for candidate in names:
        entry = by_name.get(candidate)
        if entry is not None:
            result.append(entry)
    return result


def _read_non_negative_int(raw_value: object, default_value: int = 0) -> int:
    try:
        value = int(str(raw_value).strip())
        if value < 0:
            return default_value
        return value
    except (TypeError, ValueError):
        return default_value


def _row_escape_order(row: dict) -> int:
    order_var = row.get('escape_order_var')
    if order_var is None:
        return 0
    raw = _read_non_negative_int(order_var.get(), 1)
    # UI uses 1..N for readability; internal ordering remains 0-based.
    return max(0, raw - 1)


def _row_escape_delay_ms_bounds(row: dict) -> tuple[int, int]:
    min_var = row.get('escape_delay_min_ms_var')
    max_var = row.get('escape_delay_max_ms_var')
    min_ms = _read_non_negative_int(min_var.get() if min_var is not None else 100, 100)
    max_ms = _read_non_negative_int(max_var.get() if max_var is not None else 300, 300)
    if max_ms < min_ms:
        min_ms, max_ms = max_ms, min_ms
    return min_ms, max_ms


def _build_group_escape_targets(ui, slayer_idx: int, slayer_label: str) -> list[dict]:
    targets: list[dict] = []

    if 0 <= slayer_idx < len(ui._process_tower_rows):
        slayer_row = ui._process_tower_rows[slayer_idx]
        targets.append({'idx': slayer_idx, 'row': slayer_row, 'reason': 'slayer'})

    if not slayer_label:
        return targets

    for sub_idx, sub_row in enumerate(ui._process_tower_rows):
        if sub_idx == slayer_idx:
            continue
        is_slayer = bool(sub_row.get('is_slayer_var') and sub_row['is_slayer_var'].get())
        if is_slayer:
            continue
        if sub_row['radar_var'].get() == slayer_label:
            targets.append({'idx': sub_idx, 'row': sub_row, 'reason': 'radar'})

    if targets:
        max_order = len(targets) - 1
        for target in targets:
            raw_order = _row_escape_order(target['row'])
            target['order'] = max(0, min(raw_order, max_order))

    targets.sort(key=lambda t: (int(t.get('order', 0)), int(t['idx'])))
    return targets


def _read_entry_numeric(ui, handle: int, pid: int | None, entry: dict) -> int | None:
    if entry.get('type') == 'pointer':
        return ui._read_value_pointer(handle, pid, entry['module'], entry['base_offset'], entry['offsets'])
    if '_resolved' in entry:
        return ui._read_numeric_from_process(handle, entry['_resolved'])

    raw = entry.get('address', '').replace('0x', '').replace('0X', '')
    try:
        return ui._read_numeric_from_process(handle, int(raw, 16))
    except ValueError:
        return None


def _read_entry_numeric_with_retry(
    ui,
    handle: int,
    pid: int | None,
    entry: dict,
    attempts: int = 3,
) -> int | None:
    for _ in range(max(1, attempts)):
        value = _read_entry_numeric(ui, handle, pid, entry)
        if value is not None:
            return value
        time.sleep(0.01)
    return None


def _parse_hex_int(raw: str) -> int | None:
    try:
        return int(str(raw).replace('0x', '').replace('0X', '').strip(), 16)
    except ValueError:
        return None


def _read_pointer_chain_with_offsets(
    ui,
    handle: int,
    module_name: str,
    base_offset_hex: str,
    offsets: list[int],
) -> int | None:
    module_base = ui._get_module_base(handle, module_name)
    if module_base is None or not offsets:
        return None

    base_off = _parse_hex_int(base_offset_hex)
    if base_off is None:
        return None

    ptr = ui._read_ptr_from_process(handle, module_base + base_off)
    if ptr is None:
        return None

    for off in offsets[:-1]:
        ptr = ui._read_ptr_from_process(handle, ptr + off)
        if ptr is None:
            return None

    return ui._read_numeric_from_process(handle, ptr + offsets[-1])


def _read_map_pointer_with_offset_fallback(ui, handle: int, entry: dict) -> tuple[int | None, int | None]:
    """Try the configured pointer chain, then probe nearby second-hop offsets.

    Returns (value, used_second_offset_or_none).
    """
    module_name = str(entry.get('module', '')).strip()
    base_offset_hex = str(entry.get('base_offset', '0x0')).strip()
    raw_offsets = list(entry.get('offsets', []) or [])
    offsets: list[int] = []
    for raw in raw_offsets:
        parsed = _parse_hex_int(str(raw))
        if parsed is None:
            return None, None
        offsets.append(parsed)

    if len(offsets) < 3:
        return None, None

    # 1) canonical chain first
    value = _read_pointer_chain_with_offsets(ui, handle, module_name, base_offset_hex, offsets)
    if value is not None:
        return value, offsets[1]

    # 2) probe nearby candidates for second hop (common patch-level drift)
    second = offsets[1]
    candidates = [second, second - 0x10, second - 0x8, second + 0x8, second + 0x10, second + 0x18, second + 0x20]
    seen: set[int] = set()
    for cand in candidates:
        if cand < 0 or cand in seen:
            continue
        seen.add(cand)

        trial = list(offsets)
        trial[1] = cand
        value = _read_pointer_chain_with_offsets(ui, handle, module_name, base_offset_hex, trial)
        if value is None:
            continue

        # Map state should be binary-ish. Accept only plausible values.
        if int(value) in (0, 1):
            return int(value), cand

    return None, None


def _calibrate_map_pointer_offsets(
    ui,
    handle: int,
    entry: dict,
) -> tuple[list[int], int] | None:
    """Probe nearby first/second-hop offsets and return the first binary map value match."""
    module_name = str(entry.get('module', '')).strip()
    base_offset_hex = str(entry.get('base_offset', '0x0')).strip()
    raw_offsets = list(entry.get('offsets', []) or [])

    offsets: list[int] = []
    for raw in raw_offsets:
        parsed = _parse_hex_int(str(raw))
        if parsed is None:
            return None
        offsets.append(parsed)

    if len(offsets) < 3:
        return None

    deltas = [0, -0x10, -0x8, 0x8, 0x10, -0x18, 0x18, -0x20, 0x20]

    for d0 in deltas:
        for d1 in deltas:
            trial = list(offsets)
            trial[0] = max(0, trial[0] + d0)
            trial[1] = max(0, trial[1] + d1)
            value = _read_pointer_chain_with_offsets(ui, handle, module_name, base_offset_hex, trial)
            if value is None:
                continue
            if int(value) in (0, 1):
                return trial, int(value)

    return None


def _trigger_escape_group(
    ui,
    stop_event: threading.Event,
    slayer_idx: int,
    slayer_label: str,
    value: int,
    threshold: int,
) -> None:
    targets = _build_group_escape_targets(ui, slayer_idx, slayer_label)
    if not targets:
        ui._event_queue.put(('log', f'Character #{slayer_idx + 1}: no escape targets available.'))
        return

    for target in targets:
        if stop_event.is_set():
            return

        idx = int(target['idx'])
        row = target['row']
        key_combo = row['key_var'].get().strip()
        if not key_combo:
            ui._event_queue.put(('log', f'Character #{idx + 1}: trigger key not set; skipped.'))
            continue

        pid = row.get('pid')
        if not pid:
            ui._event_queue.put(('log', f'Character #{idx + 1}: no attached PID; trigger skipped.'))
            continue

        order_value = int(target.get('order', 0))
        min_delay_ms, max_delay_ms = _row_escape_delay_ms_bounds(row)
        delay_ms = random.randint(min_delay_ms, max_delay_ms)
        if delay_ms > 0 and stop_event.wait(delay_ms / 1000.0):
            return

        try:
            ui._focus_process_window(pid)
            if not ui._send_key_combo_to_pid(pid, key_combo):
                ui._event_queue.put(('log', f'Character #{idx + 1}: PostMessage failed for PID {pid}; trigger skipped.'))
                continue
            ui._event_queue.put(('triggered', {'idx': idx, 'pid': pid, 'key': key_combo}))

            if idx == slayer_idx:
                ui._event_queue.put((
                    'log',
                    (
                        f'Character #{idx + 1}: triggered "{key_combo}" '
                        f'(value={value} > max={threshold}, order={order_value}, delay={delay_ms}ms).'
                    ),
                ))
            else:
                ui._event_queue.put((
                    'log',
                    (
                        f'Character #{idx + 1}: triggered via radar "{slayer_label}" '
                        f'(key={key_combo}, order={order_value}, delay={delay_ms}ms).'
                    ),
                ))
        except Exception as exc:
            label = 'key trigger error' if idx == slayer_idx else 'radar trigger error'
            ui._event_queue.put(('log', f'Character #{idx + 1}: {label}: {exc}'))


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


def diagnose_pointer_chain_compact(ui, handle: int, module_name: str, base_offset_hex: str, offsets_hex: list[str]) -> str:
    """One-line diagnosis for periodic runtime logs."""
    module_base = ui._get_module_base(handle, module_name)
    if module_base is None:
        return f'module-not-found ({module_name})'

    try:
        base_off = int(base_offset_hex.replace('0x', '').replace('0X', ''), 16)
    except ValueError:
        return 'invalid-base-offset'

    try:
        offsets = [int(o.replace('0x', '').replace('0X', ''), 16) for o in offsets_hex]
    except ValueError:
        return 'invalid-offsets'

    if not offsets:
        return 'no-offsets'

    static_addr = module_base + base_off
    ptr = ui._read_ptr_from_process(handle, static_addr)
    if ptr is None:
        return f'fail@static-read addr=0x{static_addr:X}'
    if ptr == 0:
        return f'fail@static-null addr=0x{static_addr:X}'

    for i, off in enumerate(offsets[:-1]):
        hop_addr = ptr + off
        next_ptr = ui._read_ptr_from_process(handle, hop_addr)
        if next_ptr is None:
            return f'fail@hop{i} off=0x{off:X} addr=0x{hop_addr:X}'
        if next_ptr == 0:
            return f'fail@hop{i}-null off=0x{off:X} addr=0x{hop_addr:X}'
        ptr = next_ptr

    value_addr = ptr + offsets[-1]
    value = ui._read_numeric_from_process(handle, value_addr)
    if value is None:
        return f'fail@value-read addr=0x{value_addr:X}'
    return f'ok value={value} addr=0x{value_addr:X}'


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
            'No address named "SLDetection" is configured.\nUse the "Addresses" button to add it.',
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

    order_value = _row_escape_order(row)
    min_delay_ms, max_delay_ms = _row_escape_delay_ms_bounds(row)

    pid = row.get('pid')
    stop_ev = row['scan_stop']
    stop_ev.clear()
    is_slayer = bool(row.get('is_slayer_var') and row['is_slayer_var'].get())
    map_entries = find_scan_address_entries_any(
        ui,
        [
            'MapOverlay', 'MinimapOverlay', 'MiniMapOverlay',
            'Map', 'MapVariable', 'MapState',
            'Minimap', 'MiniMap', 'IsMapOpen',
        ],
    ) if is_slayer else []

    if is_slayer and not map_entries:
        ui._log(
            (
                f'Character #{idx + 1}: minimap pointer not found in configured addresses '
                f'(expected names like MapOverlay/Minimap). Map will show N/A until configured.'
            )
        )

    thread = threading.Thread(
        target=scan_loop,
        args=(
            ui,
            idx,
            handle,
            pid,
            entry,
            threshold,
            key_combo,
            stop_ev,
            ui._slayer_label(idx),
            is_slayer,
            map_entries,
        ),
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
    ui._log(
        (
            f'Character #{idx + 1}: scan started '
            f'(SLDetection, max={threshold}, key={key_combo}, '
            f'order={order_value}, delay={min_delay_ms}-{max_delay_ms}ms).'
        )
    )


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
    map_entries: list[dict] | None = None,
) -> None:
    last_triggered = 0.0
    cooldown = 3.0
    overlay_open_sent = False
    map_tab_last_sent = 0.0
    map_fail_debug_last_logged = 0.0
    map_calibration_last_attempt = 0.0
    map_pointer_override_by_name: dict[str, list[int]] = {}

    while not stop_event.is_set():
        value = _read_entry_numeric_with_retry(ui, handle, pid, entry, attempts=2)

        now = time.monotonic()
        ui._event_queue.put(('radar_count', {'idx': idx, 'value': value}))

        map_value = None
        map_reason = 'ok'
        if is_slayer:
            if not map_entries:
                map_reason = 'no-address'
            else:
                saw_candidate_with_module = False
                saw_module_missing = False

                for candidate in map_entries:
                    try:
                        if candidate.get('type') == 'pointer':
                            module_name = str(candidate.get('module', '')).strip()
                            if module_name and ui._get_module_base(handle, module_name) is None:
                                saw_module_missing = True
                                continue

                            candidate_name = str(candidate.get('name', '')).strip()
                            override_offsets = map_pointer_override_by_name.get(candidate_name)
                            if override_offsets:
                                map_value = _read_pointer_chain_with_offsets(
                                    ui,
                                    handle,
                                    module_name,
                                    str(candidate.get('base_offset', '0x0')),
                                    override_offsets,
                                )
                                if map_value is not None:
                                    saw_candidate_with_module = True
                                    if int(map_value) in (0, 1):
                                        break
                                    map_value = None

                        saw_candidate_with_module = True
                        map_value = _read_entry_numeric_with_retry(ui, handle, pid, candidate, attempts=4)

                        if map_value is None and candidate.get('type') == 'pointer':
                            fallback_value, used_second_off = _read_map_pointer_with_offset_fallback(ui, handle, candidate)
                            if fallback_value is not None:
                                map_value = fallback_value
                                ui._event_queue.put(
                                    (
                                        'log',
                                        (
                                            f'Character #{idx + 1}: Map pointer fallback matched second offset '
                                            f'0x{used_second_off:X}.'
                                        ),
                                    )
                                )
                    except Exception:
                        map_value = None

                    if map_value is not None:
                        break

                if map_value is not None:
                    map_value = 1 if int(map_value) != 0 else 0
                    map_reason = 'ok'
                elif (not saw_candidate_with_module) and saw_module_missing:
                    map_reason = 'module-not-found'
                else:
                    map_reason = 'read-fail'

                if map_reason == 'read-fail' and (now - map_calibration_last_attempt) >= 5.0:
                    map_calibration_last_attempt = now
                    for candidate in map_entries:
                        if candidate.get('type') != 'pointer':
                            continue
                        module_name = str(candidate.get('module', '')).strip()
                        if module_name and ui._get_module_base(handle, module_name) is None:
                            continue

                        calibrated = _calibrate_map_pointer_offsets(ui, handle, candidate)
                        if calibrated is None:
                            continue

                        calibrated_offsets, calibrated_value = calibrated
                        candidate_name = str(candidate.get('name', '')).strip()
                        map_pointer_override_by_name[candidate_name] = calibrated_offsets
                        map_value = int(calibrated_value)
                        map_reason = 'ok'
                        ui._event_queue.put(
                            (
                                'log',
                                (
                                    f'Character #{idx + 1}: Map pointer calibrated for "{candidate_name}" '
                                    f'with offsets[0]=0x{calibrated_offsets[0]:X}, '
                                    f'offsets[1]=0x{calibrated_offsets[1]:X}.'
                                ),
                            )
                        )
                        break

            if map_reason == 'read-fail' and (now - map_fail_debug_last_logged) >= 3.0:
                map_fail_debug_last_logged = now
                try:
                    debug_entry = map_entries[0] if map_entries else None
                    if debug_entry is not None and debug_entry.get('type') == 'pointer':
                        detail = diagnose_pointer_chain_compact(
                            ui,
                            handle,
                            str(debug_entry.get('module', '')),
                            str(debug_entry.get('base_offset', '0x0')),
                            list(debug_entry.get('offsets', []) or []),
                        )
                    else:
                        detail = 'non-pointer map entry could not be read'
                except Exception as exc:
                    detail = f'diag-error: {exc}'

                ui._event_queue.put(
                    (
                        'log',
                        (
                            f'Character #{idx + 1}: Map read debug -> reason={map_reason}, '
                            f'detail={detail}'
                        ),
                    )
                )

        ui._event_queue.put(('map_count', {'idx': idx, 'value': map_value, 'reason': map_reason}))

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
                _trigger_escape_group(ui, stop_event, idx, slayer_label, int(value), threshold)
                ui._event_queue.put(('process_scan_auto_stop', {'idx': idx}))
                stop_event.set()
                break
        stop_event.wait(0.1)
