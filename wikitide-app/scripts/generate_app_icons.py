"""Export launcher icons using only the emblem in the website's original logo.

Keep the website and in-app wordmark intact. The launcher icon crops away the
right-hand lettering, then centers and enlarges the unmodified left artwork on
the live site's hex.css `--wash: #f1ebdf` background.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
WEB_LOGO = ROOT.parent / "knowledge-site/static/logo.png"
APP_LOGO = ROOT / "assets/logo.png"
WASH = (0xF1, 0xEB, 0xDF)
SOURCE_LOGO_SIZE = (1983, 793)
EMBLEM_SEED_RIGHT = 1090


def extract_emblem(source: Image.Image) -> Image.Image:
    """Keep the separate artwork components without clipping the wave's tip."""
    logo = source.convert("RGBA")
    width, height = logo.size
    alpha = logo.getchannel("A").tobytes()
    visited = bytearray(len(alpha))
    kept_alpha = bytearray(len(alpha))

    # The artwork and the lettering are separate transparent components. All
    # artwork components begin left of this boundary; the lettering does not.
    for y in range(height):
        for x in range(EMBLEM_SEED_RIGHT):
            start = y * width + x
            if not alpha[start] or visited[start]:
                continue
            queue = deque([start])
            visited[start] = 1
            while queue:
                position = queue.popleft()
                kept_alpha[position] = alpha[position]
                px, py = position % width, position // width
                for ny in range(max(0, py - 1), min(height, py + 2)):
                    for nx in range(max(0, px - 1), min(width, px + 2)):
                        adjacent = ny * width + nx
                        if alpha[adjacent] and not visited[adjacent]:
                            visited[adjacent] = 1
                            queue.append(adjacent)

    logo.putalpha(Image.frombytes("L", logo.size, bytes(kept_alpha)))
    return logo.crop(logo.getchannel("A").getbbox())


def render_icon(logo: Image.Image, size: int, *, macos: bool = False) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (*WASH, 255))
    width = round(size * 0.78)
    height = round(width * logo.height / logo.width)
    artwork = logo.resize((width, height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(artwork, ((size - width) // 2, (size - height) // 2))

    if macos:
        mask = Image.new("L", (size, size))
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255
        )
        canvas.putalpha(mask)
        return canvas
    return canvas.convert("RGB")


def main() -> None:
    if WEB_LOGO.exists() and WEB_LOGO.read_bytes() != APP_LOGO.read_bytes():
        raise SystemExit("assets/logo.png is different from the website logo")

    with Image.open(APP_LOGO) as source:
        if source.size != SOURCE_LOGO_SIZE:
            raise SystemExit("Website logo dimensions changed; review the emblem crop")
        logo = extract_emblem(source)

    master = render_icon(logo, 1024)
    master.save(ROOT / "assets/app_icon.png")

    macos_dir = ROOT / "macos/Runner/Assets.xcassets/AppIcon.appiconset"
    for size in (16, 32, 64, 128, 256, 512, 1024):
        render_icon(logo, size, macos=True).save(macos_dir / f"app_icon_{size}.png")

    ios_dir = ROOT / "ios/Runner/Assets.xcassets/AppIcon.appiconset"
    contents = json.loads((ios_dir / "Contents.json").read_text())
    for entry in contents["images"]:
        points = float(entry["size"].split("x")[0])
        scale = int(entry["scale"].removesuffix("x"))
        render_icon(logo, round(points * scale)).save(ios_dir / entry["filename"])

    android_dir = ROOT / "android/app/src/main/res"
    for density, size in {
        "mdpi": 48,
        "hdpi": 72,
        "xhdpi": 96,
        "xxhdpi": 144,
        "xxxhdpi": 192,
    }.items():
        render_icon(logo, size).save(android_dir / f"mipmap-{density}/ic_launcher.png")


if __name__ == "__main__":
    main()
