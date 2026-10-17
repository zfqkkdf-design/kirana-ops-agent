import asyncio
import logging
import os
import time
import uuid
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from sqlmodel import select

from db.engine import get_session, init_db
from db.models import BillDraft, KhataLedger, OwnerPreference, Product, Sale
from db.seed import seed_if_empty
from harness import build_harness
from tools import STORE_TOOLS, claim_message, generate_sales_deck
from tools.billing import VALID_PAYMENT_MODES
from tools.branding import save_shop_logo
from tools.khata import khata_dues_report
from tools.preferences import format_preferences_preamble, get_owner_preferences, set_owner_preference
from tools.reorder import reorder_suggestions
from tools.schedule import get_job_schedule, set_reschedule_callback
from tools._resolve import resolve_product, resolve_product_by_barcode
from tools.vision import decode_barcode, identify_product_from_photo
from tools.voice import transcribe_voice_note

from constants import (
    BOT_COMMANDS,
    BUSY_MESSAGE,
    CHOOSE_FINALIZE_PAYMENT_SENTINEL,
    CHOOSE_PAYMENT_MODE_SENTINEL,
    HELP_TEXT_DEFAULT_TITLE,
    LOGO_CAPTION_KEYWORDS,
    MAX_AMBIGUOUS_BUTTONS,
    MAX_HISTORY_MESSAGES,
    TYPING_REFRESH_SECONDS,
)
from prompts import HELP_TEXT_BODY, KIRANA_SYSTEM_PROMPT, PHOTO_IDENTIFY_NOTE

# Weekly deck (default Monday 9am IST, covers the just-finished Mon-Sun week) and
# khata dues reminder (default Friday 6pm IST) both auto-send to every chat_id
# we've ever seen activity from. Day/time defaults live in tools/schedule.py;
# set_weekly_deck_schedule / set_khata_reminder_schedule change and reschedule
# the live job immediately, no restart needed.

chat_histories: dict[str, list[dict]] = {}

TOOLS_BY_NAME = {tool.__name__: tool for tool in STORE_TOOLS}

# Pending ambiguous selections awaiting a button tap, keyed by a short token.
# In-memory like chat_histories — a lost pending selection just means the owner
# has to ask again, no data is at risk.
pending_ambiguous: dict[str, dict] = {}

# Serializes text/photo/voice turns per chat_id — a same-chat owner firing a
# second message before the first agent turn replies could otherwise race two
# LLM calls against the same in-progress bill draft. Checking .locked() and
# entering `async with lock` happen with no `await` between them, so it's
# atomic on asyncio's single-threaded event loop (no separate mutex needed).
_chat_locks: dict[str, asyncio.Lock] = {}


def _get_chat_lock(chat_id: str) -> asyncio.Lock:
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


async def _keep_typing(bot, chat_id: str) -> None:
    """Shows Telegram's native "typing..." indicator for the life of a turn,
    re-sent every TYPING_REFRESH_SECONDS since it isn't sticky. Cancelled by
    the caller once the turn completes.
    """
    while True:
        try:
            result = await bot.send_chat_action(chat_id=int(chat_id), action=ChatAction.TYPING)
            logger.info(
                "send_chat_action(action=typing) chat_id=%s -> api_result=%r", chat_id, result
            )
        except Exception:
            logger.exception("send_chat_action failed for chat_id=%s", chat_id)
            return
        await asyncio.sleep(TYPING_REFRESH_SECONDS)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
AGENT_HARNESS = os.getenv("AGENT_HARNESS", "langchain")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL: Missing TELEGRAM_BOT_TOKEN in .env")

