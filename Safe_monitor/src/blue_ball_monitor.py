import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import cv2
import mss
import numpy as np
from PIL import Image


@dataclass
class BlueBallDetection:
    found: bool
    confidence: float
    center_x: float | None
    center_y: float | None
    radius: float | None
    bbox: tuple[int, int, int, int] | None
    blue_pixels: int


DetectionCallback = Callable[[BlueBallDetection], Awaitable[None] | None]


class BlueBallMonitor:
    """Minimal blue-ball detector for a user-selected screen region."""

    def __init__(
        self,
        region: tuple[int, int, int, int],
        *,
        interval_ms: int = 180,
        confirm_frames: int = 2,
        min_movement_px: float = 12.0,
        min_confidence: float = 0.55,
        blue_lower: tuple[int, int, int] = (92, 70, 70),
        blue_upper: tuple[int, int, int] = (135, 255, 255),
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
        self.debug = debug

        self.detection_callback: Optional[DetectionCallback] = None
        self._running = False
        self._streak = 0
        self._active = False
        self._last_candidate: BlueBallDetection | None = None
        self._movement_total = 0.0
        self._missed_frames = 0
        self._rearm_missing_frames = 2

    async def start(self):
        self._running = True
        print('[📺] Watching selected region for a blue ball...')

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
                    print(f'[✗] Detection error: {exc}')
                    await asyncio.sleep(0.2)

    async def stop(self):
        self._running = False

    def _capture(self, sct: mss.MSS) -> Image.Image:
        x1, y1, x2, y2 = self.region
        screenshot = sct.grab({
            'left': x1,
            'top': y1,
            'width': x2 - x1,
            'height': y2 - y1,
        })
        return Image.frombytes('RGB', screenshot.size, screenshot.rgb)

    def detect(self, image: Image.Image) -> BlueBallDetection:
        img = np.array(image)
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)

        mask = cv2.inRange(hsv, self.blue_lower, self.blue_upper)

        mask = cv2.GaussianBlur(mask, (3, 3), 0)
        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

        blue_pixels = int(cv2.countNonZero(mask))
        height, width = mask.shape[:2]
        roi_area = max(1, width * height)
        min_side = max(1, min(width, height))

        # Scale with the selected region so 2K screens and smaller captures use the same logic.
        min_area = max(int(roi_area * 0.00018), 10)
        max_area = max(int(roi_area * 0.0080), min_area * 6)
        min_radius = max(2, int(min_side * 0.010))
        max_radius = max(min_radius + 1, int(min_side * 0.055))

        best = BlueBallDetection(False, 0.0, None, None, None, None, blue_pixels)
        best_score = -1.0

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            candidate = self._score_contour(contour, min_area, max_area)
            if candidate is None:
                continue
            score, cx, cy, radius, bbox = candidate
            if radius is not None and (radius < min_radius or radius > max_radius):
                continue
            if score > best_score:
                best_score = score
                best = BlueBallDetection(True, score, cx, cy, radius, bbox, blue_pixels)

        hough_candidate = self._score_hough_circle(mask, min_area, max_area, min_radius, max_radius)
        if hough_candidate is not None and hough_candidate.confidence > best.confidence:
            best = hough_candidate

        if self.debug and best.found:
            print(
                f"[dbg] blue_pixels={best.blue_pixels} conf={best.confidence:.2f} "
                f"center=({best.center_x:.1f},{best.center_y:.1f}) radius={best.radius:.1f} bbox={best.bbox}"
            )

        return best

    def _score_contour(
        self,
        contour: np.ndarray,
        min_area: int,
        max_area: int,
    ) -> tuple[float, float, float, float, tuple[int, int, int, int]] | None:
        area = float(cv2.contourArea(contour))
        if not (min_area <= area <= max_area):
            return None

        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            return None

        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        aspect_ratio = max(w, h) / max(1, min(w, h))
        fill_ratio = area / max(1.0, float(w * h))
        circularity = (4.0 * np.pi * area) / (perimeter * perimeter)

        if aspect_ratio > 2.8 or fill_ratio < 0.18 or circularity < 0.22:
            return None

        cx = x + (w / 2.0)
        cy = y + (h / 2.0)
        radius = max(w, h) / 2.0

        # Stronger weight on circularity and fill because the marker is a filled blue ball.
        size_score = min(1.0, area / max(1.0, float(min_area) * 4.0)) * 0.15
        shape_score = min(1.0, circularity / 0.45) * 0.55
        fill_score = min(1.0, fill_ratio / 0.70) * 0.30
        confidence = min(1.0, size_score + shape_score + fill_score)
        return confidence, cx, cy, radius, (x, y, w, h)

    def _score_hough_circle(
        self,
        mask: np.ndarray,
        min_area: int,
        max_area: int,
        min_radius: int,
        max_radius: int,
    ) -> BlueBallDetection | None:
        circles = cv2.HoughCircles(
            mask,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(4, min_radius * 2),
            param1=45,
            param2=10,
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is None:
            return None

        best: BlueBallDetection | None = None
        best_score = -1.0
        h, w = mask.shape[:2]

        for raw in circles[0]:
            cx, cy, radius = float(raw[0]), float(raw[1]), float(raw[2])
            if radius <= 0:
                continue

            area = np.pi * (radius ** 2)
            if not (min_area <= area <= max_area):
                continue

            x1 = max(0, int(cx - radius))
            y1 = max(0, int(cy - radius))
            x2 = min(w, int(cx + radius))
            y2 = min(h, int(cy + radius))
            if x2 <= x1 or y2 <= y1:
                continue

            circle_mask = np.zeros_like(mask)
            cv2.circle(circle_mask, (int(cx), int(cy)), int(radius), 255, thickness=-1)
            inside_pixels = int(cv2.countNonZero(cv2.bitwise_and(mask, circle_mask)))
            circle_pixels = int(cv2.countNonZero(circle_mask))
            if circle_pixels <= 0:
                continue

            fill_ratio = inside_pixels / float(circle_pixels)
            confidence = min(1.0, (fill_ratio * 0.8) + min(0.2, radius / max(1.0, float(max_radius))) * 0.2)
            if confidence > best_score:
                best_score = confidence
                best = BlueBallDetection(True, confidence, cx, cy, radius, (x1, y1, x2 - x1, y2 - y1), inside_pixels)

        return best

    def _should_trigger(self, detection: BlueBallDetection) -> bool:
        if not detection.found or detection.confidence < self.min_confidence:
            self._missed_frames += 1
            if self._missed_frames >= self._rearm_missing_frames:
                self._streak = 0
                self._active = False
                self._last_candidate = None
                self._movement_total = 0.0
            self._streak = 0
            return False

        self._missed_frames = 0

        if self._last_candidate is not None:
            assert self._last_candidate.radius is not None
            assert detection.radius is not None
            center_delta = self._distance(
                self._last_candidate.center_x or 0.0,
                self._last_candidate.center_y or 0.0,
                detection.center_x or 0.0,
                detection.center_y or 0.0,
            )
            radius_scale = max(self._last_candidate.radius, detection.radius)
            if center_delta > max(10.0, radius_scale * 1.10):
                self._streak = 1
                self._last_candidate = detection
                self._movement_total = 0.0
                return False

            self._movement_total += center_delta
        else:
            self._movement_total = 0.0

        self._streak += 1
        self._last_candidate = detection

        if self._streak < self.confirm_frames:
            return False

        if self._movement_total < self.min_movement_px:
            return False

        if self._active:
            return False

        self._active = True
        return True

    async def _emit_detection(self, detection: BlueBallDetection):
        print(
            f"[🎯] Blue ball detected: conf={detection.confidence:.2f} "
            f"center=({detection.center_x:.1f},{detection.center_y:.1f}) radius={detection.radius:.1f}"
        )

        if self.detection_callback is None:
            return

        result = self.detection_callback(detection)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
