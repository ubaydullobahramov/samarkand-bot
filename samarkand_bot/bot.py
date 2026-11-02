import os
import html
import time
import sqlite3
import threading
import http.server
import socketserver
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import requests
import telebot
from telebot import types


# ---------------- CONFIG ----------------

TOKEN = '9152149200:AAHI1r2wRS338HndDpcIrJNUVE7dWYOKj_Q'

ADMIN_GROUP_ID = -5563061387
INVITE_LINK = 'https://t.me/+1LxiUshcGVU4YzBi'
ADMIN_CONTACT = '@uwedsuadmin'

CARD_NUMBER = '5614 6821 0314 3055'
CARD_HOLDER = 'BAXRAMOV UBAYDULLO'
PRICE = '169,000 UZS'
PRICE_UZS = 169000

TRIP_NAME = 'SUQOQ FOREST TRIP'

# Total number of spots. Pending + approved applications take a spot.
# Users never see how many are left — the bot just closes registration
# quietly when the limit is reached.
MAX_SPOTS = 130

# If the bot was redeployed after sales started, the database is empty
# again. Put here how many people are ALREADY approved in the Google Sheet,
# so the limit and /stats stay correct.
SOLD_BEFORE_RESTART = 6

# Reminders for people who pressed /start but did not buy a ticket.
# Hours after their first /start. One message per step, 3 steps.
REMINDER_SCHEDULE_HOURS = (3, 24, 48)

# No reminders at night (Tashkent time): from 23:00 to 09:00.
QUIET_HOURS = (23, 9)

# No reminders after this moment (the evening before the trip).
REMINDERS_STOP_AT = datetime(2026, 11, 7, 22, 0)

# Google Sheet with the list of approved participants.
SHEET_WEBHOOK_URL = 'https://script.google.com/macros/s/AKfycby2ramJijYp6_lg0ERDq5HPzCsIoERhpCoa_BHrF_LCIJ-Sip31nW3HxZ9nshl1WEwq/exec'
# Must be the same word as SECRET in the Apps Script code.
SHEET_SECRET = 'suqoq-2026'

TASHKENT_TZ = timezone(timedelta(hours=5))

PORT = int(os.environ.get("PORT", "10000"))
# New file name = clean start without the test applications.
DB_PATH = os.environ.get("DB_PATH", "suqoq_trip.sqlite3")

# Render sets this automatically for web services. The bot pings itself
# every 10 minutes so the free instance does not fall asleep.
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# Put the trip poster next to this file as poster.jpg (or poster.png).
# If no poster file is found, the bot falls back to PHOTO_URL.
POSTER_FILES = ("poster.jpg", "poster.jpeg", "poster.png")
PHOTO_URL = "https://picsum.photos/800/500"

REJECT_REASONS = {
    "1": "Payment not received",
    "2": "Wrong amount — the ticket price is 169,000 UZS",
    "3": "The receipt is unreadable",
    "4": "Other — please contact the admin",
}

ACTIVE_STATUSES = (
    "collecting_fio",
    "collecting_phone",
    "collecting_university",
    "awaiting_receipt",
)


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


def add_column_if_missing(conn, name, ddl):
    columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(applications)")
    ]
    if name not in columns:
        conn.execute(f"ALTER TABLE applications ADD COLUMN {ddl}")


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

        add_column_if_missing(
            conn, "receipt_unique_id", "receipt_unique_id TEXT"
        )
        add_column_if_missing(
            conn, "for_friend", "for_friend INTEGER NOT NULL DEFAULT 0"
        )
        add_column_if_missing(
            conn, "reminded", "reminded INTEGER NOT NULL DEFAULT 0"
        )
        add_column_if_missing(
            conn, "reject_reason", "reject_reason TEXT"
        )

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_applications_user
            ON applications(user_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_applications_status
            ON applications(status)
        """)

        # Everyone who pressed /start — used for reminders.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reminder_stage INTEGER NOT NULL DEFAULT 0,
                last_reminder_at TEXT,
                unsubscribed INTEGER NOT NULL DEFAULT 0
            )
        """)


def remember_user(user):
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        """, (
            user.id,
            user.username or "",
            user.first_name or ""
        ))


def safe(value):
    return html.escape(str(value or ""), quote=True)


def create_application(user_id, username, for_friend=False):
    application_id = uuid.uuid4().hex[:12]

    with get_db() as conn:
        conn.execute("""
            INSERT INTO applications (
                application_id,
                user_id,
                username,
                status,
                for_friend
            )
            VALUES (?, ?, ?, 'collecting_fio', ?)
        """, (
            application_id,
            user_id,
            username or "no_username",
            1 if for_friend else 0
        ))

    return get_application(application_id)


def get_application(application_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM applications
            WHERE application_id = ?
        """, (application_id,)).fetchone()


