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
    """Monitors game windows and detects characters."""
    
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

        # Warm-up reduces OCR latency on the first runtime inference.
        self._warmup_ocr()

    def _warmup_ocr(self):
        try:
            sample = np.zeros((24, 64), dtype=np.uint8)
            self.reader.readtext(sample, paragraph=False, decoder='greedy')
        except Exception:
            # Warm-up failure should not block monitoring.
            pass

    def capture_window_snapshot(self, window: WindowConfig) -> Image.Image:
        """Captures a single image of a game window."""
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

        # Complementary range for cyan/light-blue marker variants.
        alt_lower = np.array([55, 20, 35], dtype=np.uint8)
        alt_upper = np.array([145, 255, 255], dtype=np.uint8)
        blue_mask = cv2.bitwise_or(blue_mask, cv2.inRange(hsv, alt_lower, alt_upper))

        blue_mask = cv2.GaussianBlur(blue_mask, (3, 3), 0)
        kernel = np.ones((2, 2), np.uint8)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        return blue_mask, int(cv2.countNonZero(blue_mask))

    def _resolve_minimap_limits(self, mask_shape: tuple[int, int]) -> tuple[int, int, int, int, int]:
        """Calculates limits proportional to minimap ROI size."""
        height, width = mask_shape[:2]
        roi_area = max(1, width * height)
        min_side = max(1, min(width, height))

        # Target range for a small-to-medium player marker in an ROI of ~260x260.
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

        # Put more weight on circular and filled shape than on size.
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
        """Fallback to find a blue circle when contour extraction is incomplete."""
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

            # Plausible circle: strong blue coverage inside projected area.
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
        """Isolates yellow text and reduces noise for faster, more accurate OCR."""
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
        """Starts continuous monitoring."""
        self.is_running = True
        print('[INFO] Starting screen monitoring...\n')

        with mss.MSS() as sct:
            while self.is_running:
                started = time.monotonic()
                try:
                    await self.capture_and_analyze(sct)

                    # Target interval: avoid extra sleep if OCR already consumed the cycle.
                    elapsed = time.monotonic() - started
                    target = self.config.capture_interval_ms / 1000.0
                    remaining = target - elapsed
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                except Exception as e:
                    print(f'[ERROR] Capture error: {e}')
                    await asyncio.sleep(0.2)
    
    async def capture_and_analyze(self, sct: mss.mss):
        """Captures the screen and analyzes each game window."""
        now = time.monotonic()
        self.cleanup_expired_tracks(now)

        for window in self.config.windows:
            screenshot = sct.grab({
                'left': window.x,
                'top': window.y,
                'width': window.width,
                'height': window.height
            })

            # Convert to PIL Image
            img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)

            # Crop useful OCR area (ignore side chat and lower HUD).
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

            # Detect characters
            detections = await self.detect_characters(roi_img, window)

            # Update tracks and notify according to movement rules.
            for detection in detections:
                track = self.match_or_create_track(window, detection, now)

                if track.notified:
                    # Re-alert if the character moved since the last notification.
                    dist = self.calculate_distance(
                        track.notified_x, track.notified_y, track.x, track.y
                    )
                    if dist > self.config.movement_retrigger_px:
                        track.notified_x = track.x
                        track.notified_y = track.y
                        await self.notify_character_found(track.char_name, window.map_name, track.guild_name)
                    continue

                # On first scan, notify immediately; after that, wait for N confirmations.
                if self.is_first_scan or track.observations >= self.config.min_observations_to_notify:
                    track.notified = True
                    track.notified_x = track.x
                    track.notified_y = track.y
                    await self.notify_character_found(track.char_name, window.map_name, track.guild_name)

        # Mark the first cycle as complete after processing all windows.
        if self.is_first_scan:
            self.is_first_scan = False

    def detect_minimap_blue_markers(self, image: Image.Image) -> MinimapDetectionResult:
        """Counts blue minimap blobs and returns the most stable candidate."""
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
        """Triggers alert only after stable frames, confidence, and center consistency."""
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

            # Allow moderate movement while still requiring the same visible candidate.
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
        """Fast path for live mode: detect names only, without guild/tracking."""
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
            print(f'[ERROR] Fast OCR error in {window.position}: {e}')
            return []

    def cleanup_expired_tracks(self, now: float):
        """Removes entities unseen long enough to allow new alerts."""
        expiry_seconds = self.config.track_expiry_ms / 1000
        self.active_tracks = [
            track for track in self.active_tracks
            if (now - track.last_seen_at) <= expiry_seconds
        ]

    def calculate_distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    def update_track_from_detection(self, track: DetectionTrack, detection: dict[str, float | str | None], now: float):
        """Updates a track while preserving the best known OCR name for the entity."""
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
        """Matches an OCR read with an already seen entity by screen position."""
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
        """Crops only the likely region where character names appear."""
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
        """Filters OCR output to keep only plausible character name candidates."""
        if not text:
            return False

        name = text.strip()
        normalized_name = self.normalize_guild_text(name)

        if not (3 <= len(name) <= 16):
            return False

        # Never treat guild tags as character names.
        if '<' in normalized_name or '>' in normalized_name:
            return False

        # Chat/event text usually includes spaces and heavy punctuation.
        if ' ' in name:
            return False

        if any(ch in name for ch in '[]{}():;,.!?/@#$%*|\\'):
            return False

        if not any(ch.isalpha() for ch in name):
            return False

        # MU Online character names usually use mixed capitalization.
        # ALL-CAPS text is usually a guild-tag fragment.
        alpha_chars = [ch for ch in name if ch.isalpha()]
        uppercase_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
        if uppercase_ratio > 0.75:
            return False

        digit_count = sum(ch.isdigit() for ch in name)
        if digit_count > 4:
            return False

        return True

    def normalize_guild_text(self, text: str) -> str:
        """Normalizes guild text to reduce OCR variations."""
        normalized = text.strip()
        normalized = normalized.replace('«', '<').replace('»', '>')
        normalized = normalized.replace('[', '<').replace(']', '>')
        return normalized

    def is_valid_guild_name(self, text: str) -> bool:
        """Detects guild tag patterns, preferring text inside < >."""
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
        """Returns the (x, y) center of an OCR detection bounding box."""
        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        return (sum(xs) / 4.0, sum(ys) / 4.0)

    def pair_guilds_with_characters(
        self,
        characters: list[dict[str, float | str]],
        guilds: list[dict[str, float | str]]
    ) -> list[dict[str, float | str | None]]:
        """Pairs a detected guild above a character name when present."""
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
        """Uses OCR to detect character name and optional guild."""
        try:
            ocr_image = self.preprocess_for_character_ocr(image)
            
            # Detect text with EasyOCR using allowlist to speed up and reduce noise.
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
                
                # Filter by confidence
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
            print(f'[ERROR] Character detection error in {window.position}: {e}')
            return []
    
    async def notify_character_found(self, char_name: str, map_name: str, guild_name: Optional[str] = None):
        """Notifies when a character is detected."""
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
        print(f'[INFO] [{timestamp}] Character detected!')
        if guild_name:
            print(f'    Guild: {guild_name}')
        print(f'    Name: {char_name}')
        print(f'    Map: {map_name}\n')
        
        # WhatsApp notification should be sent here.
        # (to be implemented in whatsapp_notifier.py)
    
    async def stop_monitoring(self):
        """Stops monitoring."""
        print('\n[INFO] Stopping monitoring...')
        self.is_running = False
        print('[INFO] Monitoring stopped')
