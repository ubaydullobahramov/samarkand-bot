import os
import html
import sqlite3
import threading
import http.server
import socketserver
import logging
import uuid
from contextlib import contextmanager

import telebot
from telebot import types


# ---------------- CONFIG ----------------

TOKEN = '8952248333:AAHsfLXQ7TgQoleDgMsYrRFU0j7Y6pa8rgA'

ADMIN_GROUP_ID = -4487103832
INVITE_LINK = 'https://t.me/+EZ9s8IqwbpMxZTcy'
ADMIN_CONTACT = '@uwedsuadmin'

CARD_NUMBER = '5614 6821 0314 3055'
CARD_HOLDER = 'BAXRAMOV UBAYDULLO'
PRICE = '169,000 UZS'

TRIP_NAME = 'SUQOQ FOREST TRIP'

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DB_PATH", "bot_data.sqlite3")

# Put the trip poster next to this file as poster.jpg (or poster.png).
# If no poster file is found, the bot falls back to PHOTO_URL.
POSTER_FILES = ("poster.jpg", "poster.jpeg", "poster.png")
PHOTO_URL = "https://picsum.photos/800/500"


# ---------------- LOGGING ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")


# ---------------- DATABASE ----------------

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                application_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT,
                fio TEXT,
                phone TEXT,
                university TEXT,
                status TEXT NOT NULL DEFAULT 'collecting_fio',
                receipt_file_id TEXT,
                receipt_type TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_applications_user
            ON applications(user_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_applications_status
            ON applications(status)
        """)


def safe(value):
    return html.escape(str(value or ""), quote=True)


def create_application(user_id, username):
    application_id = uuid.uuid4().hex[:12]

    with get_db() as conn:
        conn.execute("""
            INSERT INTO applications (
                application_id,
                user_id,
                username,
                status
            )
            VALUES (?, ?, ?, 'collecting_fio')
        """, (
            application_id,
            user_id,
            username or "no_username"
        ))

    return application_id


def get_application(application_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM applications
            WHERE application_id = ?
        """, (application_id,)).fetchone()


def update_application(application_id, field, value, status):
    allowed_fields = {"fio", "phone", "university"}

    if field not in allowed_fields:
        raise ValueError("Invalid database field")

    with get_db() as conn:
        conn.execute(
            f"""
            UPDATE applications
            SET {field} = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE application_id = ?
            """,
            (value, status, application_id)
        )


# ---------------- RENDER HEALTH SERVER ----------------

class HealthHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass


def run_web_server():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReusableServer(
        ("0.0.0.0", PORT),
        HealthHandler
    ) as server:
        logger.info("Health server running on port %s", PORT)
        server.serve_forever()


# ---------------- HELPERS ----------------

def ask_text(chat_id, prompt, next_handler, application_id):
    msg = bot.send_message(chat_id, prompt)

    bot.register_next_step_handler(
        msg,
        next_handler,
        application_id
    )


def start_application(chat_id, user_id, username):
    application_id = create_application(
        user_id,
        username
    )

    ask_text(
        chat_id,
        "Please enter your <b>Full Name</b>:",
        get_fio,
        application_id
    )


def payment_contact_message():
    return (
        "❓ <b>Questions about payment?</b>\n\n"
        "If you have any questions or problems with payment, "
        "please contact Nazar:\n"
        f"👤 {safe(ADMIN_CONTACT)}"
    )


def send_payment_details(chat_id):
    payment_info = (
        "💳 <b>Payment Details</b>\n\n"
        f"<b>Card:</b> <code>{safe(CARD_NUMBER)}</code>\n"
        f"<b>Account Holder:</b> {safe(CARD_HOLDER)}\n"
        f"<b>Amount:</b> {safe(PRICE)}\n\n"
        "After transferring the money, please send a photo "
        "or a PDF document of your payment receipt here.\n\n"
        "❓ <b>Questions about payment?</b>\n"
        f"Contact Nazar: {safe(ADMIN_CONTACT)}"
    )

    bot.send_message(chat_id, payment_info)