def get_active_application(user_id):
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)

    with get_db() as conn:
        return conn.execute(f"""
            SELECT *
            FROM applications
            WHERE user_id = ?
              AND status IN ({placeholders})
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
        """, (user_id, *ACTIVE_STATUSES)).fetchone()


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


def set_status(application_id, new_status, only_if=None, extra=None):
    """
    Changes the status. If only_if is given, the change happens only
    when the current status equals only_if. Returns True if changed.
    """
    sets = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
    params = [new_status]

    for column, value in (extra or {}).items():
        sets.append(f"{column} = ?")
        params.append(value)

    sql = f"UPDATE applications SET {', '.join(sets)} WHERE application_id = ?"
    params.append(application_id)

    if only_if:
        sql += " AND status = ?"
        params.append(only_if)

    with get_db() as conn:
        result = conn.execute(sql, params)
        return result.rowcount == 1


def taken_spots():
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS n
            FROM applications
            WHERE status IN ('pending', 'approved')
        """).fetchone()
        return row["n"] + SOLD_BEFORE_RESTART


def spots_left():
    return max(0, MAX_SPOTS - taken_spots())


def find_duplicate_receipt(unique_id, application_id):
    if not unique_id:
        return None

    with get_db() as conn:
        row = conn.execute("""
            SELECT application_id, fio
            FROM applications
            WHERE receipt_unique_id = ?
              AND application_id != ?
            ORDER BY created_at
            LIMIT 1
        """, (unique_id, application_id)).fetchone()
        return row


# ---------------- RENDER HEALTH SERVER ----------------

class HealthHandler(http.server.BaseHTTPRequestHandler):

    def _ok(self, body=True):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            if body:
                self.wfile.write(b"Not found")
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        if body:
            self.wfile.write(b"Bot is running")

    def do_GET(self):
        self._ok(body=True)

    def do_HEAD(self):
        self._ok(body=False)

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


def keep_alive():
    if not SELF_URL:
        logger.info("RENDER_EXTERNAL_URL not set — self-ping is off")
        return

    url = SELF_URL.rstrip("/") + "/health"
    logger.info("Self-ping enabled: %s", url)

    while True:
        time.sleep(600)
        try:
            requests.get(url, timeout=20)
        except Exception:
            logger.warning("Self-ping failed")


# ---------------- ADMIN GROUP ----------------

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


def is_admin_chat(message):
    return message.chat.id == ADMIN_GROUP_ID


def application_card(app):
    friend_line = (
        "👥 <b>Ticket for another participant</b> "
        f"(bought by @{safe(app['username'])})\n"
        if app["for_friend"] else ""
    )

    return (
        f"🎫 <b>Application:</b> <code>{safe(app['application_id'])}</code>\n"
        f"{friend_line}"
        f"👤 <b>Name:</b> {safe(app['fio'])}\n"
        f"📱 <b>Phone:</b> {safe(app['phone'])}\n"
        f"🎓 <b>University:</b> {safe(app['university'])}\n"
        f"🔗 <b>TG:</b> @{safe(app['username'])}\n"
        f"🆔 <b>User ID:</b> <code>{app['user_id']}</code>"
    )


def notify_admin_lead(app):
    lead = (
        f"⚠️ <b>NEW LEAD — {safe(TRIP_NAME)}</b>\n"
        "Form filled, payment details sent, receipt not yet received.\n\n"
        + application_card(app)
    )

    try:
        send_to_admin(bot.send_message, lead)
    except Exception:
        logger.exception("Failed to send lead to admin group")


def decision_markup(application_id):
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
    return markup


def reject_reasons_markup(application_id):
    markup = types.InlineKeyboardMarkup()
    for code, text in REJECT_REASONS.items():
        markup.add(
            types.InlineKeyboardButton(
                text,
                callback_data=f"rr_{code}_{application_id}"
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "↩️ Back",
            callback_data=f"back_{application_id}"
        )
    )
    return markup


# ---------------- GOOGLE SHEET ----------------

def save_to_sheet(app, approved_by):
    """
    Adds an approved participant to the Google Sheet.
    Returns True if the row was written, False otherwise.
    """
    if not SHEET_WEBHOOK_URL:
        logger.warning("SHEET_WEBHOOK_URL is empty — sheet saving is off")
        return False

    payload = {
        "secret": SHEET_SECRET,
        "approved_at": datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M"),
        "fio": app["fio"] or "",
        "phone": app["phone"] or "",
        "university": app["university"] or "",
        "username": app["username"] or "",
        "user_id": str(app["user_id"]),
        "application_id": app["application_id"],
        "approved_by": approved_by or "",
    }

    try:
        response = requests.post(
            SHEET_WEBHOOK_URL,
            json=payload,
            timeout=20
        )
        if response.ok and response.text.strip() == "ok":
            return True

        logger.error(
            "Sheet answered %s: %s",
            response.status_code,
            response.text[:200]
        )
        return False

    except Exception:
        logger.exception("Failed to save participant to sheet")
        return False


# ---------------- USER FLOW ----------------

def payment_details_text():
    return (
        "💳 <b>Payment Details</b>\n\n"
        f"<b>Card:</b> <code>{safe(CARD_NUMBER)}</code>\n"
        f"<b>Account Holder:</b> {safe(CARD_HOLDER)}\n"
        f"<b>Amount:</b> {safe(PRICE)}\n\n"
        "After transferring the money, send a <b>photo</b> "
        "or a <b>PDF</b> of your payment receipt here.\n\n"
        "❓ <b>Questions about payment?</b>\n"
        f"Contact Nazar: {safe(ADMIN_CONTACT)}"
    )


def phone_keyboard():
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=True
    )
    markup.add(
        types.KeyboardButton(
            "📱 Share my phone number",
            request_contact=True
        )
    )
    return markup


def ask_for_step(chat_id, app):
    """Asks the question that matches the application's current step."""
    status = app["status"]
    friend = bool(app["for_friend"])

    if status == "collecting_fio":
        text = (
            "👥 Enter the <b>Full Name</b> of the participant "
            "you are buying a ticket for:"
            if friend else
            "Please enter your <b>Full Name</b>:"
        )
        bot.send_message(chat_id, text, reply_markup=types.ReplyKeyboardRemove())

    elif status == "collecting_phone":
        if friend:
            bot.send_message(
                chat_id,
                "Enter the participant's <b>phone number</b>:",
                reply_markup=types.ReplyKeyboardRemove()
            )
        else:
            bot.send_message(
                chat_id,
                "Please share or type your <b>phone number</b>:",
                reply_markup=phone_keyboard()
            )

    elif status == "collecting_university":
        text = (
            "Which <b>university</b> does the participant attend?"
            if friend else
            "Which <b>university</b> do you attend?"
        )
        bot.send_message(chat_id, text, reply_markup=types.ReplyKeyboardRemove())

    elif status == "awaiting_receipt":
        bot.send_message(chat_id, payment_details_text())
        bot.send_message(
            chat_id,
            "📸 Please send your payment receipt as a photo or PDF document."
        )


