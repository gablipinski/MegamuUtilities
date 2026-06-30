import random
import threading
import time

from tkinter import messagebox


MAP_ENTRY_CANDIDATE_NAMES = [
    'MapOverlay', 'MinimapOverlay', 'MiniMapOverlay',
    'Map', 'MapVariable', 'MapState',
    'Minimap', 'MiniMap', 'IsMapOpen',
]


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


def _get_map_entries_preferred(ui) -> list[dict]:
    entries = find_scan_address_entries_any(ui, MAP_ENTRY_CANDIDATE_NAMES)
    # Prefer pointer entries first for per-character map state.
    return sorted(entries, key=lambda e: 0 if str(e.get('type', '')).lower() == 'pointer' else 1)


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
    *,
    attempt_num: int | None = None,
    max_attempts: int | None = None,
) -> None:
    targets = _build_group_escape_targets(ui, slayer_idx, slayer_label)
    if not targets:
        ui._event_queue.put(('log', f'Character #{slayer_idx + 1}: no escape targets available.'))
        return

    for target in targets:
        if stop_event.is_set():
            return
        _trigger_escape_target(
            ui,
            stop_event,
            target,
            slayer_idx,
            slayer_label,
            value,
            threshold,
            attempt_num=attempt_num,
            max_attempts=max_attempts,
        )


def _trigger_escape_target(
    ui,
    stop_event: threading.Event,
    target: dict,
    slayer_idx: int,
    slayer_label: str,
    value: int,
    threshold: int,
    *,
    attempt_num: int | None = None,
    max_attempts: int | None = None,
) -> bool:
    idx = int(target['idx'])
    row = target['row']
    key_combo = row['key_var'].get().strip()
    if not key_combo:
        ui._event_queue.put(('log', f'Character #{idx + 1}: trigger key not set; skipped.'))
        ui._event_queue.put(('retry_status', {'idx': idx, 'text': 'Retry: skipped (no trigger key)'}))
        return False

    pid = row.get('pid')
    if not pid:
        ui._event_queue.put(('log', f'Character #{idx + 1}: no attached PID; trigger skipped.'))
        ui._event_queue.put(('retry_status', {'idx': idx, 'text': 'Retry: skipped (no PID attached)'}))
        return False

    order_value = int(target.get('order', 0))
    min_delay_ms, max_delay_ms = _row_escape_delay_ms_bounds(row)
    delay_ms = random.randint(min_delay_ms, max_delay_ms)
    if delay_ms > 0 and stop_event.wait(delay_ms / 1000.0):
        return False

    try:
        ui._focus_process_window(pid)
        if not ui._send_key_combo_to_pid(pid, key_combo):
            ui._event_queue.put(('log', f'Character #{idx + 1}: PostMessage failed for PID {pid}; trigger skipped.'))
            return False
        ui._event_queue.put(('triggered', {'idx': idx, 'pid': pid, 'key': key_combo}))
        ts = time.strftime('%H:%M:%S')
        if attempt_num is not None and max_attempts is not None:
            ui._event_queue.put(
                (
                    'retry_status',
                    {
                        'idx': idx,
                        'text': f'Retry: trigger {attempt_num}/{max_attempts} sent at {ts}',
                    },
                )
            )
        else:
            ui._event_queue.put(
                (
                    'retry_status',
                    {
                        'idx': idx,
                        'text': f'Retry: trigger sent at {ts}',
                    },
                )
            )

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
        return True
    except Exception as exc:
        label = 'key trigger error' if idx == slayer_idx else 'radar trigger error'
        ui._event_queue.put(('log', f'Character #{idx + 1}: {label}: {exc}'))
        ui._event_queue.put(('retry_status', {'idx': idx, 'text': f'Retry: trigger error ({exc})'}))
        return False


