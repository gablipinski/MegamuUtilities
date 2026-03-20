import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'configs' / 'config.json'

@dataclass
class WindowConfig:
    """Configuração de uma janela de jogo"""
    position: str  # "top-left", "top-right", "bottom-left", "bottom-right"
    x: int
    y: int
    width: int
    height: int
    map_name: str  # Nome do mapa neste quadrante

@dataclass
class NotificationConfig:
    """Configuração de notificações"""
    enabled: bool
    whatsapp_group_id: Optional[str]
    whatsapp_token: Optional[str]
    notification_message: str  # Template com {char_name} e {map}

@dataclass
class MonitorConfig:
    """Configuração completa do monitor"""
    windows: list[WindowConfig]
    notification: NotificationConfig
    ocr_language: str  # Idioma para OCR
    capture_interval_ms: int  # Intervalo de captura
    char_detection_threshold: float  # Confiança mínima para OCR
    scan_region: dict[str, float]  # Região relativa para OCR (evita chat/UI)
    track_match_distance_px: int  # Distância máxima para considerar a mesma entidade
    track_expiry_ms: int  # Tempo sem ver a entidade para descartá-la
    min_observations_to_notify: int  # Quantas leituras antes de alertar
    movement_retrigger_px: int  # Deslocamento mínimo para re-alertar personagem parado

def load_config(config_file: Optional[str] = None) -> MonitorConfig:
    """Carrega configurações do arquivo JSON"""
    
    config_path = Path(config_file) if config_file else DEFAULT_CONFIG_PATH
    
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de configuração '{config_path}' não encontrado")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Parse das janelas
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
    
    # Parse de notificações
    notification = NotificationConfig(
        enabled=data.get('notification', {}).get('enabled', False),
        whatsapp_group_id=data.get('notification', {}).get('whatsapp_group_id'),
        whatsapp_token=data.get('notification', {}).get('whatsapp_token'),
        notification_message=data.get('notification', {}).get('message', 'Alguém apareceu em {map}: {char_name}')
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
    )
