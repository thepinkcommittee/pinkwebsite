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

## how to submit a new entry
Send an email to `thepinkcommittee@gmail.com` with the subject:

```text
pinkwebsite: submission
```

Attach one or more files:
- `.hack` files for the entry content
- image files such as `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, or `.svg`

The bot will:
- reply with `pinkwebsite: received`
- create a pull request with `.hack` files in `hacks/` and images in `assets/`
- reply again with `pinkwebsite: pr request made`
- if the PR is closed without merging, it will send `pinkwebsite: rejected`
- if the PR is merged, it will send `pinkwebsite: accepted`

If your submission is rejected, send a new email with the same subject and updated attachments.

## build
Generate pages and update the homepage list:

```bash
python build.py
```

Outputs per-entry pages into `hacks/<id>.html` and injects previews between the `<!-- BUILD:RECENT:START/END -->` markers in `index.html`. 