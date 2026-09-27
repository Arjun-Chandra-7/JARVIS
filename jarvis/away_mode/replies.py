"""The replies away mode sends without a model, in English, Hinglish and Hindi.

Every reply names JARVIS as the assistant the first time a conversation hears from it, and never
speaks as the owner. The Hinglish is how people actually text — Roman script, English words kept
English — not translated formal Hindi.
"""
from __future__ import annotations

from .language import EN, HI, HINGLISH


def _back(ret: str, lang: str) -> str:
    if not ret:
        return {EN: "", HINGLISH: "", HI: ""}[lang]
    return {EN: f" until around {ret}", HINGLISH: f", around {ret} tak free honge", HI: f", लगभग {ret} तक"}[lang]


def disclosure(owner: str, ret: str, lang: str = EN, *, take_message_only: bool = False) -> str:
    """The first thing a conversation hears: who is answering, and that the owner is away."""
    if lang == HINGLISH:
        tail = "Aapka message main unhe de dunga." if take_message_only else \
            "Aapka message main unhe de dunga, ya koi simple cheez ho toh bata dijiye."
        return f"Hi, main JARVIS hoon, {owner} ka assistant. {owner} abhi available nahi hain{_back(ret, lang)}. {tail}"
    if lang == HI:
        tail = "आपका संदेश मैं उन तक पहुँचा दूँगा।"
        return f"नमस्ते, मैं JARVIS हूँ, {owner} का असिस्टेंट। {owner} अभी उपलब्ध नहीं हैं{_back(ret, lang)}। {tail}"
    tail = "I can pass along a message." if take_message_only else \
        "I can pass along a message or help with something simple."
    return f"Hi, I'm JARVIS, {owner}'s assistant. {owner} is unavailable{_back(ret, lang)}. {tail}"


def _pick(lang: str, en: str, hinglish: str, hi: str) -> str:
    return {EN: en, HINGLISH: hinglish, HI: hi}.get(lang, en)


def noted(owner: str, lang: str) -> str:
    return _pick(lang, f"Got it, I've noted that for {owner}.",
                 f"Theek hai, maine {owner} ke liye note kar liya.",
                 f"ठीक है, मैंने {owner} के लिए नोट कर लिया।")


def relay(owner: str, lang: str) -> str:
    return _pick(lang, f"Noted, I'll pass this on to {owner}. I can't contact anyone else myself.",
                 f"Done, main {owner} ko bata dunga. Main khud kisi aur ko message nahi kar sakta.",
                 f"ठीक है, मैं {owner} को बता दूँगा। मैं खुद किसी और से संपर्क नहीं कर सकता।")


def call_back(owner: str, lang: str) -> str:
    return _pick(lang, f"I'll let {owner} know you'd like a call back.",
                 f"Main {owner} ko bol dunga ki aapko call back karein.",
                 f"मैं {owner} को बता दूँगा कि आपको कॉल बैक करें।")


def return_time(owner: str, ret: str, lang: str) -> str:
    if not ret:
        return _pick(lang, f"I don't have an exact time, but I'll tell {owner} you asked.",
                     f"Exact time nahi pata, par main {owner} ko bata dunga ki aapne pucha.",
                     f"सही समय मुझे नहीं पता, पर मैं {owner} को बता दूँगा।")
    return _pick(lang, f"{owner} should be free around {ret}. I'll tell them you asked.",
                 f"{owner} around {ret} tak free honge. Main bata dunga ki aapne pucha.",
                 f"{owner} लगभग {ret} तक फ्री होंगे। मैं बता दूँगा कि आपने पूछा।")


def urgent_ack(owner: str, lang: str) -> str:
    return _pick(lang, f"Understood, this sounds urgent. I'm alerting {owner} now.",
                 f"Samajh gaya, ye urgent lag raha hai. Main abhi {owner} ko alert kar raha hoon.",
                 f"समझ गया, यह ज़रूरी लग रहा है। मैं अभी {owner} को सूचित कर रहा हूँ।")


def emergency_ack(owner: str, lang: str) -> str:
    return _pick(lang, f"I'm alerting {owner} right now. If anyone is in danger, please call emergency services (112).",
                 f"Main abhi {owner} ko alert kar raha hoon. Agar koi khatre mein hai toh please 112 pe call karein.",
                 f"मैं अभी {owner} को सूचित कर रहा हूँ। अगर कोई खतरे में है तो कृपया 112 पर कॉल करें।")


def important_offer(owner: str, lang: str) -> str:
    return _pick(lang, f"Noted. If it can't wait until {owner} is back, reply \"urgent\" with a line on why and I'll alert them.",
                 f"Note kar liya. Agar wait nahi ho sakta toh \"urgent\" likh ke thoda bata dijiye, main {owner} ko alert kar dunga.",
                 f"नोट कर लिया। अगर इंतज़ार नहीं हो सकता तो \"urgent\" लिखकर कारण बताइए, मैं {owner} को सूचित कर दूँगा।")


