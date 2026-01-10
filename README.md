## content format: .hack
Each entry lives in `entries/<id>.hack` and its assets in `assets/<id>/`.

Front-matter (key: value), then a line with x then freeform body text:

```text
id: my-hack-2025
title: My Hack Title
date: 2025-09-01
location: Some building
status: temporary installation
perpetrators: anonymous
contributors: club foo
preview: hero.jpg
assets: photo1.jpg, photo2.jpg, video1.mp4
---
Paragraphs of story text here.
```

- **preview**: shown on the homepage as a grayscale, dithered thumbnail
- **assets**: comma-separated file names inside `assets/<id>/`; images will be shown on the entry page

## build
Generate pages and update the homepage list:

```bash
python build.py
```

Outputs per-entry pages into `hacks/<id>.html` and injects previews between the `<!-- BUILD:RECENT:START/END -->` markers in `index.html`. 