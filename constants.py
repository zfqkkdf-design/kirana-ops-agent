"""Tunables and fixed strings shared across bot.py handlers — kept out of the
main module so the handler wiring isn't buried under configuration values.
"""
from telegram import BotCommand

# In-memory per-chat conversation history is capped so cost/latency don't grow
# unbounded over a long trading day. Not durable — a process restart resets it,
# but that's fine: stock, khata, drafts and preferences all live in the DB.
MAX_HISTORY_MESSAGES = 20

# Above this many ambiguous candidates, a button per option gets unwieldy — ask
# the owner to type a more specific name instead of rendering a huge keyboard.
MAX_AMBIGUOUS_BUTTONS = 8

BUSY_MESSAGE = "⏳ Still working on your last request — please wait a moment."

# Telegram's typing indicator auto-expires after ~5s, so it needs refreshing
# for the life of a turn (PDF/deck generation etc. can run well past that).
TYPING_REFRESH_SECONDS = 4.0

# Sentinel prefix the model replies with when it needs the owner to pick a
# default payment mode (missing or invalid value) — lets the bot render
# tap-to-select buttons instead of making the owner type cash/upi/card/khata.
CHOOSE_PAYMENT_MODE_SENTINEL = "CHOOSE_PAYMENT_MODE:"

# Sentinel for a different case: the model is about to finalize_bill *now* and
# needs the owner to pick which mode to pay with (e.g. explicit mode wasn't
# given and there's no usable default). Distinct from the sentinel above,
# which only ever sets the standing default_payment_mode preference.
CHOOSE_FINALIZE_PAYMENT_SENTINEL = "CHOOSE_FINALIZE_PAYMENT:"

LOGO_CAPTION_KEYWORDS = ("logo", "brand")

HELP_TEXT_DEFAULT_TITLE = "Kirana Ops Agent"

BOT_COMMANDS = [
    BotCommand("start", "Check the bot is up"),
    BotCommand("help", "Instructions and sample commands"),
    BotCommand("newchat", "Clear conversation history (store data unaffected)"),
    BotCommand("reorder", "Reorder suggestions based on sales velocity"),
    BotCommand("dues", "Outstanding khata dues, highest first"),
    BotCommand("salesreport", "Send the sales analysis deck (PPTX)"),
]
