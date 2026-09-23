"""What Jarvis says, as opposed to what Jarvis writes.

A reply is written for the screen: "+91 98xxxxxx21", "a² + b² = c²", "127.0.0.1:8770",
"arjun-chandra-7", a link, a code block. Read aloud as written, each of those is either noise or
a leak — a voice reading every digit of a phone number, or a token, into the room. This layer
turns the written reply into the spoken one, and decides which language the voice should use.

Measured, and why the language part exists: Kokoro's British voice was given every reply,
including Hindi and Roman-script Hinglish, through the *English* phonemiser. "add karte hain"
came out as /ˈad kˈɑːt hˈeɪn/ — "add cart hain". Hindi text read by that voice scored a
character error rate of 0.46 through Whisper on a speaker-like channel; the Hindi voice reading
the same text through the Hindi phonemiser scored 0.31–0.37. So:

* English sentences go to the English voice, unchanged.
* Devanagari goes to the Hindi voice.
* Roman Hinglish is written in Devanagari first — Hindi words transliterated, English words left
  in Latin letters, which the Hindi phonemiser hands to its English rules — and then goes to the
  Hindi voice. The HUD still shows what the model wrote; only the voice sees this.

Pure functions, no audio: every rule here is tested directly.
"""
from __future__ import annotations

import functools
import os
import re
from typing import Iterable, Optional

# ------------------------------------------------------------------ honorific
def honorific() -> str:
    """How Jarvis addresses the person in speech. ``JARVIS_HONORIFIC``: sir (default), sire, or
    empty for none."""
    return os.environ.get("JARVIS_HONORIFIC", "sir").strip()


def with_honorific(text: str, address: Optional[str] = None) -> str:
    """Swap the fixed "sir" in canned lines for the configured address, or drop it."""
    address = honorific() if address is None else address
    if address.lower() == "sir":
        return text
    if not address:
        text = re.sub(r",\s*sir\b(?=[.!?,])", "", text)
        text = re.sub(r"\bsir,\s*", "", text)
        return re.sub(r"\s+sir\b", "", text)
    return re.sub(r"\bsir\b", address, text)


# ------------------------------------------------------------------ secrets and identifiers
# Never spoken, whatever the reply says. Long random-looking strings are tokens, keys, tracking
# ids or hashes; nobody benefits from hearing them and the room should not.
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----"
    r"|\b(?:sk|pk|rk|gsk|ghp|gho|xox[abp]|AIza|ya29)[-_A-Za-z0-9.]{12,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"   # JWT
    r"|\b[0-9a-fA-F]{24,}\b"                                          # hashes, ids
    r"|\b(?=[A-Za-z0-9_-]{22,}\b)(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]+\b",
    re.DOTALL)

_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.I)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_IP_PORT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{2,5}))?\b|\blocalhost(?::(\d{2,5}))?\b")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?")
# Phone numbers: a + and eight or more digits, or ten or more digits, allowing spaces/dashes.
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d \-]{8,}\d(?![\w.])")
_HANDLE = re.compile(r"(?<![\w@])@?([a-z][a-z]+(?:[-_.][a-z]+)+)(?:[-_.]?\d{1,4})?(?![\w@])")


def _domain_spoken(host: str) -> str:
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) > 3:
        parts = parts[-3:]
    return " dot ".join(parts)


def _url(match: re.Match) -> str:
    raw = match.group(0).rstrip(".,;:!?")
    tail = match.group(0)[len(raw):]
    host = re.sub(r"^(?:https?://)?", "", raw, flags=re.I).split("/")[0].split("?")[0]
    if not host or "." not in host:
        return "a link" + tail
    return f"the {_domain_spoken(host)} link" + tail if len(raw) > len(host) + 12 else \
        _domain_spoken(host) + tail


def _email(match: re.Match) -> str:
    user, domain = match.group(1), match.group(2)
    if len(user) > 16 or sum(c.isdigit() for c in user) >= 3:
        return f"an address at {_domain_spoken(domain)}"
    return f"{re.sub(r'[._+-]+', ' ', user)} at {_domain_spoken(domain)}"


