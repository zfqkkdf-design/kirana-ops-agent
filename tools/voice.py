"""Voice-note order transcription: Gemini audio understanding.

Not an LLM tool -- audio bytes can't be passed as a tool argument, so bot.py's
voice handler calls this directly and hands the transcript to the agent as text.
"""
import base64
import os

from langchain_core.messages import HumanMessage

from harness.base import build_chat_model

VOICE_MODEL_NAME = os.getenv("GEMINI_VOICE_MODEL", "gemini-2.5-flash")

TRANSCRIBE_PROMPT = (
    "You are transcribing speech left by an Indian Kirana (grocery/convenience) store owner or "
    "customer, for a store-billing assistant to act on. The speech may be in Hindi, English, or "
    "a Hindi-English mix, and may be a multi-item order with quantities, or a short command/"
    "question ('show the bill', 'finalize cash', 'kitni maggi bachi hai'). Transcribe ONLY words "
    "you can actually make out in THIS audio -- never invent, guess, or fill in a plausible-"
    "sounding grocery list or continuation. If the audio is short, unclear, or you're not "
    "confident what was said, transcribe just the words you're sure of (or reply EMPTY) rather "
    "than padding it out with invented items. Reply with ONLY a single plain-text line stating "
    "what was said, item names as spoken (don't translate or 'clean up' brand/product names), "
    "but normalize clearly spoken quantity words to digits where unambiguous (e.g. 'do' -> 2, "
    "'ek' -> 1, 'panch' -> 5, 'teen' -> 3). No commentary, no preamble, no quotes around the "
    "line. If the audio has no discernible speech in it, reply with exactly: EMPTY"
)


def transcribe_voice_note(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Ask Gemini to transcribe a spoken order from a voice note.

    Args:
        audio_bytes: Raw audio bytes as downloaded from Telegram.
        mime_type: Audio MIME type; Telegram voice notes are always OGG/Opus.

    Returns:
        The transcribed order as a plain-text line, or "" if no order was discernible.
    """
    model = build_chat_model("gemini", VOICE_MODEL_NAME)
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    message = HumanMessage(
        content=[
            {"type": "text", "text": TRANSCRIBE_PROMPT},
            {"type": "media", "mime_type": mime_type, "data": encoded},
        ]
    )
    response = model.invoke([message])
    transcript = (response.content or "").strip()
    return "" if transcript == "EMPTY" else transcript
