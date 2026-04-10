import base64
import os
import re
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional

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
ACCEPTED_COMMENT_MARKER = "BOT: accepted notification sent"
REJECTED_COMMENT_MARKER = "BOT: rejected notification sent"


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
    query = f'subject:"pinkwebsite: submission" -label:{label_name}'
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


def run_build_script():
    subprocess.run(["python", "build.py"], check=True)


def create_branch(repo, base_branch: str, branch_name: str):
    source_sha = repo.get_branch(base_branch).commit.sha
    ref = f"refs/heads/{branch_name}"
    repo.create_git_ref(ref=ref, sha=source_sha)


def create_pr_for_attachments(repo, branch_name: str, base_branch: str, attachments: List[Dict], sender: str, subject: str, message_id: str) -> str:
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

    # Build the site locally so generated files are created before commit
    run_build_script()

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

    pr_title = f"New submission from {sender or 'unknown sender'}"
    pr_body = (
        f"Automated submission from {sender}\n"
        f"Original subject: {subject}\n"
        f"Original message ID: {message_id}\n"
        f"\nThis pull request was created by the PinkWebsite submission bot."
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


def mark_message_processed(service, message_id: str, label_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [label_id],
        },
    ).execute()


def extract_sender_from_pr_body(body: str) -> str:
    if not body:
        return ""
    match = re.search(r"Automated submission from\s*([^\n]+)", body)
    if match:
        return match.group(1).strip()
    return ""


def has_acceptance_comment(pr) -> bool:
    for comment in pr.get_issue_comments():
        if ACCEPTED_COMMENT_MARKER in comment.body:
            return True
    return False


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
        if has_acceptance_comment(pr):
            continue

        recipient = extract_sender_from_pr_body(pr.body)
        if not recipient:
            print(f"Skipping merged PR #{pr.number}: sender email not found")
            continue

        subject = "pinkwebsite: accepted"
        body_text = (
            "Hello,\n\nYour submission has been accepted and merged. "
            "The website will update shortly.\n\nThank you."
        )
        send_message(service, make_reply(subject, body_text, recipient, None, None))
        pr.create_issue_comment(ACCEPTED_COMMENT_MARKER)
        print(f"Sent accepted email for PR #{pr.number} to {recipient}")


def send_rejected_notifications(service, repo, base_branch: str):
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if pr.merged:
            continue
        if not pr.head.ref.startswith("submission/"):
            continue
        if has_rejection_comment(pr) or has_acceptance_comment(pr):
            continue

        recipient = extract_sender_from_pr_body(pr.body)
        if not recipient:
            print(f"Skipping rejected PR #{pr.number}: sender email not found")
            continue

        subject = "pinkwebsite: rejected"
        body_text = (
            "email: rejected\n\n"
            "Your submission PR was closed without merging. "
            f"Please review the PR here: {pr.html_url}\n\n"
            "If you'd like to update the submission, send a new email with the same subject "
            "and include the updated attachments."
        )
        send_message(service, make_reply(subject, body_text, recipient, None, None))
        pr.create_issue_comment(REJECTED_COMMENT_MARKER)
        print(f"Sent rejected email for PR #{pr.number} to {recipient}")


def process_message(service, repo, label_id: str, message):
    message_id = message["id"]
    full_message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = full_message.get("payload", {})
    headers = parse_headers(payload)
    sender = headers.get("from", "unknown sender")
    subject = headers.get("subject", "pinkwebsite: submission")
    thread_id = full_message.get("threadId")

    attachments, invalid_files = download_attachments(service, message_id, payload)
    if invalid_files:
        invalid_list = "\n".join(f"- {filename}" for filename in invalid_files)
        body_text = (
            "email: the following submissions did not meet the submission requirements due to a filetype failure:\n"
            f"{invalid_list}\n\n"
            f"Allowed files: {ALLOWED_EXTENSIONS_DISPLAY}"
        )
        reply = make_reply("pinkwebsite: submission failed", body_text, sender, thread_id, headers.get("message-id"))
        send_message(service, reply)

        if not attachments:
            mark_message_processed(service, message_id, label_id)
            print(f"Processed message {message_id} with only invalid attachments")
            return

    if not attachments:
        body_text = (
            "We received your submission email but did not find any valid attachments. "
            "Please attach one or more .hack files and image files (.png, .jpg, .jpeg, .gif, .webp, .svg)."
        )
        reply = make_reply("pinkwebsite: received", body_text, sender, thread_id, headers.get("message-id"))
        send_message(service, reply)
        mark_message_processed(service, message_id, label_id)
        print(f"Processed message {message_id} with no valid attachments")
        return

    send_message(
        service,
        make_reply(
            "pinkwebsite: received",
            "Your submission email has been received. We are creating a pull request now.",
            sender,
            thread_id,
            headers.get("message-id"),
        ),
    )

    base_branch = repo.default_branch
    branch_name = safe_branch_name(message_id)
    create_branch(repo, base_branch, branch_name)
    pr_url = create_pr_for_attachments(repo, branch_name, base_branch, attachments, sender, subject, message_id)

    send_message(
        service,
        make_reply(
            "pinkwebsite: pr request made",
            f"A pull request has been created: {pr_url}",
            sender,
            thread_id,
            headers.get("message-id"),
        ),
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