if LLM_PROVIDER == "anthropic":
    if not ANTHROPIC_API_KEY:
        raise ValueError("CRITICAL: LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is missing in .env")
    MODEL_PRIORITY = [os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")]
else:
    if not GOOGLE_API_KEY:
        raise ValueError("CRITICAL: Missing GOOGLE_API_KEY in .env")
    # GEMINI_MODEL_PRIORITY: comma-separated, tried in order, falling back to the
    # next on any error (rate limit, invalid model, etc). Falls back to the single
    # GEMINI_MODEL var (then a hardcoded default) if the priority list isn't set.
    _priority_raw = os.getenv("GEMINI_MODEL_PRIORITY")
    if _priority_raw:
        MODEL_PRIORITY = [m.strip() for m in _priority_raw.split(",") if m.strip()]
    else:
        MODEL_PRIORITY = [os.getenv("GEMINI_MODEL", "gemini-2.5-flash")]
    # Ensure LangChain Gemini integration can see the key.
    os.environ.setdefault("GOOGLE_API_KEY", GOOGLE_API_KEY)


logger.info("provider=%s, harness=%s, models=%s", LLM_PROVIDER, AGENT_HARNESS, MODEL_PRIORITY)
agent = build_harness(
    AGENT_HARNESS,
    tools=STORE_TOOLS,
    system_prompt=KIRANA_SYSTEM_PROMPT,
    model_priority=MODEL_PRIORITY,
    provider=LLM_PROVIDER,
)




def _help_text(chat_id: str) -> str:
    shop_name = get_owner_preferences(chat_id).get("shop_name", HELP_TEXT_DEFAULT_TITLE)
    return f"*{shop_name}*{HELP_TEXT_BODY}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("User %s triggered /start", user.id if user else "unknown")
    await update.message.reply_text(
        "Kirana Ops Engine Initialized. System Ready.\n\n"
        "Send /help for instructions and sample commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(_help_text(chat_id), parse_mode="Markdown")


async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    chat_histories.pop(chat_id, None)
    logger.info("Cleared conversation history for chat_id=%s", chat_id)
    await update.message.reply_text(
        "Started a new chat. Conversation history cleared — "
        "stock, khata, any bill in progress, and preferences are unchanged.\n\n"
        "Commands: /start (check the bot is up) · /help (instructions + sample "
        "commands) · /newchat (clear conversation history)"
    )


async def reorder_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await _send_markdown(update.message, reorder_suggestions(chat_id))


async def sales_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    deck_path = generate_sales_deck(chat_id)
    if deck_path.startswith("REFUSED"):
        await update.message.reply_text(deck_path)
        return
    with open(deck_path, "rb") as deck_file:
        await update.message.reply_document(
            document=deck_file, filename=os.path.basename(deck_path)
        )


async def dues_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await _send_markdown(update.message, khata_dues_report(chat_id))


async def _send_markdown(reply_target, text: str, reply_markup=None) -> None:
    """Send with Telegram Markdown so tools' **bold**/- bill formatting actually
    renders, falling back to plain text if the model's free-form reply happens to
    contain characters Telegram's parser rejects as malformed markdown entities.
    """
    try:
        await reply_target.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as exc:
        logger.warning("Markdown parse failed (%s); resending as plain text.", exc)
        await reply_target.reply_text(text, reply_markup=reply_markup)


async def _run_agent_turn(chat_id: str, user_text: str, reply_target) -> None:
    """Run one full agent turn and send the reply into `reply_target` (a Telegram
    Message to call reply_text/reply_document on). Shared by real owner messages
    and by button taps that re-enter the conversation as if the owner typed
    the tapped value (e.g. a payment mode picked from CHOOSE_FINALIZE_PAYMENT buttons).
    """
    start = time.monotonic()
    try:
        prefs = get_owner_preferences(chat_id)
        preamble = format_preferences_preamble(prefs)
        today = datetime.utcnow().date().isoformat()
        prompt = f"[TODAY'S DATE: {today}]\n{preamble}\n[chat_id={chat_id}] {user_text}"
        history = chat_histories.setdefault(chat_id, [])
        messages = history + [{"role": "user", "content": prompt}]
        reply = await agent.ainvoke(messages)
        reply_text = reply["text"]
        elapsed = time.monotonic() - start
        logger.info("Agent reply length=%d, took %.2fs", len(reply_text), elapsed)

        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": reply_text})
        del history[:-MAX_HISTORY_MESSAGES]

        if reply_text.strip().startswith(CHOOSE_FINALIZE_PAYMENT_SENTINEL):
            reply_text = reply_text.strip().removeprefix(CHOOSE_FINALIZE_PAYMENT_SENTINEL).strip()
            reply_markup = _build_payment_mode_keyboard(chat_id, prefix="finpay")
        elif reply_text.strip().startswith(CHOOSE_PAYMENT_MODE_SENTINEL):
            reply_text = reply_text.strip().removeprefix(CHOOSE_PAYMENT_MODE_SENTINEL).strip()
            reply_markup = _build_payment_mode_keyboard(chat_id, prefix="pmpref")
        else:
            reply_markup = _build_ambiguous_keyboard(reply.get("ambiguous"))
        artifact_path = reply.get("artifact_path")
        await _send_markdown(reply_target, reply_text, reply_markup=reply_markup)
        if artifact_path:
            with open(artifact_path, "rb") as artifact_file:
                await reply_target.reply_document(
                    document=artifact_file, filename=os.path.basename(artifact_path)
                )
    except Exception:
        elapsed = time.monotonic() - start
        logger.exception("Agent / Gemini error after %.2fs", elapsed)
        await reply_target.reply_text("Internal processing error. Check server logs.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    message_id = update.message.message_id
    chat_id = str(update.effective_chat.id)
    logger.info("Received message: %r (chat_id=%s, message_id=%s)", user_text, chat_id, message_id)

    if not claim_message(message_id):
        logger.info("Message %s already processed, ignoring redelivery", message_id)
        return

    lock = _get_chat_lock(chat_id)
    if lock.locked():
        await update.message.reply_text(BUSY_MESSAGE)
        return
    async with lock:
        typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
        try:
            await _run_agent_turn(chat_id, user_text, update.message)
        finally:
            typing_task.cancel()



# Fed to the agent as the "user" turn after a product photo is identified, so
# the model can relay it in its usual voice and chain straight into billing/
# restock/add_new_product follow-ups, same as any other owner message.


async def _handle_identify_photo(chat_id: str, caption: str, image_bytes: bytes, reply_target) -> None:
    """Barcode-decode + Gemini-vision-identify a non-logo product photo, then hand the
    result to the normal agent turn as text so it can relay it and chain into
    billing/restock/add_new_product follow-ups.
    """
    barcode = decode_barcode(image_bytes)
    guess, vision_barcode = identify_product_from_photo(image_bytes)
    if not barcode and vision_barcode:
        barcode = vision_barcode

    with get_session() as session:
        product = resolve_product_by_barcode(session, barcode) if barcode else None
        if product is None and guess != "UNKNOWN":
            resolved = resolve_product(session, guess)
            if not isinstance(resolved, str):
                product = resolved
        if product is not None:
            match_note = (
                f"Matched existing product '{product.name}' (sku_id={product.sku_id}), "
                f"{product.quantity} in stock, MRP ₹{product.mrp}."
            )
        else:
            match_note = "No existing product matched this."

    barcode_note = f" A barcode was read: {barcode}." if barcode else " No barcode was detected."
    note = PHOTO_IDENTIFY_NOTE.format(barcode_note=barcode_note, guess=guess, match_note=match_note)
    if caption.strip():
        note += f' The owner\'s caption on the photo was: "{caption.strip()}"'

    await _run_agent_turn(chat_id, note, reply_target)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles photo uploads: shop-logo branding, or product identification.

    Deterministic branch off the LLM's plate entirely — a caption keyword decides
    the logo case (round-tripping image bytes through the text agent isn't possible,
    it has no way to accept binary attachments as a tool argument). Anything else
    (including no caption at all) is treated as a product photo to identify — that's
    the natural default when an owner snaps a shelf/packet/barcode photo.
    """
    chat_id = str(update.effective_chat.id)
    caption = update.message.caption or ""

    lock = _get_chat_lock(chat_id)
    if lock.locked():
        await update.message.reply_text(BUSY_MESSAGE)
        return
    async with lock:
        typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
        try:
            try:
                photo = update.message.photo[-1]
                file = await photo.get_file()
                image_bytes = bytes(await file.download_as_bytearray())
            except Exception:
                logger.exception("Failed to download photo for chat_id=%s", chat_id)
                await update.message.reply_text("Couldn't download that photo. Check server logs.")
                return

            if any(keyword in caption.lower() for keyword in LOGO_CAPTION_KEYWORDS):
                try:
                    logo_path = save_shop_logo(image_bytes, chat_id)
                    set_owner_preference("shop_logo_path", logo_path, chat_id)
                    await update.message.reply_text(
                        "Shop logo saved. It'll now appear on invoices and analysis decks."
                    )
                except Exception:
                    logger.exception("Failed to save shop logo for chat_id=%s", chat_id)
                    await update.message.reply_text("Couldn't save that logo. Check server logs.")
                return

            try:
                await _handle_identify_photo(chat_id, caption, image_bytes, update.message)
            except Exception:
                logger.exception("Failed to identify product photo for chat_id=%s", chat_id)
                await update.message.reply_text("Couldn't identify that photo. Check server logs.")
        finally:
            typing_task.cancel()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles voice notes: transcribe via Gemini, echo the transcript, then run it
    through the normal agent turn -- same code path as typed text, so all tool-calling,
    item resolution, and draft/finalize behavior applies unchanged.
    """
    chat_id = str(update.effective_chat.id)

    lock = _get_chat_lock(chat_id)
    if lock.locked():
        await update.message.reply_text(BUSY_MESSAGE)
        return
    async with lock:
        typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
        try:
            try:
                voice = update.message.voice
                file = await voice.get_file()
                audio_bytes = bytes(await file.download_as_bytearray())
            except Exception:
                logger.exception("Failed to download voice note for chat_id=%s", chat_id)
                await update.message.reply_text("Couldn't download that voice note. Check server logs.")
                return

            try:
                transcript = transcribe_voice_note(audio_bytes)
            except Exception:
                logger.exception("Failed to transcribe voice note for chat_id=%s", chat_id)
                await update.message.reply_text("Couldn't transcribe that voice note. Check server logs.")
                return

            if not transcript:
                await update.message.reply_text(
                    "Couldn't make out an order in that voice note -- try again or type it."
                )
                return

            await update.message.reply_text(f'🎤 Heard: "{transcript}"')
            await _run_agent_turn(chat_id, transcript, update.message)
        finally:
            typing_task.cancel()


def _lookup_candidate_ids(ambiguous: dict) -> list[str | None]:
    """Resolve each candidate's exact row id (sku_id / customer_id) by exact name.

    Candidate names came verbatim from the DB row that produced the AMBIGUOUS
    string, so an exact case-insensitive match is safe here (unlike the fuzzy/
    substring matching used to resolve free-typed text).
    """
    names = ambiguous["candidates"]
    with get_session() as session:
        if ambiguous["field"] == "item_name":
            rows = session.exec(select(Product)).all()
        else:
            rows = session.exec(select(KhataLedger)).all()
        by_name = {row.name.lower(): (row.sku_id if ambiguous["field"] == "item_name" else row.customer_id) for row in rows}
    return [by_name.get(name.lower()) for name in names]


def _build_ambiguous_keyboard(ambiguous: dict | None) -> InlineKeyboardMarkup | None:
    """Turn an extract_ambiguous() result into tap-to-select buttons, one per candidate.

    Returns None if there's nothing ambiguous, or if there are too many candidates
    for buttons to make sense (the LLM's reply already asks the owner to narrow it
    down by typing instead).
    """
    if not ambiguous or len(ambiguous["candidates"]) > MAX_AMBIGUOUS_BUTTONS:
        return None
    ambiguous["candidate_ids"] = _lookup_candidate_ids(ambiguous)
    token = uuid.uuid4().hex[:8]
    pending_ambiguous[token] = ambiguous
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"amb:{token}:{idx}")]
        for idx, name in enumerate(ambiguous["candidates"])
    ]
    return InlineKeyboardMarkup(buttons)


