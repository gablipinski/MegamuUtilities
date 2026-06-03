import asyncio
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from action_controller import ActionController
from area_selector import select_area_with_parent, select_points_with_parent
from blue_ball_monitor import BlueBallMonitor
from config import WindowConfig, load_config
from screen_monitor import ScreenMonitor


class MonitorUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Safe Monitor Controller')
        self.root.geometry('560x360')
        self.root.minsize(520, 340)

        self.region: tuple[int, int, int, int] | None = None
        self.escape_route: list[tuple[int, int]] = []

        self._event_queue: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_loop: asyncio.AbstractEventLoop | None = None
        self._blue_monitor: BlueBallMonitor | None = None
        self._safe_monitor: ScreenMonitor | None = None
        self._detected_waiting_rearm = False
        self._mode_var = tk.StringVar(value='Live Telas')
        self._last_mode_selection = 'Live Telas'

        self._build_ui()
        self._set_state_idle('Idle')
        self.root.after(120, self._drain_events)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_ui(self):
        container = tk.Frame(self.root, padx=12, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(container, text='Safe Monitor', font=('Segoe UI', 15, 'bold'))
        title.pack(anchor=tk.W)

        mode_row = tk.Frame(container)
        mode_row.pack(fill=tk.X, pady=(10, 4))

        tk.Label(mode_row, text='Operation Mode:', anchor=tk.W).pack(side=tk.LEFT)
        self.cmb_mode = ttk.Combobox(
            mode_row,
            state='readonly',
            textvariable=self._mode_var,
            values=['Live Telas', 'Safe Monitor'],
            width=18,
        )
        self.cmb_mode.pack(side=tk.LEFT, padx=(8, 0))
        self.cmb_mode.bind('<<ComboboxSelected>>', self._on_mode_changed)

        controls = tk.Frame(container)
        controls.pack(fill=tk.X, pady=(10, 8))

        self.btn_select_area = tk.Button(
            controls,
            text='Select Area',
            width=16,
            command=self._on_select_area,
        )
        self.btn_select_area.grid(row=0, column=0, padx=(0, 8), pady=4)

        self.btn_select_route = tk.Button(
            controls,
            text='Create Escape Route',
            width=20,
            command=self._on_select_route,
        )
        self.btn_select_route.grid(row=0, column=1, padx=(0, 8), pady=4)

        self.btn_toggle_scan = tk.Button(
            controls,
            text='Start Scanner',
            width=16,
            command=self._on_toggle_scanner,
        )
        self.btn_toggle_scan.grid(row=1, column=0, padx=(0, 8), pady=4)

        self.btn_rearm = tk.Button(
            controls,
            text='Rearm Scanner',
            width=20,
            state=tk.DISABLED,
            command=self._on_rearm,
        )
        self.btn_rearm.grid(row=1, column=1, padx=(0, 8), pady=4)

        state_row = tk.Frame(container)
        state_row.pack(fill=tk.X, pady=(6, 6))

        self.led = tk.Canvas(state_row, width=18, height=18, highlightthickness=0)
        self.led.pack(side=tk.LEFT)
        self.led_circle = self.led.create_oval(2, 2, 16, 16, fill='#7a7a7a', outline='#1f1f1f')

        self.lbl_state = tk.Label(state_row, text='State: Idle', font=('Segoe UI', 10, 'bold'))
        self.lbl_state.pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_region = tk.Label(container, text='Region: not selected', anchor=tk.W)
        self.lbl_region.pack(fill=tk.X)

        self.lbl_route = tk.Label(container, text='Escape route: not selected', anchor=tk.W)
        self.lbl_route.pack(fill=tk.X, pady=(2, 8))

        self.log = tk.Text(container, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True)

        self._refresh_mode_ui()

    def run(self):
        self.root.mainloop()

    def _log(self, message: str):
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = f'[{timestamp}] {message}\n'
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_led(self, color: str):
        self.led.itemconfig(self.led_circle, fill=color)

    def _set_state_idle(self, reason: str):
        self._set_led('#7a7a7a')
        self.lbl_state.configure(text=f'State: Idle ({reason})')
        self.btn_toggle_scan.configure(text='Start Scanner')
        if self._selected_mode() == 'live-telas':
            self.btn_rearm.configure(state=tk.DISABLED)
        else:
            self.btn_rearm.configure(state=tk.DISABLED)

    def _set_state_scanning(self):
        self._set_led('#00b050')
        self.lbl_state.configure(text='State: Scanning')
        self.btn_toggle_scan.configure(text='Stop Scanner')
        self.btn_rearm.configure(state=tk.DISABLED)

    def _set_state_detected(self):
        self._set_led('#d32f2f')
        self.lbl_state.configure(text='State: Detected (waiting rearm)')
        self.btn_toggle_scan.configure(text='Start Scanner')
        self.btn_rearm.configure(state=tk.NORMAL)

    def _selected_mode(self) -> str:
        return 'safe-monitor' if self._mode_var.get() == 'Safe Monitor' else 'live-telas'

    def _refresh_mode_ui(self):
        mode = self._selected_mode()
        if mode == 'safe-monitor':
            self.btn_select_route.configure(state=tk.DISABLED)
            self.lbl_route.configure(text='Escape route: not required in Safe Monitor mode')
            self.btn_rearm.configure(state=tk.DISABLED)
            self._log('Mode set to Safe Monitor.')
        else:
            self.btn_select_route.configure(state=tk.NORMAL)
            if len(self.escape_route) == 2:
                self.lbl_route.configure(text=f'Escape route: {self.escape_route[0]} -> {self.escape_route[1]}')
            else:
                self.lbl_route.configure(text='Escape route: not selected')
            self._log('Mode set to Live Telas.')

    def _on_mode_changed(self, _event=None):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before changing mode.')
            self._mode_var.set(self._last_mode_selection)
            return
        self._last_mode_selection = self._mode_var.get()
        self._detected_waiting_rearm = False
        self._set_state_idle('Mode changed')
        self._refresh_mode_ui()

    def _on_select_area(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before selecting a new area.')
            return

        selection = select_area_with_parent(
            self.root,
            help_text='Select monitor area | Enter confirm | Esc cancel',
        )
        if selection is None:
            self._log('Area selection cancelled.')
            return

        self.region = selection
        x1, y1, x2, y2 = selection
        self.lbl_region.configure(text=f'Region: ({x1}, {y1}) {x2 - x1}x{y2 - y1}')
        self._log('Area selected.')

    def _on_select_route(self):
        if self._selected_mode() != 'live-telas':
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            messagebox.showwarning('Scanner active', 'Stop scanner before editing escape route.')
            return

        points = select_points_with_parent(
            self.root,
            count=2,
            help_text='Select 2 escape clicks | Enter confirm | Esc cancel',
        )
        if len(points) != 2:
            self._log('Escape route selection cancelled.')
            return

        self.escape_route = points
        self.lbl_route.configure(text=f'Escape route: {points[0]} -> {points[1]}')
        self._log('Escape route saved.')

    def _on_toggle_scanner(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_scanner(manual_stop=True)
            return

        self._start_scanner()

    def _on_rearm(self):
        if self._selected_mode() != 'live-telas':
            return
        if not self._detected_waiting_rearm:
            return
        self._detected_waiting_rearm = False
        self._start_scanner()

    def _start_scanner(self):
        if self.region is None:
            messagebox.showwarning('Missing area', 'Select area before starting scanner.')
            return
        if self._selected_mode() == 'live-telas' and len(self.escape_route) != 2:
            messagebox.showwarning('Missing route', 'Create escape route before starting scanner.')
            return

        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._detected_waiting_rearm = False
        self._set_state_scanning()
        self._log('Scanner started.')

        self._monitor_thread = threading.Thread(target=self._run_monitor_thread, daemon=True)
        self._monitor_thread.start()

    def _stop_scanner(self, manual_stop: bool):
        if self._monitor_loop is not None:
            if self._blue_monitor is not None:
                asyncio.run_coroutine_threadsafe(self._blue_monitor.stop(), self._monitor_loop)
            if self._safe_monitor is not None:
                asyncio.run_coroutine_threadsafe(self._safe_monitor.stop_monitoring(), self._monitor_loop)

        if manual_stop:
            self._detected_waiting_rearm = False
            self._set_state_idle('Stopped')
            self._log('Scanner stop requested.')

    def _run_monitor_thread(self):
        asyncio.run(self._run_monitor_async())

    async def _run_monitor_async(self):
        self._monitor_loop = asyncio.get_running_loop()
        mode = self._selected_mode()
        triggered = False
        try:
            assert self.region is not None
            if mode == 'live-telas':
                monitor = BlueBallMonitor(
                    self.region,
                    interval_ms=160,
                    confirm_frames=2,
                    min_movement_px=12.0,
                    min_confidence=0.45,
                    debug=False,
                )
                self._blue_monitor = monitor
                action_controller = ActionController(click_points=self.escape_route, cooldown_seconds=2.0)

                async def on_detection(detection):
                    nonlocal triggered
                    if triggered:
                        return
                    triggered = True
                    self._event_queue.put(('detected', detection))
                    await action_controller.execute_escape_sequence('Blue ball detected')
                    await monitor.stop()

                monitor.detection_callback = on_detection
                await monitor.start()
            else:
                x1, y1, x2, y2 = self.region
                config = load_config()
                config.windows = [
                    WindowConfig(
                        position='ui-safe-region',
                        x=int(x1),
                        y=int(y1),
                        width=int(x2 - x1),
                        height=int(y2 - y1),
                        map_name='UIRegion',
                    )
                ]
                config.scan_region = {
                    'left_pct': 0.0,
                    'top_pct': 0.0,
                    'right_pct': 1.0,
                    'bottom_pct': 1.0,
                }
                safe_monitor = ScreenMonitor(config)
                self._safe_monitor = safe_monitor

                async def on_safe_detection(char_name: str, map_name: str, guild_name: str | None):
                    self._event_queue.put(
                        ('safe_detected', {'char_name': char_name, 'map_name': map_name, 'guild_name': guild_name})
                    )
                    return True

                safe_monitor.detection_callback = on_safe_detection
                await safe_monitor.start_monitoring()
        finally:
            self._blue_monitor = None
            self._safe_monitor = None
            self._monitor_loop = None
            self._event_queue.put(('stopped', {'triggered': triggered, 'mode': mode}))

    def _drain_events(self):
        while True:
            try:
                event, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event == 'detected':
                self._detected_waiting_rearm = True
                self._set_state_detected()
                self._log('Detected blue ball. Escape route executed. Waiting for rearm.')
            elif event == 'safe_detected':
                info = payload if isinstance(payload, dict) else {}
                char_name = info.get('char_name', 'Unknown')
                map_name = info.get('map_name', 'Unknown')
                guild_name = info.get('guild_name')
                if guild_name:
                    self._log(f'Safe detection: {char_name} [{guild_name}] in {map_name}.')
                else:
                    self._log(f'Safe detection: {char_name} in {map_name}.')
            elif event == 'stopped':
                info = payload if isinstance(payload, dict) else {}
                mode = info.get('mode', 'live-telas')
                if mode == 'live-telas' and info.get('triggered'):
                    self._detected_waiting_rearm = True
                    self._set_state_detected()
                else:
                    self._detected_waiting_rearm = False
                    self._set_state_idle('Stopped')

        self.root.after(120, self._drain_events)

    def _on_close(self):
        self._stop_scanner(manual_stop=False)
        self.root.destroy()