def _read_target_map_state(
    ui,
    target: dict,
    map_pointer_override_by_pid_name: dict[tuple[int, str], list[int]],
    map_calibration_last_attempt_by_pid: dict[int, float] | None = None,
) -> tuple[int | None, str]:
    idx = int(target['idx'])
    row = target['row']
    handle = row.get('handle')
    pid = row.get('pid')
    if not handle or not pid:
        return None, 'no-handle'

    handle_int = int(handle)
    pid_int = int(pid)

    map_entries = _get_map_entries_preferred(ui)
    overrides: dict[str, list[int]] = {}
    for entry in map_entries:
        name = str(entry.get('name', '')).strip()
        key = (pid_int, name)
        if key in map_pointer_override_by_pid_name:
            overrides[name] = list(map_pointer_override_by_pid_name[key])

    map_value, map_reason = _read_map_overlay_state_once(
        ui,
        idx,
        handle_int,
        pid_int,
        map_entries,
        overrides,
    )

    if map_reason == 'read-fail' and map_calibration_last_attempt_by_pid is not None:
        now = time.monotonic()
        last = map_calibration_last_attempt_by_pid.get(pid_int, 0.0)
        if now - last >= 5.0:
            map_calibration_last_attempt_by_pid[pid_int] = now
            for candidate in map_entries:
                if str(candidate.get('type', '')).lower() != 'pointer':
                    continue

                module_name = str(candidate.get('module', '')).strip()
                if module_name and ui._get_module_base(handle_int, module_name) is None:
                    continue

                calibrated = _calibrate_map_pointer_offsets(ui, handle_int, candidate)
                if calibrated is None:
                    continue

                calibrated_offsets, calibrated_value = calibrated
                candidate_name = str(candidate.get('name', '')).strip()
                map_pointer_override_by_pid_name[(pid_int, candidate_name)] = list(calibrated_offsets)
                map_value = int(calibrated_value)
                map_reason = 'ok'
                ui._event_queue.put(
                    (
                        'log',
                        (
                            f'Character #{idx + 1}: per-char map pointer calibrated for "{candidate_name}" '
                            f'(PID {pid_int}) with offsets[0]=0x{calibrated_offsets[0]:X}, '
                            f'offsets[1]=0x{calibrated_offsets[1]:X}.'
                        ),
                    )
                )
                break

    return map_value, map_reason


def _sync_target_minimap_state(
    ui,
    stop_event: threading.Event,
    target: dict,
    map_pointer_override_by_pid_name: dict[tuple[int, str], list[int]],
    map_calibration_last_attempt_by_pid: dict[int, float],
    map_tab_last_sent_by_pid: dict[int, float],
    *,
    tab_cooldown_seconds: float = 1.0,
    allow_force_open: bool = True,
) -> tuple[int | None, str]:
    idx = int(target['idx'])
    row = target['row']
    pid_raw = row.get('pid')

    map_value, map_reason = _read_target_map_state(
        ui,
        target,
        map_pointer_override_by_pid_name,
        map_calibration_last_attempt_by_pid,
    )

    if allow_force_open and map_value == 0 and pid_raw:
        try:
            pid = int(pid_raw)
            now = time.monotonic()
            last_sent = map_tab_last_sent_by_pid.get(pid, 0.0)
            if now - last_sent >= tab_cooldown_seconds:
                map_tab_last_sent_by_pid[pid] = now
                if ui._send_tab_key_to_pid(pid):
                    ui._event_queue.put(('log', f'Character #{idx + 1}: sent Tab because MapOverlay=0 (per-char).'))
                    if not stop_event.wait(0.12):
                        map_value, map_reason = _read_target_map_state(
                            ui,
                            target,
                            map_pointer_override_by_pid_name,
                            map_calibration_last_attempt_by_pid,
                        )
                else:
                    ui._event_queue.put(('log', f'Character #{idx + 1}: Tab injection failed for PID {pid}.'))
        except Exception as exc:
            ui._event_queue.put(('log', f'Character #{idx + 1}: per-char overlay Tab error: {exc}'))

    ui._event_queue.put(('map_count', {'idx': idx, 'value': map_value, 'reason': map_reason}))
    return map_value, map_reason


