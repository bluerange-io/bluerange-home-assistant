# Brand image sources

Home Assistant 2026.3 dropped the separate `home-assistant/brands` repository
for custom integrations: they now ship their icon and logo in a `brand/`
subfolder of the integration, and Home Assistant serves them through
`/api/brands/integration/{domain}/{image}`. This folder holds the vector
sources; the rendered PNGs live under `custom_components/bluerange/brand/`.

## Sources

| File            | Origin                              | Shape     |
| --------------- | ----------------------------------- | --------- |
| `logo.svg`      | `original/bluerange_logo_black.svg` | 669 × 117 |
| `dark_logo.svg` | `original/bluerange_logo_white.svg` | 669 × 117 |

The wordmark is black by default, so a white copy is produced for the `dark_`
variant.

## Rendered files

`brands/render.py` writes these into `custom_components/bluerange/brand/`:

| File               | Size       |
| ------------------ | ---------- |
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
and only then scales down, so that nothing is enlarged from a small render.

## Image requirements

- Logo landscape, shortest side 256 and 512 pixels.
- PNG only, transparency preferred, trimmed to the content, and optimised for a
  white background.
