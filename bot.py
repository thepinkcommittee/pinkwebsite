import base64
import os
import re
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from github import Github, Auth
from github.GithubException import UnknownObjectException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

ATTACHMENT_LABEL = "pinkwebsite-processed"
ALLOWED_HACK_EXTENSIONS = {".hack"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
ALLOWED_EXTENSIONS = ALLOWED_HACK_EXTENSIONS.union(ALLOWED_IMAGE_EXTENSIONS)
ALLOWED_EXTENSIONS_DISPLAY = ", ".join(sorted(ALLOWED_EXTENSIONS))
REJECTED_COMMENT_MARKER = "BOT: rejected notification sent"
ACCEPTED_BODY_MARKER = "<!-- pink-bot-accepted-notified -->"
SUBJECT_RECEIVED = "pinkwebsite: received"
SUBJECT_SUBMISSION_FAILED = "pinkwebsite: submission failed"
SUBJECT_PR_REQUEST_MADE = "pinkwebsite: pr request made"
SUBJECT_ACCEPTED = "pinkwebsite: accepted"
SUBJECT_REJECTED = "pinkwebsite: rejected"
SUBJECT_SUBMISSION = "pinkwebsite: submission"
SUBJECT_BOT_ERROR = "pinkwebsite: bot error"
BOT_ALERT_EMAIL = "thepinkcommittee@gmail.com"


def normalize_hack_front_matter(file_path: str):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.splitlines()
    front_matter = []
    body_start = 0
    in_front = True
    for i, line in enumerate(lines):
        if line.strip() == '---':
            if in_front:
                body_start = i + 1
                break
            else:
                in_front = True
        elif in_front:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower()
                if key == 'id':
                    continue
                if key == 'title':
                    value = value.strip()
                elif key == 'location':
                    value = value.strip().title()
                else:
                    value = value.strip().lower()
                front_matter.append(f'{key}: {value}')
            else:
                front_matter.append(line)
        else:
            break
    
    if body_start > 0:
        new_content = '\n'.join(front_matter) + '\n---\n' + '\n'.join(lines[body_start:])
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)


def build_standard_email_body(status: str, message: str, details: Optional[str] = None) -> str:
    lines = [
        "Hello,",
        "",
        f"status: {status}",
        "",
        message.strip(),
    ]
    if details:
        lines.extend(["", details.strip()])
    lines.extend(["", "Regards,", "PinkWebsite Submission Bot"])
    return "\n".join(lines)


