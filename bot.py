import base64
import os
import quopri
import re
import subprocess
import time
from email.message import EmailMessage
from email.utils import parseaddr
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
PENDING_APPROVAL_LABEL = "pinkwebsite-awaiting-approval"
ALLOWED_HACK_EXTENSIONS = {".hack"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
ALLOWED_EXTENSIONS = ALLOWED_HACK_EXTENSIONS.union(ALLOWED_IMAGE_EXTENSIONS)
ALLOWED_EXTENSIONS_DISPLAY = ", ".join(sorted(ALLOWED_EXTENSIONS))
ACCEPTED_BODY_MARKER = "<!-- pink-bot-accepted-notified -->"
REJECTED_BODY_MARKER = "<!-- pink-bot-rejected-notified -->"
SUBJECT_RECEIVED = "pinkwebsite: received"
SUBJECT_SUBMISSION_FAILED = "pinkwebsite: submission failed"
SUBJECT_PR_REQUEST_MADE = "pinkwebsite: pr request made"
SUBJECT_ACCEPTED = "pinkwebsite: accepted"
SUBJECT_REJECTED = "pinkwebsite: rejected"
SUBJECT_SUBMISSION = "pinkwebsite: submission"
SUBJECT_EDIT = "pinkwebsite: edit"
SUBJECT_BOT_ERROR = "pinkwebsite: bot error"
BOT_ALERT_EMAIL = "thepinkcommittee@gmail.com"
APPROVAL_EMAIL = "thepinkcommittee@gmail.com"
BYPASS_CODEWORD_ENV = "PINK_BYPASS_CODEWORD"
SUBMISSION_INSTRUCTIONS_URL = "https://github.com/thepinkcommittee/pinkwebsite?tab=readme-ov-file#how-to-submit-a-new-entry"
REQUIRED_CONSENT_TEXT = "i confirm that there is no personally identifiable information in the included files and that i have sent the correct files for submission. i understand that once i submit, unless there are invalid files resulting in submission rejection, my submission will be made public in the pinkwebsite github."
EDIT_DIRECTIVE_RE = re.compile(r'^\s*(?P<filename>[^=]+?\.hack)\s*=\s*"(?P<title>[^"]+)"\s*,\s*"(?P<date>[^"]+)"\s*$', re.I)


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


def parse_hack_front_matter_text(text: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        meta[key.strip().lower()] = value.strip()
    return meta


def validate_edit_request_targets(
    repo,
    base_branch: str,
    hack_attachments: List[Dict],
    directives: List[Dict[str, str]],
) -> tuple[List[str], str]:
    attachment_names = [os.path.basename(a.get("filename", "")).strip() for a in hack_attachments]
    normalized_attachment_names = [name.casefold() for name in attachment_names if name]

    if len(normalized_attachment_names) != len(set(normalized_attachment_names)):
        return [], "Duplicate .hack filenames were attached in this edit request."

    directive_map: Dict[str, Dict[str, str]] = {}
    for directive in directives:
        filename = (directive.get("filename") or "").strip()
        key = filename.casefold()
        if key in directive_map:
            return [], f"Duplicate edit directive for '{filename}'."
        directive_map[key] = directive

    attachment_name_set = set(normalized_attachment_names)
    directive_name_set = set(directive_map.keys())

    missing_directives = [name for name in attachment_names if name.casefold() not in directive_name_set]
    extra_directives = [directive_map[key]["filename"] for key in directive_name_set if key not in attachment_name_set]

    if missing_directives or extra_directives:
        details = []
        if missing_directives:
            details.append("Missing directives for: " + ", ".join(missing_directives))
        if extra_directives:
            details.append("Directives without matching attachments: " + ", ".join(extra_directives))
        return [], "; ".join(details)

    target_paths: List[str] = []
    for attachment_name in attachment_names:
        directive = directive_map[attachment_name.casefold()]
        requested_filename = directive["filename"]
        target_path = f"entries/{requested_filename}"
        try:
            existing = repo.get_contents(target_path, ref=base_branch)
        except UnknownObjectException:
            return [], f"Edit target '{requested_filename}' does not exist in entries/."

        if isinstance(existing, list) or getattr(existing, "type", "") != "file":
            return [], f"Edit target '{requested_filename}' is not a valid file in entries/."

        existing_text = existing.decoded_content.decode("utf-8", errors="replace")
        existing_meta = parse_hack_front_matter_text(existing_text)
        existing_title = (existing_meta.get("title") or "").strip()
        existing_date = (existing_meta.get("date") or "").strip()
        requested_title = (directive.get("title") or "").strip()
        requested_date = (directive.get("date") or "").strip()

        if normalize_text_for_comparison(existing_title) != normalize_text_for_comparison(requested_title) or existing_date != requested_date:
            return [], (
                f"Metadata mismatch for '{requested_filename}'. "
                f"Expected title/date to match existing entry ('{existing_title}', '{existing_date}')."
            )

        target_paths.append(existing.path.replace("\\", "/"))

    return target_paths, ""


def build_standard_email_body(status: str, message: str, details: Optional[str] = None) -> str:
    normalized_status = " ".join((status or "update").split()).title()
    message_text = (message or "").strip()
    details_text = (details or "").strip()
    signoff_timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    sections = [
        "This is an automated update from the PinkWebsite submission bot.",
        f"Status: {normalized_status}",
    ]
    if message_text:
        sections.append(message_text)
    if details_text:
        sections.append(f"Details:\n{details_text}")
    sections.extend([
        "Best regards,\nPinkWebsite Submission Bot",
        f"Sent: {signoff_timestamp}",
    ])
    return "\n\n".join(sections)


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


def list_submission_messages(service, processed_label_name: str, pending_label_name: str) -> List[Dict]:
    query = (
        f'(subject:"{SUBJECT_SUBMISSION}" OR subject:"{SUBJECT_EDIT}") '
        f'-label:{processed_label_name} -label:{pending_label_name}'
    )
    response = service.users().messages().list(userId="me", q=query).execute()
    return response.get("messages", [])


def list_pending_submission_messages(service, pending_label_name: str) -> List[Dict]:
    query = f'(subject:"{SUBJECT_SUBMISSION}" OR subject:"{SUBJECT_EDIT}") label:{pending_label_name}'
    response = service.users().messages().list(userId="me", q=query).execute()
    return response.get("messages", [])


def parse_headers(payload: Dict) -> Dict[str, str]:
    headers = payload.get("headers", [])
    return {header["name"].lower(): header["value"] for header in headers}


def decode_gmail_body_data(raw_data: str) -> str:
    if not raw_data:
        return ""
    try:
        padding = "=" * (-len(raw_data) % 4)
        decoded = base64.urlsafe_b64decode((raw_data + padding).encode("utf-8"))
        text = decoded.decode("utf-8", errors="replace")

        # Some clients send text/plain as quoted-printable. Decode only when
        # quoted-printable artifacts are present to avoid changing normal text.
        if re.search(r"=(?:\r?\n|[0-9A-Fa-f]{2})", text):
            try:
                text = quopri.decodestring(text.encode("utf-8", errors="replace")).decode("utf-8", errors="replace")
            except Exception:
                pass

        return text
    except Exception:
        return ""


def collect_parts(payload: Dict) -> List[Dict]:
    parts = []
    if payload.get("parts"):
        for part in payload["parts"]:
            parts.extend(collect_parts(part))
    else:
        parts.append(payload)
    return parts


def extract_plain_text_body(payload: Dict) -> str:
    text_chunks = []
    for part in collect_parts(payload):
        if part.get("filename"):
            continue
        mime_type = (part.get("mimeType") or "").lower()
        if mime_type != "text/plain":
            continue
        raw_data = part.get("body", {}).get("data")
        text = decode_gmail_body_data(raw_data)
        if text.strip():
            text_chunks.append(text.strip())

    if text_chunks:
        return "\n".join(text_chunks).strip()

    fallback_data = payload.get("body", {}).get("data")
    return decode_gmail_body_data(fallback_data).strip()


def normalize_text_for_comparison(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def submission_request_kind(headers: Dict[str, str]) -> str:
    subject = (headers.get("subject") or "").strip().casefold()
    if SUBJECT_EDIT.casefold() in subject:
        return "edit"
    return "submission"


def normalize_email_body_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Unwrap quoted-printable soft line breaks.
    return normalized.replace("=\n", "")


def extract_nonempty_lines(text: str) -> List[str]:
    normalized = normalize_email_body_text(text)
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def has_required_submission_consent(payload: Dict, allow_extra_lines: bool = False) -> bool:
    lines = extract_nonempty_lines(extract_plain_text_body(payload))
    if not lines:
        return False
    if normalize_text_for_comparison(lines[0]) != normalize_text_for_comparison(REQUIRED_CONSENT_TEXT):
        return False
    if allow_extra_lines:
        return True
    return len(lines) == 1


def has_hidden_codeword_bypass(payload: Dict, allow_extra_lines: bool = False) -> bool:
    codeword = (os.getenv(BYPASS_CODEWORD_ENV) or "").strip()
    if not codeword:
        return False

    lines = extract_nonempty_lines(extract_plain_text_body(payload))
    if not lines:
        return False
    if not allow_extra_lines and len(lines) != 1:
        return False

    affirmation_line = lines[0]
    match = re.match(rf"^(?P<consent>.*?)(?:\\s+){re.escape(codeword)}\\s*$", affirmation_line)
    if not match:
        return False

    consent_text = match.group("consent").strip()
    return normalize_text_for_comparison(consent_text) == normalize_text_for_comparison(REQUIRED_CONSENT_TEXT)


def parse_edit_directives(payload: Dict) -> tuple[List[Dict[str, str]], List[str]]:
    lines = extract_nonempty_lines(extract_plain_text_body(payload))
    directive_lines = lines[1:] if lines else []
    directives: List[Dict[str, str]] = []
    invalid_lines: List[str] = []

    for line in directive_lines:
        match = EDIT_DIRECTIVE_RE.match(line)
        if not match:
            invalid_lines.append(line)
            continue
        directives.append(
            {
                "filename": match.group("filename").strip(),
                "title": match.group("title").strip(),
                "date": match.group("date").strip(),
            }
        )

    return directives, invalid_lines


def has_proceed_approval_reply(service, thread_id: str) -> bool:
    if not thread_id:
        return False

    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    except Exception:
        return False

    for message in thread.get("messages", []):
        payload = message.get("payload", {})
        headers = parse_headers(payload)
        sender_header = headers.get("from", "")
        sender_email = parseaddr(sender_header)[1].strip().casefold()
        if sender_email != APPROVAL_EMAIL.casefold():
            continue

        body_text = extract_plain_text_body(payload)
        if re.match(r"^\s*proceed[.!]?\s*$", body_text, flags=re.I):
            return True

    return False


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


def create_pr_for_attachments(
    repo,
    branch_name: str,
    base_branch: str,
    attachments: List[Dict],
    message_id: str,
    request_kind: str = "submission",
    explicit_target_paths: Optional[List[str]] = None,
    include_news: bool = True,
) -> str:
    unique_paths = []
    if explicit_target_paths is not None and len(explicit_target_paths) != len(attachments):
        raise ValueError("explicit_target_paths length must match attachments length")

    for idx, attachment in enumerate(attachments):
        filename = attachment["filename"]
        if explicit_target_paths is not None:
            target_path = explicit_target_paths[idx]
        else:
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
    if include_news:
        pr_requested_at = time.strftime("%Y-%m-%d %H:%M:%S")
        run_build_script(pr_requested_at, new_entry_stems)
    else:
        run_build_script()

    # Upload attachment and generated site files to the new branch
    for path, content in unique_paths:
        action = "Update request file" if request_kind == "edit" else "Add submission file"
        write_branch_file(repo, branch_name, path, content, f"{action} {os.path.basename(path)}")

    generated_files = ["index.html"]
    generated_files += [str(p) for p in sorted(Path("hacks").glob("*.html"))]
    generated_files += [str(p) for p in sorted(Path("browse").glob("*.html"))]

    for gen_path in generated_files:
        with open(gen_path, "rb") as f:
            content = f.read()
        write_branch_file(repo, branch_name, gen_path.replace("\\", "/"), content, f"Update generated site file {gen_path}")

    if request_kind == "edit":
        pr_title = f"Edit request from {message_id}"
    else:
        pr_title = f"New submission from {message_id}"
    request_label = "edit request" if request_kind == "edit" else "submission"
    attachment_paths = [path for path, _ in unique_paths]
    attachment_links_md = build_attachment_links_markdown(repo.full_name, branch_name, attachment_paths)
    pr_body = (
        f"Automated {request_label}.\n"
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


def mark_message_pending_approval(service, message_id: str, pending_label_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [pending_label_id],
        },
    ).execute()


def mark_message_processed_and_clear_pending(service, message_id: str, processed_label_id: str, pending_label_id: str):
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD", pending_label_id],
            "addLabelIds": [processed_label_id],
        },
    ).execute()


def mark_submission_processed_state(service, message_id: str, processed_label_id: str, pending_label_id: str, was_pending: bool):
    if was_pending:
        mark_message_processed_and_clear_pending(service, message_id, processed_label_id, pending_label_id)
    else:
        mark_message_processed(service, message_id, processed_label_id)


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


def has_rejection_marker(pr) -> bool:
    return REJECTED_BODY_MARKER in (pr.body or "")


def mark_rejection_notified(pr):
    body = (pr.body or "").rstrip()
    if REJECTED_BODY_MARKER in body:
        return
    if body:
        body = f"{body}\n\n{REJECTED_BODY_MARKER}"
    else:
        body = REJECTED_BODY_MARKER
    pr.edit(body=body)


def pr_request_kind(pr) -> str:
    title = (pr.title or "").strip().casefold()
    if title.startswith("edit request"):
        return "edit"
    return "submission"


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
            (
                "Your edit request has been accepted and merged."
                if pr_request_kind(pr) == "edit"
                else "Your submission has been accepted and merged."
            ),
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

        if has_rejection_marker(pr) or has_acceptance_marker(pr):
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
            (
                "Your edit request PR was closed without merging."
                if pr_request_kind(pr) == "edit"
                else "Your submission PR was closed without merging."
            ),
            (
                f"Please review and make the changes suggested in the PR here: {pr.html_url}\n"
                f"Then to resubmit, send a NEW email (do not reply to this thread) with subject '{SUBJECT_EDIT if pr_request_kind(pr) == 'edit' else SUBJECT_SUBMISSION}' and include the updated attachments."
            ),
        )
        mark_rejection_notified(pr)
        print(f"Sent rejected email for PR #{pr.number}")


