import asyncio
import time

import pyautogui


class ActionController:
    """Executes action sequences for automatic responses in spot-tower mode."""

    _KEY_ALIASES = {
        'return': 'enter',
        'escape': 'esc',
        'control': 'ctrl',
        'prior': 'pageup',
        'next': 'pagedown',
        'pgup': 'pageup',
        'pgdn': 'pagedown',
        'ins': 'insert',
        'del': 'delete',
        'bksp': 'backspace',
        'spacebar': 'space',
        'kp_add': 'add',
        'kp_subtract': 'subtract',
        'kp_multiply': 'multiply',
        'kp_divide': 'divide',
        'kp_decimal': 'decimal',
    }

    _MODIFIER_KEYS = {
        'ctrl',
        'alt',
        'shift',
        'win',
        'winleft',
        'winright',
    }

    def __init__(
        self,
        click_points: list[tuple[int, int]] | None = None,
        actions: list[dict[str, int | str]] | None = None,
        cooldown_seconds: float = 8.0,
    ):
        if actions is None:
            actions = []

        # Backward compatibility with the old fixed two-click flow.
        if click_points:
            for x, y in click_points:
                actions.append({'type': 'click', 'x': int(x), 'y': int(y)})

        if not actions:
            raise ValueError('ActionController requires at least one configured action')

        self.actions = actions
        self.cooldown_seconds = cooldown_seconds
        self._last_execution = 0.0

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1

    @classmethod
    def _normalize_key_token(cls, token: str) -> str:
        t = token.strip().lower()
        if not t:
            return ''
        if t.startswith('kp_') and len(t) == 4 and t[-1].isdigit():
            return f'num{t[-1]}'
        if t.startswith('numpad') and len(t) == 7 and t[-1].isdigit():
            return f'num{t[-1]}'
        return cls._KEY_ALIASES.get(t, t)

    @staticmethod
    def _is_pyautogui_key_supported(token: str) -> bool:
        keys = getattr(pyautogui, 'KEYBOARD_KEYS', None)
        if not keys:
            return True
        return token in set(keys)

    @classmethod
    def _release_modifiers(cls) -> None:
        # Defensive cleanup in case previous attempts left a modifier held down.
        for mod in ('ctrl', 'alt', 'shift', 'winleft', 'winright'):
            try:
                pyautogui.keyUp(mod)
            except Exception:
                continue

    @classmethod
    def _send_key_combo_primary(cls, combo_parts: list[str], *, interval: float = 0.03) -> None:
        if len(combo_parts) > 1:
            pyautogui.hotkey(*combo_parts, interval=interval)
            return
        pyautogui.press(combo_parts[0])

    @classmethod
    def _send_key_combo_manual(cls, combo_parts: list[str], *, hold_seconds: float = 0.03) -> None:
        if len(combo_parts) == 1:
            key = combo_parts[0]
            pyautogui.keyDown(key)
            time.sleep(max(0.0, hold_seconds))
            pyautogui.keyUp(key)
            return

        modifiers = combo_parts[:-1]
        main_key = combo_parts[-1]

        down_modifiers: list[str] = []
        try:
            for token in modifiers:
                pyautogui.keyDown(token)
                down_modifiers.append(token)
                time.sleep(0.01)

            pyautogui.keyDown(main_key)
            time.sleep(max(0.0, hold_seconds))
            pyautogui.keyUp(main_key)
        finally:
            for token in reversed(down_modifiers):
                try:
                    pyautogui.keyUp(token)
                except Exception:
                    continue

    async def execute_escape_sequence(self, reason: str):
        now = time.monotonic()
        if (now - self._last_execution) < self.cooldown_seconds:
            return

        self._last_execution = now
        print(f"[INFO] Spot-tower action: {reason}")

        # Execute actions in a worker thread to avoid blocking the async loop.
        await asyncio.to_thread(self._perform_actions)

    def _perform_actions(self):
        for index, action in enumerate(self.actions, start=1):
            try:
                action_type = str(action.get('type', '')).lower()
                if action_type == 'click':
                    x = int(action.get('x', 0))
                    y = int(action.get('y', 0))
                    pyautogui.moveTo(x, y, duration=0.12)
                    pyautogui.click(x, y)
                    print(f"    [OK] Click {index} at ({x}, {y})")
                    continue

                if action_type == 'key':
                    key = str(action.get('key', '')).strip()
                    if not key:
                        print(f'    [WARN] Step {index} skipped (empty key)')
                        continue

                    combo_parts = [self._normalize_key_token(part) for part in key.split('+') if part.strip()]
                    if not combo_parts or any(not part for part in combo_parts):
                        print(f'    [WARN] Step {index} skipped (invalid key combo: {key})')
                        continue

                    unsupported = [part for part in combo_parts if not self._is_pyautogui_key_supported(part)]
                    if unsupported:
                        print(
                            f'    [WARN] Step {index} skipped (unsupported key token(s): '
                            f'{", ".join(unsupported)})'
                        )
                        continue

                    sent = False
                    for attempt in range(4):
                        try:
                            self._release_modifiers()
                            if attempt in (0, 2):
                                interval = 0.03 if attempt == 0 else 0.06
                                self._send_key_combo_primary(combo_parts, interval=interval)
                            else:
                                hold = 0.03 if attempt == 1 else 0.05
                                self._send_key_combo_manual(combo_parts, hold_seconds=hold)
                            sent = True
                            break
                        except Exception as exc:
                            if attempt == 3:
                                print(f'    [WARN] Step {index} key send failed after retries: {exc}')
                            else:
                                time.sleep(0.06)

                    self._release_modifiers()

                    if sent:
                        print(f"    [OK] Key {index}: {'+'.join(combo_parts)}")
                    continue

                if action_type == 'text':
                    text = str(action.get('text', ''))
                    if not text:
                        print(f'    [WARN] Step {index} skipped (empty text)')
                        continue
                    pyautogui.write(text, interval=0.01)
                    print(f"    [OK] Text {index}: {text}")
                    continue

                print(f"    [WARN] Step {index} skipped (unknown type: {action_type})")
            except Exception as exc:
                print(f'    [WARN] Step {index} failed: {exc}')