def approved_count():
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS n
            FROM applications
            WHERE status = 'approved'
        """).fetchone()
        return row["n"] + SOLD_BEFORE_RESTART


def ticket_button(text="🎟 Get my ticket ➡️"):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text, callback_data="step_fio")
    )
    return markup


def full_message(chat_id):
    bot.send_message(
        chat_id,
        "😔 <b>SOLD OUT!</b> All tickets are gone.\n\n"
        "Registration for the Suqoq Forest Trip is closed. "
        f"Questions: {safe(ADMIN_CONTACT)}",
        reply_markup=types.ReplyKeyboardRemove()
    )


def start_or_resume(chat_id, user_id, username, for_friend=False):
    if not for_friend:
        active = get_active_application(user_id)
        if active:
            bot.send_message(
                chat_id,
                "↩️ You already have an unfinished application — "
                "let's continue where you stopped.\n"
                "To start over, send /cancel."
            )
            ask_for_step(chat_id, active)
            return

    if spots_left() <= 0:
        full_message(chat_id)
        return

    app = create_application(user_id, username, for_friend=for_friend)
    ask_for_step(chat_id, app)


# ---------------- COMMANDS ----------------

@bot.message_handler(commands=["chatid"])
def chat_id_cmd(message):
    bot.reply_to(
        message,
        f"Chat ID: <code>{message.chat.id}</code>"
    )


@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    remember_user(message.from_user)
    sold_out = spots_left() <= 0

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
        f"💰 <b>Price:</b> {safe(PRICE)}\n\n"
        + (
            "😔 <b>SOLD OUT!</b> All tickets are gone. "
            "Follow our channels so you don't miss the next trip!"
            if sold_out else
            "Ready for the mountains? 🏔"
        )
    )

    markup = None if sold_out else ticket_button()

    poster_path = next(
        (p for p in POSTER_FILES if os.path.exists(p)),
        None
    )

    try:
        if poster_path:
            with open(poster_path, "rb") as poster:
                bot.send_photo(
                    user_id, poster, caption=caption, reply_markup=markup
                )
        else:
            bot.send_photo(
                user_id, PHOTO_URL, caption=caption, reply_markup=markup
            )
    except Exception:
        logger.exception("Could not send welcome photo")
        bot.send_message(user_id, caption, reply_markup=markup)


@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.chat.type != "private":
        return

    active = get_active_application(message.from_user.id)

    if not active:
        bot.send_message(
            message.chat.id,
            "You have no unfinished application. Send /start to begin.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return

    set_status(active["application_id"], "cancelled")
    bot.send_message(
        message.chat.id,
        "❎ Your unfinished application was cancelled. "
        "Send /start to begin again.",
        reply_markup=types.ReplyKeyboardRemove()
    )


@bot.message_handler(commands=["stop"])
def stop_cmd(message):
    if message.chat.type != "private":
        return

    remember_user(message.from_user)

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET unsubscribed = 1 WHERE user_id = ?",
            (message.from_user.id,)
        )

    bot.send_message(
        message.chat.id,
        "👌 Got it — no more reminders.\n"
        "If you change your mind, just send /start 🙂"
    )


@bot.message_handler(commands=["stats"])
def stats_cmd(message):
    if not is_admin_chat(message):
        return

    with get_db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) AS n
            FROM applications
            GROUP BY status
        """).fetchall()

        users = conn.execute("""
            SELECT
                COUNT(*) AS started,
                SUM(CASE WHEN reminder_stage > 0 THEN 1 ELSE 0 END) AS reminded,
                SUM(unsubscribed) AS stopped
            FROM users
        """).fetchone()

    counts = {row["status"]: row["n"] for row in rows}
    filling = sum(
        counts.get(s, 0)
        for s in ("collecting_fio", "collecting_phone", "collecting_university")
    )
    approved = counts.get("approved", 0) + SOLD_BEFORE_RESTART
    pending = counts.get("pending", 0)

    collected = f"{approved * PRICE_UZS:,}".replace(",", " ")

    text = (
        f"📊 <b>{safe(TRIP_NAME)} — STATS</b>\n\n"
        f"👀 Pressed /start: {users['started'] or 0}\n"
        f"🔔 Got at least one reminder: {users['reminded'] or 0}\n"
        f"🔕 Unsubscribed (/stop or blocked): {users['stopped'] or 0}\n\n"
        f"✍️ Filling the form: {filling}\n"
        f"⏳ Got payment details, no receipt: {counts.get('awaiting_receipt', 0)}\n"
        f"🔎 Receipts waiting for review: {pending}\n"
        f"✅ Approved: {approved}\n"
        f"❌ Rejected: {counts.get('rejected', 0)}\n"
        f"❎ Cancelled: {counts.get('cancelled', 0)}\n\n"
        f"💺 Spots left: {spots_left()} of {MAX_SPOTS}\n"
        f"💰 Collected (approved × price): {collected} UZS"
    )

    bot.reply_to(message, text)


