from __future__ import annotations

from collections import deque
import ctypes
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

import pyautogui
from pynput import keyboard, mouse


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

_VK_BY_KEY_NAME = {
    'ctrl': 0x11,
    'shift': 0x10,
    'alt': 0x12,
    'win': 0x5B,
    'enter': 0x0D,
    'esc': 0x1B,
    'space': 0x20,
    'tab': 0x09,
    'backspace': 0x08,
    'delete': 0x2E,
    'insert': 0x2D,
    'home': 0x24,
    'end': 0x23,
    'pageup': 0x21,
    'pagedown': 0x22,
    'up': 0x26,
    'down': 0x28,
    'left': 0x25,
    'right': 0x27,
}

_VK_MOUSE_BUTTONS = {
    'left': 0x01,
    'right': 0x02,
    'middle': 0x04,
    'x1': 0x05,
    'x2': 0x06,
}


@dataclass
class MacroDefinition:
    name: str
    hotkey_raw: str
    trigger_kind: str
    hotkey_tokens: frozenset[str]
    mouse_button: str | None
    repeat_while_held: bool
    steps: list[dict[str, int | str | bool]]


class MacroEngine:
    def __init__(self, logger: Callable[[str, str], None]):
        self._logger = logger
        self._macros_by_name: dict[str, MacroDefinition] = {}
        self._pressed_keys: set[str] = set()
        self._pressed_buttons: set[str] = set()
        self._latched: set[str] = set()
        self._listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._poll_thread: threading.Thread | None = None
        self._poll_stop_event: threading.Event | None = None
        self._input_state_lock = threading.RLock()
        self._trigger_queue_lock = threading.Lock()
        self._queued_triggers: deque[str] = deque()
        self._execution_lock = threading.Lock()
        self._repeat_state_lock = threading.Lock()
        self._repeat_stop_events: dict[str, threading.Event] = {}

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.08

    @property
    def running(self) -> bool:
        return self._listener is not None

    def set_macros(self, raw_macros: list[dict[str, object]]) -> None:
        self._stop_all_repeat_macros()
        with self._input_state_lock:
            self._macros_by_name.clear()
            for item in raw_macros:
                name = str(item.get('name', '')).strip()
                hotkey = str(item.get('hotkey', '')).strip()
                repeat_while_held = bool(item.get('repeat_while_held', False))
                steps_value = item.get('steps', [])
                if not name or not hotkey or not isinstance(steps_value, list):
                    continue

                steps = [dict(step) for step in steps_value if isinstance(step, dict)]
                trigger_kind, tokens, mouse_button = self._parse_binding(hotkey)
                if not steps or (trigger_kind == 'keyboard' and not tokens) or (trigger_kind == 'mouse' and not mouse_button):
                    continue

                self._macros_by_name[name] = MacroDefinition(
                    name=name,
                    hotkey_raw=hotkey,
                    trigger_kind=trigger_kind,
                    hotkey_tokens=tokens,
                    mouse_button=mouse_button,
                    repeat_while_held=repeat_while_held,
                    steps=steps,
                )

        with self._trigger_queue_lock:
            self._queued_triggers.clear()

    def start(self) -> None:
        if self._listener is not None:
            return

        with self._input_state_lock:
            self._pressed_keys.clear()
            self._pressed_buttons.clear()
            self._latched.clear()

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self._mouse_listener.start()

        self._poll_stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_inputs_loop, daemon=True)
        self._poll_thread.start()
        self._logger('Global hotkey listener started.', 'notification')

    def stop(self) -> None:
        if self._listener is None:
            return

        listener = self._listener
        self._listener = None
        mouse_listener = self._mouse_listener
        self._mouse_listener = None
        poll_stop_event = self._poll_stop_event
        self._poll_stop_event = None
        self._poll_thread = None
        with self._input_state_lock:
            self._pressed_keys.clear()
            self._pressed_buttons.clear()
            self._latched.clear()
        with self._trigger_queue_lock:
            self._queued_triggers.clear()
        self._stop_all_repeat_macros()

        if poll_stop_event is not None:
            poll_stop_event.set()

        try:
            listener.stop()
        except Exception:
            pass

        try:
            if mouse_listener is not None:
                mouse_listener.stop()
        except Exception:
            pass

        self._logger('Global hotkey listener stopped.', 'other')

    def trigger_macro(self, name: str) -> bool:
        macro = self._macros_by_name.get(name)
        if macro is None:
            return False

        if self._execution_lock.locked():
            with self._trigger_queue_lock:
                self._queued_triggers.append(macro.name)
                queued_count = len(self._queued_triggers)
            self._logger(
                f'Macro queued: {macro.name} will run after current macro (queue: {queued_count}).',
                'notification',
            )
            return True

        origin = self._read_cursor_position()
        threading.Thread(target=self._run_macro_once, args=(macro, origin), daemon=True).start()
        return True

    def _start_repeat_macro(self, macro: MacroDefinition) -> bool:
        with self._repeat_state_lock:
            if macro.name in self._repeat_stop_events:
                return False

            if self._execution_lock.locked():
                # Keep retrying from the polling loop while the trigger remains held.
                return False

            stop_event = threading.Event()
            self._repeat_stop_events[macro.name] = stop_event

        origin = self._read_cursor_position()
        thread = threading.Thread(target=self._run_repeat_macro, args=(macro, origin, stop_event), daemon=True)
        thread.start()
        return True

    def _stop_repeat_macro(self, macro_name: str) -> None:
        with self._repeat_state_lock:
            stop_event = self._repeat_stop_events.pop(macro_name, None)

        if stop_event is not None:
            stop_event.set()

    def _stop_all_repeat_macros(self) -> None:
        with self._repeat_state_lock:
            stop_events = list(self._repeat_stop_events.values())
            self._repeat_stop_events.clear()

        for stop_event in stop_events:
            stop_event.set()

    def _run_repeat_macro(
        self,
        macro: MacroDefinition,
        origin: tuple[int, int] | None,
        stop_event: threading.Event,
    ) -> None:
        held_keys_by_macro: set[str] = set()
        try:
            while not stop_event.is_set():
                held_keys_by_macro = self._run_macro_once(
                    macro,
                    origin,
                    release_unbalanced_keys=False,
                    carried_held_keys=held_keys_by_macro,
                    drain_queued=False,
                )
                if stop_event.is_set():
                    break
                if not self._is_macro_trigger_active(macro):
                    # Typematic/repeat key events can briefly desync pressed-state tracking.
                    # Re-check shortly before ending a hold-repeat cycle.
                    if stop_event.wait(0.12):
                        break
                    if not self._is_macro_trigger_active(macro):
                        break
        finally:
            # On hold-repeat stop, release any macro-held keys once.
            for token in list(held_keys_by_macro):
                try:
                    pyautogui.keyUp(token)
                except Exception:
                    pass
            with self._repeat_state_lock:
                current = self._repeat_stop_events.get(macro.name)
                if current is stop_event:
                    self._repeat_stop_events.pop(macro.name, None)

    def _run_macro_once(
        self,
        macro: MacroDefinition,
        origin: tuple[int, int] | None,
        *,
        release_unbalanced_keys: bool = True,
        carried_held_keys: set[str] | None = None,
        drain_queued: bool = True,
    ) -> set[str]:
        held_keys_by_macro: set[str] = set(carried_held_keys or ())
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
                        action = str(step.get('action', 'tap')).strip().lower()
                        key_tokens = [self._normalize_key(token) for token in key_name.split('+') if token.strip()]
                        if action == 'press':
                            if len(key_tokens) > 1:
                                for token in key_tokens:
                                    if token not in held_keys_by_macro:
                                        pyautogui.keyDown(token)
                                        held_keys_by_macro.add(token)
                            elif key_tokens:
                                if key_tokens[0] not in held_keys_by_macro:
                                    pyautogui.keyDown(key_tokens[0])
                                    held_keys_by_macro.add(key_tokens[0])
                            self._logger(f'    Step {index}: key press {key_name}', 'other')
                        elif action == 'release':
                            if len(key_tokens) > 1:
                                for token in reversed(key_tokens):
                                    pyautogui.keyUp(token)
                                    held_keys_by_macro.discard(token)
                            elif key_tokens:
                                pyautogui.keyUp(key_tokens[0])
                                held_keys_by_macro.discard(key_tokens[0])
                            self._logger(f'    Step {index}: key release {key_name}', 'other')
                        else:
                            if len(key_tokens) > 1:
                                pyautogui.hotkey(*key_tokens)
                            elif key_tokens:
                                pyautogui.press(key_tokens[0])
                            self._logger(f'    Step {index}: key tap {key_name}', 'other')
                    elif step_type == 'delay':
                        ms = max(0, int(step.get('ms', 0)))
                        jitter_pct = max(0, int(step.get('jitter_pct', 0)))
                        actual_ms = ms
                        if jitter_pct:
                            jitter_range = max(0, round(ms * jitter_pct / 100.0))
                            if jitter_range:
                                actual_ms = max(0, ms + random.randint(-jitter_range, jitter_range))
                        time.sleep(actual_ms / 1000.0)
                        if jitter_pct:
                            self._logger(f'    Step {index}: delay {actual_ms}ms (base {ms}ms, jitter {jitter_pct}%)', 'other')
                        else:
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
                if release_unbalanced_keys:
                    # For one-shot runs, avoid leaving keys latched.
                    for token in list(held_keys_by_macro):
                        try:
                            pyautogui.keyUp(token)
                        except Exception:
                            pass
                    held_keys_by_macro.clear()
                self._release_input_safety()

        if drain_queued:
            self._drain_queued_trigger()

        return held_keys_by_macro

    def _drain_queued_trigger(self) -> None:
        if self._execution_lock.locked():
            return

        next_name = ''
        with self._trigger_queue_lock:
            while self._queued_triggers:
                candidate = self._queued_triggers.popleft()
                if candidate in self._macros_by_name:
                    next_name = candidate
                    break

        if not next_name:
            return

        next_macro = self._macros_by_name.get(next_name)
        if next_macro is None:
            return

        origin = self._read_cursor_position()
        threading.Thread(target=self._run_macro_once, args=(next_macro, origin), daemon=True).start()

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        key_name = self._key_to_name(key)
        if not key_name:
            return

        with self._input_state_lock:
            self._pressed_keys.add(key_name)
        self._handle_input_change()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        key_name = self._key_to_name(key)
        if key_name:
            with self._input_state_lock:
                self._pressed_keys.discard(key_name)

        self._handle_input_change()

    def _on_mouse_click(self, _x: int, _y: int, button: mouse.Button, pressed: bool) -> None:
        button_name = self._mouse_button_to_name(button)
        if not button_name:
            if pressed:
                raw_name = getattr(button, 'name', '') or str(button)
                raw_value = getattr(button, 'value', None)
                self._logger(f'Mouse trigger ignored: unsupported button {raw_name} (value={raw_value})', 'ignore')
            return

        if pressed:
            with self._input_state_lock:
                self._pressed_buttons.add(button_name)
        else:
            with self._input_state_lock:
                self._pressed_buttons.discard(button_name)

        self._handle_input_change()

    def _handle_input_change(self) -> None:
        repeat_actions: list[MacroDefinition] = []
        one_shot_actions: list[str] = []
        stop_candidates: list[tuple[str, threading.Event]] = []
        with self._input_state_lock:
            matched = self._matched_macros()
            to_trigger = sorted(matched - self._latched)

            for macro_name in sorted(matched):
                macro = self._macros_by_name.get(macro_name)
                if macro is not None:
                    if macro.repeat_while_held:
                        repeat_actions.append(macro)

            for macro_name in to_trigger:
                macro = self._macros_by_name.get(macro_name)
                if macro is not None and not macro.repeat_while_held:
                    one_shot_actions.append(macro_name)

            self._latched = matched

        with self._repeat_state_lock:
            stop_candidates = list(self._repeat_stop_events.items())

        for macro in repeat_actions:
            self._start_repeat_macro(macro)

        for macro_name in one_shot_actions:
            self.trigger_macro(macro_name)

        for macro_name, stop_event in stop_candidates:
            if macro_name in matched:
                continue
            macro = self._macros_by_name.get(macro_name)
            if macro is not None and self._is_macro_trigger_active(macro):
                continue
            stop_event.set()

    def _poll_inputs_loop(self) -> None:
        poll_stop = self._poll_stop_event
        if poll_stop is None:
            return

        # Poll trigger state so brief/missed listener events do not block macro start.
        while not poll_stop.wait(0.03):
            self._handle_input_change()

    def _matched_macros(self) -> set[str]:
        matched: set[str] = set()
        for macro in self._macros_by_name.values():
            if self._is_macro_trigger_active(macro):
                matched.add(macro.name)
        return matched

    def _is_macro_trigger_active(self, macro: MacroDefinition) -> bool:
        if macro.trigger_kind == 'mouse':
            if macro.mouse_button:
                vk = _VK_MOUSE_BUTTONS.get(macro.mouse_button)
                if vk is not None and self._is_vk_down(vk):
                    return True
            return bool(macro.mouse_button and macro.mouse_button in self._pressed_buttons)

        if macro.hotkey_tokens:
            if all(self._is_key_token_down(token) for token in macro.hotkey_tokens):
                return True

        if macro.trigger_kind == 'mouse':
            return bool(macro.mouse_button and macro.mouse_button in self._pressed_buttons)
        return macro.hotkey_tokens.issubset(self._pressed_keys)

    def _is_key_token_down(self, token: str) -> bool:
        vk = _VK_BY_KEY_NAME.get(token)
        if vk is None:
            if len(token) == 1 and token.isalpha():
                vk = ord(token.upper())
            elif len(token) == 1 and token.isdigit():
                vk = ord(token)
            elif token.startswith('f') and token[1:].isdigit():
                fn = int(token[1:])
                if 1 <= fn <= 24:
                    vk = 0x70 + (fn - 1)
        if vk is None:
            return False
        return self._is_vk_down(vk)

    def _is_vk_down(self, vk: int) -> bool:
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _parse_binding(self, hotkey: str) -> tuple[str, frozenset[str], str | None]:
        normalized = hotkey.strip().lower()
        if normalized.startswith('mouse:'):
            mouse_button = self._normalize_mouse_button(normalized.split(':', 1)[1])
            return 'mouse', frozenset(), mouse_button
        return 'keyboard', self._parse_hotkey_tokens(hotkey), None

    def _parse_hotkey_tokens(self, hotkey: str) -> frozenset[str]:
        tokens = [self._normalize_key(token) for token in hotkey.split('+') if token.strip()]
        clean_tokens = [token for token in tokens if token]
        return frozenset(clean_tokens)

    def _normalize_mouse_button(self, value: str) -> str | None:
        button = value.strip().lower().replace(' ', '')
        if not button:
            return None
        aliases = {
            'back': 'x1',
            'forward': 'x2',
            'button4': 'x1',
            'button5': 'x2',
            'button8': 'x1',
            'button9': 'x2',
            'xbutton1': 'x1',
            'xbutton2': 'x2',
            'mouse4': 'x1',
            'mouse5': 'x2',
            'browser_back': 'x1',
            'browser_forward': 'x2',
        }
        button = aliases.get(button, button)
        if button.startswith('button') and button[6:].isdigit():
            if button.endswith('4') or button.endswith('8'):
                return 'x1'
            if button.endswith('5') or button.endswith('9'):
                return 'x2'
        if button in {'left', 'right', 'middle', 'x1', 'x2'}:
            return button
        return None

    def _mouse_button_to_name(self, button: mouse.Button) -> str:
        name = getattr(button, 'name', '') or str(button).split('.')[-1]
        normalized = self._normalize_mouse_button(name)
        if normalized:
            return normalized

        raw_value = getattr(button, 'value', None)
        if isinstance(raw_value, int):
            # Some drivers/reporting paths expose side buttons as numeric values.
            if raw_value in {4, 8}:
                return 'x1'
            if raw_value in {5, 9, 16}:
                return 'x2'

        return ''

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
        # Keep this minimal to avoid interrupting physical mouse-hold combat flows.
        pass


def get_cursor_position() -> tuple[int, int] | None:
    try:
        pos = pyautogui.position()
        return int(pos.x), int(pos.y)
    except Exception:
        return None
