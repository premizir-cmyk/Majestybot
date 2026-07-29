import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import threading
import time
import json
import os
from datetime import datetime

TOKEN = '8502077117:AAEHUXtn-7zExbSLk5LMGNSXnR9_1mjM2YA'
CHANNEL_ID = '@majestypublic'
BOT_USERNAME = '@Magestyper2_bot'
MY_USERNAME = '@premizir' # Твой юзернейм для связи

OWNER_ID = [7605961809, 685268569]

bot = telebot.TeleBot(TOKEN)
DB_FILE = 'users.json'
COOLDOWN_FILE = 'cooldowns.json'
NOTIFIED_FILE = 'notified.json'
HISTORY_FILE = 'history.json'
POSTS_FILE = 'active_posts.json'

COOLDOWN_TIME = 9000  # 2.5 часа (9000 сек)
AUTO_CLOSE_TIME = 7200  # 2 часа (7200 сек)

# ЧИСТЫЙ ШАБЛОН
TEMPLATE_TEXT = """🔥ГОРЯЧИЙ СЛОТ

❣️ Площадка: 
💵 Оплата: 
😀 Что нужно делать, От себя: """

RULES_TEXT = """⚠️ **ПРАВИЛА ПУБЛИКАЦИИ:**

1. **Строго по шаблону!** Бот проверяет ключевые слова.
2. **Кулдаун:** Между постами 2 часа 30 минут.
3. **Запрещено:** Скамерство, спам, флуд.

🚨 *За нарушение правил доступ аннулируется без возврата средств!*"""

def load_data(filename):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка чтения {filename}: {e}")
            return {} if not filename.endswith('history.json') else []
    return {} if not filename.endswith('history.json') else []

def save_data(filename, data):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка сохранения {filename}: {e}")

# ИСПРАВЛЕНО: Теперь правильная проверка элемента в списке
def is_owner(user_id):
    return user_id in OWNER_ID

def is_user_active(user_id):
    if is_owner(user_id):
        return True
    users = load_data(DB_FILE)
    str_id = str(user_id)
    if str_id in users:
        expire_time = users[str_id]
        if time.time() < expire_time:
            return True
        else:
            del users[str_id]
            save_data(DB_FILE, users)
    return False

def get_cooldown_left(user_id):
    if is_owner(user_id):
        return 0
    cooldowns = load_data(COOLDOWN_FILE)
    str_id = str(user_id)
    if str_id in cooldowns:
        last_post_time = cooldowns[str_id]
        elapsed = time.time() - last_post_time
        if elapsed < COOLDOWN_TIME:
            return int(COOLDOWN_TIME - elapsed)
    return 0

def set_cooldown(user_id):
    if is_owner(user_id):
        return
    cooldowns = load_data(COOLDOWN_FILE)
    cooldowns[str(user_id)] = time.time()
    save_data(COOLDOWN_FILE, cooldowns)

def reset_cooldown(user_id):
    cooldowns = load_data(COOLDOWN_FILE)
    str_id = str(user_id)
    if str_id in cooldowns:
        del cooldowns[str_id]
        save_data(COOLDOWN_FILE, cooldowns)

def save_to_history(user_id, username, text):
    history = load_data(HISTORY_FILE)
    if not isinstance(history, list):
        history = []
    
    post_entry = {
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "user_id": user_id,
        "username": username or "Без username",
        "text": text[:300]
    }
    
    history.append(post_entry)
    if len(history) > 50:
        history = history[-50:]
        
    save_data(HISTORY_FILE, history)

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} ч. {minutes} мин."
    else:
        return f"{minutes} мин."

def close_post_in_channel(message_id, original_text=None, reason=None):
    """Полное стирание информации и замена на плашку закрытия с рекламой бота"""
    
    CLOSED_CARD = (
        "🔒 **[СЛОТ ЗАКРЫТ]**\n\n"
        "━━━━━⬍━━━━━\n"
        "⛔ *Набор на этот слот завершён.*\n"
        "🔔 Включите уведомления в канале, чтобы не пропустить новые предложения!\n\n"
        f"🤖 *Хотите такого же бота в свой канал? Пишите разработчику:* {MY_USERNAME}"
    )

    try:
        # Пробуем заменить текст (для текстовых постов)
        bot.edit_message_text(
            text=CLOSED_CARD,
            chat_id=CHANNEL_ID,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=None
        )
        return True
    except Exception:
        # Для постов с картинкой/видео: удаляем и отправляем чистую карточку
        try:
            bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
            bot.send_message(
                chat_id=CHANNEL_ID,
                text=CLOSED_CARD,
                parse_mode="Markdown"
            )
            return True
        except Exception as e:
            print(f"Ошибка при удалении/очистке поста {message_id}: {e}")
            
            # Резервный вариант
            try:
                bot.edit_message_caption(
                    caption=CLOSED_CARD,
                    chat_id=CHANNEL_ID,
                    message_id=message_id,
                    parse_mode="Markdown",
                    reply_markup=None
                )
                return True
            except:
                return False