# Who receives each broadcast command.
BROADCAST_AUDIENCES = {
    # Paid and approved participants (trip logistics). Always delivered.
    "broadcast": (
        "approved participants",
        """
        SELECT DISTINCT user_id
        FROM applications
        WHERE status = 'approved'
        """,
        False,
    ),
    # Everyone who ever pressed /start (except those who sent /stop).
    "broadcast_all": (
        "everyone who pressed /start",
        """
        SELECT user_id FROM users WHERE unsubscribed = 0
        UNION
        SELECT DISTINCT user_id FROM applications
        WHERE user_id NOT IN (SELECT user_id FROM users WHERE unsubscribed = 1)
        """,
        True,
    ),
    # Pressed /start but did not buy yet (except those who sent /stop).
    "broadcast_unpaid": (
        "people who have not bought a ticket yet",
        """
        SELECT user_id FROM users
        WHERE unsubscribed = 0
          AND user_id NOT IN (
              SELECT user_id FROM applications
              WHERE status IN ('pending', 'approved')
          )
        """,
        True,
    ),
}


@bot.message_handler(commands=list(BROADCAST_AUDIENCES))
def broadcast_cmd(message):
    if not is_admin_chat(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].lstrip("/").split("@")[0].lower()
    audience_name, query, with_button = BROADCAST_AUDIENCES[command]

    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            "📢 <b>Broadcast commands</b>\n\n"
            "<code>/broadcast text</code> — approved participants\n"
            "<code>/broadcast_all text</code> — everyone who pressed /start\n"
            "<code>/broadcast_unpaid text</code> — pressed /start but haven't bought\n\n"
            "Messages to everyone / unpaid get a «🎟 Get my ticket» button."
        )
        return

    text = (
        "📢 <b>Suqoq Forest Trip</b>\n\n"
        + safe(parts[1].strip())
    )

    with get_db() as conn:
        user_ids = [row["user_id"] for row in conn.execute(query).fetchall()]

    if not user_ids:
        bot.reply_to(message, f"Nobody to send to ({audience_name}).")
        return

    bot.reply_to(
        message,
        f"📤 Sending to {len(user_ids)} — {audience_name}..."
    )

    def worker():
        sent, failed = 0, 0
        for uid in user_ids:
            try:
                markup = None
                if with_button:
                    active = get_active_application(uid)
                    markup = ticket_button(
                        "💳 Finish my registration ➡️"
                        if active and active["status"] == "awaiting_receipt"
                        else "🎟 Get my ticket ➡️"
                    )
                    # Approved people in «all» don't need a buy button.
                    with get_db() as conn:
                        bought = conn.execute(
                            "SELECT 1 FROM applications WHERE user_id = ? "
                            "AND status IN ('pending', 'approved') LIMIT 1",
                            (uid,)
                        ).fetchone()
                    if bought:
                        markup = None

                bot.send_message(uid, text, reply_markup=markup)
                sent += 1
            except Exception:
                failed += 1
            time.sleep(0.05)

        try:
            bot.send_message(
                message.chat.id,
                f"✅ Broadcast finished ({audience_name}): "
                f"{sent} sent, {failed} failed."
            )
        except Exception:
            logger.exception("Could not report broadcast result")

    threading.Thread(target=worker, daemon=True).start()


