"""Product photo identification: barcode decode (zbar) + Gemini vision guess.

Not an LLM tool — image bytes can't be passed as a tool argument, so bot.py's
photo handler calls these directly and hands the result to the agent as text.
"""
import base64
import io
import os
import re

# pyzbar's ctypes loader on macOS can't find the Homebrew-installed libzbar
# dylib via the default search paths; setting this before the first import
# lets ctypes.util.find_library see it. No-op on systems where zbar is
# already on the standard library path (e.g. most Linux installs).
os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

from langchain_core.messages import HumanMessage
from PIL import Image

from harness.base import build_chat_model

try:
    from pyzbar.pyzbar import decode as _zbar_decode
except (ImportError, OSError):
    _zbar_decode = None

VISION_MODEL_NAME = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")

IDENTIFY_PROMPT = (
    "You are helping a Kirana (Indian grocery/convenience store) owner identify a product from "
    "a photo -- could be a shelf photo, a single packet or box, or a barcode label. Reply with "
    "EXACTLY two lines, nothing else:\n"
    "NAME: <short shelf-list name: brand + a short generic type (skip marketing copy/variant "
    "wording) + pack size if visible, matching how a kirana owner would abbreviate it in their "
    "own stock list (e.g. 'Maggi 70g', not 'Maggi 2-Minute Masala Noodles 70g Pack'; 'Tata Salt "
    "1kg', not 'Tata Iodized Salt 1kg Pouch'). No extra commentary, no leading/trailing "
    "punctuation beyond what's part of the name itself. If you genuinely cannot make out any "
    "product, put exactly UNKNOWN here.>\n"
    "BARCODE: <the printed digits underneath/beside a barcode, if any are legible in the "
    "photo -- transcribe exactly what's printed, don't guess or complete missing digits. If no "
    "barcode digits are legible, put exactly NONE here.>"
)

_BARCODE_LINE_RE = re.compile(r"BARCODE:\s*([0-9 ]+|NONE)", re.IGNORECASE)
_NAME_LINE_RE = re.compile(r"NAME:\s*(.+)", re.IGNORECASE)


def _checksum_valid(digits: str) -> bool:
    """True for a digit string whose length/check-digit matches EAN-13, UPC-A, or EAN-8 --
    used to filter out a vision model's misread/hallucinated digits before we trust them
    enough to search the DB. Real printed barcodes always satisfy this; a guessed string
    only coincidentally would.
    """
    if len(digits) not in (8, 12, 13):
        return False
    nums = [int(d) for d in digits]
    body, check = nums[:-1], nums[-1]
    if len(digits) == 13:
        total = sum(n if i % 2 == 0 else n * 3 for i, n in enumerate(body))
    elif len(digits) == 12:
        total = sum(n * 3 if i % 2 == 0 else n for i, n in enumerate(body))
    else:  # EAN-8
        total = sum(n * 3 if i % 2 == 0 else n for i, n in enumerate(body))
    return (10 - (total % 10)) % 10 == check


def decode_barcode(image_bytes: bytes) -> str | None:
    """Decode a barcode/QR code from image bytes via zbar, if one is present and readable.

    Args:
        image_bytes: Raw image bytes as downloaded from Telegram.

    Returns:
        The decoded barcode string, or None if zbar is unavailable or no code was found.
    """
    if _zbar_decode is None:
        return None
    try:
        image = Image.open(io.BytesIO(image_bytes))
        results = _zbar_decode(image)
    except Exception:
        return None
    if not results:
        return None
    return results[0].data.decode("utf-8", errors="ignore")


def identify_product_from_photo(image_bytes: bytes) -> tuple[str, str | None]:
    """Ask Gemini vision to name the product shown in a photo, and to transcribe any
    barcode digits it can read as a fallback for when zbar can't decode the bars
    themselves (e.g. a blurry or low-res crop where the printed digits are still legible).

    Args:
        image_bytes: Raw image bytes as downloaded from Telegram.

    Returns:
        (name_guess, barcode_guess) -- name_guess is "UNKNOWN" if Gemini can't make one
        out. barcode_guess is the transcribed digits if they pass an EAN/UPC checksum
        (proof they're a real code, not a misread), else None.
    """
    model = build_chat_model("gemini", VISION_MODEL_NAME)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    message = HumanMessage(
        content=[
            {"type": "text", "text": IDENTIFY_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
        ]
    )
    response = model.invoke([message])
    text = (response.content or "").strip()

    name_match = _NAME_LINE_RE.search(text)
    name_guess = name_match.group(1).strip() if name_match else (text or "UNKNOWN")

    barcode_guess = None
    barcode_match = _BARCODE_LINE_RE.search(text)
    if barcode_match:
        digits = barcode_match.group(1).replace(" ", "")
        if digits.isdigit() and _checksum_valid(digits):
            barcode_guess = digits

    return name_guess if name_guess else "UNKNOWN", barcode_guess
