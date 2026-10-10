
import os
import html
import sqlite3
import threading
import http.server
import socketserver
import logging
from contextlib import contextmanager

import telebot
from telebot import types


# ---------------- CONFIG ----------------
# Токен оставлен без изменений
TOKEN = '8952248333:AAGAKzBkfbALsyoeXt9CoGuLuKNRz4GLLLk'

ADMIN_GROUP_ID = -5102424349
INVITE_LINK = 'https://t.me/+EZ9s8IqwbpMxZTcy'
ADMIN_CONTACT = '@uwedsuadmin'

CARD_NUMBER = '5614 6821 0314 3055'
CARD_HOLDER = 'BAXRAMOV UBAYDULLO'
PRICE = '299,000 UZS'

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DB_PATH", "bot_data.sqlite3")
PHOTO_URL = "https://picsum.photos/800/500"
# ----------------------------------------


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
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                fio TEXT,
                phone TEXT,
                university TEXT,
                status TEXT NOT NULL DEFAULT 'collecting_fio',
                receipt_file_id TEXT,
                receipt_type TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_applications_status
            ON applications(status)
        """)


def ensure_user(user_id, username):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO applications (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, username or "no_username"))


def get_application(user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM applications WHERE user_id = ?",
            (user_id,)
        ).fetchone()


def set_field(user_id, field, value, status):
    allowed = {"fio", "phone", "university"}
    if field not in allowed:
        raise ValueError("Invalid database field")

    with get_db() as conn:
        conn.execute(
            f"""UPDATE applications
                SET {field} = ?, status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?""",
            (value, status, user_id)
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
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass


def run_web_server():
    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReusableServer(("0.0.0.0", PORT), HealthHandler) as server:
        logger.info("Health server running on port %s", PORT)
        server.serve_forever()


# ---------------- HELPERS ----------------

def safe(value):
    return html.escape(str(value or ""), quote=True)


def ask_text(chat_id, prompt, next_handler):
    msg = bot.send_message(chat_id, prompt)
    bot.register_next_step_handler(msg, next_handler)


def get_username(user):
    return user.username or "no_username"


def notify_admin_lead(user_id):
    app = get_application(user_id)
    if not app:
        return

    lead = (
        "⚠️ <b>UNFINISHED APPLICATION (LEAD)</b>\n"
        "User reached the payment step but hasn't submitted a receipt yet.\n\n"
        f"👤 <b>Name:</b> {safe(app['fio'])}\n"
        f"📱 <b>Phone:</b> {safe(app['phone'])}\n"
        f"🎓 <b>University:</b> {safe(app['university'])}\n"
        f"🔗 <b>TG:</b> @{safe(app['username'])} "
        f"(ID: <code>{user_id}</code>)"
    )

    try:
        bot.send_message(ADMIN_GROUP_ID, lead)
    except Exception:
        logger.exception("Failed to send lead to admin group")


def send_payment_details(chat_id):
    payment_info = (
        "💳 <b>Payment Details</b>\n\n"
        f"<b>Card:</b> <code>{safe(CARD_NUMBER)}</code>\n"
        f"<b>Account Holder:</b> {safe(CARD_HOLDER)}\n"
        f"<b>Amount:</b> {safe(PRICE)}\n\n"
        "After transferring the money, please send a photo "
        "or a PDF screenshot of your payment receipt here."
    )
    bot.send_message(chat_id, payment_info)


# ---------------- 1. START ----------------

@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    ensure_user(user_id, get_username(message.from_user))

    app = get_application(user_id)

    if app and app["status"] == "approved":
        bot.send_message(
            user_id,
            f"🎉 Your payment has already been approved.\n{safe(INVITE_LINK)}"
        )
        return

    caption = (
        "🕌 <b>JOURNEY TO ANCIENT SAMARKAND!</b>\n\n"
        "Get ready to explore the heart of the Silk Road with us!\n\n"
        "<b>What's included:</b>\n"
        "🚌 Comfortable transportation (there & back)\n"
        "📸 Professional photographer\n"
        "🍲 Delicious Zigir Osh from the center of Samarkand\n"
        "🎲 UNO & Mafia games\n"
        "👥 Great team & energetic vibe\n\n"
        "📅 <b>Date:</b> October 25\n"
        "💰 <b>Price:</b> 299,000 UZS\n"
        "⚡ <b>Spots:</b> Limited to 100 places only!\n\n"
        "Ready to travel back in time with us?"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "Continue ➡️",
            callback_data="step_fio"
        )
    )

    try:
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


# ---------------- 2. FULL NAME ----------------

@bot.callback_query_handler(func=lambda call: call.data == "step_fio")
def ask_fio(call):
    bot.answer_callback_query(call.id)

    if call.message.chat.type != "private":
        return

    user_id = call.from_user.id
    ensure_user(user_id, get_username(call.from_user))

    app = get_application(user_id)

    if app and app["status"] in ("pending", "approved"):
        bot.send_message(
            user_id,
            "Your application is already being processed or approved."
        )
        return

    with get_db() as conn:
        conn.execute("""
            UPDATE applications
            SET status = 'collecting_fio',
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))

    ask_text(
        user_id,
        "Please enter your <b>Full Name</b>:",
        get_fio
    )


