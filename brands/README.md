# Brand image sources

Home Assistant 2026.3 dropped the separate `home-assistant/brands` repository
for custom integrations: they now ship their icon and logo in a `brand/`
subfolder of the integration, and Home Assistant serves them through
`/api/brands/integration/{domain}/{image}`. This folder holds the vector
sources; the rendered PNGs live under `custom_components/bluerange/brand/`.

## Sources

| File                        | Origin                                      | Shape       |
| --------------------------- | ------------------------------------------- | ----------- |
| `logo.svg`                  | `original/bluerange_logo_black.svg`         | 669 × 117   |
| `dark_logo.svg`             | `original/bluerange_logo_white.svg`         | 669 × 117   |
| `original/icon.png`         | supplied icon mark for the light theme      | 512 × 512   |
| `original/icon_dark.png`    | supplied icon mark for the dark theme       | 512 × 512   |
| `original/BR-Icon-tile.png` | the original supplied tile, kept for record | 512 × 512   |

The wordmark is black by default, so a white copy is produced for the `dark_`
variant. The icon comes as two variants on a transparent background — dark grey
for the light theme, white for the dark theme — so the renderer only downscales
them.

## Rendered files

`brands/render.py` writes these into `custom_components/bluerange/brand/`:

| File               | Size       |
| ------------------ | ---------- |
| `icon.png`         | 256 × 256  |
| `icon@2x.png`      | 512 × 512  |
| `dark_icon.png`    | 256 × 256  |
| `dark_icon@2x.png` | 512 × 512  |
| `logo.png`         | 1526 × 256 |
| `logo@2x.png`      | 3052 × 512 |
| `dark_logo.png`    | 1526 × 256 |
| `dark_logo@2x.png` | 3052 × 512 |

## Re-rendering

Needs `rsvg-convert` (`brew install librsvg`) and Pillow:

```bash
python3 brands/render.py
```

The script renders the wordmark at 1024 pixels first, trims it to its content
and only then scales down, so that nothing is enlarged from a small render. The
icon sources are already bitmaps and are only downscaled.

## Image requirements

- Icon square, 256 × 256 and 512 × 512.
- Logo landscape, shortest side 256 and 512 pixels.
- PNG only, transparency preferred, trimmed to the content, and optimised for a
  white background.
