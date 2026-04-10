## warning!
> [!WARNING]
> **DO NOT INCLUDE ANY PERSONALLY IDENTIFIABLE INFORMATION (PII) IN YOUR EMAIL.**
> This includes information in your `.hack` file, image files, and even file names.
> Submitted content may be made public when a pull request is created, and this cannot be fully reverted.

## content format: .hack
Submissions should follow the format of the following example:
    
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

If you need more clarification refer to previous [submissions](https://github.com/thepinkcommittee/pinkwebsite/tree/main/entries) or email us at [thepinkcommittee@gmail.com](mailto:thepinkcommittee@gmail.com).

## how to submit a new entry
> [!WARNING]
> **DO NOT INCLUDE ANY PERSONALLY IDENTIFIABLE INFORMATION (PII) IN YOUR EMAIL.**
> This includes information in your `.hack` file, image files, and even file names.
> Submitted content may be made public when a pull request is created, and this cannot be fully reverted.

Send an email to [thepinkcommittee@gmail.com](mailto:thepinkcommittee@gmail.com) with the subject:

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