async def handle_ambiguous_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, token, idx_str = query.data.split(":", 2)
        idx = int(idx_str)
    except ValueError:
        logger.warning("Malformed ambiguous callback_data: %r", query.data)
        return

    ambiguous = pending_ambiguous.pop(token, None)
    if ambiguous is None:
        await query.edit_message_text("That selection expired — please ask again.")
        return
    candidates = ambiguous["candidates"]
    if idx < 0 or idx >= len(candidates):
        await query.edit_message_text("That selection expired — please ask again.")
        return

    chosen = candidates[idx]
    tool = TOOLS_BY_NAME.get(ambiguous["tool_name"])
    if tool is None:
        logger.error("Ambiguous callback referenced unknown tool %r", ambiguous["tool_name"])
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Internal error resolving selection. Check server logs.")
        return

    # Remove the buttons but leave the original clarifying question/text visible,
    # then post the pick and the resolved answer as their own chat bubbles so the
    # exchange reads like a normal back-and-forth instead of an in-place edit.
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(chosen)

    args = dict(ambiguous["args"])
    args[ambiguous["field"]] = chosen
    chosen_id = ambiguous.get("candidate_ids", [None] * len(candidates))[idx]
    if chosen_id is not None:
        id_field = "sku_id" if ambiguous["field"] == "item_name" else "customer_id"
        args[id_field] = chosen_id
    try:
        result_text = tool(**args)
    except Exception:
        logger.exception("Tool %s failed resolving ambiguous selection", ambiguous["tool_name"])
        await query.message.reply_text("Internal processing error. Check server logs.")
        return

    chat_id = str(update.effective_chat.id)
    history = chat_histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": f"[chat_id={chat_id}] {chosen}"})
    history.append({"role": "assistant", "content": result_text})
    del history[:-MAX_HISTORY_MESSAGES]

    await query.message.reply_text(result_text)