# ---------------- BUTTONS (USER) ----------------

@bot.callback_query_handler(
    func=lambda call: call.data == "step_fio"
)
def continue_button(call):
    bot.answer_callback_query(call.id)

    if call.message.chat.type != "private":
        return

    start_or_resume(
        call.message.chat.id,
        call.from_user.id,
        call.from_user.username or "no_username"
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "buy_another"
)
def buy_another_ticket(call):
    bot.answer_callback_query(call.id)

    if call.message.chat.type != "private":
        return

    start_or_resume(
        call.message.chat.id,
        call.from_user.id,
        call.from_user.username or "no_username",
        for_friend=True
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("resend_")
)
def resend_receipt(call):
    application_id = call.data.split("_", 1)[1]
    app = get_application(application_id)

    if not app or app["user_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "Application not found.")
        return

    if not set_status(
        application_id,
        "awaiting_receipt",
        only_if="rejected",
        extra={"reminded": 1}
    ):
        bot.answer_callback_query(
            call.id,
            "This application can't take a new receipt.",
            show_alert=True
        )
        return

    bot.answer_callback_query(call.id)
    ask_for_step(call.message.chat.id, get_application(application_id))


# ---------------- PRIVATE MESSAGES (FORM + RECEIPT) ----------------

@bot.message_handler(
    func=lambda m: m.chat.type == "private",
    content_types=["text", "photo", "document", "contact"]
)
def private_router(message):
    if message.content_type == "text" and (message.text or "").startswith("/"):
        bot.send_message(
            message.chat.id,
            "Send /start to register or /cancel to cancel "
            "an unfinished application."
        )
        return

    app = get_active_application(message.from_user.id)

    if not app:
        bot.send_message(
            message.chat.id,
            "Send /start to register for the Suqoq Forest Trip 🌲"
        )
        return

    status = app["status"]

    if status == "collecting_fio":
        handle_fio(message, app)
    elif status == "collecting_phone":
        handle_phone(message, app)
    elif status == "collecting_university":
        handle_university(message, app)
    elif status == "awaiting_receipt":
        handle_receipt(message, app)


def handle_fio(message, app):
    fio = (message.text or "").strip() if message.content_type == "text" else ""

    if not fio:
        bot.send_message(message.chat.id, "Please enter the full name as text.")
        return

    if len(fio) > 150:
        bot.send_message(message.chat.id, "The name is too long. Please try again.")
        return

    update_application(app["application_id"], "fio", fio, "collecting_phone")
    ask_for_step(message.chat.id, get_application(app["application_id"]))


def handle_phone(message, app):
    if message.content_type == "contact" and message.contact:
        phone = message.contact.phone_number or ""
    elif message.content_type == "text":
        phone = (message.text or "").strip()
    else:
        phone = ""

    digits = "".join(ch for ch in phone if ch.isdigit())

    if len(digits) < 7 or len(digits) > 15 or len(phone) > 30:
        bot.send_message(
            message.chat.id,
            "Invalid phone number. Please try again, "
            "including the country code if possible."
        )
        return

    if not phone.startswith("+") and len(digits) >= 11:
        phone = "+" + digits

    update_application(app["application_id"], "phone", phone, "collecting_university")
    ask_for_step(message.chat.id, get_application(app["application_id"]))


def handle_university(message, app):
    university = (
        (message.text or "").strip() if message.content_type == "text" else ""
    )

    if not university:
        bot.send_message(message.chat.id, "Please enter the university as text.")
        return

    if len(university) > 200:
        bot.send_message(message.chat.id, "The name is too long. Please try again.")
        return

    if spots_left() <= 0:
        update_application(
            app["application_id"], "university", university, "cancelled"
        )
        full_message(message.chat.id)
        return

    update_application(
        app["application_id"], "university", university, "awaiting_receipt"
    )

    app = get_application(app["application_id"])
    notify_admin_lead(app)

    try:
        ask_for_step(message.chat.id, app)
    except Exception:
        logger.exception("Failed to send payment instructions")
        bot.send_message(
            message.chat.id,
            "Could not send payment details. "
            f"Please contact {safe(ADMIN_CONTACT)}."
        )


def handle_receipt(message, app):
    application_id = app["application_id"]
    file_id = None
    unique_id = None
    receipt_type = None

    if message.content_type == "photo":
        photo = message.photo[-1]
        file_id = photo.file_id
        unique_id = photo.file_unique_id
        receipt_type = "photo"

    elif message.content_type == "document":
        document = message.document
        mime_type = document.mime_type or ""

        if mime_type == "application/pdf" or mime_type.startswith("image/"):
            file_id = document.file_id
            unique_id = document.file_unique_id
            receipt_type = "document"

    if not file_id:
        bot.send_message(
            message.chat.id,
            "⚠️ Please send a <b>photo</b> or a <b>PDF</b> of the payment receipt.\n"
            "To start over, send /cancel."
        )
        return

    if not set_status(
        application_id,
        "pending",
        only_if="awaiting_receipt",
        extra={
            "receipt_file_id": file_id,
            "receipt_type": receipt_type,
            "receipt_unique_id": unique_id,
        }
    ):
        bot.send_message(
            message.chat.id,
            "This receipt has already been submitted "
            "or the application has changed."
        )
        return

    app = get_application(application_id)
    duplicate = find_duplicate_receipt(unique_id, application_id)

    duplicate_line = ""
    if duplicate:
        duplicate_line = (
            "\n\n🚨 <b>DUPLICATE RECEIPT!</b> The same file was already sent "
            f"in application <code>{safe(duplicate['application_id'])}</code> "
            f"({safe(duplicate['fio'])}). Check carefully before approving."
        )

    caption = (
        f"💳 <b>NEW PAYMENT FOR VERIFICATION — {safe(TRIP_NAME)}</b>\n\n"
        + application_card(app)
        + duplicate_line
    )

    try:
        if receipt_type == "photo":
            send_to_admin(
                bot.send_photo,
                file_id,
                caption=caption,
                reply_markup=decision_markup(application_id)
            )
        else:
            send_to_admin(
                bot.send_document,
                file_id,
                caption=caption,
                reply_markup=decision_markup(application_id)
            )

    except Exception:
        logger.exception("Failed to forward receipt to admin group")

        set_status(application_id, "awaiting_receipt", only_if="pending")

        bot.send_message(
            message.chat.id,
            "We couldn't forward your receipt for review. "
            "Please send it again in a minute."
        )
        return

    bot.send_message(
        message.chat.id,
        "✅ Your receipt has been submitted for review. "
        "Please wait for an administrator's decision."
    )


# ---------------- ADMIN DECISIONS ----------------

def edit_admin_caption(call, extra_text):
    try:
        old_caption = safe(call.message.caption or "")
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=old_caption + extra_text,
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception:
        logger.exception("Could not update admin receipt message")


def approver_name(call):
    return (
        f"@{call.from_user.username}"
        if call.from_user.username
        else call.from_user.first_name
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("app_")
)
def approve_application(call):
    application_id = call.data.split("_", 1)[1]
    app = get_application(application_id)

    if not app:
        bot.answer_callback_query(call.id, "Application not found.", show_alert=True)
        return

    if not set_status(application_id, "approved", only_if="pending"):
        bot.answer_callback_query(
            call.id,
            "This application has already been processed.",
            show_alert=True
        )
        return

    app = get_application(application_id)
    ticket_no = approved_count()

    thanks = (
        "🙏 <b>Thank you from the organizers — "
        "UWED Students' Union & DiploFrame!</b>\n"
        "We can't wait to see you in the mountains. 🏔"
    )

    if app["for_friend"]:
        user_text = (
            "🎉 <b>CONGRATULATIONS! The ticket is confirmed!</b>\n\n"
            f"🎟 Ticket for <b>{safe(app['fio'])}</b> — you're both in! 🌲\n\n"
            "📅 November 8 · ⏰ Meeting at 06:30\n\n"
            "Please send them this link to join the trip group:\n"
            f"{safe(INVITE_LINK)}\n\n"
            f"{thanks}"
        )
    else:
        user_text = (
            "🎉 <b>CONGRATULATIONS! Your ticket is confirmed!</b>\n\n"
            "Welcome to the Suqoq Forest Trip — you're officially in! 🌲\n\n"
            "📅 November 8 · ⏰ Meeting at 06:30\n\n"
            "Join our private group for trip updates and logistics "
            "(meeting point, what to bring, etc.):\n"
            f"{safe(INVITE_LINK)}\n\n"
            f"{thanks}\n\n"
            "🎟️ Want to bring a friend? Buy them a ticket below."
        )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "🎟️ Buy a ticket for someone else",
            callback_data="buy_another"
        )
    )

    try:
        bot.send_message(app["user_id"], user_text, reply_markup=markup)
    except Exception:
        logger.exception("Could not notify user %s", app["user_id"])
        set_status(application_id, "pending", only_if="approved")
        bot.answer_callback_query(
            call.id,
            "Could not notify the user. Try again.",
            show_alert=True
        )
        return

    approver = approver_name(call)

    if save_to_sheet(app, approver):
        sheet_line = "\n📋 Added to the participants sheet"
    else:
        sheet_line = "\n⚠️ NOT saved to the sheet — add manually"

    edit_admin_caption(
        call,
        f"\n\n🟢 <b>APPROVED</b> by {safe(approver)} · "
        f"ticket #{ticket_no} of {MAX_SPOTS}{sheet_line}"
    )

    bot.answer_callback_query(call.id, "Approved.")


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("rej_")
)
def reject_menu(call):
    application_id = call.data.split("_", 1)[1]

    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=reject_reasons_markup(application_id)
        )
    except Exception:
        logger.exception("Could not show reject reasons")

    bot.answer_callback_query(call.id, "Choose a reason")


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("back_")
)
def reject_back(call):
    application_id = call.data.split("_", 1)[1]

    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=decision_markup(application_id)
        )
    except Exception:
        logger.exception("Could not restore decision buttons")

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("rr_")
)
def reject_with_reason(call):
    try:
        _, code, application_id = call.data.split("_", 2)
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid action.")
        return

    reason = REJECT_REASONS.get(code, REJECT_REASONS["4"])
    app = get_application(application_id)

    if not app:
        bot.answer_callback_query(call.id, "Application not found.", show_alert=True)
        return

    if not set_status(
        application_id,
        "rejected",
        only_if="pending",
        extra={"reject_reason": reason}
    ):
        bot.answer_callback_query(
            call.id,
            "This application has already been processed.",
            show_alert=True
        )
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "📤 Send a new receipt",
            callback_data=f"resend_{application_id}"
        )
    )

    try:
        bot.send_message(
            app["user_id"],
            "❌ <b>Your payment could not be verified.</b>\n\n"
            f"<b>Reason:</b> {safe(reason)}\n\n"
            "You can send a new receipt with the button below.\n"
            f"Questions: contact Nazar {safe(ADMIN_CONTACT)}",
            reply_markup=markup
        )
    except Exception:
        logger.exception("Could not notify user %s", app["user_id"])
        set_status(application_id, "pending", only_if="rejected")
        bot.answer_callback_query(
            call.id,
            "Could not notify the user. Try again.",
            show_alert=True
        )
        return

    edit_admin_caption(
        call,
        f"\n\n🔴 <b>REJECTED</b> by {safe(approver_name(call))}\n"
        f"Reason: {safe(reason)}"
    )

    bot.answer_callback_query(call.id, "Rejected.")


