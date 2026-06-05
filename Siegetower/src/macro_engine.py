from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import pyautogui
from pynput import keyboard


_KEY_ALIASES = {
    'control': 'ctrl',
    'ctrl_l': 'ctrl',
    'ctrl_r': 'ctrl',
    'control_l': 'ctrl',
    'control_r': 'ctrl',
    'shift_l': 'shift',
    'shift_r': 'shift',
    'alt_l': 'alt',
    'alt_r': 'alt',
    'cmd': 'win',
    'cmd_l': 'win',
    'cmd_r': 'win',
    'esc': 'esc',
    'escape': 'esc',
    'return': 'enter',
    'space': 'space',
    'page_up': 'pageup',
    'page_down': 'pagedown',
    'caps_lock': 'capslock',
}


@dataclass
class MacroDefinition:
    name: str
    hotkey_raw: str
    hotkey_tokens: frozenset[str]
    steps: list[dict[str, int | str]]


class MacroEngine:
    def __init__(self, logger: Callable[[str, str], None]):
        self._logger = logger
        self._macros_by_name: dict[str, MacroDefinition] = {}
        self._pressed_keys: set[str] = set()
        self._latched: set[str] = set()
        self._listener: keyboard.Listener | None = None
        self._execution_lock = threading.Lock()

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.08

    @property
    def running(self) -> bool:
        return self._listener is not None

    def set_macros(self, raw_macros: list[dict[str, object]]) -> None:
        self._macros_by_name.clear()
        for item in raw_macros:
            name = str(item.get('name', '')).strip()
            hotkey = str(item.get('hotkey', '')).strip()
            steps_value = item.get('steps', [])
            if not name or not hotkey or not isinstance(steps_value, list):
                continue

            steps = [dict(step) for step in steps_value if isinstance(step, dict)]
            tokens = self._parse_hotkey_tokens(hotkey)
            if not steps or not tokens:
                continue

            self._macros_by_name[name] = MacroDefinition(
                name=name,
                hotkey_raw=hotkey,
                hotkey_tokens=tokens,
                steps=steps,
            )

    def start(self) -> None:
        if self._listener is not None:
            return

        self._pressed_keys.clear()
        self._latched.clear()

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        self._logger('Global hotkey listener started.', 'notification')

    def stop(self) -> None:
        if self._listener is None:
            return

        listener = self._listener
        self._listener = None
        self._pressed_keys.clear()
        self._latched.clear()

        try:
            listener.stop()
        except Exception:
            pass

        self._logger('Global hotkey listener stopped.', 'other')

    def trigger_macro(self, name: str) -> bool:
        macro = self._macros_by_name.get(name)
        if macro is None:
            return False

        if self._execution_lock.locked():
            self._logger(
                f'Macro ignored: {macro.name} skipped because another macro is still running.',
                'ignore',
            )
            return False

        origin = self._read_cursor_position()
        threading.Thread(target=self._run_macro, args=(macro, origin), daemon=True).start()
        return True

    def _run_macro(self, macro: MacroDefinition, origin: tuple[int, int] | None) -> None:
        with self._execution_lock:
            self._logger(f"Macro triggered: {macro.name} ({macro.hotkey_raw})", 'notification')
            try:
                for index, step in enumerate(macro.steps, start=1):
                    step_type = str(step.get('type', '')).strip().lower()
                    if step_type == 'click':
                        x = int(step.get('x', 0))
                        y = int(step.get('y', 0))
                        button = str(step.get('button', 'left')).strip().lower()
                        if button not in {'left', 'right'}:
                            button = 'left'
                        at_origin = bool(step.get('at_origin', False))
                        if at_origin:
                            if origin is None:
                                self._logger(f'    Step {index}: {button} click at origin skipped (origin unavailable)', 'ignore')
                                continue
                            x = int(origin[0])
                            y = int(origin[1])
                        pyautogui.moveTo(x, y, duration=0.1)
                        pyautogui.click(x, y, button=button)
                        if at_origin:
                            self._logger(f'    Step {index}: {button} click at origin ({x}, {y})', 'other')
                        else:
                            self._logger(f'    Step {index}: {button} click ({x}, {y})', 'other')
                    elif step_type == 'key':
                        key_name = str(step.get('key', '')).strip()
                        key_tokens = [self._normalize_key(token) for token in key_name.split('+') if token.strip()]
                        if len(key_tokens) > 1:
                            pyautogui.hotkey(*key_tokens)
                        elif key_tokens:
                            pyautogui.press(key_tokens[0])
                        self._logger(f'    Step {index}: key {key_name}', 'other')
                    elif step_type == 'delay':
                        ms = max(0, int(step.get('ms', 0)))
                        time.sleep(ms / 1000.0)
                        self._logger(f'    Step {index}: delay {ms}ms', 'other')
                    elif step_type == 'return_cursor':
                        if origin is None:
                            self._logger(f'    Step {index}: return cursor skipped (origin unavailable)', 'ignore')
                        else:
                            pyautogui.moveTo(origin[0], origin[1], duration=0.1)
                            self._logger(f'    Step {index}: return cursor to ({origin[0]}, {origin[1]})', 'other')
                    else:
                        self._logger(f'    Step {index}: skipped unknown type {step_type}', 'ignore')
            finally:
                self._release_input_safety()

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        key_name = self._key_to_name(key)
        if not key_name:
            return

        self._pressed_keys.add(key_name)
        matched = {
            macro.name
            for macro in self._macros_by_name.values()
            if macro.hotkey_tokens.issubset(self._pressed_keys)
        }

        to_trigger = sorted(matched - self._latched)
        for macro_name in to_trigger:
            self.trigger_macro(macro_name)

        self._latched = matched

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        key_name = self._key_to_name(key)
        if key_name:
            self._pressed_keys.discard(key_name)

        matched = {
            macro.name
            for macro in self._macros_by_name.values()
            if macro.hotkey_tokens.issubset(self._pressed_keys)
        }
        self._latched.intersection_update(matched)

    def _parse_hotkey_tokens(self, hotkey: str) -> frozenset[str]:
        tokens = [self._normalize_key(token) for token in hotkey.split('+') if token.strip()]
        clean_tokens = [token for token in tokens if token]
        return frozenset(clean_tokens)

    def _normalize_key(self, value: str) -> str:
        key = value.strip().lower().replace(' ', '')
        if not key:
            return ''
        return _KEY_ALIASES.get(key, key)

    def _key_to_name(self, key: keyboard.Key | keyboard.KeyCode) -> str:
        if isinstance(key, keyboard.KeyCode):
            char = key.char
            if char:
                return self._normalize_key(char)
            vk = key.vk
            if vk is None:
                return ''
            vk_name = self._vk_to_name(vk)
            return self._normalize_key(vk_name)

        name = str(key).split('.')[-1]
        return self._normalize_key(name)

    def _vk_to_name(self, vk: int) -> str:
        # Common alphanumeric and function virtual-key codes on Windows.
        if 48 <= vk <= 57:
            return chr(vk)
        if 65 <= vk <= 90:
            return chr(vk).lower()
        if 112 <= vk <= 123:
            return f'f{vk - 111}'
        return str(vk)

    def _read_cursor_position(self) -> tuple[int, int] | None:
        try:
            pos = pyautogui.position()
            return int(pos.x), int(pos.y)
        except Exception:
            return None

    def _release_input_safety(self) -> None:
        # Some games can keep injected button/modifier state latched; force release.
        for button in ('left', 'right', 'middle'):
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                pass

        for key_name in ('ctrl', 'shift', 'alt', 'win'):
            try:
                pyautogui.keyUp(key_name)
            except Exception:
                pass


def get_cursor_position() -> tuple[int, int] | None:
    try:
        pos = pyautogui.position()
        return int(pos.x), int(pos.y)
    except Exception:
        return None