def _ip(match: re.Match) -> str:
    ip, port, local_port = match.group(1), match.group(2), match.group(3)
    port = port or local_port
    if ip is None or ip.startswith("127.") or ip == "0.0.0.0":
        return f"local port {port}" if port else "this machine"
    return f"an address on the network, port {port}" if port else "an address on the network"


def _path(match: re.Match) -> str:
    raw = match.group(0).rstrip("/")
    name = raw.rsplit("/", 1)[-1]
    if not name:
        return "a folder"
    spoken = name.replace(".", " dot ").replace("_", " ").replace("-", " ").strip()
    return f"the file {spoken}" if "." in name else f"the {spoken} folder"


def _phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 10:
        return match.group(0)
    last = digits[-4:]
    return f"the number ending {last[:2]} {last[2:]}"


def _handle(match: re.Match) -> str:
    words = re.split(r"[-_.]+", match.group(1))
    return " ".join(w.capitalize() for w in words if w)


# ------------------------------------------------------------------ maths
_SUPERSCRIPT = {"²": " squared", "³": " cubed", "¹": " to the power one", "⁴": " to the power four",
                "⁵": " to the power five", "ⁿ": " to the power n"}
_MATH_SYMBOLS = [
    ("≤", " is less than or equal to "), ("≥", " is greater than or equal to "), ("≠", " is not equal to "),
    ("≈", " is approximately "), ("±", " plus or minus "), ("∞", " infinity "), ("π", " pi "),
    ("θ", " theta "), ("α", " alpha "), ("β", " beta "), ("Δ", " delta "), ("∑", " the sum of "),
    ("√", " square root of "), ("÷", " divided by "), ("×", " times "), ("→", " gives "),
    ("⇒", " implies "), ("°", " degrees"),
]


def _maths(t: str) -> str:
    for sup, spoken in _SUPERSCRIPT.items():
        t = t.replace(sup, spoken)
    t = re.sub(r"(?<=\w)\^2\b", " squared", t)
    t = re.sub(r"(?<=\w)\^3\b", " cubed", t)
    t = re.sub(r"(?<=\w)\^\(?(-?\w+)\)?", r" to the power \1", t)
    for sym, spoken in _MATH_SYMBOLS:
        t = t.replace(sym, spoken)
    # Operators between operands only, so "well-known" and "e-mail" keep their hyphens.
    t = re.sub(r"(?<=[\w)])\s*=\s*(?=[\w(√-])", " equals ", t)
    t = re.sub(r"(?<=[\w)])\s+\+\s+(?=[\w(])|(?<=\d)\+(?=\d)", " plus ", t)
    t = re.sub(r"(?<=[\w)])\s+[-−]\s+(?=[\w(])", " minus ", t)
    t = re.sub(r"(?<=\d)\s*[*]\s*(?=\d)|(?<=\w)\s+\*\s+(?=\w)", " times ", t)
    t = re.sub(r"(?<=\d)\s*/\s*(?=\d)", " over ", t)
    # A lone letter used as a variable ("a squared plus b squared") is read by the phonemiser as
    # the article "uh". Written as its name it is said as a letter.
    t = re.sub(r"(?<![\w'])a(?= (?:squared|cubed|to the power|plus|minus|equals|times|over)\b)", "ay", t)
    return t


# ------------------------------------------------------------------ numbers and dates
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


def _iso_date(match: re.Match) -> str:
    y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return match.group(0)
    return f"{d} {_MONTHS[m - 1]} {y}"


def _numbers(t: str) -> str:
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _iso_date, t)
    # 1,234,567 and the Indian 12,34,567 alike: the grouping is for the eye.
    t = re.sub(r"\b\d{1,3}(?:,\d{2,3})+\b", lambda m: m.group(0).replace(",", ""), t)
    t = re.sub(r"(\d)\s*[kK]\b", r"\1 thousand", t)
    return t


# ------------------------------------------------------------------ markup and emoji
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
                    "\U00002190-\U000021FF\U0000FE0F\U0000200D]")
