from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MacroStep = dict[str, int | str | bool]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'configs' / 'config.json'


@dataclass
class MacroConfig:
    name: str
    hotkey: str
    active: bool
    repeat_while_held: bool
    steps: list[MacroStep]


def _sanitize_steps(raw_steps: object) -> list[MacroStep]:
    if not isinstance(raw_steps, list):
        return []

    steps: list[MacroStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue

        step_type = str(item.get('type', '')).strip().lower()
        if step_type == 'click':
            try:
                x = int(item.get('x', 0))
                y = int(item.get('y', 0))
            except Exception:
                continue
            button = str(item.get('button', 'left')).strip().lower()
            if button not in {'left', 'right'}:
                button = 'left'
            at_origin = bool(item.get('at_origin', False))
            steps.append({'type': 'click', 'x': x, 'y': y, 'button': button, 'at_origin': at_origin})
        elif step_type == 'key':
            key_name = str(item.get('key', '')).strip()
            if key_name:
                action = str(item.get('action', 'tap')).strip().lower()
                if action not in {'tap', 'press', 'release'}:
                    action = 'tap'
                steps.append({'type': 'key', 'key': key_name, 'action': action})
        elif step_type == 'delay':
            try:
                ms = max(0, int(item.get('ms', 0)))
            except Exception:
                continue
            step: MacroStep = {'type': 'delay', 'ms': ms}
            try:
                jitter_value = max(0, int(item.get('jitter_pct', 0)))
            except Exception:
                jitter_value = 0
            if jitter_value:
                step['jitter_pct'] = jitter_value
            steps.append(step)
        elif step_type == 'return_cursor':
            steps.append({'type': 'return_cursor'})

    return steps


def load_macros(config_path: Path | None = None) -> list[MacroConfig]:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return []

    with open(path, 'r', encoding='utf-8-sig') as file:
        payload = json.load(file)

    raw_macros = payload.get('macros', []) if isinstance(payload, dict) else []
    if not isinstance(raw_macros, list):
        return []

    macros: list[MacroConfig] = []
    for raw in raw_macros:
        if not isinstance(raw, dict):
            continue

        name = str(raw.get('name', '')).strip()
        hotkey = str(raw.get('hotkey', '')).strip()
        active = bool(raw.get('active', True))
        repeat_while_held = bool(raw.get('repeat_while_held', False))
        steps = _sanitize_steps(raw.get('steps', []))
        if not name or not hotkey or not steps:
            continue

        macros.append(MacroConfig(name=name, hotkey=hotkey, active=active, repeat_while_held=repeat_while_held, steps=steps))

    return macros


def save_macros(macros: list[MacroConfig], config_path: Path | None = None) -> None:
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        'macros': [
            {
                'name': macro.name,
                'hotkey': macro.hotkey,
                'active': macro.active,
                'repeat_while_held': macro.repeat_while_held,
                'steps': macro.steps,
            }
            for macro in macros
        ]
    }

    with open(path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
