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
    character_color_filter_enabled: bool  # Usa filtro HSV para isolar nomes amarelos
    character_hsv_lower: tuple[int, int, int]  # Limite inferior HSV do nome
    character_hsv_upper: tuple[int, int, int]  # Limite superior HSV do nome
    character_ocr_allowlist: str  # Caracteres permitidos para OCR de nome
    self_name_similarity_threshold: float  # Similaridade minima para considerar leitura como nome proprio
    external_name_similarity_threshold: float  # Similaridade para agrupar variacoes OCR do mesmo alvo externo
    external_detection_streak: int  # Quantas leituras seguidas para confirmar alvo externo
    external_candidate_ttl_ms: int  # Janela maxima entre leituras para manter streak
    live_use_minimap_detection: bool  # Usa detecção por bolinhas azuis do minimapa no modo live
    minimap_blue_hsv_lower: tuple[int, int, int]  # Limite inferior HSV para azul do minimapa
    minimap_blue_hsv_upper: tuple[int, int, int]  # Limite superior HSV para azul do minimapa
    minimap_min_blob_area_px: int  # Area minima do blob azul para considerar marcador
    minimap_max_blob_area_px: int  # Area maxima do blob azul para considerar marcador
    minimap_min_markers_to_trigger: int  # Quantidade minima de marcadores azuis para alertar
    minimap_confirm_frames: int  # Quantos frames consecutivos para confirmar presença
    minimap_inner_margin_pct: float  # Margem interna para ignorar bordas do minimapa
    minimap_ignore_edge_touching: bool  # Ignora blobs que tocam a borda da area analisada
    minimap_center_zone_pct: float  # Zona central minima para aceitar o marcador do jogador
    minimap_min_confidence: float  # Confiança minima para aceitar o marcador do jogador
    minimap_center_tolerance_px: int  # Quanto o centro pode variar entre frames consecutivos

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
