## content format: .hack
Each entry lives in `entries/<name>.hack`.

A `.hack` file has two parts:
1. Front-matter lines as `key: value`
2. A separator line `---`
3. Freeform body text

Example:
    
```text
title: My Hack Title
date: 2025-09-01
location: some building
status: temporary installation
perpetrators: anonymous
contributors: club foo
topic: engineering, campus-life
---
Opening paragraph here.

===

More text after a divider.

!photo_one.jpg
```

### front-matter behavior
- Keys are case-insensitive.
- Blank lines are ignored.
- Lines starting with `#` are treated as comments and ignored.
- Do not include `id` in front matter.
- IDs are backend-internal only and are generated from each `.hack` filename stem.
- Commonly used keys in generated pages are:
	`title`, `date`, `location`, `status`, `perpetrators`, `contributors`, `topic`.

### body formatting supported
- Paragraphs: separate paragraphs with a blank line.
- Divider: a line that is exactly `===` becomes a horizontal rule.
- Inline images: `!filename.ext` inserts an image from `assets/filename.ext`.
	Allowed token characters are letters, numbers, `_`, `-`, `.`, and `@`.
- The first inline image token is used as the homepage preview thumbnail.
- Regular text is HTML-escaped before rendering.

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
- put `.hack` files into `entries/` and images into `assets/`
- run `build.py` to generate `hacks/` pages and update `index.html`
- commit those changes in a pull request
- reply again with `pinkwebsite: pr request made`
- if the PR is closed without merging, it will send `pinkwebsite: rejected`
- if the PR is merged, it will send `pinkwebsite: accepted`

If your submission is rejected, send a new email with the same subject and updated attachments as per the instructions in your closed PR. Thanks.

## build
Generate pages and update the homepage list:

```bash
python build.py
```

Outputs per-entry pages into `hacks/<generated-id>.html` (generated from each `.hack` filename) and injects previews between the `<!-- BUILD:RECENT:START/END -->` markers in `index.html`.