def _schedule_target_ghost_close(
    ui,
    target: dict,
    scheduled_pids: set[int],
    *,
    delay_seconds: float = 10.0,
) -> None:
    row = target['row']
    pid_raw = row.get('pid')
    if not pid_raw:
        return

    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        return

    if pid in scheduled_pids:
        return
    scheduled_pids.add(pid)

    idx = int(target['idx'])

    def _worker() -> None:
        ui._event_queue.put(
            (
                'log',
                f'Character #{idx + 1}: ghost close scheduled in {int(delay_seconds)}s for PID {pid}.',
            )
        )
        time.sleep(delay_seconds)
        try:
            ok = bool(ui._close_process_app(pid))
            if ok:
                ui._event_queue.put(('log', f'Character #{idx + 1}: ghost close sent to PID {pid}.'))
            else:
                ui._event_queue.put(('log', f'Character #{idx + 1}: ghost close failed for PID {pid}.'))
        except Exception as exc:
            ui._event_queue.put(('log', f'Character #{idx + 1}: ghost close error for PID {pid}: {exc}'))

    threading.Thread(target=_worker, daemon=True).start()


def _retry_group_by_minimap(
    ui,
    stop_event: threading.Event,
    slayer_idx: int,
    slayer_label: str,
    value: int,
    threshold: int,
    *,
    max_attempts: int = 3,
    wait_seconds: float = 11.0,
) -> None:
    targets = _build_group_escape_targets(ui, slayer_idx, slayer_label)
    if not targets:
        return

    slayer_row = ui._process_tower_rows[slayer_idx] if 0 <= slayer_idx < len(ui._process_tower_rows) else None
    ghost_app_enabled = bool(
        slayer_row
        and slayer_row.get('ghost_app_var') is not None
        and slayer_row['ghost_app_var'].get()
    )

    map_pointer_override_by_pid_name: dict[tuple[int, str], list[int]] = {}
    map_calibration_last_attempt_by_pid: dict[int, float] = {}
    map_tab_last_sent_by_pid: dict[int, float] = {}
    ghost_close_scheduled_pids: set[int] = set()

    post_escape_map_delay_seconds = 2
    before_retry_delay_seconds = max(0, int(wait_seconds) - post_escape_map_delay_seconds)

    states: list[dict] = []
    for target in targets:
        idx = int(target['idx'])
        states.append(
            {
                'target': target,
                'idx': idx,
                'attempt': 1,
                'phase': 'post_escape_delay',
                'remaining': post_escape_map_delay_seconds,
                'done': False,
            }
        )

    while not stop_event.is_set():
        active = [s for s in states if not bool(s.get('done'))]
        if not active:
            return

        for state in active:
            idx = int(state['idx'])
            attempt = int(state['attempt'])
            phase = str(state['phase'])
            remaining = int(state['remaining'])
            target = state['target']

            if phase == 'post_escape_delay':
                if remaining > 0:
                    ui._event_queue.put(
                        (
                            'retry_status',
                            {
                                'idx': idx,
                                'text': f'Retry {attempt}/{max_attempts}: map check in {remaining}s',
                            },
                        )
                    )
                    state['remaining'] = remaining - 1
                    continue

                map_value, _map_reason = _sync_target_minimap_state(
                    ui,
                    stop_event,
                    target,
                    map_pointer_override_by_pid_name,
                    map_calibration_last_attempt_by_pid,
                    map_tab_last_sent_by_pid,
                    allow_force_open=False,
                )

                if map_value != 1:
                    ui._event_queue.put(
                        (
                            'retry_status',
                            {
                                'idx': idx,
                                'text': f'Retry: done at {attempt}/{max_attempts} (map={map_value})',
                            },
                        )
                    )
                    if ghost_app_enabled:
                        _schedule_target_ghost_close(
                            ui,
                            target,
                            ghost_close_scheduled_pids,
                            delay_seconds=10.0,
                        )
                    state['done'] = True
                    continue

                if attempt >= max_attempts:
                    ui._event_queue.put(
                        (
                            'retry_status',
                            {
                                'idx': idx,
                                'text': f'Retry: reached {max_attempts}/{max_attempts}; map=1',
                            },
                        )
                    )
                    if ghost_app_enabled:
                        _schedule_target_ghost_close(
                            ui,
                            target,
                            ghost_close_scheduled_pids,
                            delay_seconds=10.0,
                        )
                    state['done'] = True
                    continue

                state['phase'] = 'before_retry_delay'
                state['remaining'] = before_retry_delay_seconds
                continue

            if phase == 'before_retry_delay':
                next_attempt = attempt + 1
                if remaining > 0:
                    ui._event_queue.put(
                        (
                            'retry_status',
                            {
                                'idx': idx,
                                'text': f'Retry {next_attempt}/{max_attempts}: next trigger in {remaining}s',
                            },
                        )
                    )
                    state['remaining'] = remaining - 1
                    continue

                state['attempt'] = next_attempt
                _trigger_escape_target(
                    ui,
                    stop_event,
                    target,
                    slayer_idx,
                    slayer_label,
                    value,
                    threshold,
                    attempt_num=next_attempt,
                    max_attempts=max_attempts,
                )
                state['phase'] = 'post_escape_delay'
                state['remaining'] = post_escape_map_delay_seconds

        if stop_event.wait(1.0):
            return


