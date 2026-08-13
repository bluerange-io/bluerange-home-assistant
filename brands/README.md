# Brand images

Home Assistant does not take the logo of an integration from the integration
itself. It loads brand images from <https://brands.home-assistant.io>, which is
built from the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. Until `bluerange` exists there, Home Assistant and HACS show a
placeholder, and nothing that is added to `custom_components/` changes that.

This folder holds the finished images together with the vector sources they were
rendered from, so the pull request is a matter of copying the PNGs across.

## Files

| File               | Size       | Notes                                    |
| ------------------ | ---------- | ---------------------------------------- |
| `logo.png`         | 1526 × 256 | wordmark, black on transparent, trimmed  |
| `logo@2x.png`      | 3052 × 512 |                                          |
| `dark_logo.png`    | 1526 × 256 | the same wordmark in white               |
| `dark_logo@2x.png` | 3052 × 512 |                                          |

The wordmark is black, which would disappear on a dark theme, so a white copy is
supplied as the `dark_` variant.

`icon.png` and `icon@2x.png` are missing: the previous artwork was outdated and
a replacement has not been supplied yet. Drop a new `icon.svg` into this folder
and re-render to produce them.

## Sources

| File            | Origin                                    | Shape     |
| --------------- | ----------------------------------------- | --------- |
| `logo.svg`      | `original/bluerange_logo_black.svg`       | 669 × 117 |
| `dark_logo.svg` | `original/bluerange_logo_white.svg`       | 669 × 117 |

The originals in `original/` are the wordmark as supplied by BlueRange; the two
`*.svg` files at the top of this folder are the working copies the renderer
reads. If the brand guide has moved on, drop the new files into `original/` and
copy them over.

## Re-rendering

Needs `rsvg-convert` (`brew install librsvg`) and Pillow:

```bash
python3 brands/render.py
```

The script renders at 1024 pixels first, trims the logo to its content and only
then scales down, so that nothing is enlarged from a small render.

## What the brands repository requires

Verified against its README:

- `icon.png` 256 × 256 and `icon@2x.png` 512 × 512.
- `logo.png` with its shortest side between 128 and 256 pixels, `logo@2x.png`
  between 256 and 512.
- Dark variants are optional and use a `dark_` prefix; the light file is served
  when one is missing.
- PNG only, transparency preferred, trimmed to the content, and optimised for a
  white background.
- The icon has to be square; the logo keeps its natural landscape ratio.
- A custom integration must not use Home Assistant branded imagery.

## Submitting

1. Fork [home-assistant/brands](https://github.com/home-assistant/brands).
2. Copy the PNGs into `custom_integrations/bluerange/`.
3. Open a pull request. Once it is merged, the logo shows up in Home Assistant
   and HACS without any change to this repository.

Wait for the new `icon.png`/`icon@2x.png` before submitting — the brands
repository accepts a logo without an icon, but the icon is what shows up in the
integrations list.
