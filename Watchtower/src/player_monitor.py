import asyncio
import time
from dataclasses import dataclass
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
        debug: bool = False,
    ):
        x1, y1, x2, y2 = region
        if x2 <= x1 or y2 <= y1:
            raise ValueError('Region is invalid')

        self.region = (int(x1), int(y1), int(x2), int(y2))
        self.interval_ms = max(30, int(interval_ms))
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

        # Keep template-related inputs for API compatibility, but manual mode ignores template matching.
        self._template_path = template_path

    def _reset_runtime_state(self) -> None:
        self._running = False
        self._active = False
        self._frame_index = 0
        self._confirm_streak = 0
        self._last_bbox = None
        self._bg_mean_rgb = None
        self._bg_frames_collected = 0
        self._bg_ack_done = False

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
                    if self._should_trigger(detection):
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

        match = self._find_blue_blob_match(rgb, hsv)
        if match is None:
            if self.debug:
                print(f'[DEBUG] frame={self._frame_index} found=False reason=no_candidate')
            blue_mask = cv2.inRange(hsv, self.blue_lower, self.blue_upper)
            blue_pixels = int(cv2.countNonZero(blue_mask))
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

        h0, s0, v0 = self.target_hsv
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
        if not self._bg_ack_done:
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
        print(
            f'[INFO] Marker detected: conf={detection.confidence:.3f} '
            f'center=({detection.center_x:.1f},{detection.center_y:.1f}) bbox={detection.bbox}'
        )

        if self.detection_callback is None:
            return

        result = self.detection_callback(detection)
        if asyncio.iscoroutine(result):
            await result