def send_to_admin(method, *args, **kwargs):
    """
    Sends a message to the admin group.

    If the group was upgraded to a supergroup, Telegram rejects the old
    chat ID and returns the new one. In that case we switch to the new
    ID, log it loudly and retry once.
    """
    global ADMIN_GROUP_ID

    try:
        return method(ADMIN_GROUP_ID, *args, **kwargs)

    except telebot.apihelper.ApiTelegramException as e:
        result_json = getattr(e, "result_json", None) or {}
        parameters = result_json.get("parameters") or {}
        new_id = parameters.get("migrate_to_chat_id")

        if not new_id:
            raise

        logger.warning(
            "Admin group was upgraded to a supergroup. "
            "NEW ADMIN_GROUP_ID = %s  <-- put this value into the code",
            new_id
        )

        ADMIN_GROUP_ID = new_id
        return method(ADMIN_GROUP_ID, *args, **kwargs)


def notify_admin_lead(application_id):
    app = get_application(application_id)

    if not app:
        return

    lead = (
        f"⚠️ <b>APPLICATION — PAYMENT DETAILS SENT — {safe(TRIP_NAME)}</b>\n\n"
        f"🎫 <b>Application:</b> "
        f"<code>{safe(application_id)}</code>\n"
        f"👤 <b>Name:</b> {safe(app['fio'])}\n"
        f"📱 <b>Phone:</b> {safe(app['phone'])}\n"
        f"🎓 <b>University:</b> {safe(app['university'])}\n"
        f"🔗 <b>TG:</b> @{safe(app['username'])}\n"
        f"🆔 <b>User ID:</b> <code>{app['user_id']}</code>"
    )

    try:
        send_to_admin(bot.send_message, lead)
    except Exception:
        logger.exception("Failed to send lead to admin group")


# ---------------- CHAT ID ----------------

@bot.message_handler(commands=["chatid"])
def chat_id_cmd(message):
    # Send /chatid in the admin group to see its current ID.
    bot.reply_to(
        message,
        f"Chat ID: <code>{message.chat.id}</code>"
    )


# ---------------- START ----------------

@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    username = message.from_user.username or "no_username"

    caption = (
        "🌲 <b>JOURNEY TO SUQOQ FOREST!</b>\n\n"
        "Get ready to escape the city and explore "
        "the Suqoq mountains with us!\n\n"
        "<b>What's included:</b>\n"
        "🚌 Comfortable transportation (there & back)\n"
        "🥾 Hiking through forest trails and mountain views\n"
        "📸 Professional photographer\n"
        "🥪 Sandwich for lunch on the trail\n"
        "🥤 Refreshing drinks\n"
        "🎲 UNO & Mafia games\n"
        "👥 Great team & energetic vibe\n\n"
        "📅 <b>Date:</b> November 8 (Sunday)\n"
        "⏰ <b>Meeting:</b> 06:30\n"
        "📍 <b>Destination:</b> Suqoq, Tashkent region\n"
        f"💰 <b>Price:</b> {safe(PRICE)}\n"
        "⚡ <b>Spots:</b> Limited to 150 places only!\n\n"
        "Ready for the mountains?"
    )

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "Continue ➡️",
            callback_data="step_fio"
        )
    )

    poster_path = next(
        (p for p in POSTER_FILES if os.path.exists(p)),
        None
    )

    try:
        if poster_path:
            with open(poster_path, "rb") as poster:
                bot.send_photo(
                    user_id,
                    poster,
                    caption=caption,
                    reply_markup=markup
                )
        else:
            bot.send_photo(
                user_id,
                PHOTO_URL,
                caption=caption,
                reply_markup=markup
            )
    except Exception:
        logger.exception("Could not send welcome photo")
        bot.send_message(
            user_id,
            caption,
            reply_markup=markup
        )


# ---------------- NEW APPLICATION ----------------

@bot.callback_query_handler(
    func=lambda call: call.data == "step_fio"
)
def ask_fio(call):
    bot.answer_callback_query(call.id)

    if call.message.chat.type != "private":
        return

    start_application(
        call.message.chat.id,
        call.from_user.id,
        call.from_user.username or "no_username"
    )


# ---------------- BUY ANOTHER TICKET ----------------

@bot.callback_query_handler(
    func=lambda call: call.data == "buy_another"
)
def buy_another_ticket(call):
    bot.answer_callback_query(call.id)

    if call.message.chat.type != "private":
        return

    start_application(
        call.message.chat.id,
        call.from_user.id,
        call.from_user.username or "no_username"
    )


# ---------------- FULL NAME ----------------

