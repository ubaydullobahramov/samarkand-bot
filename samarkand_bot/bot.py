import http.server
import socketserver
import threading
import telebot
from telebot import types

# 1. Запуск фейкового веб-сервера на порту 10000 для прохождения Health Check на Render
def run_dummy_server():
    port = 10000
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Dummy web server running on port {port}")
        httpd.serve_forever()

# Запускаем сервер в фоновом потоке
threading.Thread(target=run_dummy_server, daemon=True).start()

# 2. Настройки Telegram бота
TOKEN = 'ТВОЙ_ТОКЕН_БОТА'  # Вставь сюда токен своего бота
bot = telebot.TeleBot(TOKEN)

ADMIN_GROUP_ID = -5102424349
SUPPORT_USERNAME = '@uwedsuadmin'
PRICE_TEXT = "299,000 UZS"
TRIP_DATE = "25 октября"
CARD_DETAILS = "5614 6821 0314 3055 (BAXRAMOV UBAYDULLO)"

# Словарь для временного сохранения данных пользователей
user_data = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    btn_register = types.InlineKeyboardButton("📝 Записаться на поездку", callback_data='register')
    markup.add(btn_register)
    
    welcome_text = (
        f" Salam! Добро пожаловать в официальный бот поездки в Самарканд!\n\n"
        f"📅 **Дата поездки:** {TRIP_DATE}\n"
        f"💰 **Стоимость:** {PRICE_TEXT}\n"
        f"👥 **Вместимость группы:** 100 человек\n\n"
        f"Нажмите кнопку ниже, чтобы пройти регистрацию."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'register')
def request_full_name(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "Введите ваши ФИО (Фамилия Имя Отчество):")
    bot.register_next_step_handler(msg, process_full_name)

def process_full_name(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'name': message.text}
    
    msg = bot.send_message(chat_id, "Введите ваш номер телефона (например, +998901234567):")
    bot.register_next_step_handler(msg, process_phone)

def process_phone(message):
    chat_id = message.chat.id
    user_data[chat_id]['phone'] = message.text
    
    payment_text = (
        f"💳 **Реквизиты для оплаты:**\n\n"
        f"Получатель: {CARD_DETAILS}\n"
        f"Сумма: {PRICE_TEXT}\n\n"
        f"Пожалуйста, совершите перевод и отправьте **скриншот или фото чека** ответным сообщением сюда."
    )
    msg = bot.send_message(chat_id, payment_text, parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_receipt)

def process_receipt(message):
    chat_id = message.chat.id
    if not message.photo and not message.document:
        msg = bot.send_message(chat_id, "⚠️ Пожалуйста, отправьте именно фото или документ чека об оплате!")
        bot.register_next_step_handler(msg, process_receipt)
        return

    user_info = user_data.get(chat_id, {})
    name = user_info.get('name', 'Не указано')
    phone = user_info.get('phone', 'Не указано')
    username = f"@{message.from_user.username}" if message.from_user.username 'Нет username'

    # Пересылаем чек и данные в админ-группу
    caption = (
        f"🆕 **Новая заявка на Самарканд!**\n\n"
        f"👤 **ФИО:** {name}\n"
        f"📞 **Телефон:** {phone}\n"
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
        f"✅ **Спасибо! Ваша заявка и чек приняты.**\n\n"
        f"Администратор скоро проверит платеж и свяжется с вами.\n"
        f"Ссылка на закрытую группу поездки: [Нажми сюда для входа](https://t.me/+EZ9s8IqwbpMxZTcy)\n"
        f"По всем вопросам: {SUPPORT_USERNAME}"
    )
    bot.send_message(chat_id, success_text, parse_mode='Markdown', disable_web_page_preview=True)

# Запуск бота в бесконечном цикле опроса
if __name__ == '__main__':
    print("Bot is starting...")
    bot.infinity_polling()
