"""What may leave this machine.

Classified before any cloud provider sees a request. Four levels:

    public      nothing personal
    personal    about the owner's life, but ordinary ("plan my evening")
    sensitive   health, money, private messages, contacts, email contents, screenshots
    secret      passwords, OTPs, API keys, tokens, card numbers — never sent to a cloud model

``redact`` replaces secret-looking spans with typed placeholders; it is used on everything that is
written to disk (telemetry, caches) and, when policy allows cloud with redaction, on what is sent.

The policy (settings in ``registry.BrainSettings.privacy``):

    always_local      nothing goes to a cloud model
    prefer_local      local when a local model can do it, else cloud with secrets redacted
    allow_cloud       configured cloud providers may be used (secrets still never)
    ask_before_cloud  sensitive requests stay local; if local cannot, say so and ask
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .request import Privacy, Source

# --- secrets ------------------------------------------------------------------------------------
_SECRET_PATTERNS = [
    ("api_key", re.compile(r"\b(?:sk|gsk|sk-ant|sk-or|rk|pk)[-_][A-Za-z0-9_\-]{16,}\b")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("api_key", re.compile(r"\b(?:ghp|gho|ghs|github_pat|xox[abpr])_[A-Za-z0-9_]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password", re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|pass ?word)\b\s*(?:is|was|:|=)\s*[\"']?(\S{4,})")),
    ("otp", re.compile(r"(?i)\b(?:otp|one[- ]time (?:password|code)|verification code|2fa code|auth(?:entication)? code|pin)\b\D{0,12}(\d{4,8})\b")),
    ("token", re.compile(r"(?i)\b(?:token|secret|bearer|api[_ ]?key)\b\s*(?:is|:|=)?\s*[\"']?([A-Za-z0-9_\-\.]{12,})")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("aadhaar", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
]

_SENSITIVE_WORDS = [
    ("health", re.compile(r"(?i)\b(?:diagnos\w*|prescription|medication|my (?:doctor|therapist|symptoms?)|"
                          r"blood (?:test|report|sugar)|hiv|std|pregnan\w*|depress\w*|anxiety|mental health)\b")),
    ("finance", re.compile(r"(?i)\b(?:bank (?:account|balance|statement)|account number|ifsc|upi (?:id|pin)|"
                           r"salary|net ?banking|credit card|debit card|loan)\b")),
    ("private_message", re.compile(r"(?i)\b(?:this (?:message|chat|dm) from|private message|whatsapp (?:chat|message) from)\b")),
    ("contacts", re.compile(r"(?i)\b(?:my contacts?(?: list)?|phone ?book|all (?:my )?numbers)\b")),
    ("email_body", re.compile(r"(?i)\b(?:this email|the email (?:from|says)|email body)\b")),
]
_PERSONAL_WORDS = re.compile(r"(?i)\b(?:my|mine|me|i'm|i am|mera|meri|mujhe)\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def find_secrets(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) spans that look like secrets."""
    out = []
    for kind, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text or ""):
            start, end = (m.start(1), m.end(1)) if m.groups() else (m.start(), m.end())
            if kind == "card":
                digits = re.sub(r"\D", "", m.group(0))
                if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                    continue
            out.append((kind, start, end))
    return out


def redact(text: str) -> str:
    """Secrets replaced by ``<kind>`` placeholders; phone numbers kept to their last four digits."""
    spans = sorted(find_secrets(text or ""), key=lambda s: (s[1], -s[2]))
    out, pos = [], 0
    for kind, start, end in spans:
        if start < pos:
            continue
        out.append(text[pos:start])
        out.append(f"<{kind}>")
        pos = end
    out.append((text or "")[pos:])
    red = "".join(out)
    return re.sub(r"\+?\d[\d\s-]{7,}\d", lambda m: f"<number …{re.sub(r'[^0-9]', '', m.group(0))[-4:]}>", red)


@dataclass
class Sensitivity:
    level: str = Privacy.PUBLIC
    reasons: list = field(default_factory=list)


def classify(text: str, source: str = Source.OVERLAY, has_images: bool = False,
             screen: bool = False) -> Sensitivity:
    reasons = sorted({k for k, _, _ in find_secrets(text or "")})
    if reasons:
        return Sensitivity(Privacy.SECRET, reasons)
    sens = [k for k, p in _SENSITIVE_WORDS if p.search(text or "")]
    if source in {Source.MESSAGING, Source.EMAIL}:
        sens.append("incoming_message")
    if has_images and screen:
        sens.append("screenshot")
    if sens:
        return Sensitivity(Privacy.SENSITIVE, sorted(set(sens)))
    if _PERSONAL_WORDS.search(text or ""):
        return Sensitivity(Privacy.PERSONAL, ["personal"])
    return Sensitivity(Privacy.PUBLIC, [])


MODES = ("always_local", "prefer_local", "allow_cloud", "ask_before_cloud")


@dataclass
class CloudVerdict:
    allowed: bool
    prefer_local: bool
    redact: bool
    reason: str = ""


def cloud_verdict(level: str, mode: str = "allow_cloud", *, images: bool = False,
                  allow_screenshots: bool = True) -> CloudVerdict:
    """May this request go to a cloud model, and on what terms."""
    mode = mode if mode in MODES else "allow_cloud"
    if mode == "always_local":
        return CloudVerdict(False, True, True, "privacy is set to always local")
    if level == Privacy.SECRET:
        return CloudVerdict(False, True, True, "it contains something that looks like a password or key")
    if images and not allow_screenshots:
        return CloudVerdict(False, True, True, "screenshot upload is turned off")
    if level == Privacy.SENSITIVE:
        if mode == "ask_before_cloud":
            return CloudVerdict(False, True, True, "it looks private and cloud use needs your say-so")
        return CloudVerdict(True, mode == "prefer_local", True, "")
    return CloudVerdict(True, mode == "prefer_local", mode == "prefer_local", "")