_SPEECH_SUBS = [
    (r"\be\.g\.\s*", "for example, "), (r"\bi\.e\.\s*", "that is, "), (r"\betc\.?", "and so on"),
    (r"\bvs\.?(?=\s)", "versus"), (r"\bapprox\.?(?=\s)", "approximately"), (r"\bDr\.\s*", "Doctor "),
    (r"\bMr\.\s*", "Mister "), (r"\bMrs\.\s*", "Missus "), (r"\bMs\.\s*", "Miss "),
    (r"\bSt\.\s*", "Saint "), (r"\ba\.m\.", "AM"), (r"\bp\.m\.", "PM"),
]
_AMOUNT = r"(\d[\d,]*(?:\.\d+)?)"
_CURRENCY = [(r"\$" + _AMOUNT, r"\1 dollars"), (r"£" + _AMOUNT, r"\1 pounds"),
             (r"€" + _AMOUNT, r"\1 euros"), (r"₹\s?" + _AMOUNT, r"\1 rupees"),
             (r"\bRs\.?\s?" + _AMOUNT, r"\1 rupees")]
# Written for the eye, said differently. Kept small: the phonemiser already spells most
# all-capital words letter by letter.
_WORDS = {"JARVIS": "Jarvis", "HUD": "hud", "WhatsApp": "WhatsApp", "YouTube": "YouTube",
          "UI": "U I", "OK": "okay", "Ok": "okay", "w/": "with", "w/o": "without"}


def _markup(t: str) -> str:
    code = bool(re.search(r"```", t))
    t = re.sub(r"```.*?(?:```|$)", " ", t, flags=re.DOTALL)
    t = re.sub(r"`([^`]{1,24})`", r"\1", t)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_]{1,3}([^*_\n]+)[*_]{1,3}", r"\1", t)
    t = re.sub(r"\|", ", ", t)
    t = t.replace("*", " ").replace("#", " ").replace("`", " ")
    if code and not t.strip():
        return "The code is on screen."
    if code:
        t += " The code is on screen."
    return t


# ------------------------------------------------------------------ pronunciation dictionary
@functools.lru_cache(maxsize=1)
def _pronunciations_cached(stamp: float) -> tuple[tuple[str, str], ...]:
    try:
        from ..flow import dictionary
        pairs = [(e.written, e.pronunciation) for e in dictionary.load()
                 if getattr(e, "pronunciation", "") and e.written]
    except Exception:  # noqa: BLE001 — no dictionary is an ordinary state
        return ()
    return tuple(sorted(pairs, key=lambda p: -len(p[0])))


def _pronunciations() -> tuple[tuple[str, str], ...]:
    try:
        from ..flow import dictionary
        stamp = dictionary._path().stat().st_mtime
    except Exception:  # noqa: BLE001
        stamp = 0.0
    return _pronunciations_cached(stamp)


def _apply_pronunciations(t: str, pairs: Iterable[tuple[str, str]]) -> str:
    for written, spoken in pairs:
        t = re.sub(rf"(?<!\w){re.escape(written)}(?!\w)", spoken.replace("-", " "), t)
    return t


# ------------------------------------------------------------------ the whole thing
def normalize(text: str, *, pronunciations: Optional[Iterable[tuple[str, str]]] = None,
              address: Optional[str] = None) -> str:
    """The written reply as it should be said. Never contains a secret, a raw URL or a phone
    number read digit by digit."""
    t = text or ""
    t = _SECRET.sub(" ", t)
    t = _markup(t)
    t = _URL.sub(_url, t)
    t = _EMAIL.sub(_email, t)
    t = _IP_PORT.sub(_ip, t)
    t = _PATH.sub(_path, t)
    t = _PHONE.sub(_phone, t)
    t = _HANDLE.sub(_handle, t)
    t = _apply_pronunciations(t, _pronunciations() if pronunciations is None else pronunciations)
    for pat, rep in _SPEECH_SUBS:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    for pat, rep in _CURRENCY:
        t = re.sub(pat, rep, t)
    t = _numbers(t)
    t = _maths(t)
    for written, spoken in _WORDS.items():
        t = re.sub(rf"(?<![\w/]){re.escape(written)}(?![\w/])", spoken, t)
    t = t.replace("&", " and ").replace("%", " percent").replace("~", " about ").replace("@", " at ")
    t = _EMOJI.sub("", t)
    t = with_honorific(t, address)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"([.!?])(?:\s*\.)+", r"\1", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip(" ,;")


