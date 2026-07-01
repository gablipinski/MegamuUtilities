from action_controller import ActionController
from player_monitor import PlayerMonitor
import time


def create_spot_tower_app():
    from monitor_ui import MonitorUI
    return MonitorUI(initial_mode='SPOT TOWER')


async def run_spot_tower_monitor(ui) -> None:
    marker_template = ui.template_path if ui.template_path.exists() else None
    monitor = PlayerMonitor(
        ui.region,
        interval_ms=100,
        confirm_frames=1,
        min_movement_px=0.0,
        min_confidence=0.50,
        template_path=str(marker_template) if marker_template is not None else None,
        template_match_threshold=0.50,
        startup_ignore_frames=0,
        background_ack_frames=1,
        require_background_ack=False,
        log_each_poll=True,
        fast_trigger_on_blue_spike=True,
        fast_trigger_min_blue_pixels=170,
        fast_trigger_min_increase=100,
        fast_trigger_ratio=2.1,
        fast_trigger_confidence=0.56,
        fast_trigger_circle_min_area_px=24,
        fast_trigger_circle_max_area_px=320,
        fast_trigger_circle_min_circularity=0.68,
        fast_trigger_circle_min_aspect=0.75,
        fast_trigger_circle_min_new_pixels=30,
        debug=False,
    )
    ui._player_monitor = monitor
    action_controller = ActionController(actions=ui.escape_route, cooldown_seconds=2.0)
    triggered = False

    async def on_detection(detection):
        nonlocal triggered
        if triggered:
            return
        triggered = True
        captured_at = time.time()
        snapshot = ui._capture_full_screen_snapshot()
        if snapshot is not None:
            ui._event_queue.put(
                (
                    'trigger_snapshot',
                    {
                        'image': snapshot,
                        'mode': 'SPOT TOWER',
                        'scope': 'full screen',
                        'captured_at': captured_at,
                    },
                )
            )
        ui._event_queue.put(('detected', detection))
        await action_controller.execute_escape_sequence('Player detected')
        ui._event_queue.put(('safe_zone', None))
        await monitor.stop()

    monitor.detection_callback = on_detection
    await monitor.start()