def get_fio(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id

    if not message.text or not message.text.strip():
        ask_text(
            user_id,
            "Please enter your full name as text.",
            get_fio
        )
        return

    fio = message.text.strip()

    if len(fio) > 150:
        ask_text(
            user_id,
            "Your name is too long. Please try again.",
            get_fio
        )
        return

    ensure_user(user_id, get_username(message.from_user))
    set_field(user_id, "fio", fio, "collecting_phone")

    ask_text(
        user_id,
        "Please enter your <b>phone number</b>:",
        get_phone
    )


# ---------------- 3. PHONE ----------------

def get_phone(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id

    if not message.text or not message.text.strip():
        ask_text(
            user_id,
            "Please enter your phone number as text.",
            get_phone
        )
        return

    phone = message.text.strip()

    # Permissive validation for international phone formats.
    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) < 7 or len(digits) > 15 or len(phone) > 30:
        ask_text(
            user_id,
            "Invalid phone number. Please enter it again, including country code if possible.",
            get_phone
        )
        return

    ensure_user(user_id, get_username(message.from_user))
    set_field(user_id, "phone", phone, "collecting_university")

    ask_text(
        user_id,
        "Which <b>university</b> do you attend?",
        get_university
    )


# ---------------- 4. UNIVERSITY ----------------

def get_university(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id

    if not message.text or not message.text.strip():
        ask_text(
            user_id,
            "Please enter your university as text.",
            get_university
        )
        return

    university = message.text.strip()

    if len(university) > 200:
        ask_text(
            user_id,
            "University name is too long. Please try again.",
            get_university
        )
        return

    ensure_user(user_id, get_username(message.from_user))
    set_field(user_id, "university", university, "awaiting_receipt")

    notify_admin_lead(user_id)

    try:
        send_payment_details(user_id)
        ask_text(
            user_id,
            "Send your payment receipt as a photo or PDF document.",
            get_receipt
        )
    except Exception:
        logger.exception("Failed to send payment details to user")
        bot.send_message(
            user_id,
            f"Could not send payment details. Please contact {safe(ADMIN_CONTACT)}."
        )


# ---------------- 5. PAYMENT RECEIPT ----------------

def get_receipt(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    app = get_application(user_id)

    if not app or app["status"] != "awaiting_receipt":
        bot.send_message(
            user_id,
            "There is no active receipt submission step. "
            "Use /start to check your application."
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

        if mime_type == "application/pdf" or mime_type.startswith("image/"):
            file_id = document.file_id
            receipt_type = "document"

    if not file_id:
        ask_text(
            user_id,
            "⚠️ Please send a photo or a PDF/image document of the receipt.",
            get_receipt
        )
        return

    # Change status only if the application is still awaiting a receipt.
    with get_db() as conn:
        result = conn.execute("""
            UPDATE applications
            SET status = 'pending',
                receipt_file_id = ?,
                receipt_type = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'awaiting_receipt'
        """, (file_id, receipt_type, user_id))

        if result.rowcount != 1:
            bot.send_message(
                user_id,
                "Your receipt has already been submitted or your application changed."
            )
            return

    app = get_application(user_id)

    caption = (
        "💳 <b>NEW PAYMENT FOR VERIFICATION — SAMARKAND TRIP</b>\n\n"
        f"👤 <b>Name:</b> {safe(app['fio'])}\n"
        f"📱 <b>Phone:</b> {safe(app['phone'])}\n"
        f"🎓 <b>University:</b> {safe(app['university'])}\n"
        f"🔗 <b>TG:</b> @{safe(app['username'])} "
        f"(ID: <code>{user_id}</code>)"
    )

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"app_{user_id}"
        ),
        types.InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"rej_{user_id}"
        )
    )

    try:
        if receipt_type == "photo":
            bot.send_photo(
                ADMIN_GROUP_ID,
                file_id,
                caption=caption,
                reply_markup=markup
            )
        else:
            bot.send_document(
                ADMIN_GROUP_ID,
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
                WHERE user_id = ? AND status = 'pending'
            """, (user_id,))

        bot.send_message(
            user_id,
            "We couldn't forward your receipt for review. "
            "Please try sending it again in a moment."
        )
        ask_text(
            user_id,
            "Send your receipt as a photo or PDF document.",
            get_receipt
        )
        return

    bot.send_message(
        user_id,
        "✅ Your receipt has been submitted for review. "
        "Please wait for an administrator's decision."
    )


# ---------------- 6. APPROVE / REJECT ----------------

@bot.callback_query_handler(
    func=lambda call: call.data.startswith(("app_", "rej_"))
)
def process_admin_decision(call):
    # As requested, no administrator-ID restriction.
    # Anyone who can access the buttons can use them.

    try:
        action, user_id_text = call.data.split("_", 1)
        user_id = int(user_id_text)
    except (ValueError, TypeError):
        bot.answer_callback_query(
            call.id,
            "Invalid application.",
            show_alert=True
        )
        return

    if action not in ("app", "rej"):
        bot.answer_callback_query(call.id, "Invalid action.")
        return

    # Atomic status transition prevents duplicate processing.
    new_status = "approved" if action == "app" else "rejected"

    with get_db() as conn:
        result = conn.execute("""
            UPDATE applications
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'pending'
        """, (new_status, user_id))

        if result.rowcount != 1:
            bot.answer_callback_query(
                call.id,
                "This application has already been processed or is no longer pending.",
                show_alert=True
            )
            return

    try:
        if action == "app":
            bot.send_message(
                user_id,
                "🎉 <b>Payment Approved!</b>\n\n"
                "Welcome to the Samarkand trip! Join our private "
                "group for trip updates and logistics:\n"
                f"{safe(INVITE_LINK)}"
            )
            result_text = "\n\n🟢 <b>APPROVED</b>"

        else:
            bot.send_message(
                user_id,
                "❌ Unfortunately, your payment could not be verified.\n\n"
                f"If you think this is an error, contact the organizers: "
                f"{safe(ADMIN_CONTACT)}"
            )
            result_text = "\n\n🔴 <b>REJECTED</b>"

    except Exception:
        logger.exception("Could not notify user %s", user_id)

        # Revert the state so the decision can be retried.
        with get_db() as conn:
            conn.execute("""
                UPDATE applications
                SET status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND status = ?
            """, (user_id, new_status))

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
        logger.exception("Could not update admin receipt message")

    bot.answer_callback_query(call.id, "Decision recorded.")


# ---------------- START APPLICATION ----------------

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
