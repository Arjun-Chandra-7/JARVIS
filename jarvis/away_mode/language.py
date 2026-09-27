"""What language an incoming message is in, and a spelling-normalised copy to classify.

``jarvis.hinglish.normalise`` rewrites the owner's Hindi *commands* into English commands. That is
exactly wrong for someone else's message — "bhai usko bol dena" is a request to Aviral, not an
instruction to Jarvis — so incoming text is never passed through it. This module only folds slang
spellings together ("kr", "krna", "kro" → "kar", "karna", "karo") so the classifier sees one form,
and says which language to answer in. The normalised copy is never executed or sent anywhere.
"""
from __future__ import annotations

import re

from ..hinglish import looks_hindi

EN, HI, HINGLISH = "en", "hi", "hinglish"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Common Roman-Hindi words. Two or more in a message make it Hinglish; one ("bhai", "acha") in an
# otherwise English sentence does not.
_ROMAN_HINDI = {
    "hai", "hain", "kya", "kyu", "kyun", "kab", "kaha", "kahan", "kaise", "kitne", "nahi", "nhi",
    "nahin", "haan", "han", "bhai", "bhaiya", "yaar", "abe", "arre", "acha", "achha",
    "theek", "thik", "bol", "bolo", "bolna", "batana", "bata", "dena", "lena",
    "kar", "kr", "karo", "kro", "karna", "krna", "karunga", "krunga", "karega", "krega", "baad",
    "mein", "mujhe", "muje", "tum", "aap", "unko", "usko", "usse", "unhe", "unse", "woh", "yeh",
    "kal", "aaj", "abhi", "jaldi", "baje", "milna", "milte", "aaunga", "aunga", "aayega", "ayega",
    "raha", "rha", "rahi", "rhi", "hoon", "puch", "pooch", "puchna", "rehne", "rehnde", "chalo",
    "suno", "matlab", "bhi", "sirf", "bas", "phir", "papa", "mummy", "hoge", "hogi", "gaya", "gayi",
    "ghar", "paise", "paisa", "kaam", "khana", "sab", "koi", "kuch", "kuchh", "haal", "chahiye",
}

# Slang spelling → one form. Applied word by word, only for classification.
_VARIANTS = {
    "kr": "kar", "kro": "karo", "krna": "karna", "krunga": "karunga", "krenge": "karenge", "krega": "karega",
    "krdo": "kar do", "kardo": "kar do", "krde": "kar de", "nhi": "nahi", "nahin": "nahi", "na": "na",
    "rha": "raha", "rhi": "rahi", "rhe": "rahe", "hu": "hoon", "h": "hai", "hn": "hain", "muje": "mujhe",
    "mje": "mujhe", "aunga": "aaunga", "ayega": "aayega", "pls": "please", "plz": "please", "plzz": "please",
    "u": "you", "ur": "your", "r": "are", "msg": "message", "tmrw": "tomorrow", "tmr": "tomorrow",
    "2mrw": "tomorrow", "asap": "asap", "rn": "right now", "abhi": "abhi", "acha": "achha", "thik": "theek",
    "bhaiyya": "bhaiya", "urgnt": "urgent", "argent": "urgent", "emergncy": "emergency", "cl": "call",
    "fone": "phone", "fon": "phone", "pooch": "puch", "puchh": "puch", "pucho": "puchna", "rehnde": "rehne de",
    "bd": "baad", "bad": "baad", "mei": "me", "mai": "main", "wo": "woh", "ye": "yeh", "kyu": "kyun",
}


def detect(text: str) -> str:
    raw = text or ""
    if _DEVANAGARI.search(raw):
        return HI
    words = re.findall(r"[a-z]+", raw.lower())
    hits = sum(1 for w in words if w in _ROMAN_HINDI)
    if hits >= 2 or (hits == 1 and len(words) <= 2) or (hits and looks_hindi(raw) and len(words) <= 4):
        return HINGLISH
    return EN


def normalise(text: str) -> str:
    """Lower-case, slang folded, punctuation spaced; for classification only."""
    lowered = re.sub(r"(.)\1{2,}", r"\1", (text or "").lower())        # "urgenttt" → "urgent"
    words = re.findall(r"[\wऀ-ॿ]+|[^\w\s]", lowered)
    return " ".join(_VARIANTS.get(w, w) for w in words)


def reply_language(detected: str, preferences: list[str]) -> str:
    """Answer in the sender's language when the owner allowed it, else in English."""
    return detected if detected in (preferences or [EN]) else EN
