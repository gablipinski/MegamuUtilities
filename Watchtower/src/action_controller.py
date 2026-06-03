import asyncio
import time

import pyautogui


class ActionController:
    """Executa sequencias de clique para respostas automaticas no modo spot-tower."""

    def __init__(self, click_points: list[tuple[int, int]], cooldown_seconds: float = 8.0):
        if len(click_points) != 2:
            raise ValueError('ActionController requer exatamente 2 pontos de clique')

        self.click_points = click_points
        self.cooldown_seconds = cooldown_seconds
        self._last_execution = 0.0

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1

    async def execute_escape_sequence(self, reason: str):
        now = time.monotonic()
        if (now - self._last_execution) < self.cooldown_seconds:
            return

        self._last_execution = now
        print(f"[🛡️] Acao spot-tower: {reason}")

        # Executa os cliques no thread pool para nao bloquear o loop assíncrono.
        await asyncio.to_thread(self._perform_clicks)

    def _perform_clicks(self):
        for index, (x, y) in enumerate(self.click_points, start=1):
            pyautogui.moveTo(x, y, duration=0.12)
            pyautogui.click(x, y)
            print(f"    [✓] Clique {index} em ({x}, {y})")
