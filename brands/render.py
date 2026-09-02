"""Renders the icon and logo PNGs that Home Assistant loads from the integration.

Needs ``rsvg-convert`` (``brew install librsvg``) and Pillow:

    python3 brands/render.py

Since Home Assistant 2026.3 dropped the brands repository for custom
integrations, these files are served from ``custom_components/bluerange/brand/``
through ``/api/brands/integration/bluerange/{image}``. ``logo.svg`` and
``dark_logo.svg`` carry the black and the white wordmark; ``original/icon.png``
and ``original/icon_dark.png`` carry the icon mark for the light and the dark
theme on a transparent background.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from PIL import Image

HERE = Path(__file__).parent
TARGET = HERE.parent / "custom_components" / "bluerange" / "brand"

#: Rendered before scaling, so that trimming and resampling have pixels to work
#: with rather than enlarging a small render.
SOURCE_HEIGHT = 1024

ICON_SIZES = (256, 512)
LOGO_HEIGHTS = (256, 512)


def rasterise(source: Path, *arguments: str) -> Path:
    """Render an SVG to a temporary PNG next to it."""
    target = source.with_name(f"_{source.stem}_source.png")
    with target.open("wb") as handle:
        subprocess.run(
            ["rsvg-convert", *arguments, str(source)],
            stdout=handle,
            check=True,
        )
    return target


def trim(image: Image.Image) -> Image.Image:
    """Drop fully transparent borders so the image is as tight as it can be."""
    box = image.getchannel("A").getbbox()
    return image.crop(box) if box else image


def to_height(image: Image.Image, height: int) -> Image.Image:
    """Scale an image to a height, keeping its aspect ratio."""
    width = round(image.width * height / image.height)
    return image.resize((width, height), Image.LANCZOS)


def suffix(index: int) -> str:
    """Return the file name suffix for the first or the second size."""
    return "" if index == 0 else "@2x"


def main() -> int:
    """Render every icon and logo file the integration ships with."""
    for prefix, stem in (("", "icon"), ("dark_", "icon_dark")):
        source = HERE / "original" / f"{stem}.png"
        if not source.exists():
            print(f"original/{stem}.png: skipped (source missing)")
            continue
        mark = Image.open(source).convert("RGBA")
        for index, size in enumerate(ICON_SIZES):
            name = f"{prefix}icon{suffix(index)}.png"
            mark.resize((size, size), Image.LANCZOS).save(TARGET / name, optimize=True)
            print(f"{name}: {size}x{size}")

    for prefix, stem in (("", "logo"), ("dark_", "dark_logo")):
        source = rasterise(HERE / f"{stem}.svg", "-h", str(SOURCE_HEIGHT))
        logo = trim(Image.open(source).convert("RGBA"))
        for index, height in enumerate(LOGO_HEIGHTS):
            scaled = to_height(logo, height)
            name = f"{prefix}logo{suffix(index)}.png"
            scaled.save(TARGET / name, optimize=True)
            print(f"{name}: {scaled.width}x{scaled.height}")
        source.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