def _read_map_overlay_state_once(
    ui,
    idx: int,
    handle: int,
    pid: int | None,
    map_entries: list[dict] | None,
    map_pointer_override_by_name: dict[str, list[int]],
) -> tuple[int | None, str]:
    if not map_entries:
        return None, 'no-address'

    saw_candidate_with_module = False
    saw_module_missing = False
    map_value = None

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
        return (1 if int(map_value) != 0 else 0), 'ok'

    if (not saw_candidate_with_module) and saw_module_missing:
        return None, 'module-not-found'
    return None, 'read-fail'


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
    map_entries = _get_map_entries_preferred(ui) if is_slayer else []

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
    group_map_poll_last = 0.0
    group_map_poll_interval = 0.05
    map_pointer_override_by_name: dict[str, list[int]] = {}
    map_pointer_override_by_pid_name: dict[tuple[int, str], list[int]] = {}
    target_map_calibration_last_attempt_by_pid: dict[int, float] = {}
    group_map_tab_last_sent_by_pid: dict[int, float] = {}

    while not stop_event.is_set():
        value = _read_entry_numeric_with_retry(ui, handle, pid, entry, attempts=2)

        now = time.monotonic()
        ui._event_queue.put(('radar_count', {'idx': idx, 'value': value}))

        map_value = None
        map_reason = 'ok'
        if is_slayer:
            map_value, map_reason = _read_map_overlay_state_once(
                ui,
                idx,
                handle,
                pid,
                map_entries,
                map_pointer_override_by_name,
            )

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

        if is_slayer and (now - group_map_poll_last) >= group_map_poll_interval:
            group_map_poll_last = now
            for target in _build_group_escape_targets(ui, idx, slayer_label):
                target_idx = int(target['idx'])
                if target_idx == idx:
                    continue
                _sync_target_minimap_state(
                    ui,
                    stop_event,
                    target,
                    map_pointer_override_by_pid_name,
                    target_map_calibration_last_attempt_by_pid,
                    group_map_tab_last_sent_by_pid,
                )

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
                _trigger_escape_group(
                    ui,
                    stop_event,
                    idx,
                    slayer_label,
                    int(value),
                    threshold,
                    attempt_num=1,
                    max_attempts=3,
                )

                if is_slayer and not stop_event.is_set():
                    _retry_group_by_minimap(
                        ui,
                        stop_event,
                        idx,
                        slayer_label,
                        int(value),
                        threshold,
                        max_attempts=3,
                        wait_seconds=11.0,
                    )

                ui._event_queue.put(('process_scan_auto_stop', {'idx': idx}))
                stop_event.set()
                break
        stop_event.wait(0.05)
