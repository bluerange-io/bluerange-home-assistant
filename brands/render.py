"""Renders the brand PNGs that home-assistant/brands expects.

Needs ``rsvg-convert`` (``brew install librsvg``) and Pillow:

    python3 brands/render.py

The sizes come from the brands repository: a square icon at 256 and 512 pixels,
and a logo whose shortest side is 256 and 512 pixels.  The logo is trimmed to its
content, as the brands repository asks for.  ``logo.svg`` and ``dark_logo.svg``
carry the black and the white wordmark respectively.  The icon carries its own
background and reads on either theme, so it needs no dark variant.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from PIL import Image

HERE = Path(__file__).parent

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
    """Return the brands suffix for the first or the second size."""
    return "" if index == 0 else "@2x"


def main() -> int:
    """Render every file the brands repository accepts for this integration."""
    icon_svg = HERE / "icon.svg"
    if icon_svg.exists():
        source = rasterise(icon_svg, "-w", str(SOURCE_HEIGHT), "-h", str(SOURCE_HEIGHT))
        # The icon is a full bleed tile, so there is nothing transparent to trim.
        icon = Image.open(source).convert("RGB")
        for index, size in enumerate(ICON_SIZES):
            name = f"icon{suffix(index)}.png"
            icon.resize((size, size), Image.LANCZOS).save(HERE / name, optimize=True)
            print(f"{name}: {size}x{size}")
        source.unlink()
    else:
        print("icon.svg: skipped (source missing, awaiting new artwork)")

    for prefix, stem in (("", "logo"), ("dark_", "dark_logo")):
        source = rasterise(HERE / f"{stem}.svg", "-h", str(SOURCE_HEIGHT))
        logo = trim(Image.open(source).convert("RGBA"))
        for index, height in enumerate(LOGO_HEIGHTS):
            scaled = to_height(logo, height)
            name = f"{prefix}logo{suffix(index)}.png"
            scaled.save(HERE / name, optimize=True)
            print(f"{name}: {scaled.width}x{scaled.height}")
        source.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
