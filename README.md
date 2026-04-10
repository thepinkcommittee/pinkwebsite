## content format: .hack
Submissions should follow the format of the following example:
    
```text
title: My Hack Title
date: 2025-09-23
location: some building
status: temporary installation
perpetrators: anonymous
contributors: club foo
topic: engineering, campus-life
---
Opening paragraph here.

===

This above (===) is a dotted line divider.
More text after a divider.

!photo_one.jpg
```

If you need more clarification refer to previous [submissions](https://github.com/thepinkcommittee/pinkwebsite/tree/main/entries) or email us at [thepinkcommittee@gmail.com](mailto:thepinkcommittee@gmail.com).

## how to submit a new entry
> [!WARNING]
> **DO NOT INCLUDE ANY PERSONALLY IDENTIFIABLE INFORMATION IN YOUR EMAIL.**
> This includes information in your `.hack` file, image files, and even file names.
> Submitted content WILL BE MADE PUBLIC once a pull request is created as part of this process. THIS CANNOT BE REVERTED.

Send an email to [thepinkcommittee@gmail.com](mailto:thepinkcommittee@gmail.com) with the subject:

```text
pinkwebsite: submission
```

The first non-empty line of the email body must be this exact consent statement:

```text
i confirm that there is no personally identifiable information in the included files 
and that i have sent the correct files for submission. i understand that once i submit, 
unless there are invalid files resulting in submission rejection, my submission will be 
made public in the pinkwebsite github.
```

Attach one or more files:
- `.hack` files for the entry content
- image files such as `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, or `.svg`

## how to edit an existing entry
Send an email with this subject:

```text
pinkwebsite: edit
```

Email body format:
- first non-empty line: the same consent statement shown above
- then one mapping line per attached `.hack` file, in this exact format:

```text
filename.hack = "title of hack", "date of hack"
```

Example:

```text
i confirm that there is no personally identifiable information in the included files and that i have sent the correct files for submission. i understand that once i submit, unless there are invalid files resulting in submission rejection, my submission will be made public in the pinkwebsite github.
r2d2-1999.hack = "r2d2", "1999-01-01"
```

Edit request rules:
- only `.hack` attachments are allowed (no images)
- number of `.hack` attachments must match number of mapping lines
- each mapping filename must match an attached `.hack` filename
- each mapped file must already exist in `entries/`
- mapped title and date must match the current front matter in the existing file
- accepted edits overwrite the target `.hack` file and regenerate site pages

## review and PR flow
For both `pinkwebsite: submission` and `pinkwebsite: edit`:
- bot replies with `pinkwebsite: received`
- request is queued for manual approval
- a PR is created only after a reply from the committee in the same email thread containing `proceed`
- bot replies with `pinkwebsite: pr request made` when PR is opened
- if PR is closed without merge: `pinkwebsite: rejected`
- if PR is merged: `pinkwebsite: accepted`
- PR body includes clickable links to submitted files

## common rejection reasons
- consent statement missing, malformed, or not first non-empty line
- invalid attachment file types
- no valid attachments
- for `pinkwebsite: submission`: any attached filename already exists in the repository (case-insensitive)
- for `pinkwebsite: edit`: non-`.hack` attachment included
- for `pinkwebsite: edit`: malformed mapping line
- for `pinkwebsite: edit`: mismatch between `.hack` attachment count and mapping line count
- for `pinkwebsite: edit`: mapped filename/title/date does not match an existing entry

If your request is rejected, send a **new email** (do not reply to the existing thread) with the same subject (`pinkwebsite: submission` or `pinkwebsite: edit`) and corrected attachments/body. You will receive notice if this happens.