_DECLINE = {
    "payment": ("anything about money or payments", "paise ya payment wali baat", "पैसे या भुगतान से जुड़ी बात"),
    "secret": ("codes, OTPs or passwords — please don't send those here", "OTP, password ya code — please yahan mat bhejiye",
               "OTP, पासवर्ड या कोड — कृपया यहाँ न भेजें"),
    "private_info": ("personal details", "personal details", "निजी जानकारी"),
    "media": ("files or photos", "files ya photos", "फ़ाइलें या फ़ोटो"),
    "legal": ("agreements or anything legal", "agreement ya legal cheezein", "समझौते या कानूनी बातें"),
    "purchase": ("purchases or orders", "kuch kharidna ya order karna", "ख़रीदारी या ऑर्डर"),
    "commitment": (None, None, None),
    "sensitive": ("something this personal", "itni personal baat", "इतनी निजी बात"),
    "account_recovery": ("account or login help", "account ya login ki help", "अकाउंट या लॉगिन"),
    "location": ("where they are", "woh kahan hain", "वे कहाँ हैं"),
    "contact_others": ("contacting other people", "kisi aur ko contact karna", "किसी और से संपर्क"),
    "policy_change": ("that", "woh", "वह"),
}


def decline(owner: str, topics: list[str], lang: str) -> str:
    """Can't do this as the assistant; the owner will see it. One topic named, the first."""
    topic = next((t for t in topics if t in _DECLINE), "private_info")
    if topic == "commitment":
        return _pick(lang, f"I can't confirm anything on {owner}'s behalf, but I'll ask them and they'll get back to you.",
                     f"Main {owner} ki taraf se confirm nahi kar sakta, par main unse puch ke bata dunga.",
                     f"मैं {owner} की ओर से पक्का नहीं कर सकता, पर मैं उनसे पूछकर बताऊँगा।")
    en, hg, hi = _DECLINE[topic]
    if topic == "secret":
        return _pick(lang, f"I can't handle {en}. I'll let {owner} know you got in touch.",
                     f"Please OTP, password ya koi code yahan mat bhejiye. {owner} ko bata dunga ki aapne message kiya.",
                     f"मैं {hi} नहीं संभाल सकता। {owner} को बता दूँगा कि आपने संदेश भेजा।")
    if topic == "sensitive":
        return _pick(lang, f"This is best talked about with {owner} directly. I've let them know you messaged.",
                     f"Ye baat {owner} se directly karna better hoga. Maine unhe bata diya hai ki aapne message kiya.",
                     f"यह बात {owner} से सीधे करना बेहतर होगा। मैंने उन्हें बता दिया है।")
    return _pick(lang, f"I can't help with {en} as the assistant, but I've passed it to {owner} to decide.",
                 f"Assistant hone ke naate main {hg} nahi kar sakta, par maine {owner} ko bata diya hai.",
                 f"असिस्टेंट होने के नाते मैं {hi} में मदद नहीं कर सकता, पर मैंने {owner} को बता दिया है।")


def closing(owner: str, lang: str) -> str:
    return _pick(lang, f"Okay, I'll let {owner} know.", f"Theek hai, main {owner} ko bata dunga.",
                 f"ठीक है, मैं {owner} को बता दूँगा।")


def wrap_up(owner: str, lang: str) -> str:
    """Said once when a thread reaches its turn limit; nothing is sent after it."""
    return _pick(lang, f"I've noted everything for {owner} and they'll follow up when they're back.",
                 f"Maine sab note kar liya hai, {owner} free hote hi reply karenge.",
                 f"मैंने सब नोट कर लिया है, {owner} लौटकर जवाब देंगे।")


def call_disclosure(owner: str, lang: str) -> str:
    return _pick(lang, f"Hello, I'm JARVIS, {owner}'s assistant. {owner} is unavailable right now. May I take a message?",
                 f"Hello, main JARVIS hoon, {owner} ka assistant. {owner} abhi available nahi hain. Kya main message le loon?",
                 f"नमस्ते, मैं JARVIS हूँ, {owner} का असिस्टेंट। {owner} अभी उपलब्ध नहीं हैं। क्या मैं संदेश ले लूँ?")


def call_text_back(owner: str, ret: str, lang: str = EN) -> str:
    return _pick(lang, f"Hi, this is JARVIS, {owner}'s assistant. {owner} can't take calls right now{_back(ret, EN)}. "
                       "Reply here with a message and I'll pass it on.",
                 f"Hi, main JARVIS, {owner} ka assistant. {owner} abhi call nahi le sakte{_back(ret, HINGLISH)}. "
                 "Yahan message kar dijiye, main unhe de dunga.",
                 f"नमस्ते, मैं JARVIS, {owner} का असिस्टेंट। {owner} अभी कॉल नहीं ले सकते। यहाँ संदेश भेजिए।")
