import os
import time
import sqlite3
import threading
from collections import defaultdict, deque
import telebot
from telebot import types
from flask import Flask

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'ВСТАВЬ_СЮДА_ТОКЕН_ДЛЯ_ЛОКАЛЬНОГО_ТЕСТА') 
bot = telebot.TeleBot(BOT_TOKEN)

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

# ==================== БАЗА ДАННЫХ ====================
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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except:
        return False

def apply_punishment(chat_id, user_id, hours, ptype):
    if ptype == 'ban':
        until = int(time.time()) + (hours * 3600) if hours > 0 else 0
        try:
            bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=until)
            return f"🔨 Забанен на {hours} ч." if hours > 0 else "🔨 Забанен навсегда."
        except Exception as e:
            return f"Ошибка бана: {e}"
            
    elif ptype == 'mute':
        until = int(time.time()) + (hours * 3600)
        perms = types.ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        )
        try:
            bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=perms, until_date=until)
            return f"🔇 Замучен на {hours} ч."
        except Exception as e:
            return f"Ошибка мута: {e}"
            
    else: # warn
        return "⚠️ Только предупреждение (мут не выдан)."

# ==================== КОМАНДЫ МОДЕРАЦИИ ====================
@bot.message_handler(commands=list(RULES.keys()))
def handle_rule_violation(message):
    chat_id = message.chat.id

    if not is_admin(chat_id, message.from_user.id):
        return

    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответьте на сообщение нарушителя этой командой.")
        return

    target_user = message.reply_to_message.from_user
    target_id = target_user.id

    if is_admin(chat_id, target_id):
        bot.reply_to(message, "❌ Нельзя наказать администратора.")
        return

    command = message.text.split()[0][1:].split('@')[0]
    if command not in RULES:
        return

    hours, ptype, reason = RULES[command]
    warn_count = add_warn(chat_id, target_id)
    action_text = apply_punishment(chat_id, target_id, hours, ptype)

    # УДАЛЕНИЕ СООБЩЕНИЯ УБРАНО (закомментировано)
    # try:
    #     bot.delete_message(chat_id, message.reply_to_message.message_id)
    # except:
    #     pass

    report = (f"👤 Нарушитель: {target_user.first_name}\n"
              f"📜 Правило: {reason}\n"
              f"⚖️ Наказание: {action_text}\n"
              f"⚠️ Варнов: {warn_count}")
    bot.reply_to(message, report)

# ==================== РАЗМУТ, РАЗБАН И СБРОС ВАРНОВ ====================
@bot.message_handler(commands=['unmute'])
def unmute_user(message):
    chat_id = message.chat.id
    if not is_admin(chat_id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответьте на сообщение пользователя.")
        return
    target_id = message.reply_to_message.from_user.id
    perms = types.ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
    try:
        bot.restrict_chat_member(chat_id=chat_id, user_id=target_id, permissions=perms)
        bot.reply_to(message, f"🔊 Пользователь размучен.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка размута: {e}")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    chat_id = message.chat.id
    if not is_admin(chat_id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответьте на сообщение пользователя.")
        return
    target_id = message.reply_to_message.from_user.id
    try:
        bot.unban_chat_member(chat_id=chat_id, user_id=target_id)
        bot.reply_to(message, f"🔓 Пользователь разбанен.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка разбана: {e}")

@bot.message_handler(commands=['reset_warns'])
def reset_warns(message):
    chat_id = message.chat.id
    if not is_admin(chat_id, message.from_user.id): return
    if not message.reply_to_message:
        bot.reply_to(message, "Ответьте на сообщение пользователя.")
        return
    target_id = message.reply_to_message.from_user.id
    cursor.execute('UPDATE warns SET count = 0 WHERE user_id = ? AND chat_id = ?', (target_id, chat_id))
    conn.commit()
    bot.reply_to(message, "✅ Варны сброшены.")

# ==================== АВТО-АНТИФЛУД ====================
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
        action_text = apply_punishment(chat_id, user_id, FLOOD_MUTE_HOURS, 'mute')

        try:
            bot.send_message(chat_id, f"🚨 Автофлуд\n👤 {message.from_user.first_name}\n⚖️ {action_text}\n⚠️ Варнов: {warn_count}")
        except: pass

# ==================== ЗАПУСК ДЛЯ RENDER ====================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

def run_bot():
    print("Запуск Telegram-бота...")
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)