def get_fio(message, application_id):
    if message.chat.type != "private":
        return

    if not message.text or not message.text.strip():
        ask_text(
            message.chat.id,
            "Please enter your full name as text.",
            get_fio,
            application_id
        )
        return

    fio = message.text.strip()

    if len(fio) > 150:
        ask_text(
            message.chat.id,
            "Your name is too long. Please try again.",
            get_fio,
            application_id
        )
        return

    update_application(
        application_id,
        "fio",
        fio,
        "collecting_phone"
    )

    ask_text(
        message.chat.id,
        "Please enter your <b>phone number</b>:",
        get_phone,
        application_id
    )


# ---------------- PHONE ----------------

def get_phone(message, application_id):
    if message.chat.type != "private":
        return

    if not message.text or not message.text.strip():
        ask_text(
            message.chat.id,
            "Please enter your phone number as text.",
            get_phone,
            application_id
        )
        return

    phone = message.text.strip()
    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) < 7 or len(digits) > 15 or len(phone) > 30:
        ask_text(
            message.chat.id,
            "Invalid phone number. Please enter it again, "
            "including country code if possible.",
            get_phone,
            application_id
        )
        return

    update_application(
        application_id,
        "phone",
        phone,
        "collecting_university"
    )

    ask_text(
        message.chat.id,
        "Which <b>university</b> do you attend?",
        get_university,
        application_id
    )


# ---------------- UNIVERSITY ----------------

def get_university(message, application_id):
    if message.chat.type != "private":
        return

    if not message.text or not message.text.strip():
        ask_text(
            message.chat.id,
            "Please enter your university as text.",
            get_university,
            application_id
        )
        return

    university = message.text.strip()

    if len(university) > 200:
        ask_text(
            message.chat.id,
            "University name is too long. Please try again.",
            get_university,
            application_id
        )
        return

    update_application(
        application_id,
        "university",
        university,
        "awaiting_receipt"
    )

    try:
        notify_admin_lead(application_id)

        # Contact is shown before payment instructions.
        bot.send_message(
            message.chat.id,
            payment_contact_message()
        )

        send_payment_details(message.chat.id)

        ask_text(
            message.chat.id,
            "📸 Please send your payment receipt as a photo "
            "or PDF document.",
            get_receipt,
            application_id
        )

    except Exception:
        logger.exception("Failed to send payment instructions")

        bot.send_message(
            message.chat.id,
            "Could not send payment details. "
            f"Please contact {safe(ADMIN_CONTACT)}."
        )


# ---------------- PAYMENT RECEIPT ----------------

def get_receipt(message, application_id):
    if message.chat.type != "private":
        return

    app = get_application(application_id)

    if not app or app["status"] != "awaiting_receipt":
        bot.send_message(
            message.chat.id,
            "This application is no longer waiting for a receipt. "
            "Use /start to create a new application."
        )
        return

    file_id = None
    receipt_type = None

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
        receipt_type = "photo"

    elif message.content_type == "document":
        document = message.document
        mime_type = document.mime_type or ""

        if (
            mime_type == "application/pdf"
            or mime_type.startswith("image/")
        ):
            file_id = document.file_id
            receipt_type = "document"

    if not file_id:
        ask_text(
            message.chat.id,
            "⚠️ Please send a photo or a PDF/image document "
            "of the payment receipt.",
            get_receipt,
            application_id
        )
        return

    # Atomically move application to pending.
    with get_db() as conn:
        result = conn.execute("""
            UPDATE applications
            SET status = 'pending',
                receipt_file_id = ?,
                receipt_type = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE application_id = ?
              AND status = 'awaiting_receipt'
        """, (
            file_id,
            receipt_type,
            application_id
        ))

        if result.rowcount != 1:
            bot.send_message(
                message.chat.id,
                "This receipt has already been submitted "
                "or the application has changed."
            )
            return

    app = get_application(application_id)

    caption = (
        f"💳 <b>NEW PAYMENT FOR VERIFICATION — {safe(TRIP_NAME)}</b>\n\n"
        f"🎫 <b>Application:</b> "
        f"<code>{safe(application_id)}</code>\n"
        f"👤 <b>Name:</b> {safe(app['fio'])}\n"
        f"📱 <b>Phone:</b> {safe(app['phone'])}\n"
        f"🎓 <b>University:</b> {safe(app['university'])}\n"
        f"🔗 <b>TG:</b> @{safe(app['username'])}\n"
        f"🆔 <b>User ID:</b> <code>{app['user_id']}</code>"
    )

    markup = types.InlineKeyboardMarkup()

    markup.row(
        types.InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"app_{application_id}"
        ),
        types.InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"rej_{application_id}"
        )
    )

    try:
        if receipt_type == "photo":
            send_to_admin(
                bot.send_photo,
                file_id,
                caption=caption,
                reply_markup=markup
            )
        else:
            send_to_admin(
                bot.send_document,
                file_id,
                caption=caption,
                reply_markup=markup
            )

    except Exception:
        logger.exception("Failed to forward receipt to admin group")

        with get_db() as conn:
            conn.execute("""
                UPDATE applications
                SET status = 'awaiting_receipt',
                    updated_at = CURRENT_TIMESTAMP
                WHERE application_id = ?
                  AND status = 'pending'
            """, (application_id,))

        bot.send_message(
            message.chat.id,
            "We couldn't forward your receipt for review. "
            "Please try again."
        )

        ask_text(
            message.chat.id,
            "Send your receipt as a photo or PDF document.",
            get_receipt,
            application_id
        )
        return

    bot.send_message(
        message.chat.id,
        "✅ Your receipt has been submitted for review. "
        "Please wait for an administrator's decision."
    )


