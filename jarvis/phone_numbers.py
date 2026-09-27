"""A spoken or typed phone number, turned into one E.164 number or a question.

"Message 98xxxxxxxx with the country code plus nine one" reached the recogniser as
"… with the country code plus 911": Whisper glued the country code to the "1" it heard next.
Taken literally that is +911…, a number that does not exist. Every way of saying the Indian
country code — "+91", "91", "plus nine one", "plus ninety-one", "country code nine one", and the
misheard "plus 911" — has to arrive at the same +91XXXXXXXXXX, and anything that cannot be made
into a valid number is asked about rather than guessed.

Nothing here logs; callers that need to mention a number use ``mask``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")

# The Indian country code as it is said or misheard. "911" is the recogniser joining "91" to a
# stray "1"; it is only ever read as 91 when the rest is a valid Indian mobile number.
_CC_WORDS = r"(?:\+\s*|plus\s+)?(?:91|911|nine[\s-]*one|ninety[\s-]*one)"
COUNTRY_CODE_PHRASE = re.compile(
    rf"(?ix)(?:\s*,?\s*(?:with|using|use|and|in|on)?\s*(?:the\s+)?"
    rf"(?:country\s*code|code|prefix|isd(?:\s+code)?)\s*(?:of\s+|as\s+|is\s+)?{_CC_WORDS}\b"
    rf"|\s*(?:\+\s*|plus\s+)(?:91|911|nine[\s-]*one|ninety[\s-]*one)\b(?!\s*\d))")

# A run of digits that could be a phone number, with the separators people and recognisers put in.
NUMBER_SPAN = re.compile(r"(?<![\w+])(?:\+\s*|plus\s+)?\d[\d\s().-]{6,}\d(?!\w)", re.I)


@dataclass
class Parsed:
    e164: str = ""          # "+91XXXXXXXXXX" when ok
    status: str = "none"    # ok, ambiguous, invalid, none
    note: str = ""          # what was corrected, for diagnostics (never the number)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


_SPOKEN_CC = re.compile(r"(?i)\b(plus\s+|country\s*code\s+(?:of\s+|is\s+|as\s+)?)(?:nine[\s-]+one|ninety[\s-]*one)\b")


def spoken_country_code(text: str) -> str:
    """"plus ninety-one" → "plus 91", so the words cannot be read as anything else — the hyphen
    in "ninety-one" is also how a message is separated from its recipient."""
    return _SPOKEN_CC.sub(lambda m: m.group(1) + "91", text or "")


def mask(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    return f"the number ending {digits[-4:]}" if len(digits) >= 4 else "that number"


def is_e164(number: str) -> bool:
    return bool(_E164.match(number or ""))


def mentions_indian_code(text: str) -> bool:
    return bool(COUNTRY_CODE_PHRASE.search(text or ""))


def normalize(raw: str, *, country_hint: Optional[str] = None) -> Parsed:
    """One number, as E.164, from what was said. ``country_hint`` is "91" when the sentence
    named the Indian country code separately from the digits ("… with country code plus 91")."""
    text = (raw or "").strip()
    plus = bool(re.match(r"^(?:\+|plus\b)", text, re.I))
    digits = re.sub(r"\D", "", text)
    if not digits:
        return Parsed(status="none")

    if len(digits) == 10:
        if _INDIAN_MOBILE.match(digits):
            return _checked("91" + digits, "ten-digit Indian mobile")
        return Parsed(status="invalid", note="ten digits but not an Indian mobile")
    if len(digits) == 11 and digits.startswith("0") and _INDIAN_MOBILE.match(digits[1:]):
        return _checked("91" + digits[1:], "trunk zero dropped")
    if len(digits) == 12 and digits.startswith("91") and _INDIAN_MOBILE.match(digits[2:]):
        return _checked(digits, "91 prefix")
    if len(digits) == 13 and digits.startswith("911") and _INDIAN_MOBILE.match(digits[3:]):
        # "+911XXXXXXXXXX": +91 with a stray 1. +911 is not a country code anyone can dial.
        return _checked("91" + digits[3:], "misheard 911 read as 91")
    if len(digits) == 14 and digits.startswith("0091") and _INDIAN_MOBILE.match(digits[4:]):
        return _checked(digits[2:], "00 prefix")
    if country_hint == "91":
        # The sentence said +91 but the digits do not make an Indian mobile: ask, don't guess.
        return Parsed(status="ambiguous", note="country code 91 but digits are not a mobile number")
    if plus and not digits.startswith("91") and 8 <= len(digits) <= 15:
        return _checked(digits, "international")
    return Parsed(status="ambiguous", note=f"{len(digits)} digits")


def _checked(digits: str, note: str) -> Parsed:
    e164 = "+" + digits
    if digits.startswith("911") and len(digits) != 12:
        return Parsed(status="invalid", note="+911 prefix refused")
    if not is_e164(e164):
        return Parsed(status="invalid", note="not E.164")
    return Parsed(e164=e164, status="ok", note=note)


def find(text: str) -> tuple[Optional[re.Match], Parsed]:
    """The number mentioned in a sentence, with any separate country-code phrase applied."""
    hint = "91" if mentions_indian_code(text) else None
    best: Optional[re.Match] = None
    for m in NUMBER_SPAN.finditer(text or ""):
        if best is None or len(re.sub(r"\D", "", m.group(0))) > len(re.sub(r"\D", "", best.group(0))):
            best = m
    if best is None:
        return None, Parsed(status="none")
    return best, normalize(best.group(0), country_hint=hint)
