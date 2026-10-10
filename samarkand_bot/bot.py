import http.server
import socketserver
import threading
import telebot
from telebot import types

# 1. Dummy HTTP server on port 10000 for Render Health Check
def run_dummy_server():
    port = 10000
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Dummy web server running on port {port}")
        httpd.serve_forever()

# Run the HTTP server in a background thread
threading.Thread(target=run_dummy_server, daemon=True).start()

# 2. Telegram Bot Configuration
TOKEN = '8952248333:AAGAKzBkfbALsyoeXt9CoGuLuKNRz4GLLLk'
bot = telebot.TeleBot(TOKEN)

ADMIN_GROUP_ID = -5102424349
SUPPORT_USERNAME = '@uwedsuadmin'
PRICE_TEXT = "299,000 UZS"
TRIP_DATE = "October 25"
CARD_DETAILS = "5614 6821 0314 3055 (BAXRAMOV UBAYDULLO)"

# Temporary storage for user details
user_data = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    btn_register = types.InlineKeyboardButton("📝 Register for the trip", callback_data='register')
    markup.add(btn_register)
    
    welcome_text = (
        f"Salam! Welcome to the official Samarkand Trip bot!\n\n"
        f"📅 **Trip Date:** {TRIP_DATE}\n"
        f"💰 **Price:** {PRICE_TEXT}\n"
        f"👥 **Group Capacity:** 100 people\n\n"
        f"Click the button below to start your registration."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'register')
def request_full_name(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Please enter your Full Name (First & Last Name):")
    bot.register_next_step_handler(msg, process_full_name)

def process_full_name(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'name': message.text}
    
    msg = bot.send_message(chat_id, "Please enter your phone number (e.g., +998901234567):")
    bot.register_next_step_handler(msg, process_phone)

def process_phone(message):
    chat_id = message.chat.id
    user_data[chat_id]['phone'] = message.text
    
    payment_text = (
        f"💳 **Payment Details:**\n\n"
        f"Recipient: {CARD_DETAILS}\n"
        f"Amount: {PRICE_TEXT}\n\n"
        f"Please complete the payment and reply with a **screenshot or photo of your receipt**."
    )
    msg = bot.send_message(chat_id, payment_text, parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_receipt)

def process_receipt(message):
    chat_id = message.chat.id
    if not message.photo and not message.document:
        msg = bot.send_message(chat_id, "⚠️ Please send an image or document of your payment receipt!")
        bot.register_next_step_handler(msg, process_receipt)
        return

    user_info = user_data.get(chat_id, {})
    name = user_info.get('name', 'Not provided')
    phone = user_info.get('phone', 'Not provided')
    
    username = f"@{message.from_user.username}" if message.from_user.username else "No username"

    # Forward receipt and info to Admin group
    caption = (
        f"🆕 **New Application for Samarkand!**\n\n"
        f"👤 **Name:** {name}\n"
        f"📞 **Phone:** {phone}\n"
        f"🔗 **Telegram:** {username}\n"
        f"🆔 **ID:** `{chat_id}`"
    )

    if message.photo:
        file_id = message.photo[-1].file_id
        bot.send_photo(ADMIN_GROUP_ID, file_id, caption=caption, parse_mode='Markdown')
    elif message.document:
        file_id = message.document.file_id
        bot.send_document(ADMIN_GROUP_ID, file_id, caption=caption, parse_mode='Markdown')

    success_text = (
        f"✅ **Thank you! Your registration and receipt have been received.**\n\n"
        f"An administrator will review your payment shortly.\n"
        f"Join the private trip chat here: [Click to join](https://t.me/+EZ9s8IqwbpMxZTcy)\n"
        f"For questions: {SUPPORT_USERNAME}"
    )
    bot.send_message(chat_id, success_text, parse_mode='Markdown', disable_web_page_preview=True)

# Start bot polling loop
if __name__ == '__main__':
    print("Bot is starting...")
    bot.infinity_polling()
