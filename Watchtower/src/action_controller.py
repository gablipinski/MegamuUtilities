import asyncio
import time

import pyautogui


class ActionController:
    """Executes action sequences for automatic responses in spot-tower mode."""

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
                pyautogui.press(key)
                print(f"    [OK] Key {index}: {key}")
                continue

            print(f"    [WARN] Step {index} skipped (unknown type: {action_type})")