# ---------------- APPROVE / REJECT ----------------

@bot.callback_query_handler(
    func=lambda call: call.data.startswith(("app_", "rej_"))
)
def process_admin_decision(call):
    # As requested, no administrator-ID restriction.

    try:
        action, application_id = call.data.split("_", 1)
    except (ValueError, AttributeError):
        bot.answer_callback_query(
            call.id,
            "Invalid application.",
            show_alert=True
        )
        return

    if action not in ("app", "rej"):
        bot.answer_callback_query(call.id, "Invalid action.")
        return

    app = get_application(application_id)

    if not app:
        bot.answer_callback_query(
            call.id,
            "Application not found.",
            show_alert=True
        )
        return

    new_status = (
        "approved" if action == "app"
        else "rejected"
    )

    # Only one click can process a pending application.
    with get_db() as conn:
        result = conn.execute("""
            UPDATE applications
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE application_id = ?
              AND status = 'pending'
        """, (
            new_status,
            application_id
        ))

        if result.rowcount != 1:
            bot.answer_callback_query(
                call.id,
                "This application has already been processed.",
                show_alert=True
            )
            return

    user_id = app["user_id"]

    try:
        if action == "app":
            markup = types.InlineKeyboardMarkup()

            markup.add(
                types.InlineKeyboardButton(
                    "🎟️ Buy a ticket for someone else",
                    callback_data="buy_another"
                )
            )

            bot.send_message(
                user_id,
                "🎉 <b>Payment Approved!</b>\n\n"
                "Your payment has been verified successfully!\n\n"
                "Welcome to the Suqoq Forest Trip! 🌲\n"
                "📅 November 8 · ⏰ Meeting at 06:30\n\n"
                "Join our private group for trip updates and logistics "
                "(meeting point, what to bring, etc.):\n"
                f"{safe(INVITE_LINK)}\n\n"
                "🎟️ <b>Want to buy a ticket for someone else?</b>\n"
                "You can register another participant below.",
                reply_markup=markup
            )

            result_text = "\n\n🟢 <b>APPROVED</b>"

        else:
            bot.send_message(
                user_id,
                "❌ Unfortunately, your payment could not be verified.\n\n"
                "If you think this is an error, please contact Nazar:\n"
                f"{safe(ADMIN_CONTACT)}"
            )

            result_text = "\n\n🔴 <b>REJECTED</b>"

    except Exception:
        logger.exception(
            "Could not notify user %s",
            user_id
        )

        # Return to pending so the decision can be retried.
        with get_db() as conn:
            conn.execute("""
                UPDATE applications
                SET status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE application_id = ?
                  AND status = ?
            """, (
                application_id,
                new_status
            ))

        bot.answer_callback_query(
            call.id,
            "Could not notify the user. Try again.",
            show_alert=True
        )
        return

    try:
        old_caption = call.message.caption or ""

        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=old_caption + result_text,
            parse_mode="HTML",
            reply_markup=None
        )

    except Exception:
        logger.exception(
            "Could not update admin receipt message"
        )

    bot.answer_callback_query(
        call.id,
        "Decision recorded."
    )


# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    logger.info("Bot is starting...")

    bot.infinity_polling(
        skip_pending=False,
        timeout=30,
        long_polling_timeout=30
    )
