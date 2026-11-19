"""
Translation Agent.

Step 1 - detect the language from the start of the document (small, cheap LLM call
         that returns only a language code).
Step 2 - English: keep the original text exactly, no translation call.
         Anything else: translate to English and record a confidence score.

Results are cached in Redis by the LLM client, so the same content is never
detected or translated twice.
"""

from app.services.llm_client import ask_llm_json, persona, prompt
from app.settings import TRANSLATION_MODEL

DETECT_SAMPLE_CHARS = 2000   # the language is clear from the first part of the document


def detect_language(raw_text: str, language_hint: str | None = None) -> str:
    hint = ""
    if language_hint:
        hint = f"- The email header suggests the language is '{language_hint}', but verify it yourself."

    result = ask_llm_json(
        TRANSLATION_MODEL,
        persona("translator"),
        prompt("detect_language", text=raw_text[:DETECT_SAMPLE_CHARS], language_hint=hint),
        max_tokens=50,
    )
    return str(result.get("source_language") or "unknown").strip().lower()[:8]


def has_non_english_letters(text: str) -> bool:
    """True when the text contains letters outside plain A-Z (é, ü, ñ, ư, ...)."""
    return any(ch.isalpha() and not ch.isascii() for ch in text)


def translate_invoice(raw_text: str, language_hint: str | None = None) -> dict:
    language = detect_language(raw_text, language_hint)

    # Safety net for mixed documents (English headings, foreign line items):
    # accented letters such as "ư", "ö", "ñ" mean there is non-English text to translate.
    if language == "en" and has_non_english_letters(raw_text):
        language = "mixed"

    # English: no translation call at all - keep the original text exactly.
    if language == "en":
        return {
            "source_language": "en",
            "translation_confidence": 1.0,
            "translated_text": raw_text,
        }

    result = ask_llm_json(
        TRANSLATION_MODEL,
        persona("translator"),
        prompt("translate", text=raw_text,
               source_language="English mixed with another language" if language == "mixed" else language),
        max_tokens=4000,
    )
    confidence = float(result.get("confidence") or 0.0)
    translated = result.get("translated_text") or raw_text

    return {
        "source_language": language,
        "translation_confidence": max(0.0, min(confidence, 1.0)),
        "translated_text": translated,
    }