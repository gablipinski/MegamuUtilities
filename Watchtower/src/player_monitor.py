import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import cv2
import mss
import numpy as np
from PIL import Image


@dataclass
class PlayerDetection:
    found: bool
    confidence: float
    center_x: float | None
    center_y: float | None
    radius: float | None
    bbox: tuple[int, int, int, int] | None
    blue_pixels: int


DetectionCallback = Callable[[PlayerDetection], Awaitable[None] | None]


class PlayerMonitor:
    """Manual blue-blob detector: color + shape + local contrast."""

    def __init__(
        self,
        region: tuple[int, int, int, int],
        *,
        interval_ms: int = 180,
        confirm_frames: int = 1,
        min_movement_px: float = 0.0,
        min_confidence: float = 0.60,
        blue_lower: tuple[int, int, int] = (92, 70, 70),
        blue_upper: tuple[int, int, int] = (135, 255, 255),
        target_blue_hex: str = '#5C95BE',
        target_hue_tolerance: int = 10,
        target_sat_tolerance: int = 72,
        target_val_tolerance: int = 78,
        target_color_distance_max: float = 34.0,
        target_color_ratio_min: float = 0.16,
        target_blob_min_area_px: int = 4,
        target_blob_max_area_px: int = 220,
        background_ack_frames: int = 16,
        background_color_delta_threshold: float = 22.0,
        background_min_change_ratio: float = 0.20,
        flood_blue_ratio_trigger: float = 0.055,
        flood_min_area_px: int = 70,
        flood_min_contrast: float = 0.11,
        flood_max_ring_blue: float = 0.10,
        template_path: str | None = None,
        template_match_threshold: float = 0.56,
        startup_ignore_frames: int = 10,
        require_background_ack: bool = True,
        log_each_poll: bool = False,
        fast_trigger_on_blue_spike: bool = True,
        fast_trigger_min_blue_pixels: int = 180,
        fast_trigger_min_increase: int = 110,
        fast_trigger_ratio: float = 2.4,
        fast_trigger_confidence: float = 0.56,
        fast_trigger_circle_min_area_px: int = 22,
        fast_trigger_circle_max_area_px: int = 900,
        fast_trigger_circle_min_circularity: float = 0.46,
        fast_trigger_circle_min_aspect: float = 0.58,
        fast_trigger_circle_min_new_pixels: int = 24,
        fast_trigger_circle_min_extent: float = 0.62,
        fast_trigger_circle_min_enclosing_fill: float = 0.74,
        fast_trigger_circle_min_solidity: float = 0.86,
        adaptive_color_calibration: bool = True,
        adaptive_calibration_frames: int = 18,
        adaptive_calibration_min_pixels: int = 36,
        adaptive_update_alpha: float = 0.22,
        debug: bool = False,
    ):
        x1, y1, x2, y2 = region
        if x2 <= x1 or y2 <= y1:
            raise ValueError('Region is invalid')

        self.region = (int(x1), int(y1), int(x2), int(y2))
        self.interval_ms = max(10, int(interval_ms))
        self.confirm_frames = max(1, int(confirm_frames))
        self.min_movement_px = max(0.0, float(min_movement_px))
        self.min_confidence = float(min_confidence)
        self.blue_lower = np.array(blue_lower, dtype=np.uint8)
        self.blue_upper = np.array(blue_upper, dtype=np.uint8)
        self.target_hsv = self._hex_to_hsv(target_blue_hex)

        # Keep existing constructor inputs for compatibility.
        self.target_blue_hex = target_blue_hex
        self.target_hue_tolerance = int(target_hue_tolerance)
        self.target_sat_tolerance = int(target_sat_tolerance)
        self.target_val_tolerance = int(target_val_tolerance)
        self.target_color_distance_max = float(target_color_distance_max)
        self.target_color_ratio_min = float(target_color_ratio_min)
        self.target_blob_min_area_px = int(target_blob_min_area_px)
        self.target_blob_max_area_px = int(target_blob_max_area_px)
        self.background_ack_frames = max(1, int(background_ack_frames))
        self.background_color_delta_threshold = max(1.0, float(background_color_delta_threshold))
        self.background_min_change_ratio = max(0.0, min(1.0, float(background_min_change_ratio)))
        self.flood_blue_ratio_trigger = max(0.0, min(1.0, float(flood_blue_ratio_trigger)))
        self.flood_min_area_px = max(1, int(flood_min_area_px))
        self.flood_min_contrast = max(0.0, float(flood_min_contrast))
        self.flood_max_ring_blue = max(0.0, min(1.0, float(flood_max_ring_blue)))

        self.template_match_threshold = max(0.1, min(1.0, float(template_match_threshold)))
        self.startup_ignore_frames = max(0, int(startup_ignore_frames))
        self.require_background_ack = bool(require_background_ack)
        self.log_each_poll = bool(log_each_poll)
        self.fast_trigger_on_blue_spike = bool(fast_trigger_on_blue_spike)
        self.fast_trigger_min_blue_pixels = max(1, int(fast_trigger_min_blue_pixels))
        self.fast_trigger_min_increase = max(1, int(fast_trigger_min_increase))
        self.fast_trigger_ratio = max(1.0, float(fast_trigger_ratio))
        self.fast_trigger_confidence = max(0.1, min(1.0, float(fast_trigger_confidence)))
        self.fast_trigger_circle_min_area_px = max(1, int(fast_trigger_circle_min_area_px))
        self.fast_trigger_circle_max_area_px = max(
            self.fast_trigger_circle_min_area_px + 1,
            int(fast_trigger_circle_max_area_px),
        )
        self.fast_trigger_circle_min_circularity = max(0.0, min(1.0, float(fast_trigger_circle_min_circularity)))
        self.fast_trigger_circle_min_aspect = max(0.0, min(1.0, float(fast_trigger_circle_min_aspect)))
        self.fast_trigger_circle_min_new_pixels = max(1, int(fast_trigger_circle_min_new_pixels))
        self.fast_trigger_circle_min_extent = max(0.0, min(1.0, float(fast_trigger_circle_min_extent)))
        self.fast_trigger_circle_min_enclosing_fill = max(0.0, min(1.0, float(fast_trigger_circle_min_enclosing_fill)))
        self.fast_trigger_circle_min_solidity = max(0.0, min(1.0, float(fast_trigger_circle_min_solidity)))
        self.adaptive_color_calibration = bool(adaptive_color_calibration)
        self.adaptive_calibration_frames = max(1, int(adaptive_calibration_frames))
        self.adaptive_calibration_min_pixels = max(1, int(adaptive_calibration_min_pixels))
        self.adaptive_update_alpha = max(0.01, min(1.0, float(adaptive_update_alpha)))
        self.debug = debug

        self.detection_callback: Optional[DetectionCallback] = None
        self._running = False
        self._active = False
        self._frame_index = 0
        self._confirm_streak = 0
        self._last_bbox: tuple[int, int, int, int] | None = None
        self._stable_iou_min = 0.25
        self._bg_mean_rgb: np.ndarray | None = None
        self._bg_frames_collected = 0
        self._bg_ack_done = False
        self._blue_px_ema = 0.0
        self._prev_blue_mask: np.ndarray | None = None
        self._target_hsv_runtime = np.array(self.target_hsv, dtype=np.float32)
        self._adaptive_updates = 0

        self._template_path = Path(template_path) if template_path else None
        self._template_gray: np.ndarray | None = None
        self._template_edges: np.ndarray | None = None
        self._template_sizes: tuple[float, ...] = (0.90, 1.00, 1.10)
        self._load_template()

    def _reset_runtime_state(self) -> None:
        self._running = False
        self._active = False
        self._frame_index = 0
        self._confirm_streak = 0
        self._last_bbox = None
        self._bg_mean_rgb = None
        self._bg_frames_collected = 0
        self._bg_ack_done = False
        self._blue_px_ema = 0.0
        self._prev_blue_mask = None
        self._target_hsv_runtime = np.array(self.target_hsv, dtype=np.float32)
        self._adaptive_updates = 0

    def _update_adaptive_target_hsv(self, hsv: np.ndarray, generic_mask: np.ndarray) -> None:
        if not self.adaptive_color_calibration:
            return
        if self._adaptive_updates >= self.adaptive_calibration_frames:
            return

        # Use only reliable blue candidates with enough saturation/value.
        valid = (generic_mask > 0) & (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 40)
        if not np.any(valid):
            return

        h_vals = hsv[:, :, 0][valid]
        s_vals = hsv[:, :, 1][valid]
        v_vals = hsv[:, :, 2][valid]
        if h_vals.size < self.adaptive_calibration_min_pixels:
            return

        sample = np.array(
            [
                float(np.median(h_vals)),
                float(np.median(s_vals)),
                float(np.median(v_vals)),
            ],
            dtype=np.float32,
        )

        alpha = self.adaptive_update_alpha
        self._target_hsv_runtime = ((1.0 - alpha) * self._target_hsv_runtime) + (alpha * sample)
        self._adaptive_updates += 1

        if self.debug and self._adaptive_updates in {1, self.adaptive_calibration_frames}:
            h, s, v = self._target_hsv_runtime
            print(
                f'[DEBUG] adaptive_hsv: update={self._adaptive_updates}/{self.adaptive_calibration_frames} '
                f'hsv=({h:.1f},{s:.1f},{v:.1f})'
            )

    def _is_blue_spike_trigger(self, blue_pixels: int) -> bool:
        prev = float(self._blue_px_ema)
        if prev <= 0.0:
            self._blue_px_ema = float(blue_pixels)
            return False

        is_spike = (
            self.fast_trigger_on_blue_spike
            and blue_pixels >= self.fast_trigger_min_blue_pixels
            and blue_pixels >= int(prev * self.fast_trigger_ratio)
            and (blue_pixels - prev) >= self.fast_trigger_min_increase
        )

        # Keep an adaptive baseline so sudden jumps stand out, but the baseline still follows scene drift.
        alpha = 0.08 if blue_pixels < prev else 0.16
        self._blue_px_ema = ((1.0 - alpha) * prev) + (alpha * float(blue_pixels))
        return is_spike

    def _load_template(self) -> None:
        if self._template_path is None or not self._template_path.exists():
            return

        template = cv2.imread(str(self._template_path), cv2.IMREAD_COLOR)
        if template is None:
            return

        self._template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        self._template_edges = cv2.Canny(self._template_gray, 50, 150)

    @staticmethod
    def _resize_template(template: np.ndarray, scale: float) -> np.ndarray | None:
        if scale <= 0:
            return None
        width = max(1, int(round(template.shape[1] * scale)))
        height = max(1, int(round(template.shape[0] * scale)))
        if width < 2 or height < 2:
            return None
        return cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)

    def _match_template(self, rgb: np.ndarray) -> tuple[float, int, int, int, int] | None:
        if self._template_gray is None or self._template_edges is None:
            return None

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        best_score = -1.0
        best_box: tuple[int, int, int, int] | None = None

        for scale in self._template_sizes:
            template_gray = self._resize_template(self._template_gray, scale)
            template_edges = self._resize_template(self._template_edges, scale)
            if template_gray is None or template_edges is None:
                continue

            th, tw = template_gray.shape[:2]
            if tw > gray.shape[1] or th > gray.shape[0]:
                continue

            gray_result = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
            edge_result = cv2.matchTemplate(edges, template_edges, cv2.TM_CCOEFF_NORMED)

            _, gray_score, _, gray_loc = cv2.minMaxLoc(gray_result)
            _, edge_score, _, edge_loc = cv2.minMaxLoc(edge_result)

            if gray_score >= edge_score:
                score = float(gray_score)
                x, y = gray_loc
            else:
                score = float(edge_score)
                x, y = edge_loc

            if score > best_score:
                best_score = score
                best_box = (int(x), int(y), int(tw), int(th))

        if best_box is None:
            return None

        return best_score, best_box[0], best_box[1], best_box[2], best_box[3]

    def _validate_template_box(self, blue_mask: np.ndarray, x: int, y: int, w: int, h: int) -> dict[str, float] | None:
        if w <= 0 or h <= 0:
            return None

        x2 = min(blue_mask.shape[1], x + w)
        y2 = min(blue_mask.shape[0], y + h)
        x = max(0, x)
        y = max(0, y)
        if x >= x2 or y >= y2:
            return None

        roi_mask = blue_mask[y:y2, x:x2]
        roi_blue_pixels = int(cv2.countNonZero(roi_mask))
        roi_area = float(max(1, (x2 - x) * (y2 - y)))
        blue_ratio = float(roi_blue_pixels / roi_area)

        # Reject template hits that land on UI clutter or non-circle blue noise.
        if roi_blue_pixels < 70:
            return None
        if blue_ratio < 0.14:
            return None

        contours, _ = cv2.findContours(roi_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        best = None
        best_score = -1.0
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < 20.0:
                continue

            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue

            bx, by, bw, bh = cv2.boundingRect(contour)
            if bw <= 0 or bh <= 0:
                continue

            circularity = float((4.0 * np.pi * area) / (perimeter * perimeter))
            aspect = float(min(bw, bh) / max(bw, bh))
            extent = float(area / float(max(1, bw * bh)))

            (_, _), enclosing_radius = cv2.minEnclosingCircle(contour)
            enclosing_area = float(np.pi * (enclosing_radius * enclosing_radius)) if enclosing_radius > 0 else 0.0
            enclosing_fill = float(area / enclosing_area) if enclosing_area > 0 else 0.0

            hull = cv2.convexHull(contour)
            hull_area = float(cv2.contourArea(hull)) if hull is not None else 0.0
            solidity = float(area / hull_area) if hull_area > 0 else 0.0

            if circularity < 0.72:
                continue
            if aspect < 0.72:
                continue
            if extent < 0.60:
                continue
            if enclosing_fill < 0.74:
                continue
            if solidity < 0.86:
                continue

            score = (
                (circularity * 0.30)
                + (aspect * 0.15)
                + (extent * 0.20)
                + (enclosing_fill * 0.20)
                + (solidity * 0.15)
            )
            if score > best_score:
                best_score = score
                best = {
                    'x': float(x + bx),
                    'y': float(y + by),
                    'w': float(bw),
                    'h': float(bh),
                    'blue_pixels': float(roi_blue_pixels),
                    'blue_ratio': blue_ratio,
                    'circularity': circularity,
                    'extent': extent,
                    'enclosing_fill': enclosing_fill,
                    'solidity': solidity,
                    'score': score,
                }

        return best

    def _match_new_blue_circle(self, blue_mask: np.ndarray):
        if self._prev_blue_mask is None or self._prev_blue_mask.shape != blue_mask.shape:
            return None

        new_blue_mask = cv2.bitwise_and(blue_mask, cv2.bitwise_not(self._prev_blue_mask))
        new_blue_pixels = int(cv2.countNonZero(new_blue_mask))
        if new_blue_pixels < self.fast_trigger_circle_min_new_pixels:
            return None

        kernel = np.ones((3, 3), dtype=np.uint8)
        new_blue_mask = cv2.morphologyEx(new_blue_mask, cv2.MORPH_OPEN, kernel)
        new_blue_mask = cv2.morphologyEx(new_blue_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(new_blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        best = None
        best_score = -1.0
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w <= 0 or h <= 0:
                continue

            area = float(cv2.contourArea(contour))
            if area < self.fast_trigger_circle_min_area_px or area > self.fast_trigger_circle_max_area_px:
                continue

            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue

            circularity = float((4.0 * np.pi * area) / (perimeter * perimeter))
            aspect = float(min(w, h) / max(w, h))
            extent = float(area / float(max(1, w * h)))

            (_, _), enclosing_radius = cv2.minEnclosingCircle(contour)
            enclosing_area = float(np.pi * (enclosing_radius * enclosing_radius)) if enclosing_radius > 0 else 0.0
            enclosing_fill = float(area / enclosing_area) if enclosing_area > 0 else 0.0

            hull = cv2.convexHull(contour)
            hull_area = float(cv2.contourArea(hull)) if hull is not None else 0.0
            solidity = float(area / hull_area) if hull_area > 0 else 0.0

            if circularity < self.fast_trigger_circle_min_circularity:
                continue
            if aspect < self.fast_trigger_circle_min_aspect:
                continue
            if extent < self.fast_trigger_circle_min_extent:
                continue
            if enclosing_fill < self.fast_trigger_circle_min_enclosing_fill:
                continue
            if solidity < self.fast_trigger_circle_min_solidity:
                continue

            score = (
                (circularity * 0.35)
                + (aspect * 0.15)
                + (extent * 0.20)
                + (enclosing_fill * 0.20)
                + (solidity * 0.10)
            )
            if score > best_score:
                best_score = score
                best = (x, y, w, h)

        return best

    def _update_background_ack(self, rgb: np.ndarray) -> None:
        frame = rgb.astype(np.float32)
        if self._bg_mean_rgb is None:
            self._bg_mean_rgb = frame
            self._bg_frames_collected = 1
        elif not self._bg_ack_done:
            n = float(self._bg_frames_collected)
            self._bg_mean_rgb = ((self._bg_mean_rgb * n) + frame) / (n + 1.0)
            self._bg_frames_collected += 1

        if (not self._bg_ack_done) and self._bg_frames_collected >= self.background_ack_frames:
            self._bg_ack_done = True
            if self.debug:
                print(f'[DEBUG] background_ack_done: frames={self._bg_frames_collected}')

    @staticmethod
    def _hex_to_hsv(hex_color: str) -> tuple[int, int, int]:
        color = hex_color.strip().lstrip('#')
        if len(color) != 6:
            return (108, 133, 190)
        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
        except ValueError:
            return (108, 133, 190)

        rgb = np.array([[[r, g, b]]], dtype=np.uint8)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0]
        return int(hsv[0]), int(hsv[1]), int(hsv[2])


    async def start(self):
        self._reset_runtime_state()
        self._running = True
        print('[INFO] Spot detector polling started')

        with mss.MSS() as sct:
            while self._running:
                started = time.monotonic()
                try:
                    image = self._capture(sct)
                    detection = self.detect(image)
                    should_trigger = self._should_trigger(detection)
                    if self.log_each_poll and self.debug:
                        status = 'FOUND' if detection.found else 'MISS'
                        print(
                            f'[POLL] frame={self._frame_index} status={status} '
                            f'conf={detection.confidence:.3f} blue_px={detection.blue_pixels} '
                            f'streak={self._confirm_streak}/{self.confirm_frames} '
                            f'bg_ack={int(self._bg_ack_done)} trigger={int(should_trigger)}'
                        )
                    if should_trigger:
                        await self._emit_detection(detection)

                    elapsed = time.monotonic() - started
                    remaining = (self.interval_ms / 1000.0) - elapsed
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                except Exception as exc:
                    print(f'[ERROR] Detection error: {exc}')
                    await asyncio.sleep(0.2)

    async def stop(self):
        self._running = False
        self._active = False
        self._confirm_streak = 0
        self._last_bbox = None

    def _capture(self, sct: mss.MSS) -> Image.Image:
        x1, y1, x2, y2 = self.region
        screenshot = sct.grab(
            {
                'left': x1,
                'top': y1,
                'width': x2 - x1,
                'height': y2 - y1,
            }
        )
        return Image.frombytes('RGB', screenshot.size, screenshot.rgb)

    def detect(self, image: Image.Image) -> PlayerDetection:
        self._frame_index += 1
        rgb = np.array(image)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        blue_mask = cv2.inRange(hsv, self.blue_lower, self.blue_upper)
        self._update_adaptive_target_hsv(hsv, blue_mask)

        template_match = self._match_template(rgb)
        if template_match is not None:
            score, x, y, w, h = template_match
            if score >= self.template_match_threshold:
                template_valid = self._validate_template_box(blue_mask, x, y, w, h)
                if template_valid is None:
                    if self.debug:
                        print(
                            f'[DEBUG] frame={self._frame_index} template_rejected=True '
                            f'score={score:.3f} threshold={self.template_match_threshold:.3f} '
                            f'bbox=({x},{y},{w},{h})'
                        )
                else:
                    tx = int(template_valid['x'])
                    ty = int(template_valid['y'])
                    tw = int(template_valid['w'])
                    th = int(template_valid['h'])

                    if self.debug:
                        print(
                            f'[DEBUG] frame={self._frame_index} template_found=True '
                            f'score={score:.3f} threshold={self.template_match_threshold:.3f} '
                            f'bbox=({tx},{ty},{tw},{th}) blue_ratio={template_valid["blue_ratio"]:.3f} '
                            f'circularity={template_valid["circularity"]:.3f} '
                            f'extent={template_valid["extent"]:.3f} '
                            f'enclosing_fill={template_valid["enclosing_fill"]:.3f} '
                            f'solidity={template_valid["solidity"]:.3f}'
                        )

                    self._prev_blue_mask = blue_mask
                    return PlayerDetection(
                        True,
                        float(score),
                        float(tx + (tw / 2.0)),
                        float(ty + (th / 2.0)),
                        float(max(tw, th) / 2.0),
                        (tx, ty, tw, th),
                        int(template_valid['blue_pixels']),
                    )

        match = self._find_blue_blob_match(rgb, hsv)
        if match is None:
            if self.debug:
                print(f'[DEBUG] frame={self._frame_index} found=False reason=no_candidate')
            blue_pixels = int(cv2.countNonZero(blue_mask))
            if self._is_blue_spike_trigger(blue_pixels):
                circle_bbox = self._match_new_blue_circle(blue_mask)
                if circle_bbox is not None:
                    x, y, w, h = circle_bbox
                    self._prev_blue_mask = blue_mask
                    return PlayerDetection(
                        True,
                        max(self.min_confidence, self.fast_trigger_confidence),
                        float(x + (w / 2.0)),
                        float(y + (h / 2.0)),
                        float(max(w, h) / 2.0),
                        (x, y, w, h),
                        blue_pixels,
                    )
                if self.debug:
                    print(
                        f'[DEBUG] frame={self._frame_index} spike_rejected reason=no_new_circle '
                        f'blue_pixels={blue_pixels}'
                    )

            self._prev_blue_mask = blue_mask
            return PlayerDetection(False, 0.0, None, None, None, None, blue_pixels)

        score, x, y, w, h, blue_pixels, metrics = match
        quality = bool(metrics.get('quality', 0.0) >= 0.5)
        found = ((score + 1e-6) >= self.template_match_threshold) and quality

        if self.debug:
            print(
                f'[DEBUG] frame={self._frame_index} score={score:.3f} '
                f'threshold={self.template_match_threshold:.3f} found={found} '
                f'bbox=({x},{y},{w},{h}) '
                f'blue_ratio={metrics.get("blue_ratio", 0.0):.3f} '
                f'circularity={metrics.get("circularity", 0.0):.3f} '
                f'area={metrics.get("area", 0.0):.1f} '
                f'contrast={metrics.get("contrast", 0.0):.3f} '
                f'ring_blue={metrics.get("ring_blue", 0.0):.3f} '
                f'scene_blue={metrics.get("scene_blue", 0.0):.3f} '
                f'flood={int(metrics.get("flood", 0.0))} '
                f'bg_change={metrics.get("bg_change", 0.0):.3f} '
                f'bg_ack={int(self._bg_ack_done)} '
                f'sat={metrics.get("sat", 0.0):.3f} '
                f'val={metrics.get("val", 0.0):.3f} '
                f'quality={int(quality)} '
                f'blue_pixels={blue_pixels}'
            )

        center_x = x + (w / 2.0)
        center_y = y + (h / 2.0)
        self._prev_blue_mask = blue_mask

        return PlayerDetection(
            found=found,
            confidence=score,
            center_x=float(center_x),
            center_y=float(center_y),
            radius=float(max(w, h) / 2.0),
            bbox=(x, y, w, h),
            blue_pixels=blue_pixels,
        )

    def _find_blue_blob_match(
        self, rgb: np.ndarray, hsv: np.ndarray
    ) -> tuple[float, int, int, int, int, int, dict[str, float]] | None:
        self._update_background_ack(rgb)

        generic_mask = cv2.inRange(hsv, self.blue_lower, self.blue_upper)

        h0, s0, v0 = [int(round(v)) for v in self._target_hsv_runtime]
        lower = np.array(
            [
                max(0, h0 - self.target_hue_tolerance),
                max(0, s0 - self.target_sat_tolerance),
                max(0, v0 - self.target_val_tolerance),
            ],
            dtype=np.uint8,
        )
        upper = np.array(
            [
                min(179, h0 + self.target_hue_tolerance),
                min(255, s0 + self.target_sat_tolerance),
                min(255, v0 + self.target_val_tolerance),
            ],
            dtype=np.uint8,
        )
        target_mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.bitwise_and(generic_mask, target_mask)

        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        blue_pixels = int(cv2.countNonZero(mask))
        scene_blue_ratio = float(blue_pixels / float(mask.shape[0] * mask.shape[1]))
        flood_mode = scene_blue_ratio >= self.flood_blue_ratio_trigger
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        min_area = max(28, self.target_blob_min_area_px)
        if flood_mode:
            min_area = max(min_area, self.flood_min_area_px)
        max_area = max(min_area + 1, min(520, self.target_blob_max_area_px))

        min_contrast = 0.06
        max_ring_blue = 0.18
        if flood_mode:
            min_contrast = max(min_contrast, self.flood_min_contrast)
            max_ring_blue = min(max_ring_blue, self.flood_max_ring_blue)

        best_score = -1.0
        best_box: tuple[int, int, int, int] | None = None
        best_metrics: dict[str, float] | None = None

        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < min_area or area > max_area:
                continue

            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            if w <= 0 or h <= 0:
                continue
            if w < 7 or h < 7:
                continue
            if w > 36 or h > 36:
                continue

            circularity = float((4.0 * np.pi * area) / (perimeter * perimeter))
            aspect = min(w, h) / max(w, h)
            extent = float(area / float(max(1, w * h)))

            (_, _), enclosing_radius = cv2.minEnclosingCircle(contour)
            enclosing_area = float(np.pi * (enclosing_radius * enclosing_radius)) if enclosing_radius > 0 else 0.0
            enclosing_fill = float(area / enclosing_area) if enclosing_area > 0 else 0.0

            hull = cv2.convexHull(contour)
            hull_area = float(cv2.contourArea(hull)) if hull is not None else 0.0
            solidity = float(area / hull_area) if hull_area > 0 else 0.0

            roi_mask = mask[y : y + h, x : x + w]
            bbox_area = float(max(1, w * h))
            blue_ratio = float(cv2.countNonZero(roi_mask) / bbox_area)

            pad = max(2, int(round(max(w, h) * 0.7)))
            x0 = max(0, x - pad)
            y0 = max(0, y - pad)
            x1 = min(mask.shape[1], x + w + pad)
            y1 = min(mask.shape[0], y + h + pad)
            expanded = mask[y0:y1, x0:x1]
            ring = np.ones_like(expanded, dtype=np.uint8) * 255
            inner_x0 = x - x0
            inner_y0 = y - y0
            inner_x1 = inner_x0 + w
            inner_y1 = inner_y0 + h
            ring[inner_y0:inner_y1, inner_x0:inner_x1] = 0
            ring_pixels = float(max(1, cv2.countNonZero(ring)))
            ring_blue = float(cv2.countNonZero(cv2.bitwise_and(expanded, expanded, mask=ring)) / ring_pixels)
            contrast = max(0.0, blue_ratio - ring_blue)

            if self._bg_mean_rgb is not None and self._bg_ack_done:
                roi_cur = rgb[y : y + h, x : x + w].astype(np.float32)
                roi_bg = self._bg_mean_rgb[y : y + h, x : x + w]
                delta = np.linalg.norm(roi_cur - roi_bg, axis=2)
                bg_change = float(np.count_nonzero(delta >= self.background_color_delta_threshold) / bbox_area)
            else:
                bg_change = 1.0

            contour_mask = np.zeros(mask.shape, dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, thickness=-1)
            sat_mean = float(cv2.mean(hsv[:, :, 1], mask=contour_mask)[0] / 255.0)
            val_mean = float(cv2.mean(hsv[:, :, 2], mask=contour_mask)[0] / 255.0)

            score = (
                (max(0.0, min(1.0, circularity)) * 0.30)
                + (max(0.0, min(1.0, blue_ratio / 0.55)) * 0.22)
                + (max(0.0, min(1.0, contrast / 0.18)) * 0.20)
                + (max(0.0, min(1.0, sat_mean / 0.55)) * 0.13)
                + (max(0.0, min(1.0, val_mean / 0.60)) * 0.08)
                + (max(0.0, min(1.0, aspect)) * 0.07)
            )

            quality = (
                circularity >= 0.42
                and aspect >= 0.62
                and blue_ratio >= self.target_color_ratio_min
                and contrast >= min_contrast
                and ring_blue <= max_ring_blue
                and extent >= 0.60
                and enclosing_fill >= 0.72
                and solidity >= 0.84
                and ((not self._bg_ack_done) or (bg_change >= self.background_min_change_ratio))
                and sat_mean >= 0.30
            )

            if not quality:
                continue

            if score > best_score:
                best_score = score
                best_box = (x, y, w, h)
                best_metrics = {
                    'blue_ratio': blue_ratio,
                    'circularity': circularity,
                    'extent': extent,
                    'enclosing_fill': enclosing_fill,
                    'solidity': solidity,
                    'contrast': contrast,
                    'ring_blue': ring_blue,
                    'scene_blue': scene_blue_ratio,
                    'flood': 1.0 if flood_mode else 0.0,
                    'bg_change': bg_change,
                    'sat': sat_mean,
                    'val': val_mean,
                    'area': area,
                    'quality': 1.0 if quality else 0.0,
                }

        if best_box is None or best_metrics is None:
            return None

        return (
            float(max(0.0, min(1.0, best_score))),
            best_box[0],
            best_box[1],
            best_box[2],
            best_box[3],
            blue_pixels,
            best_metrics,
        )

    def _should_trigger(self, detection: PlayerDetection) -> bool:
        if self.require_background_ack and not self._bg_ack_done:
            if self.debug:
                print(
                    f'[DEBUG] background_ack: frame={self._frame_index} '
                    f'progress={self._bg_frames_collected}/{self.background_ack_frames}'
                )
            self._active = False
            self._confirm_streak = 0
            self._last_bbox = None
            return False

        if self._frame_index <= self.startup_ignore_frames:
            if self.debug:
                print(
                    f'[DEBUG] startup_guard: frame={self._frame_index}/{self.startup_ignore_frames} '
                    f'conf={detection.confidence:.3f}'
                )
            self._active = False
            self._confirm_streak = 0
            self._last_bbox = None
            return False

        if not detection.found or detection.confidence < self.min_confidence:
            self._active = False
            self._confirm_streak = 0
            self._last_bbox = None
            return False

        if detection.bbox is not None:
            if self._last_bbox is None:
                self._confirm_streak = 1
            else:
                if self._bbox_iou(self._last_bbox, detection.bbox) >= self._stable_iou_min:
                    self._confirm_streak += 1
                else:
                    self._confirm_streak = 1
            self._last_bbox = detection.bbox
        else:
            self._confirm_streak += 1

        if self._confirm_streak < self.confirm_frames:
            if self.debug:
                print(
                    f'[DEBUG] confirm_streak={self._confirm_streak}/{self.confirm_frames} '
                    f'conf={detection.confidence:.3f}'
                )
            return False

        if self._active:
            return False

        self._active = True
        return True

    @staticmethod
    def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b

        ax2, ay2 = ax + aw, ay + ah
        bx2, by2 = bx + bw, by + bh

        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)

        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = float(iw * ih)
        if inter <= 0:
            return 0.0

        union = float((aw * ah) + (bw * bh) - inter)
        if union <= 0:
            return 0.0
        return inter / union

    async def _emit_detection(self, detection: PlayerDetection):
        center_text = 'n/a'
        if detection.center_x is not None and detection.center_y is not None:
            center_text = f'({detection.center_x:.1f},{detection.center_y:.1f})'

        print(
            f'[INFO] Marker detected: conf={detection.confidence:.3f} '
            f'center={center_text} bbox={detection.bbox}'
        )

        if self.detection_callback is None:
            return

        result = self.detection_callback(detection)
        if asyncio.iscoroutine(result):
            await result
