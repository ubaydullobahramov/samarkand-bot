import telebot
from telebot import types

# ---------------- CONFIG ----------------
TOKEN = '8952248333:AAGAKzBkfbALsyoeXt9CoGuLuKNRz4GLLLk'  # Ваш токен
ADMIN_GROUP_ID = -5102424349                            # ID группы для лидов и чеков
INVITE_LINK = 'https://t.me/+EZ9s8IqwbpMxZTcy'         # Ссылка после оплаты
ADMIN_CONTACT = '@uwedsuadmin'                          # Контакт админа для связи

CARD_NUMBER = '5614 6821 0314 3055'
CARD_HOLDER = 'BAXRAMOV UBAYDULLO'
PRICE = '299,000 UZS'
# ----------------------------------------

bot = telebot.TeleBot(TOKEN)
user_data = {}

# 1. СТАРТ И ИНФО О ТРИПЕ
@bot.message_handler(commands=['start'])
def start_cmd(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'username': message.from_user.username or "no_username"}

    caption_text = (
        "🕌 **JOURNEY TO ANCIENT SAMARKAND!**\n\n"
        "Get ready to explore the heart of the Silk Road with us!\n\n"
        "**What’s included:**\n"
        "🚌 Comfortable transportation (there & back)\n"
        "📸 Professional photographer\n"
        "🍲 Delicious Zigir Osh from the center of Samarkand\n"
        "🎲 UNO & Mafia games\n"
        "👥 Great team & energetic vibe\n\n"
        "📅 **Date:** October 25\n"
        "💰 **Price:** 299,000 UZS\n"
        "⚡ **Spots:** Limited to 100 places only!\n\n"
        "Ready to travel back in time with us?"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Continue ➡️", callback_data="step_fio"))

    photo_url = "https://picsum.photos/800/500" 
    bot.send_photo(chat_id, photo_url, caption=caption_text, parse_mode="Markdown", reply_markup=markup)

# 2. ЗАПРОС ФИО
@bot.callback_query_handler(func=lambda call: call.data == "step_fio")
def ask_fio(call):
    chat_id = call.message.chat.id
    msg = bot.send_message(chat_id, "Please enter your **Full Name**:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, get_fio)

def get_fio(message):
    chat_id = message.chat.id
    user_data[chat_id]['fio'] = message.text

    # 3. ЗАПРОС ТЕЛЕФОНА
    msg = bot.send_message(chat_id, "Please enter your **phone number**:")
    bot.register_next_step_handler(msg, get_phone)

def get_phone(message):
    chat_id = message.chat.id
    user_data[chat_id]['phone'] = message.text

    # 4. ЗАПРОС УНИВЕРСИТЕТА
    msg = bot.send_message(chat_id, "Which **university** do you attend?")
    bot.register_next_step_handler(msg, get_university)

def get_university(message):
    chat_id = message.chat.id
    user_data[chat_id]['university'] = message.text

    # 5. ФОРМАТ ПОЕЗДКИ (ИНЛАЙН КНОПКИ)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("Solo", callback_data="comp_Solo"),
        types.InlineKeyboardButton("With a Friend", callback_data="comp_With a Friend"),
        types.InlineKeyboardButton("With a Partner", callback_data="comp_With a Partner")
    )
    bot.send_message(chat_id, "Are you traveling solo or coming with someone?", reply_markup=markup)

# 6. ФИКСАЦИЯ ЛИДА И ОТПРАВКА РЕКВИЗИТОВ
@bot.callback_query_handler(func=lambda call: call.data.startswith("comp_"))
def process_companion(call):
    chat_id = call.message.chat.id
    companion = call.data.split("comp_")[1]
    user_data[chat_id]['companion'] = companion

    # --- ОТПРАВКА ЛИДА В АДМИН-ГРУППУ ---
    lead_info = (
        "⚠️ **UNFINISHED APPLICATION (LEAD)**\n"
        "User reached the payment step but hasn't sent a receipt yet!\n\n"
        f"👤 **Name:** {user_data[chat_id].get('fio')}\n"
        f"📱 **Phone:** {user_data[chat_id].get('phone')}\n"
        f"🎓 **University:** {user_data[chat_id].get('university')}\n"
        f"👥 **Format:** {user_data[chat_id].get('companion')}\n"
        f"🔗 **TG:** @{user_data[chat_id].get('username')} (ID: `{chat_id}`)"
    )
    bot.send_message(ADMIN_GROUP_ID, lead_info, parse_mode="Markdown")

    # --- ОТПРАВКА РЕКВИЗИТОВ ПОЛЬЗОВАТЕЛЮ ---
    payment_info = (
        "💳 **Payment Details:**\n\n"
        f"**Card:** `{CARD_NUMBER}`\n"
        f"**Account Holder:** {CARD_HOLDER}\n"
        f"**Amount:** {PRICE}\n\n"
        "After transfer, **please send a screenshot/receipt of your payment here**."
    )
    msg = bot.send_message(chat_id, payment_info, parse_mode="Markdown")
    bot.register_next_step_handler(msg, get_receipt)

# 7. ПОЛУЧЕНИЕ СКРИНА ОПЛАТЫ
def get_receipt(message):
    chat_id = message.chat.id

    if message.content_type != 'photo':
        msg = bot.send_message(chat_id, "Please send a photo/screenshot of the payment receipt.")
        bot.register_next_step_handler(msg, get_receipt)
        return

    photo_id = message.photo[-1].file_id

    caption_admin = (
        "💳 **NEW PAYMENT FOR VERIFICATION (Samarkand Trip)!**\n\n"
        f"👤 **Name:** {user_data[chat_id].get('fio')}\n"
        f"📱 **Phone:** {user_data[chat_id].get('phone')}\n"
        f"🎓 **University:** {user_data[chat_id].get('university')}\n"
        f"👥 **Format:** {user_data[chat_id].get('companion')}\n"
        f"🔗 **TG:** @{user_data[chat_id].get('username')} (ID: `{chat_id}`)"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"app_{chat_id}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"rej_{chat_id}")
    )

    bot.send_photo(ADMIN_GROUP_ID, photo_id, caption=caption_admin, parse_mode="Markdown", reply_markup=markup)
    bot.send_message(chat_id, "Thank you! Your receipt has been submitted for review. Please wait for confirmation from an administrator.")

# 8. КНОПКИ АДМИНА (APPROVE / REJECT)
@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_', 'rej_')))
def process_admin_decision(call):
    action, user_id = call.data.split('_')
    user_id = int(user_id)

    if action == 'app':
        bot.send_message(
            user_id,
            f"🎉 **Payment Approved!**\n\nWelcome to the Samarkand trip! Join our private chat group for all trip updates and logistics:\n{INVITE_LINK}",
            parse_mode="Markdown"
        )
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=call.message.caption + "\n\n🟢 **APPROVED**"
        )
    elif action == 'rej':
        bot.send_message(
            user_id,
            f"❌ Unfortunately, your payment could not be verified. If you think this is an error, please contact the trip organizers: {ADMIN_CONTACT}"
        )
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=call.message.caption + "\n\n🔴 **REJECTED**"
        )

bot.infinity_polling()