def _build_payment_mode_keyboard(chat_id: str, prefix: str) -> InlineKeyboardMarkup:
    """One button per valid payment mode, for the payment-mode sentinel prompts.

    `prefix` routes the tap to the right callback handler: "pmpref" sets the
    standing default preference, "finpay" re-enters the conversation to finalize
    the current bill with the chosen mode.
    """
    buttons = [
        [InlineKeyboardButton(mode, callback_data=f"{prefix}:{chat_id}:{mode}")]
        for mode in sorted(VALID_PAYMENT_MODES)
    ]
    return InlineKeyboardMarkup(buttons)


async def handle_payment_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, callback_chat_id, mode = query.data.split(":", 2)
    except ValueError:
        logger.warning("Malformed payment mode callback_data: %r", query.data)
        return

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(mode)

    result_text = set_owner_preference("default_payment_mode", mode, callback_chat_id)

    chat_id = str(update.effective_chat.id)
    history = chat_histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": f"[chat_id={chat_id}] {mode}"})
    history.append({"role": "assistant", "content": result_text})
    del history[:-MAX_HISTORY_MESSAGES]

    await query.message.reply_text(result_text)


async def handle_finalize_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tapped mode for an in-progress finalize — re-enters the conversation with the
    mode as the next owner turn, so the model (with full context: draft, any named
    khata customer) actually calls finalize_bill, same as if the owner had typed it.
    """
    query = update.callback_query
    await query.answer()

    try:
        _, callback_chat_id, mode = query.data.split(":", 2)
    except ValueError:
        logger.warning("Malformed finalize payment callback_data: %r", query.data)
        return

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(mode)

    await _run_agent_turn(callback_chat_id, mode, query.message)


def _all_known_chat_ids() -> set[str]:
    """Every chat_id we've ever seen activity from — union of preferences set,
    a bill drafted, or a sale finalized. Broader than just OwnerPreference so a
    shop that never touched /preferences still gets the weekly deck."""
    with get_session() as session:
        pref_ids = session.exec(select(OwnerPreference.chat_id)).all()
        draft_ids = session.exec(select(BillDraft.chat_id)).all()
        sale_ids = session.exec(select(Sale.chat_id)).all()
    return set(pref_ids) | set(draft_ids) | set(sale_ids)


async def send_weekly_decks(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly job: build and send last week's sales deck to every known chat."""
    end_date = date.today() - timedelta(days=1)  # the Sunday just gone
    start_date = end_date - timedelta(days=6)  # the Monday before that

    for chat_id in _all_known_chat_ids():
        try:
            deck_path = generate_sales_deck(
                chat_id, start_date=start_date.isoformat(), end_date=end_date.isoformat()
            )
            if deck_path.startswith("REFUSED"):
                logger.warning("Weekly deck refused for chat_id=%s: %s", chat_id, deck_path)
                continue
            with open(deck_path, "rb") as deck_file:
                await context.bot.send_document(
                    chat_id=int(chat_id),
                    document=deck_file,
                    filename=os.path.basename(deck_path),
                    caption=f"Weekly sales report: {start_date.isoformat()} to {end_date.isoformat()}",
                )
        except Forbidden:
            logger.info("Chat %s blocked the bot; skipping weekly deck.", chat_id)
        except Exception:
            logger.exception("Failed to send weekly deck to chat_id=%s", chat_id)