# ================================================================== language
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Roman-script Hindi, the words that make a sentence Hinglish. Common and unambiguous: a word here
# is almost never English. Words that are also everyday English — "is", "me", "do", "to", "the"
# (थे), "hum", "sun", "log", "tab", "ya", "par" — are deliberately absent: "the square of the
# hypotenuse" counted two Hindi words and went to the Hindi voice. They are still transliterated
# once a sentence is known to be Hinglish.
_HINDI_MARKERS = frozenset("""
hai hain tha thi hoon hun raha rahi rahe kya kyun kyon kaise kaisa kaisi kahan kab kaun nahi
nahin nhi mein mai mujhe mujhko tum tumhe tumhara tumhari aap aapka aapki apna apni apne humein
hamara hamari yeh woh wo vo voh unka unki uska uski iska iski usko isko inko unko ka ki ke ko se
aur lekin magar kyunki toh bhi sirf bahut zyada thoda thodi kuch sab sabhi agar abhi phir fir
kar karo karna karke karte karta karti kiya kiye kijiye dekho dekh samjho samjhao samjhana
samjha batao bata bataiye bolo chalo chal suno likho padho khol kholo bajao chalao lagao ruko
rukna jao aao aana jana dena lena diya liya gaya gayi gaye hota hoti hote hua hui hue sakta
sakti sakte chahiye chahta chahti matlab yaani jaise waise bilkul theek thik accha acha achha
haan bhai yaar arre arey aaj parso wala wali wale
""".split())