def process_submission_to_pr(
    service,
    repo,
    processed_label_id: str,
    pending_label_id: str,
    message_id: str,
    payload: Dict,
    sender: str,
    thread_id: Optional[str],
    references: Optional[str],
    was_pending: bool,
    request_kind: str,
):
    resubmit_subject = SUBJECT_EDIT if request_kind == "edit" else SUBJECT_SUBMISSION
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
                f"Please send a NEW email (do not reply) with subject '{resubmit_subject}' and corrected attachments."
            ),
        )
        mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
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
                f"Then send a NEW email (do not reply) with subject '{resubmit_subject}'."
            ),
        )
        mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
        print(f"Processed message {message_id} with no valid attachments")
        return

    base_branch = repo.default_branch
    attachments_to_commit = attachments
    explicit_target_paths: Optional[List[str]] = None
    include_news = True

    if request_kind == "edit":
        include_news = False
        hack_attachments = [
            attachment
            for attachment in attachments
            if os.path.splitext(attachment.get("filename", ""))[1].lower() in ALLOWED_HACK_EXTENSIONS
        ]
        non_hack_attachments = [
            attachment.get("filename", "")
            for attachment in attachments
            if os.path.splitext(attachment.get("filename", ""))[1].lower() not in ALLOWED_HACK_EXTENSIONS
        ]

        if non_hack_attachments:
            non_hack_list = "\n".join(f"- {name}" for name in non_hack_attachments)
            send_standard_reply(
                service,
                sender,
                thread_id,
                references,
                SUBJECT_SUBMISSION_FAILED,
                "submission failed",
                "Edit requests can only include .hack files.",
                f"Non-.hack attachments found:\n{non_hack_list}",
            )
            mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
            print(f"Rejected edit message {message_id} due to non-.hack attachments")
            return

        directives, invalid_directive_lines = parse_edit_directives(payload)
        if invalid_directive_lines:
            invalid_lines_text = "\n".join(f"- {line}" for line in invalid_directive_lines)
            send_standard_reply(
                service,
                sender,
                thread_id,
                references,
                SUBJECT_SUBMISSION_FAILED,
                "submission failed",
                "Your edit directives are not in the required format.",
                (
                    "Expected format per line: filename.hack = \"title of hack\", \"date of hack\"\n"
                    f"Invalid lines:\n{invalid_lines_text}"
                ),
            )
            mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
            print(f"Rejected edit message {message_id} due to invalid directive lines")
            return

        if len(directives) != len(hack_attachments):
            send_standard_reply(
                service,
                sender,
                thread_id,
                references,
                SUBJECT_SUBMISSION_FAILED,
                "submission failed",
                "Mismatch between number of .hack attachments and edit directives.",
                f".hack attachments: {len(hack_attachments)}; directives: {len(directives)}",
            )
            mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
            print(f"Rejected edit message {message_id} due to hack/directive count mismatch")
            return

        target_paths, validation_error = validate_edit_request_targets(repo, base_branch, hack_attachments, directives)
        if validation_error:
            send_standard_reply(
                service,
                sender,
                thread_id,
                references,
                SUBJECT_SUBMISSION_FAILED,
                "submission failed",
                "Your edit request could not be matched to existing entries.",
                validation_error,
            )
            mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
            print(f"Rejected edit message {message_id}: {validation_error}")
            return

        attachments_to_commit = hack_attachments
        explicit_target_paths = target_paths
    else:
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
                    f"Please rename the files, then send a NEW email (do not reply) with subject '{resubmit_subject}'."
                ),
            )
            mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
            print(f"Rejected message {message_id} due to duplicate filenames")
            return

    branch_name = safe_branch_name(message_id)
    create_branch(repo, base_branch, branch_name)
    pr_url = create_pr_for_attachments(
        repo,
        branch_name,
        base_branch,
        attachments_to_commit,
        message_id,
        request_kind=request_kind,
        explicit_target_paths=explicit_target_paths,
        include_news=include_news,
    )

    request_label = "edit request" if request_kind == "edit" else "submission"

    send_standard_reply(
        service,
        sender,
        thread_id,
        references,
        SUBJECT_PR_REQUEST_MADE,
        "pr request made",
        f"A pull request has been created for your {request_label}.",
        f"PR URL: {pr_url}",
    )
    mark_submission_processed_state(service, message_id, processed_label_id, pending_label_id, was_pending)
    print(f"Created PR for message {message_id}: {pr_url}")