# ---------------- REMINDERS ----------------

# One text per step of REMINDER_SCHEDULE_HOURS.
REMINDER_TEXTS = (
    "👀 <b>P.S. bro, you're coming to Suqoq, right?</b>\n\n"
    "November 8 — forest trails, mountain views, fresh air and the best "
    "crew from Tashkent universities 🏔\n\n"
    "Don't leave it for later — getting a ticket takes 2 minutes 👇",

    "🤫 <b>Has anyone told you yet that we throw really good events?</b>\n\n"
    "Ask anyone who's been on our trips 😉\n"
    "Suqoq Forest Trip on November 8: hiking, a photographer, games on "
    "the bus and lunch on us. You'll regret missing this one 😅\n\n"
    "Grab your ticket 👇",

    "🔥 <b>Psst... did you know another event is coming soon — "
    "one we haven't done in a long time?</b> 👀\n\n"
    "Our Suqoq crew will be the first to hear about it.\n"
    "Be part of the crew — see you in the mountains on November 8 🏔\n\n"
    "Last call 👇",
)

UNFINISHED_LINE = (
    "\n\n💳 <i>Your form is already filled in — just transfer the payment "
    "and send the receipt here.</i>"
)

STOP_LINE = "\n\n<i>Not interested? Send /stop and we won't remind you again.</i>"