# Hindi words written in Latin letters, rendered for the voice. The common ones, spelled every
# way they are commonly spelled; anything else is transliterated by rule below.
_HINDI_WORDS = {
    "hai": "है", "hain": "हैं", "hei": "है", "tha": "था", "thi": "थी", "the": "थे", "ho": "हो",
    "hoon": "हूँ", "hun": "हूँ", "hu": "हूँ", "raha": "रहा", "rahi": "रही", "rahe": "रहे",
    "kya": "क्या", "kyun": "क्यों", "kyon": "क्यों", "kyu": "क्यों", "kaise": "कैसे", "kaisa": "कैसा",
    "kaisi": "कैसी", "kahan": "कहाँ", "kab": "कब", "kaun": "कौन", "nahi": "नहीं", "nahin": "नहीं",
    "nhi": "नहीं", "na": "ना", "mein": "में", "main": "मैं", "mai": "मैं", "me": "में", "mujhe": "मुझे",
    "mujhko": "मुझको", "tum": "तुम", "tumhe": "तुम्हें", "tumhara": "तुम्हारा", "tumhari": "तुम्हारी",
    "aap": "आप", "aapka": "आपका", "aapki": "आपकी", "aapke": "आपके", "apna": "अपना", "apni": "अपनी",
    "apne": "अपने", "hum": "हम", "humein": "हमें", "hame": "हमें", "hamara": "हमारा", "hamari": "हमारी",
    "yeh": "यह", "ye": "ये", "woh": "वो", "wo": "वो", "vo": "वो", "voh": "वो", "is": "इस", "us": "उस",
    "iska": "इसका", "iski": "इसकी", "iske": "इसके", "uska": "उसका", "uski": "उसकी", "uske": "उसके",
    "unka": "उनका", "unki": "उनकी", "unke": "उनके", "isko": "इसको", "usko": "उसको", "inko": "इनको",
    "unko": "उनको", "ka": "का", "ki": "की", "ke": "के", "ko": "को", "se": "से", "par": "पर",
    "pe": "पे", "aur": "और", "ya": "या", "lekin": "लेकिन", "magar": "मगर", "kyunki": "क्योंकि",
    "toh": "तो", "to": "तो", "bhi": "भी", "sirf": "सिर्फ़", "bahut": "बहुत", "zyada": "ज़्यादा",
    "jyada": "ज़्यादा", "thoda": "थोड़ा", "thodi": "थोड़ी", "kuch": "कुछ", "kuchh": "कुछ", "sab": "सब",
    "sabhi": "सभी", "jab": "जब", "tab": "तब", "agar": "अगर", "abhi": "अभी", "phir": "फिर", "fir": "फिर",
    "kar": "कर", "karo": "करो", "karna": "करना", "karke": "करके", "karte": "करते", "karta": "करता",
    "karti": "करती", "kiya": "किया", "kiye": "किए", "kijiye": "कीजिए", "dekho": "देखो", "dekh": "देख",
    "dekhte": "देखते", "samjho": "समझो", "samjhao": "समझाओ", "samjhana": "समझाना", "samjha": "समझा",
    "samajh": "समझ", "batao": "बताओ", "bata": "बता", "bataiye": "बताइए", "bolo": "बोलो", "bol": "बोल",
    "chalo": "चलो", "chal": "चल", "suno": "सुनो", "sun": "सुन", "likho": "लिखो", "padho": "पढ़ो",
    "khol": "खोल", "kholo": "खोलो", "bajao": "बजाओ", "chalao": "चलाओ", "lagao": "लगाओ", "ruko": "रुको",
    "jao": "जाओ", "aao": "आओ", "aana": "आना", "jana": "जाना", "dena": "देना", "lena": "लेना",
    "diya": "दिया", "liya": "लिया", "gaya": "गया", "gayi": "गई", "gaye": "गए", "hota": "होता",
    "hoti": "होती", "hote": "होते", "hua": "हुआ", "hui": "हुई", "hue": "हुए", "sakta": "सकता",
    "sakti": "सकती", "sakte": "सकते", "chahiye": "चाहिए", "chahta": "चाहता", "chahti": "चाहती",
    "matlab": "मतलब", "yaani": "यानी", "jaise": "जैसे", "waise": "वैसे", "bilkul": "बिल्कुल",
    "theek": "ठीक", "thik": "ठीक", "accha": "अच्छा", "acha": "अच्छा", "achha": "अच्छा", "haan": "हाँ",
    "han": "हाँ", "ji": "जी", "bhai": "भाई", "yaar": "यार", "arre": "अरे", "arey": "अरे", "kal": "कल",
    "aaj": "आज", "parso": "परसों", "wala": "वाला", "wali": "वाली", "wale": "वाले", "do": "दो",
    "dono": "दोनों", "ek": "एक", "teen": "तीन", "char": "चार", "paanch": "पाँच", "pehla": "पहला",
    "pehle": "पहले", "doosra": "दूसरा", "dusra": "दूसरा", "baad": "बाद", "saath": "साथ", "sath": "साथ",
    "liye": "लिए", "baat": "बात", "cheez": "चीज़", "jo": "जो", "jaata": "जाता", "jata": "जाता",
    "jaati": "जाती", "aata": "आता", "ata": "आता", "aati": "आती", "milta": "मिलता", "milti": "मिलती",
    "banta": "बनता", "banata": "बनाता", "banati": "बनाती", "khana": "खाना", "pani": "पानी",
    "paani": "पानी", "sahi": "सही", "galat": "ग़लत", "sawaal": "सवाल", "sawal": "सवाल", "jawab": "जवाब",
    "yahan": "यहाँ", "wahan": "वहाँ", "upar": "ऊपर", "neeche": "नीचे", "andar": "अंदर", "bahar": "बाहर",
    "kaam": "काम", "log": "लोग", "samay": "समय", "waqt": "वक़्त", "din": "दिन", "raat": "रात",
    "ab": "अब", "sirji": "सरजी", "bas": "बस", "ho gaya": "हो गया", "kitna": "कितना", "kitni": "कितनी",
    "kitne": "कितने", "matra": "मात्रा", "barabar": "बराबर", "jod": "जोड़", "jodo": "जोड़ो",
    "hisaab": "हिसाब", "tarah": "तरह", "tarike": "तरीके", "tareeka": "तरीका", "wajah": "वजह",
}

# Common English words that are also in Hindi-looking spellings: in a Hinglish sentence these stay
# English. Anything in the system word list stays English as well, unless it is in _HINDI_WORDS.
_ENGLISH_KEEP = frozenset("""
a an and the of in on at for with this that these those it its is are was were be been step steps
side sides square answer video first second last next play pause search open close explain please
right left angle triangle theorem formula example question chapter topic network model data
""".split())


