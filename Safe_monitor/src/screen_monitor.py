import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Optional
import mss
import easyocr
import numpy as np
import cv2
from PIL import Image
from config import MonitorConfig, WindowConfig


@dataclass
class DetectionTrack:
    window_position: str
    char_name: str
    guild_name: Optional[str]
    x: float
    y: float
    notified_x: float
    notified_y: float
    best_confidence: float
    observations: int
    last_seen_at: float
    notified: bool = False


@dataclass
class MinimapDetectionResult:
    marker_count: int
    confidence: float
    center_x: float | None
    center_y: float | None
    blue_pixels: int
    bbox_x: int | None = None
    bbox_y: int | None = None
    bbox_w: int | None = None
    bbox_h: int | None = None

class ScreenMonitor:
    """Monitora janelas de jogo e detecta personagens"""
    
    def __init__(self, config: MonitorConfig, detection_callback=None, fast_live_mode: bool = False):
        self.config = config
        self.detection_callback = detection_callback
        self.fast_live_mode = fast_live_mode
        self.reader = easyocr.Reader([config.ocr_language])
        self.character_hsv_lower = np.array(config.character_hsv_lower, dtype=np.uint8)
        self.character_hsv_upper = np.array(config.character_hsv_upper, dtype=np.uint8)
        self.minimap_blue_hsv_lower = np.array(config.minimap_blue_hsv_lower, dtype=np.uint8)
        self.minimap_blue_hsv_upper = np.array(config.minimap_blue_hsv_upper, dtype=np.uint8)
        self.active_tracks: list[DetectionTrack] = []
        self.live_minimap_state: dict[str, dict[str, int | bool]] = {}
        self.known_guild_words: set[str] = set()  # inner words from bracket-confirmed guild tags
        self.is_first_scan = True
        self.is_running = False

        # Warm-up reduz o atraso da primeira inferencia OCR em runtime.
        self._warmup_ocr()

    def _warmup_ocr(self):
        try:
            sample = np.zeros((24, 64), dtype=np.uint8)
            self.reader.readtext(sample, paragraph=False, decoder='greedy')
        except Exception:
            # Falha de warm-up nao deve impedir o monitor.
            pass

    def capture_window_snapshot(self, window: WindowConfig) -> Image.Image:
        """Captura uma unica imagem de uma janela do jogo."""
        with mss.MSS() as sct:
            screenshot = sct.grab({
                'left': window.x,
                'top': window.y,
                'width': window.width,
                'height': window.height,
            })
            return Image.frombytes('RGB', screenshot.size, screenshot.rgb)

    def _build_minimap_blue_mask(self, hsv: np.ndarray) -> tuple[np.ndarray, int]:
        lower = self.minimap_blue_hsv_lower
        upper = self.minimap_blue_hsv_upper

        blue_mask = cv2.inRange(hsv, lower, upper)

        # Faixa complementar para variações ciano/azul-claras do marcador.
        alt_lower = np.array([55, 20, 35], dtype=np.uint8)
        alt_upper = np.array([145, 255, 255], dtype=np.uint8)
        blue_mask = cv2.bitwise_or(blue_mask, cv2.inRange(hsv, alt_lower, alt_upper))

        blue_mask = cv2.GaussianBlur(blue_mask, (3, 3), 0)
        kernel = np.ones((2, 2), np.uint8)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        return blue_mask, int(cv2.countNonZero(blue_mask))

    def _resolve_minimap_limits(self, mask_shape: tuple[int, int]) -> tuple[int, int, int, int, int]:
        """Calcula limites proporcionais ao tamanho da ROI do minimapa."""
        height, width = mask_shape[:2]
        roi_area = max(1, width * height)
        min_side = max(1, min(width, height))

        # Faixa pensada para um marcador pequeno-médio de jogador em uma ROI de ~260x260.
        scaled_min_area = int(roi_area * 0.0010)
        scaled_max_area = int(roi_area * 0.0120)

        min_area = max(int(self.config.minimap_min_blob_area_px), scaled_min_area, 6)
        max_area = max(int(self.config.minimap_max_blob_area_px), scaled_max_area, min_area * 4)
        min_radius = max(2, int(min_side * 0.015))
        max_radius = max(min_radius + 1, int(min_side * 0.075))
        center_tolerance = max(int(getattr(self.config, 'minimap_center_tolerance_px', 5)), int(min_side * 0.035))
        return min_area, max_area, min_radius, max_radius, center_tolerance

    def _score_minimap_contour(
        self,
        contour: np.ndarray,
        mask_shape: tuple[int, int],
        min_area: int,
        max_area: int,
        min_circularity: float,
        min_fill_ratio: float,
        max_aspect_ratio: float,
    ) -> tuple[bool, float, float | None, float | None, tuple[int, int, int, int] | None]:
        area = float(cv2.contourArea(contour))
        if not (min_area <= area <= max_area):
            return False, 0.0, None, None, None

        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            return False, 0.0, None, None, None

        x, y, w, h = cv2.boundingRect(contour)

        if self.config.minimap_ignore_edge_touching:
            if x <= 1 or y <= 1 or (x + w) >= (mask_shape[1] - 2) or (y + h) >= (mask_shape[0] - 2):
                return False, 0.0, None, None, None

        center_x = x + (w / 2.0)
        center_y = y + (h / 2.0)
        aspect_ratio = max(w, h) / max(1, min(w, h))
        fill_ratio = area / max(1.0, float(w * h))
        circularity = (4.0 * np.pi * area) / (perimeter * perimeter)

        if aspect_ratio > max_aspect_ratio or fill_ratio < min_fill_ratio or circularity < min_circularity:
            return False, 0.0, None, None, None

        # Dá mais peso a ser circular e bem preenchido do que ao tamanho.
        shape_score = min(1.0, (circularity / max(min_circularity, 0.001)) * 0.55)
        fill_score = min(1.0, (fill_ratio / max(min_fill_ratio, 0.001)) * 0.30)
        aspect_score = max(0.0, 1.0 - ((aspect_ratio - 1.0) / max(1.0, max_aspect_ratio - 1.0))) * 0.15
        confidence = min(1.0, shape_score + fill_score + aspect_score)
        return True, confidence, center_x, center_y, (x, y, w, h)

    def _detect_circle_from_mask(
        self,
        blue_mask: np.ndarray,
        min_area: int,
        max_area: int,
        min_radius: int,
        max_radius: int,
    ) -> tuple[bool, float, float | None, float | None, tuple[int, int, int, int] | None]:
        """Fallback para encontrar um círculo azul quando o contorno fica incompleto."""
        circles = cv2.HoughCircles(
            blue_mask,
            cv2.HOUGH_GRADIENT,
            dp=1.15,
            minDist=max(4, min_radius * 2),
            param1=40,
            param2=9,
            minRadius=min_radius,
            maxRadius=max_radius,
        )

        if circles is None:
            return False, 0.0, None, None, None

        best_confidence = 0.0
        best_center_x = None
        best_center_y = None
        best_bbox = None
        best_score = -1.0
        h, w = blue_mask.shape[:2]

        for raw_circle in circles[0]:
            cx, cy, radius = float(raw_circle[0]), float(raw_circle[1]), float(raw_circle[2])
            if radius <= 0:
                continue

            x1 = max(0, int(cx - radius))
            y1 = max(0, int(cy - radius))
            x2 = min(w, int(cx + radius))
            y2 = min(h, int(cy + radius))
            if x2 <= x1 or y2 <= y1:
                continue

            circle_mask = np.zeros_like(blue_mask)
            cv2.circle(circle_mask, (int(cx), int(cy)), int(radius), 255, thickness=-1)
            inside_pixels = int(cv2.countNonZero(cv2.bitwise_and(blue_mask, circle_mask)))
            circle_pixels = int(cv2.countNonZero(circle_mask))
            if circle_pixels <= 0:
                continue

            fill_ratio = inside_pixels / float(circle_pixels)
            area = np.pi * (radius ** 2)
            if not (min_area <= area <= max_area):
                continue

            # Círculo plausível: boa cobertura azul dentro da área projetada.
            confidence = min(1.0, (fill_ratio * 0.85) + min(0.15, radius / max(1.0, float(max_radius))) * 0.15)
            score = confidence + fill_ratio
            if score > best_score:
                best_score = score
                best_confidence = confidence
                best_center_x = cx
                best_center_y = cy
                best_bbox = (x1, y1, x2 - x1, y2 - y1)

        if best_center_x is None or best_center_y is None:
            return False, 0.0, None, None, None

        return True, best_confidence, best_center_x, best_center_y, best_bbox

    def preprocess_for_character_ocr(self, image: Image.Image) -> np.ndarray:
        """Isola texto amarelo e reduz ruido para OCR mais rapido e preciso."""
        img_array = np.array(image)

        if not self.config.character_color_filter_enabled:
            return img_array

        hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
        yellow_mask = cv2.inRange(hsv, self.character_hsv_lower, self.character_hsv_upper)

        kernel = np.ones((2, 2), np.uint8)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        yellow_mask = cv2.dilate(yellow_mask, kernel, iterations=1)

        filtered = cv2.bitwise_and(img_array, img_array, mask=yellow_mask)
        gray = cv2.cvtColor(filtered, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary
        
    async def start_monitoring(self):
        """Inicia o monitoramento contínuo"""
        self.is_running = True
        print('[📺] Iniciando monitoramento de tela...\n')

        with mss.MSS() as sct:
            while self.is_running:
                started = time.monotonic()
                try:
                    await self.capture_and_analyze(sct)

                    # Intervalo alvo: evita adicionar espera extra se o OCR ja consumiu o ciclo.
                    elapsed = time.monotonic() - started
                    target = self.config.capture_interval_ms / 1000.0
                    remaining = target - elapsed
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                except Exception as e:
                    print(f'[✗] Erro durante captura: {e}')
                    await asyncio.sleep(0.2)
    
    async def capture_and_analyze(self, sct: mss.mss):
        """Captura tela e analisa cada janela de jogo"""
        now = time.monotonic()
        self.cleanup_expired_tracks(now)

        for window in self.config.windows:
            screenshot = sct.grab({
                'left': window.x,
                'top': window.y,
                'width': window.width,
                'height': window.height
            })

            # Converte para PIL Image
            img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)

            # Recorta área útil para OCR (ignora chat lateral e HUD inferior)
            roi_img = self.crop_scan_region(img)

            if self.fast_live_mode:
                if self.config.live_use_minimap_detection:
                    detection = self.detect_minimap_blue_markers(roi_img)
                    if self.should_trigger_minimap_alert(window.position, detection):
                        await self.notify_character_found(
                            f'Blue markers: {detection.marker_count}',
                            window.map_name,
                            None,
                        )
                    continue

                fast_names = await self.detect_character_names_fast(roi_img, window)
                for name in fast_names:
                    await self.notify_character_found(name, window.map_name, None)
                continue

            # Detecta personagens
            detections = await self.detect_characters(roi_img, window)

            # Atualiza trilhas e notifica conforme regra de movimento.
            for detection in detections:
                track = self.match_or_create_track(window, detection, now)

                if track.notified:
                    # Re-alerta se o personagem se moveu desde a última notificação
                    dist = self.calculate_distance(
                        track.notified_x, track.notified_y, track.x, track.y
                    )
                    if dist > self.config.movement_retrigger_px:
                        track.notified_x = track.x
                        track.notified_y = track.y
                        await self.notify_character_found(track.char_name, window.map_name, track.guild_name)
                    continue

                # No primeiro scan, notifica imediatamente; depois, espera N confirmações
                if self.is_first_scan or track.observations >= self.config.min_observations_to_notify:
                    track.notified = True
                    track.notified_x = track.x
                    track.notified_y = track.y
                    await self.notify_character_found(track.char_name, window.map_name, track.guild_name)

        # Marca o primeiro ciclo como concluído após processar todas as janelas
        if self.is_first_scan:
            self.is_first_scan = False

    def detect_minimap_blue_markers(self, image: Image.Image) -> MinimapDetectionResult:
        """Conta blobs azuis no minimapa e devolve o candidato mais estável."""
        img_array = np.array(image)
        hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
        blue_mask, blue_pixels = self._build_minimap_blue_mask(hsv)
        min_area, max_area, min_radius, max_radius, _ = self._resolve_minimap_limits(blue_mask.shape)
        min_blue_pixels = max(5, min_area // 4)
        if blue_pixels < min_blue_pixels:
            return MinimapDetectionResult(0, 0.0, None, None, blue_pixels)

        contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        count = 0
        best_confidence = 0.0
        best_center_x = None
        best_center_y = None
        best_bbox = None
        min_circularity = 0.16
        min_fill_ratio = 0.12
        max_aspect_ratio = 3.2

        for contour in contours:
            accepted, confidence, center_x, center_y, bbox = self._score_minimap_contour(
                contour,
                blue_mask.shape,
                min_area,
                max_area,
                min_circularity,
                min_fill_ratio,
                max_aspect_ratio,
            )
            if not accepted:
                continue
            count += 1
            if confidence > best_confidence:
                best_confidence = confidence
                best_center_x = center_x
                best_center_y = center_y
                best_bbox = bbox

        if count == 0:
            circle_ok, circle_confidence, circle_center_x, circle_center_y, circle_bbox = self._detect_circle_from_mask(
                blue_mask,
                min_area,
                max_area,
                min_radius,
                max_radius,
            )
            if circle_ok:
                x, y, w, h = circle_bbox if circle_bbox is not None else (None, None, None, None)
                return MinimapDetectionResult(1, circle_confidence, circle_center_x, circle_center_y, blue_pixels, x, y, w, h)

        x, y, w, h = best_bbox if best_bbox is not None else (None, None, None, None)
        return MinimapDetectionResult(count, best_confidence, best_center_x, best_center_y, blue_pixels, x, y, w, h)

    def should_trigger_minimap_alert(self, window_position: str, detection: MinimapDetectionResult) -> bool:
        """Dispara alerta apenas após estabilidade de frames, confiança e centro consistentes."""
        state = self.live_minimap_state.setdefault(
            window_position,
            {
                'streak': 0,
                'active': False,
                'last_center_x': None,
                'last_center_y': None,
                'last_bbox_x': None,
                'last_bbox_y': None,
                'last_bbox_w': None,
                'last_bbox_h': None,
            }
        )

        if detection.marker_count >= self.config.minimap_min_markers_to_trigger and detection.confidence >= float(getattr(self.config, 'minimap_min_confidence', 0.65)):
            last_center_x = state.get('last_center_x')
            last_center_y = state.get('last_center_y')
            _, _, _, _, dynamic_center_tolerance = self._resolve_minimap_limits((
                max(1, int(detection.bbox_h or 1)),
                max(1, int(detection.bbox_w or 1)),
            ))
            center_tolerance = max(int(getattr(self.config, 'minimap_center_tolerance_px', 5)), dynamic_center_tolerance)

            # Permite movimento moderado, mas ainda exige que seja o mesmo candidato visível.
            if detection.center_x is not None and detection.center_y is not None and last_center_x is not None and last_center_y is not None:
                center_shift = self.calculate_distance(float(last_center_x), float(last_center_y), float(detection.center_x), float(detection.center_y))
                if center_shift > center_tolerance:
                    state['streak'] = 0
                    state['last_center_x'] = detection.center_x
                    state['last_center_y'] = detection.center_y
                    state['last_bbox_x'] = detection.bbox_x
                    state['last_bbox_y'] = detection.bbox_y
                    state['last_bbox_w'] = detection.bbox_w
                    state['last_bbox_h'] = detection.bbox_h
                    return False

            last_bbox_x = state.get('last_bbox_x')
            last_bbox_y = state.get('last_bbox_y')
            last_bbox_w = state.get('last_bbox_w')
            last_bbox_h = state.get('last_bbox_h')

            if (
                detection.bbox_x is not None and detection.bbox_y is not None and detection.bbox_w is not None and detection.bbox_h is not None
                and last_bbox_x is not None and last_bbox_y is not None and last_bbox_w is not None and last_bbox_h is not None
            ):
                iou = self.calculate_bbox_iou(
                    (int(last_bbox_x), int(last_bbox_y), int(last_bbox_w), int(last_bbox_h)),
                    (int(detection.bbox_x), int(detection.bbox_y), int(detection.bbox_w), int(detection.bbox_h)),
                )
                if iou < 0.10 and center_shift > center_tolerance:
                    state['streak'] = 0
                    state['last_center_x'] = detection.center_x
                    state['last_center_y'] = detection.center_y
                    state['last_bbox_x'] = detection.bbox_x
                    state['last_bbox_y'] = detection.bbox_y
                    state['last_bbox_w'] = detection.bbox_w
                    state['last_bbox_h'] = detection.bbox_h
                    return False

            state['streak'] = int(state['streak']) + 1
            state['last_center_x'] = detection.center_x
            state['last_center_y'] = detection.center_y
            state['last_bbox_x'] = detection.bbox_x
            state['last_bbox_y'] = detection.bbox_y
            state['last_bbox_w'] = detection.bbox_w
            state['last_bbox_h'] = detection.bbox_h

            if int(state['streak']) < int(getattr(self.config, 'minimap_confirm_frames', 2)):
                return False

            if bool(state['active']):
                return False

            state['active'] = True
            return True

        state['streak'] = 0
        state['active'] = False
        state['last_center_x'] = None
        state['last_center_y'] = None
        state['last_bbox_x'] = None
        state['last_bbox_y'] = None
        state['last_bbox_w'] = None
        state['last_bbox_h'] = None
        return False

    def calculate_bbox_iou(
        self,
        bbox_a: tuple[int, int, int, int],
        bbox_b: tuple[int, int, int, int],
    ) -> float:
        ax, ay, aw, ah = bbox_a
        bx, by, bw, bh = bbox_b

        a_x2 = ax + aw
        a_y2 = ay + ah
        b_x2 = bx + bw
        b_y2 = by + bh

        inter_x1 = max(ax, bx)
        inter_y1 = max(ay, by)
        inter_x2 = min(a_x2, b_x2)
        inter_y2 = min(a_y2, b_y2)

        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return 0.0

        inter_area = float((inter_x2 - inter_x1) * (inter_y2 - inter_y1))
        area_a = float(aw * ah)
        area_b = float(bw * bh)
        union = area_a + area_b - inter_area
        if union <= 0:
            return 0.0
        return inter_area / union

    async def detect_character_names_fast(self, image: Image.Image, window: WindowConfig) -> list[str]:
        """Fast path para live mode: detecta apenas nomes, sem guild/tracking."""
        try:
            ocr_image = self.preprocess_for_character_ocr(image)
            texts = self.reader.readtext(
                ocr_image,
                allowlist=self.config.character_ocr_allowlist,
                paragraph=False,
                decoder='greedy',
                detail=0,
            )

            names: list[str] = []
            seen: set[str] = set()
            for raw in texts:
                candidate = str(raw).strip()
                if not self.is_valid_character_name(candidate):
                    continue
                key = candidate.lower()
                if key in seen:
                    continue
                seen.add(key)
                names.append(candidate)
            return names
        except Exception as e:
            print(f'[✗] Erro no fast OCR em {window.position}: {e}')
            return []

    def cleanup_expired_tracks(self, now: float):
        """Remove entidades que sumiram por tempo suficiente para permitir novos alertas."""
        expiry_seconds = self.config.track_expiry_ms / 1000
        self.active_tracks = [
            track for track in self.active_tracks
            if (now - track.last_seen_at) <= expiry_seconds
        ]

    def calculate_distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    def update_track_from_detection(self, track: DetectionTrack, detection: dict[str, float | str | None], now: float):
        """Atualiza a trilha mantendo o melhor nome OCR conhecido para a entidade."""
        track.x = float(detection['x'])
        track.y = float(detection['y'])
        track.last_seen_at = now
        track.observations += 1

        detection_confidence = float(detection['confidence'])
        if detection_confidence >= track.best_confidence:
            track.best_confidence = detection_confidence
            track.char_name = str(detection['char_name'])
            detection_guild = detection.get('guild_name')
            track.guild_name = str(detection_guild) if detection_guild else None

    def match_or_create_track(
        self,
        window: WindowConfig,
        detection: dict[str, float | str | None],
        now: float,
    ) -> DetectionTrack:
        """Relaciona uma leitura OCR com uma entidade já vista pela posição na tela."""
        detection_x = float(detection['x'])
        detection_y = float(detection['y'])

        best_track = None
        best_distance = float('inf')

        for track in self.active_tracks:
            if track.window_position != window.position:
                continue

            distance = self.calculate_distance(track.x, track.y, detection_x, detection_y)
            if distance <= self.config.track_match_distance_px and distance < best_distance:
                best_track = track
                best_distance = distance

        if best_track is not None:
            self.update_track_from_detection(best_track, detection, now)
            return best_track

        new_track = DetectionTrack(
            window_position=window.position,
            char_name=str(detection['char_name']),
            guild_name=str(detection['guild_name']) if detection.get('guild_name') else None,
            x=detection_x,
            y=detection_y,
            notified_x=detection_x,
            notified_y=detection_y,
            best_confidence=float(detection['confidence']),
            observations=1,
            last_seen_at=now,
        )
        self.active_tracks.append(new_track)
        return new_track

    def crop_scan_region(self, image: Image.Image) -> Image.Image:
        """Recorta somente a região provável de nomes de personagens."""
        width, height = image.size

        left_pct = float(self.config.scan_region.get('left_pct', 0.25))
        top_pct = float(self.config.scan_region.get('top_pct', 0.08))
        right_pct = float(self.config.scan_region.get('right_pct', 0.95))
        bottom_pct = float(self.config.scan_region.get('bottom_pct', 0.72))

        left = max(0, min(width - 1, int(width * left_pct)))
        top = max(0, min(height - 1, int(height * top_pct)))
        right = max(left + 1, min(width, int(width * right_pct)))
        bottom = max(top + 1, min(height, int(height * bottom_pct)))

        return image.crop((left, top, right, bottom))

    def is_valid_character_name(self, text: str) -> bool:
        """Filtra OCR para manter apenas candidatos plausíveis a nome de personagem."""
        if not text:
            return False

        name = text.strip()
        normalized_name = self.normalize_guild_text(name)

        if not (3 <= len(name) <= 16):
            return False

        # Nunca trate tags de guild como nome de personagem.
        if '<' in normalized_name or '>' in normalized_name:
            return False

        # Nomes de chat/eventos costumam incluir espaços e pontuação pesada.
        if ' ' in name:
            return False

        if any(ch in name for ch in '[]{}():;,.!?/@#$%*|\\'):
            return False

        if not any(ch.isalpha() for ch in name):
            return False

        # Nomes de personagem em MU Online têm capitalização mista.
        # Texto em ALL-CAPS é quase sempre um fragmento de tag de guild.
        alpha_chars = [ch for ch in name if ch.isalpha()]
        uppercase_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
        if uppercase_ratio > 0.75:
            return False

        digit_count = sum(ch.isdigit() for ch in name)
        if digit_count > 4:
            return False

        return True

    def normalize_guild_text(self, text: str) -> str:
        """Normaliza o texto de guild para reduzir variações de OCR."""
        normalized = text.strip()
        normalized = normalized.replace('«', '<').replace('»', '>')
        normalized = normalized.replace('[', '<').replace(']', '>')
        return normalized

    def is_valid_guild_name(self, text: str) -> bool:
        """Detecta padrões de guild tag, preferindo texto entre < >."""
        if not text:
            return False

        guild_text = self.normalize_guild_text(text)
        if not (3 <= len(guild_text) <= 24):
            return False

        has_brackets = '<' in guild_text or '>' in guild_text
        if not has_brackets:
            return False

        inner = guild_text.replace('<', '').replace('>', '').strip()
        if not inner:
            return False

        # Multi-word guild tags like < ASGARDV - TRUETAG4 > are valid — allow spaces/dashes
        if not any(ch.isalpha() for ch in inner):
            return False

        if any(ch in inner for ch in '[]{}():;,.!?/@#$%*|\\'):
            return False

        return True

    def get_box_center(self, bbox) -> tuple[float, float]:
        """Retorna o centro (x, y) do bounding box da detecção OCR."""
        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        return (sum(xs) / 4.0, sum(ys) / 4.0)

    def pair_guilds_with_characters(
        self,
        characters: list[dict[str, float | str]],
        guilds: list[dict[str, float | str]]
    ) -> list[dict[str, float | str | None]]:
        """Relaciona guild detectada acima do nome do personagem (quando existir)."""
        combined = []

        for char_item in characters:
            char_x = float(char_item['x'])
            char_y = float(char_item['y'])

            best_guild = None
            best_score = float('inf')

            for guild_item in guilds:
                guild_x = float(guild_item['x'])
                guild_y = float(guild_item['y'])

                dy = char_y - guild_y
                if dy <= 0 or dy > 90:
                    continue

                dx = abs(char_x - guild_x)
                if dx > 140:
                    continue

                score = dx + (dy * 0.6)
                if score < best_score:
                    best_score = score
                    best_guild = str(guild_item['text'])

            combined.append({
                'char_name': str(char_item['text']),
                'guild_name': best_guild,
                'x': char_x,
                'y': char_y,
                'confidence': float(char_item['confidence']),
            })

        return combined
    
    async def detect_characters(self, image: Image.Image, window: WindowConfig) -> list[dict[str, float | str | None]]:
        """Usa OCR para detectar nome do personagem e guild (opcional)."""
        try:
            ocr_image = self.preprocess_for_character_ocr(image)
            
            # Detecta texto com EasyOCR usando allowlist para acelerar e reduzir ruído.
            results = self.reader.readtext(
                ocr_image,
                allowlist=self.config.character_ocr_allowlist,
                paragraph=False,
                decoder='greedy',
            )
            
            characters = []
            guilds = []
            should_detect_guild = not self.config.character_color_filter_enabled

            for detection in results:
                bbox = detection[0]
                text = detection[1]
                confidence = detection[2]
                
                # Filtra por confiança
                if confidence >= self.config.char_detection_threshold:
                    candidate = text.strip()

                    if should_detect_guild and self.is_valid_guild_name(candidate):
                        x, y = self.get_box_center(bbox)
                        normalized = self.normalize_guild_text(candidate)
                        # Strip any stray bracket chars before storing/displaying
                        clean_guild = normalized.replace('<', '').replace('>', '').strip()
                        guilds.append({'text': clean_guild, 'x': x, 'y': y})
                        # Extract individual words so that OCR fragments of this guild
                        # are rejected as character names in future frames.
                        for word in clean_guild.split():
                            token = ''.join(ch for ch in word if ch.isalnum())
                            if len(token) >= 3:
                                self.known_guild_words.add(token.upper())
                        continue

                    if self.is_valid_character_name(candidate):
                        # Reject if this token was previously seen inside a bracket-confirmed guild tag
                        if candidate.upper() in self.known_guild_words:
                            continue
                        x, y = self.get_box_center(bbox)
                        characters.append({
                            'text': candidate,
                            'x': x,
                            'y': y,
                            'confidence': float(confidence),
                        })
                        continue

            return self.pair_guilds_with_characters(characters, guilds)
        except Exception as e:
            print(f'[✗] Erro ao detectar personagens em {window.position}: {e}')
            return []
    
    async def notify_character_found(self, char_name: str, map_name: str, guild_name: Optional[str] = None):
        """Notifica quando um personagem é detectado"""
        should_log = True
        if self.detection_callback is not None:
            callback_result = self.detection_callback(char_name, map_name, guild_name)
            if inspect.isawaitable(callback_result):
                callback_result = await callback_result
            if callback_result is False:
                should_log = False

        if not should_log:
            return

        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f'[🎯] [{timestamp}] Personagem detectado!')
        if guild_name:
            print(f'    Guild: {guild_name}')
        print(f'    Nome: {char_name}')
        print(f'    Mapa: {map_name}\n')
        
        # Aqui a notificação WhatsApp será enviada
        # (será implementada em whatsapp_notifier.py)
    
    async def stop_monitoring(self):
        """Para o monitoramento"""
        print('\n[⏹️] Parando monitoramento...')
        self.is_running = False
        print('[✓] Monitoramento encerrado')