def parse_utc(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def reminders_allowed_now():
    now = datetime.now(TASHKENT_TZ)

    if now.replace(tzinfo=None) >= REMINDERS_STOP_AT:
        return False

    quiet_from, quiet_to = QUIET_HOURS
    if now.hour >= quiet_from or now.hour < quiet_to:
        return False

    return spots_left() > 0


def send_due_reminders():
    """Sends the next reminder to everyone who is due. Returns how many."""
    if not reminders_allowed_now():
        return 0

    with get_db() as conn:
        users = conn.execute("""
            SELECT *
            FROM users
            WHERE unsubscribed = 0
              AND reminder_stage < ?
              AND user_id NOT IN (
                  SELECT user_id
                  FROM applications
                  WHERE status IN ('pending', 'approved')
              )
        """, (len(REMINDER_SCHEDULE_HOURS),)).fetchall()

    now = datetime.now(timezone.utc)
    sent = 0

    for user in users:
        stage = user["reminder_stage"]
        started = parse_utc(user["started_at"])
        last = parse_utc(user["last_reminder_at"])

        if not started:
            continue

        due_at = started + timedelta(hours=REMINDER_SCHEDULE_HOURS[stage])
        if now < due_at:
            continue

        # Never two reminders closer than 6 hours apart.
        if last and now - last < timedelta(hours=6):
            continue

        text = REMINDER_TEXTS[min(stage, len(REMINDER_TEXTS) - 1)]
        active = get_active_application(user["user_id"])

        if active and active["status"] == "awaiting_receipt":
            text += UNFINISHED_LINE
            button = ticket_button("💳 Finish my registration ➡️")
        elif active:
            button = ticket_button("✍️ Continue my registration ➡️")
        else:
            button = ticket_button()

        text += STOP_LINE
        unsubscribe = 0

        try:
            bot.send_message(user["user_id"], text, reply_markup=button)
            sent += 1
        except telebot.apihelper.ApiTelegramException as e:
            # 403 = the user blocked the bot: stop reminding them.
            if getattr(e, "error_code", None) == 403:
                unsubscribe = 1
            else:
                logger.warning("Reminder failed for %s: %s", user["user_id"], e)
        except Exception:
            logger.warning("Reminder failed for %s", user["user_id"])

        with get_db() as conn:
            conn.execute("""
                UPDATE users
                SET reminder_stage = reminder_stage + 1,
                    last_reminder_at = CURRENT_TIMESTAMP,
                    unsubscribed = MAX(unsubscribed, ?)
                WHERE user_id = ?
            """, (unsubscribe, user["user_id"]))

        time.sleep(0.05)

    if sent:
        logger.info("Sent %s reminders", sent)

    return sent


def reminder_loop():
    while True:
        time.sleep(900)

        try:
            send_due_reminders()
        except Exception:
            logger.exception("Reminder loop error")


# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()

    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()

    logger.info("Bot is starting...")

    bot.infinity_polling(
        skip_pending=False,
        timeout=30,
        long_polling_timeout=30
    )
