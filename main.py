import os
import time
import sqlite3
import threading
from collections import defaultdict, deque
import telebot
from flask import Flask

# ==================== 1. НАСТРОЙКИ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '9146779687:AAHjOZITm8MsDp26jCXcSAyT1rPM6O1rx5I') 
bot = telebot.TeleBot(BOT_TOKEN)

# Словарь правил: (часы, тип_наказания, название)
RULES = {
    'spam':         (0,   'warn', 'Спам (п.1)'),
    'group_spam':   (2,   'mute', 'Групповой спам (п.1.3)'),
    'flood':        (1,   'mute', 'Флуд (п.2)'),
    '18plus':       (6,   'mute', 'Красота 18+ (п.3)'),
    'shock':        (6,   'mute', 'Шок-контент (п.4)'),
    'doxx':         (0,   'ban',  'Докс (п.5)'),
    'ad':           (12,  'mute', 'Реклама (п.6)'),
    'raid':         (0,   'ban',  'Рейд (п.8)'),
    'bypass':       (720, 'ban',  'Обход бана (п.9)'),
    'fake_rep':     (1,   'mute', 'Ложная репа (п.10)'),
    'conflict':     (5,   'mute', 'Конфликт (п.11)'),
    'begging':      (2,   'mute', 'Попрошайничество (п.12)'),
    'admin_crit':   (2,   'mute', 'Критика админов (п.14)'),
    'provocation':  (3,   'mute', 'Провокации (п.15)'),
}

FLOOD_LIMIT = 4
FLOOD_WINDOW = 5
FLOOD_MUTE_HOURS = 1

user_messages = defaultdict(lambda: deque(maxlen=20))
recently_punished = {}