# ЕДИНЫЙ ФОНОВЫЙ ПОТОК ПРОВЕРКИ АВТОЗАКРЫТИЯ
def auto_close_checker():
    while True:
        try:
            posts_data = load_data(POSTS_FILE)
            now = time.time()
            changed = False
            
            for p_id, p_info in list(posts_data.items()):
                created_at = p_info.get("created_at", 0)
                if now - created_at >= AUTO_CLOSE_TIME:
                    msg_id = int(p_id)
                    close_post_in_channel(msg_id)
                    del posts_data[p_id]
                    changed = True
                    
            if changed:
                save_data(POSTS_FILE, posts_data)
                
        except Exception as e:
            print(f"Ошибка автозакрытия: {e}")
            
        time.sleep(30)

def check_expiring_subscriptions():
    while True:
        try:
            users = load_data(DB_FILE)
            notified = load_data(NOTIFIED_FILE)
            now = time.time()
            
            for u_id, exp_time in list(users.items()):
                time_left = exp_time - now
                if 0 < time_left <= 86400 and u_id not in notified:
                    try:
                        bot.send_message(
                            int(u_id), 
                            "⚠️ **Внимание!** Ваша подписка закончится через 24 часа."
                        )
                        notified[u_id] = True
                        save_data(NOTIFIED_FILE, notified)
                    except ApiTelegramException as e:
                        if e.error_code in [403, 400]:
                            notified[u_id] = True
                            save_data(NOTIFIED_FILE, notified)
                    except Exception as e:
                        print(f"Ошибка предупреждения {u_id}: {e}")
        except Exception as e:
            print(f"Ошибка проверки подписок: {e}")
            
        time.sleep(3600)

# ================= ГЛАВНОЕ МЕНЮ И КНОПКИ =================

def get_main_menu_keyboard(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(text="📋 Шаблон поста", callback_data="get_template"),
        types.InlineKeyboardButton(text="📖 Правила", callback_data="show_rules"),
        types.InlineKeyboardButton(text="⏳ Мой профиль / КД", callback_data="my_profile")
    )
    # Кнопка связи с тобой для покупки бота
    markup.add(types.InlineKeyboardButton(text="🤖 Хочу такого же бота себе", url=f"https://t.me/premizir"))
    
    if is_owner(user_id):
        markup.add(types.InlineKeyboardButton(text="🛠 Админ-панель", callback_data="open_admin_help"))
    return markup

def get_back_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu"))
    return markup

def get_admin_help_text():
    return (
        "🛠 **ПАНЕЛЬ УПРАВЛЕНИЯ ВЛАДЕЛЬЦА:**\n\n"
        "🟢 `/add ID ДНИ` — Выдать доступ пользователю\n"
        "🔴 `/del ID` — Забрать доступ у пользователя\n"
        "📋 `/list` — Список активных подписок\n"
        "⚡ `/uncd ID` — Сбросить кулдаун юзеру\n"
        "📜 `/history` — Посмотреть последние посты\n"
    )

# ================= КОМАНДЫ ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА =================

@bot.message_handler(commands=['adminhelp'])
def admin_help_cmd(message):
    if not is_owner(message.from_user.id):
        return
    bot.reply_to(message, get_admin_help_text(), parse_mode="Markdown")

@bot.message_handler(commands=['add'])
def add_user(message):
    if not is_owner(message.from_user.id):
        return
    try:
        args = message.text.split()
        target_id = str(args[1])
        days = int(args[2])
        users = load_data(DB_FILE)
        users[target_id] = time.time() + (days * 86400)
        save_data(DB_FILE, users)
        
        notified = load_data(NOTIFIED_FILE)
        if target_id in notified:
            del notified[target_id]
            save_data(NOTIFIED_FILE, notified)

        bot.reply_to(message, f"✅ Доступ для ID {target_id} выдан на {days} дней!")
        try:
            bot.send_message(int(target_id), f"🎉 **Вам выдан доступ на {days} дн.!**\n\nНажмите /start, чтобы начать.")
        except:
            pass
    except Exception:
        bot.reply_to(message, "❌ Формат: /add ID ДНИ")

@bot.message_handler(commands=['del'])
def del_user(message):
    if not is_owner(message.from_user.id):
        return
    try:
        target_id = str(message.text.split()[1])
        users = load_data(DB_FILE)
        if target_id in users:
            del users[target_id]
            save_data(DB_FILE, users)
            bot.reply_to(message, f"⛔ Доступ для ID {target_id} аннулирован.")
        else:
            bot.reply_to(message, "Юзер не найден в базе.")
    except:
        bot.reply_to(message, "Формат: /del ID")