def process_message(service, repo, processed_label_id: str, pending_label_id: str, message):
    message_id = message["id"]
    full_message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = full_message.get("payload", {})
    headers = parse_headers(payload)
    sender = headers.get("from", "unknown sender")
    thread_id = full_message.get("threadId")
    references = headers.get("message-id")
    request_kind = submission_request_kind(headers)
    allow_extra_lines = request_kind == "edit"

    bypass_approved = has_hidden_codeword_bypass(payload, allow_extra_lines=allow_extra_lines)

    if not bypass_approved and not has_required_submission_consent(payload, allow_extra_lines=allow_extra_lines):
        send_standard_reply(
            service,
            sender,
            thread_id,
            references,
            SUBJECT_SUBMISSION_FAILED,
            "submission failed",
            "Your consent statement is missing or invalid.",
            (
                "Your email body must start with exactly this sentence:\n"
                f"{REQUIRED_CONSENT_TEXT}\n\n"
                f"Review the instructions here: {SUBMISSION_INSTRUCTIONS_URL}"
            ),
        )
        mark_message_processed(service, message_id, processed_label_id)
        print(f"Rejected message {message_id} due to missing/invalid consent text")
        return

    if bypass_approved:
        request_label = "edit request" if request_kind == "edit" else "submission"
        send_standard_reply(
            service,
            sender,
            thread_id,
            references,
            SUBJECT_RECEIVED,
            "received",
            f"Your {request_label} email has been received.",
            "Bypass codeword accepted. We are creating a pull request now.",
        )
        process_submission_to_pr(
            service,
            repo,
            processed_label_id,
            pending_label_id,
            message_id,
            payload,
            sender,
            thread_id,
            references,
            False,
            request_kind,
        )
        return

    request_label = "edit request" if request_kind == "edit" else "submission"
    send_standard_reply(
        service,
        sender,
        thread_id,
        references,
        SUBJECT_RECEIVED,
        "received",
        f"Your {request_label} email has been received and is pending manual approval.",
        (
            f"A pull request will only be created after a reply from {APPROVAL_EMAIL} in this thread "
            "that says 'proceed'."
        ),
    )
    mark_message_pending_approval(service, message_id, pending_label_id)
    print(f"Queued message {message_id} for manual approval")