@functools.lru_cache(maxsize=1)
def _english_words() -> frozenset:
    for path in ("/usr/share/dict/words", "/usr/share/dict/american-english",
                 "/usr/share/dict/british-english"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return frozenset(w.strip().lower() for w in f if w.strip().isalpha())
        except OSError:
            continue
    return frozenset()


def language_of(sentence: str) -> str:
    """"hi" for Devanagari, "hinglish" for Roman-script Hindi, otherwise "en"."""
    s = sentence or ""
    deva = len(_DEVANAGARI.findall(s))
    latin = len(re.findall(r"[A-Za-z]", s))
    if deva and deva >= latin * 0.25:
        return "hi"
    words = re.findall(r"[a-z]+", s.lower())
    if not words:
        return "en"
    hindi = sum(w in _HINDI_MARKERS for w in words)
    # Two markers, or one in a short sentence ("theek hai"): "the" and "ki" alone are not enough
    # to take an English sentence away from the English voice.
    if hindi >= 2 and hindi / len(words) >= 0.2:
        return "hinglish"
    if hindi >= 1 and len(words) <= 3 and hindi / len(words) >= 0.5:
        return "hinglish"
    return "en"


# Rule-based Roman → Devanagari for Hindi words the table does not know.
_CONS = [("chh", "छ"), ("ksh", "क्ष"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"),
         ("dh", "ध"), ("ph", "फ"), ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("j", "ज"),
         ("t", "त"), ("d", "द"), ("n", "न"), ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"),
         ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"), ("h", "ह"), ("z", "ज़"), ("f", "फ़"),
         ("q", "क़"), ("x", "क्स"), ("c", "क")]
_VOWELS = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ao", "आओ", "ाओ"), ("ee", "ई", "ी"), ("ii", "ई", "ी"),
           ("oo", "ऊ", "ू"), ("uu", "ऊ", "ू"), ("ei", "ए", "े"), ("a", "अ", ""), ("i", "इ", "ि"),
           ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो")]


def transliterate_word(word: str) -> str:
    """Roman Hindi → Devanagari by rule. Approximate — the table above covers the common words —
    but always closer to Hindi than the English phonemiser's reading of the same letters."""
    w, out, i = word.lower(), [], 0
    prev_cons = False
    while i < len(w):
        # A nasal after a vowel, before a consonant or at the end, is an anusvara: "hain", "mein".
        if w[i] == "n" and out and not prev_cons and (i + 1 == len(w) or w[i + 1] not in "aeiou"):
            out.append("ं")
            i += 1
            prev_cons = False
            continue
        for rom, dev, sign in _VOWELS:
            if w.startswith(rom, i):
                out.append(sign if prev_cons else dev)
                i += len(rom)
                prev_cons = False
                break
        else:
            for rom, dev in _CONS:
                if w.startswith(rom, i):
                    if prev_cons:
                        out.append("्")
                    out.append(dev)
                    i += len(rom)
                    prev_cons = True
                    break
            else:
                out.append(w[i])
                i += 1
                prev_cons = False
    return "".join(out)


def to_devanagari(sentence: str) -> str:
    """A Roman Hinglish sentence as the Hindi voice should read it: Hindi words in Devanagari,
    English words left in Latin letters."""
    english = _english_words()

    def one(match: re.Match) -> str:
        word = match.group(0)
        low = word.lower()
        if low in _HINDI_WORDS:
            return _HINDI_WORDS[low]
        if low in _ENGLISH_KEEP or low in english or word[:1].isupper() and len(word) > 1 \
                and low not in _HINDI_MARKERS:
            return word
        return transliterate_word(word)

    out = re.sub(r"[A-Za-z]+", one, sentence)
    return re.sub(r"(?<=[ऀ-ॿ])\.(\s|$)", r"।\1", out)


def voice_text(sentence: str) -> tuple[str, str]:
    """(language, text for the synthesiser): "en" with the sentence, or "hi" with Devanagari."""
    lang = language_of(sentence)
    if lang == "hinglish":
        return "hi", to_devanagari(sentence)
    return lang, sentence