@bot.message_handler(commands=['list'])
def list_users(message):
    if not is_owner(message.from_user.id):
        return
    users = load_data(DB_FILE)
    if not users:
        bot.reply_to(message, "Список платных подписок пуст.")
        return
    text = "📋 Активные подписки:\n\n"
    now = time.time()
    for u_id, exp_time in list(users.items()):
        left_days = round((exp_time - now) / 86400, 1)
        if left_days > 0:
            text += f"• ID: {u_id} — осталось {left_days} дн.\n"
        else:
            del users[u_id]
            save_data(DB_FILE, users)
    bot.reply_to(message, text)

@bot.message_handler(commands=['uncd', 'uncooldown'])
def reset_cooldown_command(message):
    if not is_owner(message.from_user.id):
        return
    try:
        target_id = str(message.text.split()[1])
        reset_cooldown(target_id)
        bot.reply_to(message, f"⚡ Кулдаун для ID `{target_id}` успешно сброшен!", parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, "❌ Формат: /uncd ID", parse_mode="Markdown")

@bot.message_handler(commands=['history'])
def show_history(message):
    if not is_owner(message.from_user.id):
        return
    history = load_data(HISTORY_FILE)
    if not history:
        bot.reply_to(message, "История постов пуста.")
        return
    text = "📜 **История последних публикаций:**\n\n"
    for item in history[-10:]:
        user_str = f"@{item['username']}" if item['username'] != "Без username" else f"ID {item['user_id']}"
        text += f"🕒 [{item['timestamp']}] {user_str}\n💬 {item['text'][:80]}...\n---\n"
    bot.reply_to(message, text)

# ================= КОМАНДА ЗАКРЫТИЯ ПОСТА ВРУЧНУЮ =================

@bot.message_handler(commands=['close'])
def close_user_post(message):
    user_id = message.from_user.id
    posts_data = load_data(POSTS_FILE)
    
    target_post_id = None
    
    if message.reply_to_message:
        for p_id, p_info in posts_data.items():
            if p_info.get("confirm_msg_id") == message.reply_to_message.message_id:
                target_post_id = p_id
                break
                
    if not target_post_id:
        args = message.text.split()
        if len(args) > 1 and args[1].isdigit():
            target_post_id = args[1]

    if not target_post_id:
        user_posts = [p_id for p_id, info in posts_data.items() if info.get("user_id") == user_id]
        if user_posts:
            target_post_id = user_posts[-1]

    if not target_post_id:
        bot.reply_to(message, "❌ Активный пост не найден.\nДля старых постов укажи ID сообщения: `/close ID`", parse_mode="Markdown")
        return

    if target_post_id in posts_data:
        post_info = posts_data[target_post_id]
        if post_info["user_id"] != user_id and not is_owner(user_id):
            bot.reply_to(message, "⛔ Вы не можете закрыть чужой пост.")
            return

        created_at = post_info.get("created_at", time.time())
        time_passed = time.time() - created_at

        close_post_in_channel(int(target_post_id))

        del posts_data[target_post_id]
        save_data(POSTS_FILE, posts_data)

        if time_passed <= 60:
            reset_cooldown(user_id)
            bot.reply_to(message, "✅ **Пост успешно закрыт!**\n⚡ Кулдаун сброшен!")
        else:
            bot.reply_to(message, "✅ **Пост успешно закрыт!**")
            
    else:
        if is_owner(user_id):
            res = close_post_in_channel(int(target_post_id))
            if res:
                bot.reply_to(message, f"✅ Старый пост `{target_post_id}` успешно закрыт в канале!", parse_mode="Markdown")
            else:
                bot.reply_to(message, f"❌ Не удалось закрыть пост `{target_post_id}`. Проверь ID.", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Пост не найден в списке активных.")

# ================= ОБРАБОТКА CALLBACKS =================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    
    if call.data == "main_menu":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="👋 Привет! Добро пожаловать в бот автопубликаций.\nВыберите нужный раздел ниже:",
            reply_markup=get_main_menu_keyboard(user_id)
        )

    elif call.data == "get_template":
        bot.answer_callback_query(call.id, "Шаблон готов к копированию!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"📋 **Нажми на текст ниже, чтобы скопировать его:**\n\n<code>{TEMPLATE_TEXT}</code>",
            parse_mode="HTML",
            reply_markup=get_back_keyboard()
        )

    elif call.data == "show_rules":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=RULES_TEXT,
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )

    elif call.data == "my_profile":
        bot.answer_callback_query(call.id)
        if is_owner(user_id):
            prof_text = "👑 У вас статус **Владельца** (без КД и без ограничений по подписке)."
        elif is_user_active(user_id):
            users = load_data(DB_FILE)
            left = round((users[str(user_id)] - time.time()) / 86400, 1)
            cd = get_cooldown_left(user_id)
            cd_str = format_time(cd) if cd > 0 else "Отсутствует (можно выкладывать пост)"
            prof_text = f"👤 **Ваш профиль:**\n\n• Подписка активна ещё: ~**{left} дн.**\n• Текущий кулдаун: **{cd_str}**"
        else:
            prof_text = "⛔ У вас нет активной подписки."

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=prof_text,
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )

    elif call.data == "open_admin_help":
        if is_owner(user_id):
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=get_admin_help_text(),
                parse_mode="Markdown",
                reply_markup=get_back_keyboard()
            )

