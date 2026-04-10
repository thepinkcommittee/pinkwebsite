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
> Submitted content WILL BE MADE PUBLIC as pull request is created as part of this submission process. THIS CANNOT BE REVERTED.

Send an email to [thepinkcommittee@gmail.com](mailto:thepinkcommittee@gmail.com) with the subject:

```text
pinkwebsite: submission
```

The email body must contain the following consent statement:

```text
i confirm that there is no personally identifiable information in the included files
and that i have sent the correct files for submission. i understand that once i submit,
unless there are invalid files resulting in submission rejection, my submission will be
made public in the pinkwebsite github.
```

Attach one or more files:
- `.hack` files for the entry content
- image files such as `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, or `.svg`

The bot will:
- reply with `pinkwebsite: received`
- put `.hack` files into `entries/` and images into `assets/`
- run `build.py` to generate `hacks/` pages and update `index.html`
- commit those changes in a pull request
- include a list of submitted `.hack` and image files in the PR body, with clickable links to each file
- reply again with `pinkwebsite: pr request made`
- if the PR is closed without merging, it will send `pinkwebsite: rejected`
- if the PR is merged, it will send `pinkwebsite: accepted`

The bot will reject the submission if any of the following occur:
- the consent statement in the email body is missing or does not match
- invalid attachment file types are included
- no valid attachments are included
- any attached filename already exists in the repository (case-insensitive)

If your submission is rejected, send a **new email** (do not reply to the existing thread) with the same subject and updated attachments as per the instructions in your closed PR. You will receive notice of this if it happens. Thanks.