def get_env(name: str, required: bool = True) -> str:
    value = os.getenv(name)
    if required and not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value or ""


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=get_env("GMAIL_REFRESH_TOKEN"),
        client_id=get_env("GMAIL_CLIENT_ID"),
        client_secret=get_env("GMAIL_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_github_repo():
    token = get_env("GITHUB_TOKEN")
    repository = get_env("GITHUB_REPOSITORY")
    gh = Github(auth=Auth.Token(token))
    return gh.get_repo(repository)


def get_or_create_label(service, label_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == label_name:
            return label["id"]
    label = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return label["id"]


def list_submission_messages(service, label_name: str) -> List[Dict]:
    query = f'subject:"{SUBJECT_SUBMISSION}" -label:{label_name}'
    response = service.users().messages().list(userId="me", q=query).execute()
    return response.get("messages", [])


def parse_headers(payload: Dict) -> Dict[str, str]:
    headers = payload.get("headers", [])
    return {header["name"].lower(): header["value"] for header in headers}


def collect_parts(payload: Dict) -> List[Dict]:
    parts = []
    if payload.get("parts"):
        for part in payload["parts"]:
            parts.extend(collect_parts(part))
    else:
        parts.append(payload)
    return parts


def is_valid_attachment(filename: str) -> bool:
    extension = os.path.splitext(filename)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def attachment_target_dir(filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    return "entries" if extension in ALLOWED_HACK_EXTENSIONS else "assets"


def build_repo_file_link(repo_full_name: str, branch_name: str, file_path: str) -> str:
    encoded_path = quote(file_path, safe="/")
    return f"https://github.com/{repo_full_name}/blob/{branch_name}/{encoded_path}"


def build_attachment_links_markdown(repo_full_name: str, branch_name: str, attachment_paths: List[str]) -> str:
    hack_lines = []
    image_lines = []

    for file_path in attachment_paths:
        url = build_repo_file_link(repo_full_name, branch_name, file_path)
        line = f"- [{file_path}]({url})"
        extension = os.path.splitext(file_path)[1].lower()
        if extension in ALLOWED_HACK_EXTENSIONS:
            hack_lines.append(line)
        else:
            image_lines.append(line)

    sections = ["#### .hack files"]
    sections.extend(hack_lines or ["- none"])
    sections.append("")
    sections.append("#### image files")
    sections.extend(image_lines or ["- none"])
    return "\n".join(sections)


def find_case_insensitive_duplicate_filenames(repo, base_branch: str, attachments: List[Dict]) -> List[str]:
    existing_names_by_dir: Dict[str, set] = {"entries": set(), "assets": set()}

    for directory in ("entries", "assets"):
        try:
            directory_contents = repo.get_contents(directory, ref=base_branch)
        except UnknownObjectException:
            directory_contents = []

        if not isinstance(directory_contents, list):
            directory_contents = [directory_contents]

        existing_names_by_dir[directory] = {
            item.name.casefold()
            for item in directory_contents
            if getattr(item, "type", "") == "file"
        }

    seen_submission_names: Dict[str, set] = {"entries": set(), "assets": set()}
    duplicates = set()

    for attachment in attachments:
        raw_filename = attachment.get("filename", "")
        filename = os.path.basename(raw_filename).strip()
        if not filename:
            continue

        directory = attachment_target_dir(filename)
        normalized = filename.casefold()

        if normalized in seen_submission_names[directory] or normalized in existing_names_by_dir[directory]:
            duplicates.add(filename)

        seen_submission_names[directory].add(normalized)

    return sorted(duplicates, key=str.casefold)


def download_attachments(service, message_id: str, payload: Dict) -> tuple[List[Dict], List[str]]:
    attachments = []
    invalid_files = []
    for part in collect_parts(payload):
        filename = part.get("filename")
        if not filename:
            continue

        if not is_valid_attachment(filename):
            invalid_files.append(filename)
            continue

        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        raw_data = body.get("data")
        if attachment_id:
            data_obj = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            raw_data = data_obj.get("data")

        if not raw_data:
            continue

        try:
            content = base64.urlsafe_b64decode(raw_data.encode("utf-8"))
        except Exception:
            continue

        attachments.append(
            {
                "filename": filename,
                "content": content,
                "mimeType": part.get("mimeType", "application/octet-stream"),
            }
        )
    return attachments, invalid_files


def safe_branch_name(message_id: str) -> str:
    timestamp = int(time.time())
    short_id = message_id.replace("<", "").replace(">", "")[:8]
    return f"submission/{timestamp}-{short_id}"


def unique_target_path(repo, base_branch: str, original_path: str) -> str:
    candidate = original_path
    root, extension = os.path.splitext(original_path)
    counter = 1
    while True:
        try:
            repo.get_contents(candidate, ref=base_branch)
            candidate = f"{root}-{counter}{extension}"
            counter += 1
        except UnknownObjectException:
            return candidate


def write_branch_file(repo, branch_name: str, file_path: str, content, message: str):
    try:
        existing = repo.get_contents(file_path, ref=branch_name)
        repo.update_file(file_path, message, content, existing.sha, branch=branch_name)
    except UnknownObjectException:
        repo.create_file(file_path, message, content, branch=branch_name)


def run_build_script(news_timestamp: str = "", new_entry_stems: Optional[List[str]] = None):
    env = os.environ.copy()
    if news_timestamp:
        env["PINK_NEWS_TIMESTAMP"] = news_timestamp
    if new_entry_stems:
        env["PINK_NEW_ENTRY_STEMS"] = ",".join(new_entry_stems)
    subprocess.run(["python", "build.py"], check=True, env=env)


def create_branch(repo, base_branch: str, branch_name: str):
    source_sha = repo.get_branch(base_branch).commit.sha
    ref = f"refs/heads/{branch_name}"
    repo.create_git_ref(ref=ref, sha=source_sha)


def create_pr_for_attachments(repo, branch_name: str, base_branch: str, attachments: List[Dict], message_id: str) -> str:
    unique_paths = []
    for attachment in attachments:
        filename = attachment["filename"]
        extension = os.path.splitext(filename)[1].lower()
        if extension in ALLOWED_HACK_EXTENSIONS:
            target_path = os.path.join("entries", filename)
        else:
            target_path = os.path.join("assets", filename)
        target_path = unique_target_path(repo, base_branch, target_path)
        unique_paths.append((target_path.replace("\\", "/"), attachment["content"]))

    # Save attachments locally before building
    for path, content in unique_paths:
        local_path = Path(path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(content)

    # Normalize front matter in .hack files
    for path, _ in unique_paths:
        if os.path.splitext(path)[1].lower() == '.hack':
            normalize_hack_front_matter(path)

    # Build the site locally so generated files are created before commit
    new_entry_stems = sorted(
        {
            Path(path).stem
            for path, _ in unique_paths
            if os.path.splitext(path)[1].lower() in ALLOWED_HACK_EXTENSIONS
        }
    )
    pr_requested_at = time.strftime("%Y-%m-%d %H:%M:%S")
    run_build_script(pr_requested_at, new_entry_stems)

    # Upload attachment and generated site files to the new branch
    for path, content in unique_paths:
        write_branch_file(repo, branch_name, path, content, f"Add submission file {os.path.basename(path)}")

    generated_files = ["index.html"]
    generated_files += [str(p) for p in sorted(Path("hacks").glob("*.html"))]
    generated_files += [str(p) for p in sorted(Path("browse").glob("*.html"))]

    for gen_path in generated_files:
        with open(gen_path, "rb") as f:
            content = f.read()
        write_branch_file(repo, branch_name, gen_path.replace("\\", "/"), content, f"Update generated site file {gen_path}")

    pr_title = f"New submission from {message_id}"
    attachment_paths = [path for path, _ in unique_paths]
    attachment_links_md = build_attachment_links_markdown(repo.full_name, branch_name, attachment_paths)
    pr_body = (
        "Automated submission.\n"
        f"Original message ID: {message_id}\n"
        "\n"
        "Submitted files from email:\n"
        "\n"
        f"{attachment_links_md}\n"
        "\n"
        "This pull request was created by the PinkWebsite submission bot."
    )

    pr = repo.create_pull(
        title=pr_title,
        body=pr_body,
        head=branch_name,
        base=base_branch,
    )
    return pr.html_url


def make_reply(subject: str, body_text: str, to_address: str, thread_id: Optional[str], references: Optional[str]) -> Dict:
    message = EmailMessage()
    message["To"] = to_address
    message["From"] = get_env("GMAIL_USER")
    message["Subject"] = subject
    if references:
        message["In-Reply-To"] = references
        message["References"] = references
    message.set_content(body_text)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return payload


def send_message(service, message_body: Dict) -> Dict:
    return service.users().messages().send(userId="me", body=message_body).execute()


def send_standard_reply(
    service,
    to_address: str,
    thread_id: Optional[str],
    references: Optional[str],
    subject: str,
    status: str,
    message: str,
    details: Optional[str] = None,
) -> Dict:
    body = build_standard_email_body(status, message, details)
    return send_message(service, make_reply(subject, body, to_address, thread_id, references))


def mark_message_processed(service, message_id: str, label_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [label_id],
        },
    ).execute()


def extract_message_id_from_pr_body(body: str) -> str:
    if not body:
        return ""
    match = re.search(r"Original message ID:\s*([^\n]+)", body)
    if match:
        return match.group(1).strip()
    return ""


def get_message_reply_context(service, message_id: str) -> tuple[Optional[Dict[str, str]], str]:
    try:
        message = service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
    except Exception as exc:
        return None, f"Failed to fetch Gmail message metadata: {exc}"

    headers = parse_headers(message.get("payload", {}))
    sender = headers.get("from", "")
    if not sender:
        return None, "Missing sender in Gmail message headers"

    thread_id = message.get("threadId", "")
    references = headers.get("message-id") or headers.get("in-reply-to") or headers.get("references") or ""

    return (
        {
            "sender": sender,
            "thread_id": thread_id,
            "references": references,
        },
        "",
    )


def notify_thread_context_error(service, pr_number: int, message_id: str, error_text: str):
    details = (
        f"PR number: #{pr_number}\n"
        f"Original message ID: {message_id}\n"
        f"Error: {error_text or 'Unknown error'}"
    )
    try:
        send_standard_reply(
            service,
            BOT_ALERT_EMAIL,
            None,
            None,
            SUBJECT_BOT_ERROR,
            "bot error",
            "Could not fetch original message context for threaded notification.",
            details,
        )
    except Exception as exc:
        print(f"Failed to send bot error alert email for PR #{pr_number}: {exc}")

def has_acceptance_marker(pr) -> bool:
    return ACCEPTED_BODY_MARKER in (pr.body or "")


def mark_acceptance_notified(pr):
    body = (pr.body or "").rstrip()
    if ACCEPTED_BODY_MARKER in body:
        return
    if body:
        body = f"{body}\n\n{ACCEPTED_BODY_MARKER}"
    else:
        body = ACCEPTED_BODY_MARKER
    pr.edit(body=body)


def has_rejection_comment(pr) -> bool:
    for comment in pr.get_issue_comments():
        if REJECTED_COMMENT_MARKER in comment.body:
            return True
    return False


def send_accepted_notifications(service, repo, base_branch: str):
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if not pr.merged:
            continue
        if not pr.head.ref.startswith("submission/"):
            continue

        if has_acceptance_marker(pr):
            continue

        message_id = extract_message_id_from_pr_body(pr.body)
        if not message_id:
            print(f"Skipping merged PR #{pr.number}: original message ID not found")
            continue

        reply_context, context_error = get_message_reply_context(service, message_id)
        if not reply_context:
            notify_thread_context_error(service, pr.number, message_id, context_error)
            print(f"Skipping merged PR #{pr.number}: reply context not found")
            continue

        send_standard_reply(
            service,
            reply_context["sender"],
            reply_context.get("thread_id") or None,
            reply_context.get("references") or None,
            SUBJECT_ACCEPTED,
            "accepted",
            "Your submission has been accepted and merged.",
            f"PR URL: {pr.html_url}\nThe website will update shortly.",
        )
        mark_acceptance_notified(pr)
        print(f"Sent accepted email for PR #{pr.number}")


def send_rejected_notifications(service, repo, base_branch: str):
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if pr.merged:
            continue
        if not pr.head.ref.startswith("submission/"):
            continue

        if has_rejection_comment(pr) or has_acceptance_marker(pr):
            continue

        message_id = extract_message_id_from_pr_body(pr.body)
        if not message_id:
            print(f"Skipping rejected PR #{pr.number}: original message ID not found")
            continue

        reply_context, context_error = get_message_reply_context(service, message_id)
        if not reply_context:
            notify_thread_context_error(service, pr.number, message_id, context_error)
            print(f"Skipping rejected PR #{pr.number}: reply context not found")
            continue

        send_standard_reply(
            service,
            reply_context["sender"],
            reply_context.get("thread_id") or None,
            reply_context.get("references") or None,
            SUBJECT_REJECTED,
            "rejected",
            "Your submission PR was closed without merging.",
            (
                f"Please review and make the changes suggested in the PR here: {pr.html_url}\n"
                f"Then to resubmit, send a NEW email (do not reply to this thread) with subject '{SUBJECT_SUBMISSION}' and include the updated attachments."
            ),
        )
        pr.create_issue_comment(REJECTED_COMMENT_MARKER)
        print(f"Sent rejected email for PR #{pr.number}")


def process_message(service, repo, label_id: str, message):
    message_id = message["id"]
    full_message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = full_message.get("payload", {})
    headers = parse_headers(payload)
    sender = headers.get("from", "unknown sender")
    thread_id = full_message.get("threadId")
    references = headers.get("message-id")

    attachments, invalid_files = download_attachments(service, message_id, payload)
    if invalid_files:
        invalid_list = "\n".join(f"- {filename}" for filename in invalid_files)
        send_standard_reply(
            service,
            sender,
            thread_id,
            references,
            SUBJECT_SUBMISSION_FAILED,
            "submission failed",
            "Your submission included invalid file types.",
            (
                f"Invalid files:\n{invalid_list}\n"
                f"Allowed files: {ALLOWED_EXTENSIONS_DISPLAY}\n"
                f"Please send a NEW email (do not reply) with subject '{SUBJECT_SUBMISSION}' and corrected attachments."
            ),
        )
        mark_message_processed(service, message_id, label_id)
        print(f"Rejected message {message_id} due to invalid attachments")
        return

    if not attachments:
        send_standard_reply(
            service,
            sender,
            thread_id,
            references,
            SUBJECT_SUBMISSION_FAILED,
            "submission failed",
            "We did not find any valid attachments in your submission email.",
            (
                "Please attach one or more .hack files and image files (.png, .jpg, .jpeg, .gif, .webp, .svg).\n"
                f"Then send a NEW email (do not reply) with subject '{SUBJECT_SUBMISSION}'."
            ),
        )
        mark_message_processed(service, message_id, label_id)
        print(f"Processed message {message_id} with no valid attachments")
        return

    base_branch = repo.default_branch
    duplicate_filenames = find_case_insensitive_duplicate_filenames(repo, base_branch, attachments)
    if duplicate_filenames:
        duplicate_list = "\n".join(f"- {filename}" for filename in duplicate_filenames)
        send_standard_reply(
            service,
            sender,
            thread_id,
            references,
            SUBJECT_SUBMISSION_FAILED,
            "submission failed",
            "Some attached filenames already exist (case-insensitive match).",
            (
                f"Conflicting files:\n{duplicate_list}\n"
                f"Please rename the files, then send a NEW email (do not reply) with subject '{SUBJECT_SUBMISSION}'."
            ),
        )
        mark_message_processed(service, message_id, label_id)
        print(f"Rejected message {message_id} due to duplicate filenames")
        return

    send_standard_reply(
        service,
        sender,
        thread_id,
        references,
        SUBJECT_RECEIVED,
        "received",
        "Your submission email has been received.",
        "We are creating a pull request now.",
    )

    branch_name = safe_branch_name(message_id)
    create_branch(repo, base_branch, branch_name)
    pr_url = create_pr_for_attachments(repo, branch_name, base_branch, attachments, message_id)

    send_standard_reply(
        service,
        sender,
        thread_id,
        references,
        SUBJECT_PR_REQUEST_MADE,
        "pr request made",
        "A pull request has been created for your submission.",
        f"PR URL: {pr_url}",
    )
    mark_message_processed(service, message_id, label_id)
    print(f"Created PR for message {message_id}: {pr_url}")


def main():
    gmail_service = get_gmail_service()
    repo = get_github_repo()
    label_id = get_or_create_label(gmail_service, ATTACHMENT_LABEL)

    try:
        messages = list_submission_messages(gmail_service, ATTACHMENT_LABEL)
        if messages:
            print(f"Found {len(messages)} submission message(s) to process")
            for message in messages:
                try:
                    process_message(gmail_service, repo, label_id, message)
                except HttpError as exc:
                    print(f"Gmail API error for message {message['id']}: {exc}")
        else:
            print("No new submission emails found")

        send_accepted_notifications(gmail_service, repo, repo.default_branch)
        send_rejected_notifications(gmail_service, repo, repo.default_branch)
    except Exception as exc:
        print(f"Fatal error: {exc}")
        raise


if __name__ == "__main__":
    main()