# ================= ОБРАБОТКА СТАРТА =================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id

    if is_user_active(user_id):
        bot.reply_to(
            message, 
            "👋 Привет! Добро пожаловать в бот автопубликаций.\nВыберите нужный раздел ниже:", 
            reply_markup=get_main_menu_keyboard(user_id)
        )
    else:
        bot.reply_to(
            message, 
            f"⛔ У вас нет доступа к публикации.\nВаш Telegram ID: `{user_id}`\n\nДля покупки доступа напишите администратору.", 
            parse_mode="Markdown"
        )

# ================= ОБРАБОТКА И ПУБЛИКАЦИЯ ПОСТОВ =================

@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'animation'])
def handle_post(message):
    if message.text and message.text.startswith('/'):
        return

    user_id = message.from_user.id
    
    if not is_user_active(user_id):
        bot.reply_to(message, f"⛔ Публикация отклонена. Подписка истекла или не куплена.\nВаш ID: `{user_id}`", parse_mode="Markdown")
        return

    cooldown_left = get_cooldown_left(user_id)
    if cooldown_left > 0:
        time_str = format_time(cooldown_left)
        bot.reply_to(message, f"⏳ **Кулдаун!**\nВы сможете опубликовать следующий пост через **{time_str}**.")
        return

    post_text = message.text or message.caption or ""
    
    has_forbidden = "Писать строго сюда" in post_text
    has_required = ("🔥ГОРЯЧИЙ СЛОТ" in post_text) and ("Площадка:" in post_text) and ("Оплата:" in post_text)

    if has_forbidden or not has_required:
        bot.reply_to(
            message, 
            "❌ **Неправильный шаблон!**\n\n"
            "⚠️ *Убедитесь, что вы НЕ используете устаревшую строку 'Писать строго сюда'.*\n\n"
            "👇 **Скопируйте актуальный чистый шаблон ниже:**\n\n" + f"<code>{TEMPLATE_TEXT}</code>", 
            parse_mode="HTML"
        )
        return

    try:
        user = message.from_user
        username = user.username
        author_link = f"https://t.me/{username}" if username else f"tg://user?id={user_id}"

        published_msg = bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="Перейти к выполнению 💬", url=author_link))
        
        bot.edit_message_reply_markup(
            chat_id=CHANNEL_ID,
            message_id=published_msg.message_id,
            reply_markup=markup
        )

        set_cooldown(user_id)
        save_to_history(user_id, username, post_text)
        
        confirm_msg = bot.reply_to(
            message, 
            "🚀 **Пост успешно выложен в канал!**\n\n"
            "📌 **Как закрыть пост:**\n"
            "Ответьте командой `/close` прямо на это сообщение.\n\n"
            "⏱ *Если вы закроете пост в течение 1 минуты — кулдаун сбросится!*",
            parse_mode="Markdown"
        )

        posts_data = load_data(POSTS_FILE)
        posts_data[str(published_msg.message_id)] = {
            "user_id": user_id,
            "created_at": time.time(),
            "confirm_msg_id": confirm_msg.message_id,
            "original_text": post_text
        }
        save_data(POSTS_FILE, posts_data)

        # ИСПРАВЛЕНО: Теперь лог о новом посте отправляется обоим администраторам из списка
        if not is_owner(user_id):
            user_tag = f"@{username}" if username else f"ID {user_id}"
            admin_log = f"📩 **Новый пост в канале!**\n👤 Автор: {user_tag}\n📝 Текст:\n{post_text}"
            for admin_id in OWNER_ID:
                try:
                    bot.send_message(admin_id, admin_log, parse_mode="Markdown")
                except:
                    pass

    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка публикации: {e}")

# Запуск фоновых процессов
threading.Thread(target=auto_close_checker, daemon=True).start()
threading.Thread(target=check_expiring_subscriptions, daemon=True).start()

while True:
    try:
        print("Бот запущен и готов к работе...")
        bot.polling(none_stop=True, timeout=20, long_polling_timeout=20, skip_pending=True)
    except Exception as e:
        print(f"Ошибка сети: {e}. Переподключение...")
        time.sleep(3)