# ==================== 2. БАЗА ДАННЫХ (ВАРНЫ) ====================
conn = sqlite3.connect('warns.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS warns 
                  (user_id INTEGER, chat_id INTEGER, count INTEGER, PRIMARY KEY (user_id, chat_id))''')
conn.commit()

def add_warn(chat_id, user_id):
    cursor.execute('INSERT OR IGNORE INTO warns (user_id, chat_id, count) VALUES (?, ?, 0)', (user_id, chat_id))
    cursor.execute('UPDATE warns SET count = count + 1 WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
    conn.commit()
    cursor.execute('SELECT count FROM warns WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
    return cursor.fetchone()[0]

# ==================== 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except:
        return False

def mention_user(user):
    return f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

def parse_time(time_str):
    time_str = time_str.lower()
    try:
        if time_str.endswith('d'): return int(time_str[:-1]) * 86400
        elif time_str.endswith('h'): return int(time_str[:-1]) * 3600
        elif time_str.endswith('m'): return int(time_str[:-1]) * 60
        else: return int(time_str) * 3600
    except: return 0

def format_duration(seconds):
    if seconds >= 86400: return f"{seconds // 86400} д."
    elif seconds >= 3600: return f"{seconds // 3600} ч."
    elif seconds >= 60: return f"{seconds // 60} мин."
    else: return f"{seconds} сек."

def apply_punishment(chat_id, user_id, seconds, ptype):
    if ptype == 'ban':
        until = int(time.time()) + seconds if seconds > 0 else 0
        try:
            bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=until)
            return f"🔨 Бан на {format_duration(seconds)}" if seconds > 0 else "🔨 Бан навсегда."
        except Exception as e: return f"Ошибка бана: {e}"
            
    elif ptype == 'mute':
        until = int(time.time()) + seconds
        try:
            # Тот самый безопасный синтаксис без ChatPermissions
            bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id, until_date=until, 
                can_send_messages=False, can_send_media_messages=False, 
                can_send_other_messages=False, can_add_web_page_previews=False
            )
            return f"🔇 Мут на {format_duration(seconds)}"
        except Exception as e: return f"Ошибка мута: {e}"
            
    else: return "⚠️ Предупреждение (без мута)"

# ==================== 4. МЕНЮ ПОМОЩИ (/help) ====================
@bot.message_handler(commands=['help', 'start'])
def send_help(message):
    text = (
        "🛡 <b>Панель управления ботом</b>\n\n"
        "Чтобы наказать пользователя, <b>ответьте</b> на его сообщение нужной командой:\n\n"
        "🔹 <b>Быстрые правила:</b>\n"
        "<code>/spam</code> — Спам\n"
        "<code>/flood</code> — Флуд\n"
        "<code>/18plus</code> — 18+\n"
        "<code>/shock</code> — Шок-контент\n"
        "<code>/doxx</code> — Докс (бан)\n"
        "<code>/ad</code> — Реклама\n"
        "<code>/raid</code> — Рейд (бан)\n"
        "<code>/bypass</code> — Обход (бан)\n"
        "<code>/admin_crit</code> — Критика админов\n"
        "<code>/provocation</code> — Провокации\n"
        "<i>(и другие правила по аналогии)</i>\n\n"
        "🔸 <b>Ручная выдача:</b>\n"
        "<code>/mute 2h</code> — Мут на время\n"
        "<code>/ban 1d</code> — Бан на время\n"
        "<code>/unmute</code> — Снять мут\n"
        "<code>/unban</code> — Разбанить\n\n"
        "⚠️ <b>Работа с варнами:</b>\n"
        "<code>/unwarn</code> — Снять 1 варн\n"
        "<code>/reset_warns</code> — Сбросить все варны"
    )
    bot.reply_to(message, text, parse_mode='HTML')

# ==================== 5. ОБРАБОТКА КОМАНД ПРАВИЛ ====================
@bot.message_handler(commands=list(RULES.keys()))
def handle_rule_violation(message):
    chat_id = message.chat.id
    admin_user = message.from_user

    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответьте на сообщение нарушителя этой командой.")
        return

    target_user = message.reply_to_message.from_user
    if is_admin(chat_id, target_user.id):
        bot.reply_to(message, "❌ Нельзя наказать администратора.")
        return

    command = message.text.split()[0][1:].split('@')[0]
    if command not in RULES: return

    hours, ptype, reason = RULES[command]
    warn_count = add_warn(chat_id, target_user.id)
    action_text = apply_punishment(chat_id, target_user.id, hours * 3600, ptype)

    report = (
        f"⚡️ <b>Решение модератора</b>\n\n"
        f"👤 <b>Нарушитель:</b> {mention_user(target_user)}\n"
        f"🛡 <b>Модератор:</b> {mention_user(admin_user)}\n"
        f"📜 <b>Причина:</b> {reason}\n"
        f"⚖️ <b>Наказание:</b> {action_text}\n"
        f"⚠️ <b>Варнов:</b> {warn_count}"
    )
    bot.reply_to(message, report, parse_mode='HTML')

# ==================== 6. РУЧНОЙ МУТ И БАН (/mute, /ban) ====================
@bot.message_handler(commands=['mute'])
def custom_mute(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответьте на сообщение.\nПример: <code>/mute 2h</code>", parse_mode='HTML')
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажите время: <code>/mute 2h</code>, <code>/mute 30m</code> или <code>/mute 1d</code>.", parse_mode='HTML')
        return
    
    seconds = parse_time(args[1])
    if seconds <= 0: return

    target_user = message.reply_to_message.from_user
    if is_admin(chat_id, target_user.id): return

    action_text = apply_punishment(chat_id, target_user.id, seconds, 'mute')
    report = (
        f"⚡️ <b>Ручной мут</b>\n\n"
        f"👤 <b>Нарушитель:</b> {mention_user(target_user)}\n"
        f"🛡 <b>Модератор:</b> {mention_user(admin_user)}\n"
        f"⚖️ <b>Наказание:</b> {action_text}"
    )
    bot.reply_to(message, report, parse_mode='HTML')

@bot.message_handler(commands=['ban'])
def custom_ban(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message: return
    
    args = message.text.split()
    seconds = 0 
    if len(args) >= 2:
        seconds = parse_time(args[1])
        if seconds <= 0: return

    target_user = message.reply_to_message.from_user
    if is_admin(chat_id, target_user.id): return

    action_text = apply_punishment(chat_id, target_user.id, seconds, 'ban')
    report = (
        f"⚡️ <b>Ручной бан</b>\n\n"
        f"👤 <b>Нарушитель:</b> {mention_user(target_user)}\n"
        f"🛡 <b>Модератор:</b> {mention_user(admin_user)}\n"
        f"⚖️ <b>Наказание:</b> {action_text}"
    )
    bot.reply_to(message, report, parse_mode='HTML')

# ==================== 7. СНЯТИЕ НАКАЗАНИЙ (/unmute, /unban, /unwarn) ====================
@bot.message_handler(commands=['unmute'])
def unmute_user(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message: return

    target_user = message.reply_to_message.from_user
    try:
        bot.restrict_chat_member(
            chat_id=chat_id, user_id=target_user.id, 
            can_send_messages=True, can_send_media_messages=True, 
            can_send_other_messages=True, can_add_web_page_previews=True
        )
        bot.reply_to(message, f"🔊 {mention_user(target_user)} был <b>размучен</b> модератором {mention_user(admin_user)}.", parse_mode='HTML')
    except Exception as e: bot.reply_to(message, f"Ошибка: {e}")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message: return

    target_user = message.reply_to_message.from_user
    try:
        bot.unban_chat_member(chat_id=chat_id, user_id=target_user.id)
        bot.reply_to(message, f"🔓 {mention_user(target_user)} был <b>разбанен</b> модератором {mention_user(admin_user)}.", parse_mode='HTML')
    except Exception as e: bot.reply_to(message, f"Ошибка: {e}")

@bot.message_handler(commands=['unwarn'])
def unwarn_user(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message: return

    target_user = message.reply_to_message.from_user
    cursor.execute('SELECT count FROM warns WHERE user_id = ? AND chat_id = ?', (target_user.id, chat_id))
    row = cursor.fetchone()
    
    if not row or row[0] <= 0:
        bot.reply_to(message, "У этого пользователя нет варнов.")
        return
        
    cursor.execute('UPDATE warns SET count = count - 1 WHERE user_id = ? AND chat_id = ?', (target_user.id, chat_id))
    conn.commit()
    bot.reply_to(message, f"✅ {mention_user(admin_user)} снял 1 варн с {mention_user(target_user)}.\nОсталось варнов: <b>{row[0] - 1}</b>", parse_mode='HTML')

@bot.message_handler(commands=['reset_warns'])
def reset_warns(message):
    chat_id = message.chat.id
    admin_user = message.from_user
    if not is_admin(chat_id, admin_user.id): return
    if not message.reply_to_message: return
    
    target_user = message.reply_to_message.from_user
    cursor.execute('UPDATE warns SET count = 0 WHERE user_id = ? AND chat_id = ?', (target_user.id, chat_id))
    conn.commit()
    bot.reply_to(message, f"🧹 {mention_user(admin_user)} полностью сбросил варны пользователя {mention_user(target_user)}.", parse_mode='HTML')

# ==================== 8. АВТО-АНТИФЛУД ====================
@bot.message_handler(content_types=['text', 'photo', 'video', 'sticker', 'document', 'voice', 'audio'])
def antiflood(message):
    if not message.from_user or message.from_user.is_bot: return
    chat_id = message.chat.id
    user_id = message.from_user.id
    if is_admin(chat_id, user_id): return

    now = time.time()
    key = (chat_id, user_id)
    history = user_messages[key]

    while history and now - history[0] > FLOOD_WINDOW:
        history.popleft()
    history.append(now)

    if len(history) >= FLOOD_LIMIT:
        last = recently_punished.get(key, 0)
        if now - last < 30: return
        recently_punished[key] = now
        history.clear()

        warn_count = add_warn(chat_id, user_id)
        action_text = apply_punishment(chat_id, user_id, FLOOD_MUTE_HOURS * 3600, 'mute')

        try:
            report = (
                f"🚨 <b>Авто-модерация (Антифлуд)</b>\n\n"
                f"👤 <b>Нарушитель:</b> {mention_user(message.from_user)}\n"
                f"⚖️ <b>Наказание:</b> {action_text}\n"
                f"⚠️ <b>Варнов:</b> {warn_count}"
            )
            bot.send_message(chat_id, report, parse_mode='HTML')
        except: pass

# ==================== 9. ЗАПУСК ДЛЯ RENDER (FLASK) ====================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

def run_bot():
    print("Запуск Telegram-бота...")
    time.sleep(3) 
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print(f"Ошибка polling: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)