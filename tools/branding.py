"""Shop logo storage: center-crop to square, resize, persist to disk."""
import io
import os

from PIL import Image

LOGO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logos")
LOGO_SIZE = 512


def save_shop_logo(image_bytes: bytes, chat_id: str) -> str:
    """Center-crop an uploaded image to a square, resize it, and save it as the shop logo.

    Overwrites any existing logo for this chat_id — one logo per shop.

    Args:
        image_bytes: Raw image bytes as downloaded from Telegram.
        chat_id: Telegram chat id, used as the filename so each shop's logo is isolated.

    Returns:
        Absolute filesystem path to the saved PNG logo.
    """
    os.makedirs(LOGO_DIR, exist_ok=True)

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    resized = cropped.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)

    target_path = os.path.join(LOGO_DIR, f"{chat_id}.png")
    resized.save(target_path, format="PNG")
    return os.path.abspath(target_path)
