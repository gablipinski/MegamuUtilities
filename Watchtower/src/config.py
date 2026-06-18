import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_CONFIGS_DIR = PROJECT_ROOT / 'configs'


def get_app_data_dir() -> Path:
    if getattr(sys, 'frozen', False):
        appdata = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
        target_dir = appdata / 'Watchtower'
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir
    return PROJECT_ROOT


def get_runtime_config_path(file_name: str) -> Path:
    bundled_path = BUNDLED_CONFIGS_DIR / file_name
    if not getattr(sys, 'frozen', False):
        return bundled_path

    target_dir = get_app_data_dir() / 'configs'
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file_name

    if not target_path.exists() and bundled_path.exists():
        shutil.copy2(bundled_path, target_path)

    return target_path


DEFAULT_CONFIG_PATH = get_runtime_config_path('config.json')

@dataclass
class WindowConfig:
    """Configuration for one game window."""
    position: str  # "top-left", "top-right", "bottom-left", "bottom-right"
    x: int
    y: int
    width: int
    height: int
    map_name: str  # Map name for this quadrant

@dataclass
class NotificationConfig:
    """Notification configuration."""
    enabled: bool
    notification_message: str

@dataclass
class MonitorConfig:
    """Full monitor configuration."""
    windows: list[WindowConfig]
    notification: NotificationConfig
    ocr_language: str  # OCR language
    capture_interval_ms: int  # Capture interval
    char_detection_threshold: float  # Minimum OCR confidence
    scan_region: dict[str, float]  # Relative OCR region (avoids chat/UI)
    track_match_distance_px: int  # Max distance to treat as the same entity
    track_expiry_ms: int  # Time without sighting before discarding an entity
    min_observations_to_notify: int  # Number of reads before alerting
    movement_retrigger_px: int  # Minimum movement to re-alert a stationary character
    character_color_filter_enabled: bool  # Use HSV filter to isolate yellow names
    character_hsv_lower: tuple[int, int, int]  # Lower HSV bound for names
    character_hsv_upper: tuple[int, int, int]  # Upper HSV bound for names
    character_ocr_allowlist: str  # Allowed characters for name OCR
    self_name_similarity_threshold: float  # Similarity threshold to treat a read as own name
    external_name_similarity_threshold: float  # Similarity threshold to group OCR variants of the same external target
    external_detection_streak: int  # Consecutive reads required to confirm external target
    external_candidate_ttl_ms: int  # Maximum window between reads to keep streak
    live_use_minimap_detection: bool  # Use minimap blue marker detection in live mode
    minimap_blue_hsv_lower: tuple[int, int, int]  # Lower HSV bound for minimap blue
    minimap_blue_hsv_upper: tuple[int, int, int]  # Upper HSV bound for minimap blue
    minimap_min_blob_area_px: int  # Minimum blue blob area to count as marker
    minimap_max_blob_area_px: int  # Maximum blue blob area to count as marker
    minimap_min_markers_to_trigger: int  # Minimum blue marker count to alert
    minimap_confirm_frames: int  # Consecutive frames required to confirm presence
    minimap_inner_margin_pct: float  # Inner margin to ignore minimap edges
    minimap_ignore_edge_touching: bool  # Ignore blobs touching the analyzed region edge
    minimap_center_zone_pct: float  # Minimum center zone to accept the player marker
    minimap_min_confidence: float  # Minimum confidence to accept the player marker
    minimap_center_tolerance_px: int  # Allowed center movement across consecutive frames

def load_config(config_file: Optional[str] = None) -> MonitorConfig:
    """Loads configuration from a JSON file."""
    
    config_path = Path(config_file) if config_file else DEFAULT_CONFIG_PATH
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file '{config_path}' was not found")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Parse windows
    windows = []
    for win in data.get('windows', []):
        window = WindowConfig(
            position=win['position'],
            x=win['x'],
            y=win['y'],
            width=win['width'],
            height=win['height'],
            map_name=win.get('map_name', 'Unknown')
        )
        windows.append(window)
    
    # Parse notifications
    notification = NotificationConfig(
        enabled=data.get('notification', {}).get('enabled', False),
        notification_message=data.get('notification', {}).get('message', 'Someone appeared in {map}: {char_name}')
    )
    
    return MonitorConfig(
        windows=windows,
        notification=notification,
        ocr_language=data.get('ocr_language', 'pt'),
        capture_interval_ms=data.get('capture_interval_ms', 1000),
        char_detection_threshold=data.get('char_detection_threshold', 0.5),
        scan_region=data.get('scan_region', {
            'left_pct': 0.0,
            'top_pct': 0.0,
            'right_pct': 1.0,
            'bottom_pct': 1.0
        }),
        track_match_distance_px=data.get('track_match_distance_px', 80),
        track_expiry_ms=data.get('track_expiry_ms', 120000),
        min_observations_to_notify=data.get('min_observations_to_notify', 1),
        movement_retrigger_px=data.get('movement_retrigger_px', 120),
        character_color_filter_enabled=data.get('character_color_filter_enabled', True),
        character_hsv_lower=tuple(data.get('character_hsv_lower', [18, 70, 70])),
        character_hsv_upper=tuple(data.get('character_hsv_upper', [42, 255, 255])),
        character_ocr_allowlist=data.get('character_ocr_allowlist', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'),
        self_name_similarity_threshold=float(data.get('self_name_similarity_threshold', 0.80)),
        external_name_similarity_threshold=float(data.get('external_name_similarity_threshold', 0.72)),
        external_detection_streak=int(data.get('external_detection_streak', 2)),
        external_candidate_ttl_ms=int(data.get('external_candidate_ttl_ms', 1400)),
        live_use_minimap_detection=bool(data.get('live_use_minimap_detection', True)),
        minimap_blue_hsv_lower=tuple(data.get('minimap_blue_hsv_lower', [88, 80, 80])),
        minimap_blue_hsv_upper=tuple(data.get('minimap_blue_hsv_upper', [130, 255, 255])),
        minimap_min_blob_area_px=int(data.get('minimap_min_blob_area_px', 8)),
        minimap_max_blob_area_px=int(data.get('minimap_max_blob_area_px', 220)),
        minimap_min_markers_to_trigger=int(data.get('minimap_min_markers_to_trigger', 1)),
        minimap_confirm_frames=int(data.get('minimap_confirm_frames', 2)),
        minimap_inner_margin_pct=float(data.get('minimap_inner_margin_pct', 0.18)),
        minimap_ignore_edge_touching=bool(data.get('minimap_ignore_edge_touching', True)),
        minimap_center_zone_pct=float(data.get('minimap_center_zone_pct', 0.28)),
        minimap_min_confidence=float(data.get('minimap_min_confidence', 0.65)),
        minimap_center_tolerance_px=int(data.get('minimap_center_tolerance_px', 5)),
    )
