"""Gmail: list, read, and send. Returns None if Google isn't connected."""

from __future__ import annotations

import base64
from email.message import EmailMessage

from .auth import service


def _headers(payload) -> dict:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _extract_body(payload) -> str:
    if payload.get("mimeType", "").startswith("text/plain"):
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
    for part in payload.get("parts", []) or []:
        body = _extract_body(part)
        if body:
            return body
    return ""


def check(config, query: str = "is:unread", max_results: int = 10):
    svc = service(config, "gmail", "v1")
    if svc is None:
        return None
    msgs = (
        svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute().get("messages", [])
    )
    if not msgs:
        return "No matching emails."
    out = []
    for m in msgs:
        full = (
            svc.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"])
            .execute()
        )
        h = _headers(full.get("payload", {}))
        out.append(
            f"[{m['id']}] {h.get('from', '?')} — {h.get('subject', '(no subject)')} — {full.get('snippet', '')[:120]}"
        )
    return "\n".join(out)


def read(config, message_id: str):
    svc = service(config, "gmail", "v1")
    if svc is None:
        return None
    full = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = _headers(full.get("payload", {}))
    body = _extract_body(full.get("payload", {}))
    return (
        f"From: {h.get('from', '?')}\nSubject: {h.get('subject', '(no subject)')}\n"
        f"Date: {h.get('date', '')}\n\n{body[:4000]}"
    )


def send(config, to: str, subject: str, body: str):
    svc = service(config, "gmail", "v1")
    if svc is None:
        return None
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Sent to {to} (id {sent.get('id', '?')})."
