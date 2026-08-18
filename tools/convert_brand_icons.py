"""Convert branded raster images into transparent PNG and Windows ICO assets."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image


def _is_background(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, _ = pixel
    return red >= 225 and green >= 225 and blue >= 225 and max(pixel[:3]) - min(pixel[:3]) <= 8


def _remove_border_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    pending = deque()
    visited: set[tuple[int, int]] = set()

    for x in range(width):
        pending.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        pending.extend(((0, y), (width - 1, y)))

    while pending:
        x, y = pending.popleft()
        if (x, y) in visited or not _is_background(pixels[x, y]):
            continue
        visited.add((x, y))
        pixels[x, y] = (pixels[x, y][0], pixels[x, y][1], pixels[x, y][2], 0)
        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            nx, ny = neighbor
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in visited:
                pending.append(neighbor)

    bbox = rgba.getbbox()
    return rgba.crop(bbox) if bbox else rgba


def convert(source_path: Path, png_path: Path, ico_path: Path) -> None:
    subject = _remove_border_background(Image.open(source_path))
    subject.thumbnail((240, 240), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    canvas.alpha_composite(subject, ((256 - subject.width) // 2, (256 - subject.height) // 2))
    png_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(png_path, format="PNG")
    canvas.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--png", type=Path, required=True)
    parser.add_argument("--ico", type=Path, required=True)
    args = parser.parse_args()
    convert(args.source, args.png, args.ico)


if __name__ == "__main__":
    main()