def process_pending_message(service, repo, processed_label_id: str, pending_label_id: str, message):
    message_id = message["id"]
    full_message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = full_message.get("payload", {})
    headers = parse_headers(payload)
    sender = headers.get("from", "unknown sender")
    thread_id = full_message.get("threadId")
    references = headers.get("message-id")
    request_kind = submission_request_kind(headers)

    if not has_proceed_approval_reply(service, thread_id):
        print(f"Pending message {message_id}: waiting for proceed approval")
        return

    process_submission_to_pr(
        service,
        repo,
        processed_label_id,
        pending_label_id,
        message_id,
        payload,
        sender,
        thread_id,
        references,
        True,
        request_kind,
    )


def main():
    gmail_service = get_gmail_service()
    repo = get_github_repo()
    processed_label_id = get_or_create_label(gmail_service, ATTACHMENT_LABEL)
    pending_label_id = get_or_create_label(gmail_service, PENDING_APPROVAL_LABEL)

    try:
        messages = list_submission_messages(gmail_service, ATTACHMENT_LABEL, PENDING_APPROVAL_LABEL)
        if messages:
            print(f"Found {len(messages)} submission/edit message(s) to process")
            for message in messages:
                try:
                    process_message(gmail_service, repo, processed_label_id, pending_label_id, message)
                except HttpError as exc:
                    print(f"Gmail API error for message {message['id']}: {exc}")
        else:
            print("No new submission emails found")

        pending_messages = list_pending_submission_messages(gmail_service, PENDING_APPROVAL_LABEL)
        if pending_messages:
            print(f"Found {len(pending_messages)} pending submission(s) awaiting approval")
            for message in pending_messages:
                try:
                    process_pending_message(gmail_service, repo, processed_label_id, pending_label_id, message)
                except HttpError as exc:
                    print(f"Gmail API error for pending message {message['id']}: {exc}")
        else:
            print("No pending submissions awaiting approval")

        send_accepted_notifications(gmail_service, repo, repo.default_branch)
        send_rejected_notifications(gmail_service, repo, repo.default_branch)
    except Exception as exc:
        print(f"Fatal error: {exc}")
        raise


if __name__ == "__main__":
    main()