async def send_khata_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly job: nag the owner about outstanding khata dues, Friday evening.

    Dues are store-wide, not per-chat, so the same report goes to every known
    chat (mirrors how close_day_summary/low_stock_report read store-wide data
    regardless of which chat asks) — skipped entirely if nothing is owed, to
    avoid a pointless weekly ping.
    """
    report = khata_dues_report("")
    if report.startswith("No outstanding"):
        return

    for chat_id in _all_known_chat_ids():
        try:
            await context.bot.send_message(
                chat_id=int(chat_id), text=f"**Weekly khata reminder**\n\n{report}", parse_mode="Markdown"
            )
        except Forbidden:
            logger.info("Chat %s blocked the bot; skipping khata reminder.", chat_id)
        except Exception:
            logger.exception("Failed to send khata reminder to chat_id=%s", chat_id)


def _http_request() -> HTTPXRequest:
    # Default PTB timeouts (5s) are too aggressive on flaky Telegram routes.
    return HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=5.0,
    )




async def _post_init(app: Application) -> None:
    # Registers the commands with Telegram so they show up in the client's
    # slash-command autocomplete — has nothing to do with our CommandHandlers,
    # which work regardless; this is purely a discoverability UI hint.
    await app.bot.set_my_commands(BOT_COMMANDS)


def main() -> None:
    init_db()
    seed_if_empty()

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(_http_request())
        .get_updates_request(_http_request())
        .post_init(_post_init)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("newchat", newchat))
    app.add_handler(CommandHandler("reorder", reorder_command))
    app.add_handler(CommandHandler("dues", dues_command))
    app.add_handler(CommandHandler("salesreport", sales_report_command))
    app.add_handler(CallbackQueryHandler(handle_ambiguous_callback, pattern=r"^amb:"))
    app.add_handler(CallbackQueryHandler(handle_payment_mode_callback, pattern=r"^pmpref:"))
    app.add_handler(CallbackQueryHandler(handle_finalize_payment_callback, pattern=r"^finpay:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    def _register_scheduled_job(job_name: str, callback) -> None:
        def _run(day: int, time_val) -> None:
            for job in app.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            app.job_queue.run_daily(
                callback,
                time=time_val.replace(tzinfo=ZoneInfo("Asia/Kolkata")),
                days=(day,),
                name=job_name,
            )

        saved_day, saved_time = get_job_schedule(job_name)
        _run(saved_day, saved_time)
        set_reschedule_callback(job_name, _run)

    _register_scheduled_job("weekly_deck", send_weekly_decks)
    _register_scheduled_job("khata_reminder", send_khata_reminders)

    logger.info("Bot is polling updates...")
    # bootstrap_retries > 0: getMe can time out once on cold start without aborting.
    app.run_polling(bootstrap_retries=5, drop_pending_updates=True)


if __name__ == "__main__":
    main()
