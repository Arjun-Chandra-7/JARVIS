"""What Jarvis says, as opposed to what he writes: speech normalisation and the voice's language.

Fictional data throughout: numbers, names and addresses here belong to nobody.
"""
import numpy as np
import pytest

from jarvis.audio import loudness, speech_text as st


def say(text, **k):
    return st.normalize(text, pronunciations=(), address="sir", **k)


# ----------------------------------------------------------------- never read aloud
@pytest.mark.parametrize("secret", [
    "sk-proj-abcdefghijklmnop1234567890",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
    "3f9a1c77e2b04d5e8f6a1b2c3d4e5f60",
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    "AbC123dEf456GhI789jKl012MnO",
])
def test_tokens_keys_and_ids_are_never_spoken(secret):
    out = say(f"Here it is: {secret} — done.")
    assert secret.split()[0][:10] not in out
    assert "done" in out


def test_a_phone_number_is_not_read_digit_by_digit():
    out = say("Calling +91 98765 43210 now.")
    assert "98765" not in out and "the number ending 32 10" in out


def test_a_long_url_becomes_its_site():
    out = say("Watch https://www.youtube.com/watch?v=abcdefghijk&t=42s first.")
    assert "watch?v" not in out and "youtube dot com" in out


def test_a_short_email_is_said_and_a_long_one_is_not_spelled():
    assert say("Mail asha.k@example.com.") == "Mail asha k at example dot com."
    assert "an address at example dot com" in say("From x9f8e7d6c5b4a3@example.com today")


def test_a_username_becomes_a_name():
    assert say("Send it to arjun-chandra-7 please.") == "Send it to Arjun Chandra please."


def test_localhost_is_this_machine_unless_the_port_matters():
    assert say("The server is on 127.0.0.1:8770.") == "The server is on local port 8770."
    assert say("Ping 127.0.0.1 now.") == "Ping this machine now."


def test_file_paths_become_file_names():
    assert say("Saved to /home/someone/Documents/notes_final.pdf") == \
        "Saved to the file notes final dot pdf"


# ----------------------------------------------------------------- maths and numbers
def test_pythagoras_is_read_as_maths():
    assert say("a² + b² = c²") == "ay squared plus b squared equals c squared"


def test_powers_minus_and_fractions():
    assert say("x^2 - 4 = 0") == "x squared minus 4 equals 0"
    assert "3 over 4" in say("That is 3/4 of it.")
    assert "square root of" in say("√2 is irrational")


def test_hyphens_in_words_are_not_minus():
    assert "minus" not in say("A well-known e-mail client.")


def test_grouped_numbers_lose_their_commas_indian_or_western():
    assert say("It costs ₹1,20,000.") == "It costs 120000 rupees."
    assert say("About 1,234,567 people.") == "About 1234567 people."


def test_iso_dates_are_said_as_dates():
    assert say("Due 2026-09-24.") == "Due 24 September 2026."


# ----------------------------------------------------------------- markup, code, emoji
def test_code_is_left_on_screen_not_read():
    out = say("Run this:\n```python\nprint('hello')\n```")
    assert "print" not in out and "The code is on screen." in out


def test_markdown_and_emoji_are_dropped():
    assert say("**Done**, sir! 🎉") == "Done, sir!"


# ----------------------------------------------------------------- the honorific
def test_the_address_is_configurable():
    assert st.with_honorific("Done, sir.", "sire") == "Done, sire."
    assert st.with_honorific("Done, sir.", "") == "Done."
    assert st.with_honorific("Sorry sir, I didn't catch that.", "") == "Sorry I didn't catch that."


# ----------------------------------------------------------------- pronunciation dictionary
def test_the_personal_dictionary_sets_pronunciation():
    out = st.normalize("Open Viralyst.", pronunciations=[("Viralyst", "vai-ruh-list")], address="sir")
    assert out == "Open vai ruh list."


# ----------------------------------------------------------------- language
@pytest.mark.parametrize("text, lang", [
    ("The main thing is to add them.", "en"),
    ("Please open the second video.", "en"),
    ("Is step mein hum dono sides ka square add karte hain.", "hinglish"),
    ("Ab ye step Hinglish mein samjhao.", "hinglish"),
    ("Theek hai.", "hinglish"),
    ("पाइथागोरस प्रमेय क्या है?", "hi"),
    ("इस step में square add करते हैं।", "hi"),
])
def test_which_voice_a_sentence_needs(text, lang):
    assert st.language_of(text) == lang


def test_hinglish_is_written_for_the_hindi_voice_with_english_kept_english():
    lang, text = st.voice_text("Is step mein hum dono sides ka square add karte hain.")
    assert lang == "hi"
    assert text == "इस step में हम दोनों sides का square add करते हैं।"


def test_unknown_hindi_words_are_transliterated_by_rule():
    # Close enough for the Hindi phonemiser: the vowels and consonants are the right ones.
    assert st.transliterate_word("samjhao") == "सम्झाओ"
    assert st.transliterate_word("kitaab") == "किताब"
    assert st.transliterate_word("hain") == "हैं"


def test_english_stays_with_the_english_voice():
    assert st.voice_text("Done, sir. YouTube is open.") == ("en", "Done, sir. YouTube is open.")


# ----------------------------------------------------------------- loudness
def _tone(peak, n=24000):
    t = np.arange(n) / 24000
    return (np.sin(2 * np.pi * 220 * t) * peak * 32767).astype(np.int16).tobytes()


def _peak(pcm):
    return np.abs(np.frombuffer(pcm, np.int16)).max() / 32767


def test_quiet_speech_is_brought_up_to_one_loudness():
    out = loudness.shape(_tone(0.05))
    rms = np.sqrt(np.mean((np.frombuffer(out, np.int16) / 32767.0) ** 2))
    assert abs(20 * np.log10(rms) - loudness.TARGET_DBFS) < 1.0


def test_nothing_clips_at_the_speaker_volume_found_on_this_machine():
    gain = loudness.parse_volume("Volume: 1.53")            # 153%, cubic → x3.58
    assert abs(gain - 1.53 ** 3) < 1e-6
    out = loudness.shape(_tone(0.6), gain)
    assert _peak(out) * gain <= loudness.CEILING + 1e-3


def test_muted_or_unreadable_volume_is_treated_as_unity():
    assert loudness.parse_volume("Volume: 0.40 [MUTED]") == 1.0
    assert loudness.parse_volume("garbage") == 1.0


def test_silence_is_left_alone():
    assert loudness.shape(b"\0\0" * 100) == b"\0\0" * 100
