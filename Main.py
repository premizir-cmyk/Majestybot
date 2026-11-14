import os
import json
import time
import html
import zipfile
import telebot
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from telebot import types

# --- ОСНОВНЫЕ НАСТРОЙКИ ПОД БОТА ---
# ВАЖНО: токен храни в переменной окружения BOT_TOKEN (на хостинге), а не в коде.
# Старый токен перевыпусти в @BotFather (/revoke), он был опубликован.
TOKEN = os.getenv('BOT_TOKEN') or 'PUT_NEW_TOKEN_HERE'

# Список каналов для публикаций
CHANNELS = [
    '-1003932211788'
]
PRIMARY_CHANNEL_DISPLAY = CHANNELS[0]

BOT_USERNAME = '@Qbodiq_bot'
MY_USERNAME = '@premizir'

# Владелец бота
OWNER_ID = [7605961809, 993847303]
MAIN_ADMIN_ID = OWNER_ID[0]

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=16)

# Смещение для Москвы (UTC+3)
MSK = timezone(timedelta(hours=3))

# --- ХРАНЕНИЕ ФАЙЛОВ ---
DATA_DIR = '/app/data' if os.path.exists('/app/data') else '.'
DB_FILE = os.path.join(DATA_DIR, 'users.json')
COOLDOWN_FILE = os.path.join(DATA_DIR, 'cooldowns.json')
PLATFORM_HISTORY_FILE = os.path.join(DATA_DIR, 'platform_history.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
POSTS_FILE = os.path.join(DATA_DIR, 'active_posts.json')
SCHEDULED_POSTS_FILE = os.path.join(DATA_DIR, 'scheduled_posts.json')
BAN_FILE = os.path.join(DATA_DIR, 'blacklist.json')
SLOT_COUNTER_FILE = os.path.join(DATA_DIR, 'slot_counter.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
NIGHT_MODE_FILE = os.path.join(DATA_DIR, 'night_mode.json')
STATS_FILE = os.path.join(DATA_DIR, 'stats.json')
USERNAMES_FILE = os.path.join(DATA_DIR, 'usernames.json')

# ПРАВИЛА И ПАРАМЕТРЫ
USER_COOLDOWN_TIME = 1800          # 30 минут кулдаун между постами одного юзера
SLOT_STEP_MINUTES = 15             # Шаг сетки 15 минут
MAX_AHEAD_HOURS = 2                # Максимум 2 часа вперед для брони
AUTO_CLOSE_TIME = 3600             # 1 час для автозакрытия поста
PLATFORM_COOLDOWN_SECONDS = 5400   # КД платформы: 1,5 часа между сменами одной платформы
REMINDER_BEFORE_SECONDS = 600      # напоминание за 10 минут до публикации брони
MISSED_GRACE_SECONDS = 600         # если бот пропустил бронь не более чем на 10 мин - публикуем сразу, иначе отменяем

file_lock = threading.Lock()
booking_lock = threading.RLock()   # защита броней и истории платформ
posts_lock = threading.RLock()     # защита активных постов
stats_lock = threading.Lock()
username_lock = threading.Lock()
user_creation_data = {}
admin_action_data = {}

RULES_TEXT = """⚠️ <b>ПРАВИЛА БИКИНИ БОТТОМ ЗАРАБОТОК:</b>

1. <b>Шаг сетки:</b> 15 минут. Бронь доступна максимум на 2 часа вперёд.
2. <b>Ночной режим:</b> С 00:00 до 10:00 по МСК Бикини Боттон спит, публикация смен недоступна. Последнее время брони вечером — 23:45.
3. <b>Кулдаун платформы:</b> Если на платформе забронирована смена, следующая смена на этой же платформе возможна только через 1,5 часа.
4. <b>Кулдаун сотрудника:</b> Между своими сменами необходимо выждать 30 минут.
5. <b>Обязательна подписка</b> на наш канал.

🚨 <i>За нарушение правил доступ аннулируется без возврата средств!</i>"""

ADMIN_PANEL_TEXT = (
    "🛠 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ВЛАДЕЛЬЦА:</b>\n\n"
    "Используйте кнопки ниже для быстрой выдачи прав и публикации смен по каналам, "
    "или введите команды вручную:\n\n"
    "🔴 <code>/del ID</code> — Забрать доступ\n"
    "⛔ <code>/ban ID</code> / <code>/unban ID</code> — Бан/Разбан\n"
    "👤 <code>/user ID</code> — Карточка пользователя\n"
    "📋 <code>/list</code> — Список подписок\n"
    "📊 <code>/stats</code> — Статистика\n"
    "📢 <code>/broadcast ТЕКСТ</code> — Рассылка\n"
    "⚡ <code>/uncd ID</code> — Сбросить КД\n"
    "📜 <code>/history</code> — История смен\n"
    "📦 <code>/backup</code> — Бэкап в .zip"
)

# --- ВСПОМОГАТЕЛЬНЫЕ ---

def esc(text):
    """Экранирование пользовательского текста для parse_mode=HTML."""
    return html.escape(str(text), quote=False)


def answer(call, text=None, alert=False):
    """Безопасный answer_callback_query (не падает при повторном ответе)."""
    try:
        bot.answer_callback_query(call.id, text, show_alert=alert) if text else bot.answer_callback_query(call.id)
    except Exception:
        pass


def notify_user(user_id, text, reply_markup=None):
    try:
        return bot.send_message(int(user_id), text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"[LOG] Не удалось написать пользователю {user_id}: {e}", flush=True)
        return None


# --- БАЗЫ ДАННЫХ ---

def _default_for(filename):
    return [] if filename.endswith(('history.json', 'blacklist.json', 'platform_history.json')) else {}


def load_data(filename):
    with file_lock:
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                print(f"[LOG] Файл {filename} повреждён: {e}. Сохраняю копию .corrupt", flush=True)
                try:
                    os.replace(filename, filename + '.corrupt')
                except Exception:
                    pass
                return _default_for(filename)
            except Exception as e:
                print(f"[LOG] Ошибка чтения {filename}: {e}", flush=True)
                return _default_for(filename)
        return _default_for(filename)


def save_data(filename, data):
    """Атомарная запись: сначала во временный файл, потом замена."""
    with file_lock:
        tmp_name = filename + '.tmp'
        try:
            with open(tmp_name, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, filename)
        except Exception as e:
            print(f"[LOG] Ошибка сохранения {filename}: {e}", flush=True)
            try:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)
            except Exception:
                pass


def get_next_slot_id():
    with booking_lock:
        counter_data = load_data(SLOT_COUNTER_FILE)
        if not isinstance(counter_data, dict):
            counter_data = {"count": 1000}
        current = counter_data.get("count", 1000) + 1
        counter_data["count"] = current
        save_data(SLOT_COUNTER_FILE, counter_data)
        return current


def is_banned(user_id):
    blacklist = load_data(BAN_FILE)
    return str(user_id) in blacklist or user_id in blacklist


def is_owner(user_id):
    return user_id in OWNER_ID


def is_night_time():
    now = datetime.now(MSK)
    return 0 <= now.hour < 10


# --- ЮЗЕРНЕЙМЫ ---

_loaded_names = load_data(USERNAMES_FILE)
username_cache = _loaded_names if isinstance(_loaded_names, dict) else {}


def remember_user(user):
    if not user:
        return
    uid = str(user.id)
    name = user.username or ""
    with username_lock:
        if username_cache.get(uid) != name:
            username_cache[uid] = name
            save_data(USERNAMES_FILE, username_cache)


def get_username(uid):
    """Возвращает '@name' или 'без username'. Если в кэше нет — пробует спросить у Telegram."""
    uid = str(uid)
    name = username_cache.get(uid)
    if not name:
        try:
            chat = bot.get_chat(int(uid))
            name = chat.username or ""
            with username_lock:
                username_cache[uid] = name
                save_data(USERNAMES_FILE, username_cache)
        except Exception:
            name = ""
    return f"@{name}" if name else "без username"


# --- ДОСТУП, КД ---

def check_channel_subscription(user_id):
    if is_owner(user_id):
        return True
    for ch in CHANNELS:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['creator', 'administrator', 'member']:
                return False
        except Exception:
            pass
    return True


def is_user_active(user_id, channel_id=None):
    if is_owner(user_id):
        return True
    if is_banned(user_id):
        return False
    users = load_data(DB_FILE)
    str_id = str(user_id)
    if str_id in users:
        data = users[str_id]
        if isinstance(data, dict):
            allowed_ch = data.get("channel", "all")
            if channel_id and allowed_ch != "all" and str(allowed_ch) != str(channel_id):
                return False
            exp_time = data.get("expire", 0)
            posts_left = data.get("posts", 0)
            if (exp_time > 0 and time.time() < exp_time) or posts_left > 0:
                return True
        elif isinstance(data, (int, float)):
            if time.time() < data:
                return True
    return False


def consume_post_credit(user_id):
    if is_owner(user_id):
        return
    users = load_data(DB_FILE)
    str_id = str(user_id)
    if str_id in users and isinstance(users[str_id], dict):
        if users[str_id].get("posts", 0) > 0:
            users[str_id]["posts"] -= 1
            save_data(DB_FILE, users)


def get_cooldown_left(user_id):
    if is_owner(user_id):
        return 0
    cooldowns = load_data(COOLDOWN_FILE)
    str_id = str(user_id)
    if str_id in cooldowns:
        elapsed = time.time() - cooldowns[str_id]
        if elapsed < USER_COOLDOWN_TIME:
            return int(USER_COOLDOWN_TIME - elapsed)
    return 0


def set_cooldown(user_id):
    if is_owner(user_id):
        return
    cooldowns = load_data(COOLDOWN_FILE)
    cooldowns[str(user_id)] = time.time()
    save_data(COOLDOWN_FILE, cooldowns)


def normalize_platform_name(platform_text):
    return " ".join(str(platform_text).split()).capitalize()


def save_to_history(user_id, username, text):
    history = load_data(HISTORY_FILE)
    if not isinstance(history, list):
        history = []
    history.append({
        "timestamp": datetime.now(MSK).strftime("%d.%m.%Y %H:%M"),
        "user_id": user_id,
        "username": username or "Без username",
        "text": text
    })
    save_data(HISTORY_FILE, history[-100:])


def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} ч. {minutes} мин." if hours > 0 else f"{minutes} мин."


def fmt_hm(ts):
    return datetime.fromtimestamp(float(ts), MSK).strftime('%H:%M')


# --- СТАТИСТИКА ---

def update_stats(user_id, username, platform):
    with stats_lock:
        stats = load_data(STATS_FILE)
        if not isinstance(stats, dict):
            stats = {}
        stats.setdefault("platforms", {})
        stats.setdefault("users", {})
        pname = normalize_platform_name(platform)
        stats["platforms"][pname] = stats["platforms"].get(pname, 0) + 1
        u = stats["users"].setdefault(str(user_id), {"count": 0, "username": ""})
        u["count"] += 1
        if username:
            u["username"] = username
        save_data(STATS_FILE, stats)


# --- ТЕКСТ СМЕНЫ ---

def build_shift_text(platform, payment, desc):
    """Описание задачи оформляется цитатой (blockquote). Весь пользовательский текст экранируется."""
    return (
        f"🍔 Внимание, повара Бикини‑Боттома! Новый заказ на кухне «Красти Краб»!\n"
        f"📌 Платформа: {esc(platform)}\n"
        f"💰 Награда: {esc(payment)} ₽\n"
        f"📝 Описание: Задача легче, чем приготовить крабсбургер по секретному рецепту. Справится каждый!\n"
        f"💬 <b>Суть задачи:</b>\n"
        f"<blockquote>{esc(desc)}</blockquote>"
    )


def build_direct_url(username, user_id, platform, payment):
    auto_msg = f"Здравствуйте! Хочу выйти на смену: {platform} за {payment} руб!"
    encoded_text = urllib.parse.quote(auto_msg)
    return f"https://t.me/{username}?text={encoded_text}" if username else f"tg://user?id={user_id}"


def publish_shift(user_id, username, final_text, platform, payment, target_channels):
    direct_url = build_direct_url(username, user_id, platform, payment)
    clean_bot_username = BOT_USERNAME.replace('@', '')
    published_message_ids = {}

    for ch_id in target_channels:
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(text="👨‍🍳 Взяться за приготовление", url=direct_url),
            types.InlineKeyboardButton(text="📜 Рецепт крабсбургера (обучение)", url="https://t.me/qbodiqob"),
            types.InlineKeyboardButton(text="🚫 У меня спам-блок", url=f"https://t.me/{clean_bot_username}?start=spamblock_{user_id}"),
            types.InlineKeyboardButton(text="🍔 Пожаловаться на невкусный крабсбургер", callback_data=f"complain_{user_id}")
        )
        published_msg = bot.send_message(ch_id, final_text, parse_mode="HTML", reply_markup=markup)
        published_message_ids[str(ch_id)] = published_msg.message_id
    return published_message_ids


def register_active_post(slot_num, user_id, message_ids, platform, payment, confirm_msg_id=None):
    with posts_lock:
        posts_data = load_data(POSTS_FILE)
        if not isinstance(posts_data, dict):
            posts_data = {}
        entry = {
            "user_id": user_id,
            "created_at": time.time(),
            "message_ids": message_ids,
            "platform": platform,
            "payment": payment,
            "slot_num": f"СМЕНА-{slot_num}"
        }
        if confirm_msg_id:
            entry["confirm_msg_id"] = confirm_msg_id
        posts_data[str(slot_num)] = entry
        save_data(POSTS_FILE, posts_data)


def close_posts_in_channels(message_ids_dict):
    CLOSED_CARD = (
        "🔒 <b>Смена закрыта, повара!</b>\n"
        "━━━━━⬍━━━━━\n"
        "🍍 <b>Следующий заказ уже на подходе — включи уведомления в канале, чтобы не пропустить новую смену и срубить баксов.</b>\n"
        f"🤖 <i>Хотите такого же бота в свой канал? Пишите разработчику:</i> {MY_USERNAME}"
    )

    closed_markup = types.InlineKeyboardMarkup(row_width=1)
    closed_markup.add(types.InlineKeyboardButton(text="📜 Рецепт крабсбургера (обучение)", url="https://t.me/qbodiqob"))

    for ch_id, msg_id in message_ids_dict.items():
        try:
            channel_id = int(ch_id)
            message_id = int(msg_id)

            bot.edit_message_text(
                text=CLOSED_CARD,
                chat_id=channel_id,
                message_id=message_id,
                parse_mode="HTML",
                reply_markup=closed_markup
            )
            print(f"[LOG] ✅ Смена {message_id} в канале {channel_id} успешно закрыта.", flush=True)
        except Exception as e:
            print(f"[LOG] ❌ Не удалось отредактировать смену {msg_id} в {ch_id}: {e}", flush=True)
            try:
                bot.delete_message(chat_id=int(ch_id), message_id=int(msg_id))
                bot.send_message(chat_id=int(ch_id), text=CLOSED_CARD, parse_mode="HTML", reply_markup=closed_markup)
                print(f"[LOG] ✅ Смена {msg_id} пересоздана как закрытая.", flush=True)
            except Exception as e2:
                print(f"[LOG] 🚨 Критическая ошибка закрытия в {ch_id}: {e2}", flush=True)


# --- БРОНИ, КД ПЛАТФОРМЫ ---

def load_platform_history():
    history = load_data(PLATFORM_HISTORY_FILE)
    if not isinstance(history, list):
        history = []
    cutoff = time.time() - 6 * 3600
    return [h for h in history if isinstance(h, dict) and h.get("ts", 0) > cutoff]


def find_platform_conflict(norm_platform, slot_ts, history):
    """Возвращает время (ts) ближайшей конфликтующей брони этой же платформы или None.
    Конфликт: между сменами одной платформы меньше PLATFORM_COOLDOWN_SECONDS (в обе стороны)."""
    conflict = None
    for h in history:
        if h.get("platform") == norm_platform and abs(h.get("ts", 0) - slot_ts) < PLATFORM_COOLDOWN_SECONDS:
            if conflict is None or h["ts"] > conflict:
                conflict = h["ts"]
    return conflict


def get_user_earliest(user_id, scheduled_data):
    if is_owner(user_id):
        return 0
    cd_left = get_cooldown_left(user_id)
    latest_booking = 0
    for ts_str, p_info in scheduled_data.items():
        if p_info.get("user_id") == user_id:
            latest_booking = max(latest_booking, float(ts_str))
    booking_cd_expire = latest_booking + USER_COOLDOWN_TIME if latest_booking > 0 else 0
    return max(time.time() + cd_left, booking_cd_expire)


def is_night_slot(slot_dt):
    return slot_dt.hour < 10 or (slot_dt.hour == 23 and slot_dt.minute > 45)


def validate_slot(user_id, platform, slot_ts, scheduled_data, history, user_earliest=None):
    """Единая проверка слота (для клавиатуры и для самой брони).
    Возвращает (ok, code, extra)."""
    now_ts = time.time()
    if slot_ts <= now_ts:
        return False, 'past', None
    slot_dt = datetime.fromtimestamp(slot_ts, MSK)
    if is_night_slot(slot_dt):
        return False, 'night', None
    if slot_ts > now_ts + MAX_AHEAD_HOURS * 3600 + 60:
        return False, 'too_far', None

    key = str(int(slot_ts))
    if key in scheduled_data:
        return False, 'busy', scheduled_data[key].get("platform", "")

    conflict = find_platform_conflict(normalize_platform_name(platform), slot_ts, history)
    if conflict is not None:
        return False, 'platform', conflict

    if user_earliest is None:
        user_earliest = get_user_earliest(user_id, scheduled_data)
    if slot_ts < user_earliest:
        return False, 'user', None
    return True, 'ok', None


def slot_error_text(code, extra, platform, slot_ts=None):
    if code == 'busy':
        return "❌ Этот временной слот уже занят другим поваром!"
    if code == 'platform':
        pname = str(platform)[:40]
        free_from = fmt_hm(extra + PLATFORM_COOLDOWN_SECONDS)
        if slot_ts is not None and slot_ts < extra:
            # выбранный слот раньше существующей брони: смены оказались бы слишком близко
            free_until = fmt_hm(extra - PLATFORM_COOLDOWN_SECONDS)
            return (f"⏳ {pname} уже забронирована на {fmt_hm(extra)}. "
                    f"Между сменами одной платформы должно пройти 1 ч 30 мин, этот слот слишком близко. "
                    f"Выберите время не позже {free_until} или с {free_from}.")
        return (f"⏳ {pname} уже забронирована на {fmt_hm(extra)}. "
                f"Следующая смена на этой платформе будет доступна с {free_from}.")
    if code == 'user':
        return "⏳ Этот слот попадает под ваш персональный кулдаун (или уже имеющуюся бронь)!"
    if code == 'past':
        return "⌛ Это время уже прошло. Выберите другой слот."
    if code == 'night':
        return "🌙 В это время Бикини Боттон спит."
    if code == 'too_far':
        return "⏳ Бронь доступна максимум на 2 часа вперёд."
    return "⚠️ Этот слот недоступен."


def remove_booking(ts_str, drop_history=True):
    """Удаляет бронь. drop_history=True — снять и КД платформы (отмена), False — оставить (смена опубликована)."""
    with booking_lock:
        scheduled_data = load_data(SCHEDULED_POSTS_FILE)
        if not isinstance(scheduled_data, dict):
            scheduled_data = {}
        info = scheduled_data.pop(ts_str, None)
        if info is not None:
            save_data(SCHEDULED_POSTS_FILE, scheduled_data)
            if drop_history:
                history = load_platform_history()
                slot_ts = float(ts_str)
                new_history = [h for h in history
                               if not (abs(h.get("ts", 0) - slot_ts) < 1 and h.get("user_id") == info.get("user_id"))]
                if len(new_history) != len(history):
                    save_data(PLATFORM_HISTORY_FILE, new_history)
        return info


def update_booking(ts_str, **fields):
    with booking_lock:
        scheduled_data = load_data(SCHEDULED_POSTS_FILE)
        if isinstance(scheduled_data, dict) and ts_str in scheduled_data:
            scheduled_data[ts_str].update(fields)
            save_data(SCHEDULED_POSTS_FILE, scheduled_data)


# --- ГЕНЕРАЦИЯ СЛОТОВ ---
def get_available_slots_keyboard(user_id, platform_name):
    now = datetime.now(MSK)

    scheduled_data = load_data(SCHEDULED_POSTS_FILE)
    if not isinstance(scheduled_data, dict):
        scheduled_data = {}
    history = load_platform_history()
    user_earliest = get_user_earliest(user_id, scheduled_data)

    rem = now.minute % SLOT_STEP_MINUTES
    add_min = SLOT_STEP_MINUTES - rem if rem != 0 else SLOT_STEP_MINUTES
    start_dt = now + timedelta(minutes=add_min)
    start_dt = start_dt.replace(second=0, microsecond=0)

    markup = types.InlineKeyboardMarkup(row_width=2)
    slot_dt = start_dt
    slots_count = 0
    max_end_time = now + timedelta(hours=MAX_AHEAD_HOURS)

    while slot_dt <= max_end_time and slots_count < 8:
        slot_ts = float(int(slot_dt.timestamp()))
        slot_str = slot_dt.strftime("%H:%M")

        ok, code, extra = validate_slot(user_id, platform_name, slot_ts, scheduled_data, history, user_earliest)
        if code == 'night':
            break

        if ok:
            btn_text = f"🟢 {slot_str}"
            callback_data = f"book_slot_{int(slot_ts)}"
            slots_count += 1
        elif code == 'busy':
            btn_text = f"❌ {slot_str} ({extra})"
            callback_data = "slot_busy"
        elif code == 'platform':
            btn_text = f"⏳ {slot_str} (КД платф.)"
            # в callback кладём и время брони-конфликта, и время самого слота
            callback_data = f"slot_pcd_{int(extra)}_{int(slot_ts)}"
        else:
            btn_text = f"⏳ {slot_str} (Ваш КД)"
            callback_data = "slot_user_cooldown"

        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=callback_data))
        slot_dt += timedelta(minutes=SLOT_STEP_MINUTES)

    markup.add(types.InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_slots"))
    markup.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_publish"))
    return markup


# --- ФОНОВЫЕ ПОТОКИ ---

def publish_scheduled(ts_str, p_info):
    user_id = p_info['user_id']
    username = p_info.get('username')
    final_text = p_info['final_text']
    slot_num = p_info['slot_num']
    platform = p_info['platform']
    payment = p_info['payment']
    target_channels = p_info.get('channels', CHANNELS)

    published_ids = None
    try:
        published_ids = publish_shift(user_id, username, final_text, platform, payment, target_channels)
    except Exception as e:
        print(f"[LOG] Ошибка автоотправки: {e}", flush=True)

    if published_ids is None:
        remove_booking(ts_str, drop_history=True)
        notify_user(user_id, f"❌ Не удалось опубликовать вашу смену #{slot_num} ({esc(platform)}). Бронь снята, попробуйте ещё раз.")
        return

    # сначала убираем бронь, чтобы при любой ошибке ниже смена не опубликовалась дважды
    remove_booking(ts_str, drop_history=False)

    try:
        consume_post_credit(user_id)
        set_cooldown(user_id)
        save_to_history(user_id, username, final_text)
        update_stats(user_id, username, platform)

        confirm_markup = types.InlineKeyboardMarkup(row_width=2)
        confirm_markup.add(
            types.InlineKeyboardButton(text="🍔 Новый заказ", callback_data="start_create_post"),
            types.InlineKeyboardButton(text="📱 Главное меню", callback_data="main_menu")
        )
        confirm_msg = notify_user(
            user_id,
            f"🚀 <b>Ваша забронированная смена #{slot_num} успешно опубликована!</b>",
            reply_markup=confirm_markup
        )
        register_active_post(slot_num, user_id, published_ids, platform, payment,
                             confirm_msg.message_id if confirm_msg else None)
        print(f"[LOG] Смена #{slot_num} опубликована.", flush=True)
    except Exception as e:
        print(f"[LOG] Ошибка после публикации смены #{slot_num}: {e}", flush=True)
        # даже если что-то упало, пост нужно уметь автозакрыть
        try:
            register_active_post(slot_num, user_id, published_ids, platform, payment)
        except Exception:
            pass


def cancel_missed_booking(ts_str, p_info):
    info = remove_booking(ts_str, drop_history=True)
    if info is None:
        return
    notify_user(
        info['user_id'],
        f"⚠️ Бот был недоступен, и ваша бронь смены #{info['slot_num']} на <code>{fmt_hm(ts_str)} МСК</code> "
        f"({esc(info['platform'])}) не была опубликована вовремя. Бронь снята, смена не списана — забронируйте заново."
    )
    print(f"[LOG] Пропущенная бронь #{info['slot_num']} отменена.", flush=True)


def scheduled_posts_checker():
    while True:
        try:
            if is_night_time():
                time.sleep(30)
                continue

            scheduled_data = load_data(SCHEDULED_POSTS_FILE)
            if isinstance(scheduled_data, dict) and scheduled_data:
                now_ts = time.time()
                for ts_str, p_info in sorted(scheduled_data.items(), key=lambda x: float(x[0])):
                    try:
                        slot_ts = float(ts_str)

                        # напоминание за 10 минут
                        if (not p_info.get('reminded')
                                and now_ts < slot_ts <= now_ts + REMINDER_BEFORE_SECONDS):
                            update_booking(ts_str, reminded=True)
                            mins = max(1, round((slot_ts - now_ts) / 60))
                            notify_user(
                                p_info['user_id'],
                                f"⏰ <b>Напоминание!</b> Ваша смена #{p_info['slot_num']} ({esc(p_info['platform'])}) "
                                f"будет опубликована в <code>{fmt_hm(slot_ts)} МСК</code> — через ~{mins} мин."
                            )

                        if now_ts >= slot_ts:
                            if now_ts - slot_ts > MISSED_GRACE_SECONDS:
                                cancel_missed_booking(ts_str, p_info)
                            else:
                                publish_scheduled(ts_str, p_info)
                    except Exception as e:
                        print(f"[LOG] Ошибка обработки брони {ts_str}: {e}", flush=True)
        except Exception as e:
            print(f"[LOG] Ошибка потока расписания: {e}", flush=True)
        time.sleep(1)


def auto_close_checker():
    print("[LOG] 🔄 Поток автозакрытия смен запущен (интервал 1 час)!", flush=True)
    while True:
        try:
            expired = []
            with posts_lock:
                posts_data = load_data(POSTS_FILE)
                if isinstance(posts_data, dict) and posts_data:
                    now = time.time()
                    for p_key, p_info in list(posts_data.items()):
                        created_at = p_info.get("created_at", 0)
                        if now - created_at >= AUTO_CLOSE_TIME:
                            expired.append((p_key, p_info.get("message_ids", {})))
                            del posts_data[p_key]
                    if expired:
                        save_data(POSTS_FILE, posts_data)
            for p_key, msg_dict in expired:
                if msg_dict:
                    print(f"[LOG] Время вышло (1 час) для смены {p_key}. Закрываем...", flush=True)
                    close_posts_in_channels(msg_dict)
        except Exception as e:
            print(f"[LOG] Ошибка в фоновом цикле автозакрытия: {e}", flush=True)
        time.sleep(15)


def night_mode_scheduler():
    print("[LOG] 🌙 Поток ночного режима запущен!", flush=True)
    last_sent_date = ""
    while True:
        try:
            now = datetime.now(MSK)
            current_date_str = now.strftime("%Y-%m-%d")

            if now.hour == 0 and now.minute == 0:
                if last_sent_date != current_date_str + "_night":
                    night_text = "💤 <b>Бикини Боттон уходит спать до 10:00 утра.</b> Публикация смен приостановлена."
                    for ch in CHANNELS:
                        try:
                            bot.send_message(ch, night_text, parse_mode="HTML")
                        except Exception:
                            pass
                    last_sent_date = current_date_str + "_night"
                    print("[LOG] 🌙 Отправлено сообщение о ночном режиме.", flush=True)

            elif now.hour == 10 and now.minute == 0:
                if last_sent_date != current_date_str + "_morning":
                    morning_text = "☀️ <b>Доброе утро, повара! Бикини Боттон проснулся и снова принимает брони!</b> 🚀"
                    for ch in CHANNELS:
                        try:
                            bot.send_message(ch, morning_text, parse_mode="HTML")
                        except Exception:
                            pass
                    last_sent_date = current_date_str + "_morning"
                    print("[LOG] ☀️ Отправлено сообщение об утреннем открытии.", flush=True)
        except Exception as e:
            print(f"[LOG] Ошибка в потоке ночного режима: {e}", flush=True)
        time.sleep(30)


def make_backup_file():
    backup_filename = os.path.join(DATA_DIR, f"backup_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.zip")
    with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(DATA_DIR):
            for file in files:
                if file.endswith('.json'):
                    zipf.write(os.path.join(root, file), arcname=file)
    return backup_filename


def backup_scheduler():
    while True:
        time.sleep(86400)
        try:
            backup_filename = make_backup_file()
            for owner in OWNER_ID:
                try:
                    with open(backup_filename, 'rb') as doc:
                        bot.send_document(owner, doc, caption="📦 Авто-бэкап (раз в сутки)", parse_mode="HTML")
                except Exception:
                    pass
            if os.path.exists(backup_filename):
                os.remove(backup_filename)
        except Exception:
            pass


# --- КЛАВИАТУРЫ ---

def get_persistent_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📱 Главное меню"))
    return markup


def get_main_menu_keyboard(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(text="🍔 Выйти на смену", callback_data="start_create_post"),
        types.InlineKeyboardButton(text="❌ Закрыть мою смену", callback_data="my_active_posts"),
        types.InlineKeyboardButton(text="📖 Правила", callback_data="show_rules"),
        types.InlineKeyboardButton(text="⏳ Мой профиль / КД", callback_data="my_profile"),
        types.InlineKeyboardButton(text="📅 Мои брони", callback_data="my_scheduled_posts"),
        types.InlineKeyboardButton(text="🚨 Пожаловаться на скам", callback_data="report_scam")
    )
    if is_owner(user_id):
        markup.add(types.InlineKeyboardButton(text="🛠 Админ-панель", callback_data="open_admin_panel"))
    return markup


def get_back_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu"))
    return markup


def get_admin_panel_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(text="🟢 Выдать доступ (по дням)", callback_data="admin_grant_days"),
        types.InlineKeyboardButton(text="📦 Выдать посты (по штукам)", callback_data="admin_grant_posts"),
        types.InlineKeyboardButton(text="🚀 Выложить смену вне очереди", callback_data="admin_force_post"),
        types.InlineKeyboardButton(text="📅 Все брони по платформам", callback_data="admin_manage_bookings"),
        types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")
    )
    return markup


def get_channels_selector_keyboard(prefix):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, ch in enumerate(CHANNELS, 1):
        try:
            chat_info = bot.get_chat(ch)
            title = chat_info.title
        except Exception:
            title = f"ТГК #{idx}"

        btn_text = f"📢 {title} ({ch})"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"{prefix}_ch_{ch}"))

    markup.add(types.InlineKeyboardButton(text="🌐 Все ТГК сразу", callback_data=f"{prefix}_ch_all"))
    markup.add(types.InlineKeyboardButton(text="⬅️ Отмена", callback_data="open_admin_panel"))
    return markup


# --- АДМИН КОМАНДЫ ---

@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if not is_owner(message.from_user.id): return
    bot.reply_to(message, ADMIN_PANEL_TEXT, parse_mode="HTML", reply_markup=get_admin_panel_keyboard())


@bot.message_handler(commands=['del'])
def del_user(message):
    if not is_owner(message.from_user.id): return
    try:
        target_id = str(message.text.split()[1])
        users = load_data(DB_FILE)
        if target_id in users:
            del users[target_id]
            save_data(DB_FILE, users)
            bot.reply_to(message, f"🗑 Доступ у сотрудника <code>{target_id}</code> успешно забран.", parse_mode="HTML")
        else:
            bot.reply_to(message, f"⚠️ Сотрудник <code>{target_id}</code> не найден в базе.", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Формат: `/del ID`", parse_mode="Markdown")


@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_owner(message.from_user.id): return
    try:
        target_id = str(message.text.split()[1])
        b = load_data(BAN_FILE)
        if target_id not in b:
            b.append(target_id)
            save_data(BAN_FILE, b)
        bot.reply_to(message, f"⛔ Пользователь <code>{target_id}</code> забанен.", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Формат: `/ban ID`", parse_mode="Markdown")


@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_owner(message.from_user.id): return
    try:
        target_id = str(message.text.split()[1])
        b = load_data(BAN_FILE)
        if target_id in b:
            b.remove(target_id)
            save_data(BAN_FILE, b)
        bot.reply_to(message, f"✅ Пользователь <code>{target_id}</code> разбанен.", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Формат: `/unban ID`", parse_mode="Markdown")


@bot.message_handler(commands=['user'])
def user_card(message):
    if not is_owner(message.from_user.id): return
    try:
        target_id = str(message.text.split()[1])
        users = load_data(DB_FILE)
        b = load_data(BAN_FILE)
        is_b = target_id in b
        uname = esc(get_username(target_id))

        if target_id in users:
            u_data = users[target_id]
            if isinstance(u_data, dict):
                exp = u_data.get("expire", 0)
                exp_str = datetime.fromtimestamp(exp, MSK).strftime('%d.%m.%Y %H:%M') if exp > 0 else "Бессрочно / Нет"
                posts = u_data.get("posts", 0)
                ch = u_data.get("channel", "all")
            else:
                exp_str = datetime.fromtimestamp(u_data, MSK).strftime('%d.%m.%Y %H:%M')
                posts = 0
                ch = "all"
            bot.reply_to(message, f"👤 <b>Карточка сотрудника <code>{target_id}</code>:</b>\n• Юзернейм: {uname}\n• Бан: <code>{'Да' if is_b else 'Нет'}</code>\n• Канал: <code>{ch}</code>\n• Доступ до: <code>{exp_str}</code>\n• Остаток смен: <code>{posts}</code>", parse_mode="HTML")
        else:
            bot.reply_to(message, f"👤 Сотрудник <code>{target_id}</code> ({uname}) не найден в базе активных подписок. Бан: <code>{'Да' if is_b else 'Нет'}</code>", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Формат: `/user ID`", parse_mode="Markdown")


@bot.message_handler(commands=['list'])
def list_users(message):
    if not is_owner(message.from_user.id): return
    users = load_data(DB_FILE)
    if not users:
        bot.reply_to(message, "📋 Список подписок пуст.")
        return

    now = time.time()
    active_users = {}
    expired_count = 0
    text = "📋 <b>Список активных сотрудников в Бикини Боттон:</b>\n\n"

    for uid, data in list(users.items()):
        if isinstance(data, dict):
            exp = data.get("expire", 0)
            posts = data.get("posts", 0)
            ch = data.get("channel", "all")
            if (exp > 0 and now < exp) or posts > 0:
                exp_str = datetime.fromtimestamp(exp, MSK).strftime('%d.%m.%Y') if exp > 0 else "Бессрочно"
                text += f"• <code>{uid}</code> {esc(get_username(uid))} ({ch}): До <code>{exp_str}</code>, смен: <code>{posts}</code>\n"
                active_users[uid] = data
            else:
                expired_count += 1
        else:
            if now < data:
                exp_str = datetime.fromtimestamp(data, MSK).strftime('%d.%m.%Y')
                text += f"• <code>{uid}</code> {esc(get_username(uid))}: До <code>{exp_str}</code>\n"
                active_users[uid] = data
            else:
                expired_count += 1

    if expired_count > 0:
        save_data(DB_FILE, active_users)
        text += f"\n🧹 <i>Автоматически удалено просроченных записей:</i> <code>{expired_count}</code>"

    if len(active_users) == 0:
        bot.reply_to(message, "📋 Список активных подписок пуст (просроченные записи очищены).")
    else:
        bot.reply_to(message, text, parse_mode="HTML")


@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    if not is_owner(message.from_user.id): return
    users = load_data(DB_FILE)
    banned = load_data(BAN_FILE)
    sched = load_data(SCHEDULED_POSTS_FILE)
    history = load_data(HISTORY_FILE)
    stats = load_data(STATS_FILE)
    if not isinstance(stats, dict):
        stats = {}

    text = (
        "📊 <b>Статистика Бикини Боттон Заработок:</b>\n\n"
        f"• Подключенных каналов: <code>{len(CHANNELS)}</code>\n"
        f"• Активных сотрудников в базе: <code>{len(users)}</code>\n"
        f"• Заблокированных: <code>{len(banned)}</code>\n"
        f"• Активных броней в сетке: <code>{len(sched)}</code>\n"
        f"• Всего записей в истории: <code>{len(history)}</code>"
    )

    platforms = stats.get("platforms", {})
    if platforms:
        text += "\n\n🏆 <b>Топ платформ (опубликовано смен):</b>\n"
        top_p = sorted(platforms.items(), key=lambda x: x[1], reverse=True)[:7]
        for i, (name, cnt) in enumerate(top_p, 1):
            text += f"{i}. {esc(name)} — <code>{cnt}</code>\n"

    stat_users = stats.get("users", {})
    if stat_users:
        text += "\n👨‍🍳 <b>Топ сотрудников:</b>\n"
        top_u = sorted(stat_users.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:5]
        for i, (uid, info) in enumerate(top_u, 1):
            uname = f"@{info['username']}" if info.get("username") else "без username"
            text += f"{i}. <code>{uid}</code> {esc(uname)} — <code>{info.get('count', 0)}</code>\n"

    if not platforms and not stat_users:
        text += "\n\n<i>Статистика по платформам и сотрудникам начнёт копиться с новых публикаций.</i>"

    bot.reply_to(message, text, parse_mode="HTML")


@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if not is_owner(message.from_user.id): return
    text_to_send = message.text.replace('/broadcast', '', 1).strip()
    if not text_to_send:
        bot.reply_to(message, "Формат: `/broadcast ТЕКСТ`", parse_mode="Markdown")
        return

    users = load_data(DB_FILE)
    success = 0
    fail = 0
    for uid in users:
        try:
            bot.send_message(int(uid), f"📢 <b>Сообщение от Мистера Крабса:</b>\n\n{esc(text_to_send)}", parse_mode="HTML")
            success += 1
            time.sleep(0.1)
        except Exception:
            fail += 1
    bot.reply_to(message, f"📢 Рассылка завершена!\n✅ Успешно: <code>{success}</code>\n❌ Ошибок: <code>{fail}</code>", parse_mode="HTML")


@bot.message_handler(commands=['uncd'])
def uncd_cmd(message):
    if not is_owner(message.from_user.id): return
    try:
        target_id = str(message.text.split()[1])
        cooldowns = load_data(COOLDOWN_FILE)
        if target_id in cooldowns:
            del cooldowns[target_id]
            save_data(COOLDOWN_FILE, cooldowns)
            bot.reply_to(message, f"⚡ Кулдаун для сотрудника <code>{target_id}</code> успешно сброшен!", parse_mode="HTML")
        else:
            bot.reply_to(message, f"⚠️ У сотрудника <code>{target_id}</code> не было активного кулдауна.", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "Формат: `/uncd ID`", parse_mode="Markdown")


@bot.message_handler(commands=['history'])
def history_cmd(message):
    if not is_owner(message.from_user.id): return
    history = load_data(HISTORY_FILE)
    if not history:
        bot.reply_to(message, "📜 История смен пуста.")
        return

    bot.reply_to(message, "📜 <b>Последние опубликованные смены:</b>", parse_mode="HTML")
    for item in history[-5:]:
        full_text = (
            f"▪️ <b>Время:</b> <code>{item['timestamp']}</code>\n"
            f"▪️ <b>ID сотрудника:</b> <code>{item['user_id']}</code> ({esc(item['username'])})\n\n"
            f"{item['text']}\n"
            "━━━━━━━━━━━━━━━━━━━"
        )
        try:
            bot.send_message(message.chat.id, full_text, parse_mode="HTML")
        except Exception as e:
            print(f"[LOG] Ошибка показа истории: {e}", flush=True)


@bot.message_handler(commands=['backup'])
def backup_cmd(message):
    if not is_owner(message.from_user.id): return
    try:
        backup_filename = make_backup_file()
        with open(backup_filename, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption="📦 Бэкап баз данных Бикини Боттон", parse_mode="HTML")
        if os.path.exists(backup_filename):
            os.remove(backup_filename)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка создания бэкапа: {e}")


# --- ОБРАБОТКА CALLBACK ---

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    remember_user(call.from_user)

    if is_banned(user_id):
        return

    if call.data == "admin_grant_days":
        if not is_owner(user_id): return
        answer(call)
        bot.edit_message_text(
            "🟢 <b>ВЫДАЧА ДОСТУПА (ПО ДНЯМ):</b>\nВыберите ТГК, на который вы хотите выдать доступ, или 'Все ТГК':",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_channels_selector_keyboard("grantdays"),
            parse_mode="HTML"
        )

    elif call.data == "admin_grant_posts":
        if not is_owner(user_id): return
        answer(call)
        bot.edit_message_text(
            "📦 <b>ВЫДАЧА СМЕН (ПО ШТУКАМ):</b>\nВыберите ТГК, на который вы хотите выдать смены, или 'Все ТГК':",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_channels_selector_keyboard("grantposts"),
            parse_mode="HTML"
        )

    elif call.data == "admin_force_post":
        if not is_owner(user_id): return
        answer(call)
        bot.edit_message_text(
            "🚀 <b>ПУБЛИКАЦИЯ СМЕНЫ ВНЕ ОЧЕРЕДИ:</b>\nВыберите ТГК для публикации:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_channels_selector_keyboard("forcepost"),
            parse_mode="HTML"
        )

    elif call.data.startswith("grantdays_ch_"):
        if not is_owner(user_id): return
        answer(call)
        target_ch = call.data.replace("grantdays_ch_", "")
        admin_action_data[user_id] = {'action': 'grant_days_step1', 'channel': target_ch}
        bot.edit_message_text(
            f"🟢 <b>Выбран канал:</b> <code>{target_ch}</code>\n\n"
            "Введите Telegram ID сотрудника:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )

    elif call.data.startswith("grantposts_ch_"):
        if not is_owner(user_id): return
        answer(call)
        target_ch = call.data.replace("grantposts_ch_", "")
        admin_action_data[user_id] = {'action': 'grant_posts_step1', 'channel': target_ch}
        bot.edit_message_text(
            f"📦 <b>Выбран канал:</b> <code>{target_ch}</code>\n\n"
            "Введите Telegram ID сотрудника:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )

    elif call.data.startswith("forcepost_ch_"):
        if not is_owner(user_id): return
        answer(call)
        target_ch = call.data.replace("forcepost_ch_", "")
        admin_action_data[user_id] = {'action': 'forcepost_step1', 'channel': target_ch}
        bot.edit_message_text(
            f"🚀 <b>Выбран канал:</b> <code>{target_ch}</code>\n\n"
            "📌 <b>Шаг 1 из 3:</b>\nВведите название платформы (например: <i>Авито</i>, <i>Яндекс Карты</i>):",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )

    elif call.data == "my_active_posts":
        answer(call)
        posts_data = load_data(POSTS_FILE)
        if not isinstance(posts_data, dict):
            posts_data = {}
        my_posts = {}
        for p_key, p_info in posts_data.items():
            if p_info.get("user_id") == user_id or is_owner(user_id):
                my_posts[p_key] = p_info

        if not my_posts:
            bot.edit_message_text(
                "❌ <b>У вас нет активных смен в каналах.</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard(),
                parse_mode="HTML"
            )
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        text = "📌 <b>Ваши активные смены в Бикини Боттон:</b>\n\nНажмите на кнопку ниже, чтобы досрочно закрыть нужную смену:\n\n"
        for p_key, p_info in my_posts.items():
            slot_name = p_info.get("slot_num", f"СМЕНА #{p_key}")
            platform = p_info.get("platform", "Заказ")
            text += f"• <b>{esc(slot_name)}</b> ({esc(platform)})\n"
            markup.add(types.InlineKeyboardButton(text=f"🔒 Закрыть {slot_name} ({platform})", callback_data=f"close_published_slot_{p_key}"))

        markup.add(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

    elif call.data.startswith("close_published_slot_"):
        slot_key = call.data.replace("close_published_slot_", "")
        result = "missing"
        msg_dict = {}
        with posts_lock:
            posts_data = load_data(POSTS_FILE)
            if not isinstance(posts_data, dict):
                posts_data = {}
            if slot_key in posts_data:
                p_info = posts_data[slot_key]
                if p_info.get("user_id") == user_id or is_owner(user_id):
                    msg_dict = p_info.get("message_ids", {})
                    del posts_data[slot_key]
                    save_data(POSTS_FILE, posts_data)
                    result = "closed"
                else:
                    result = "forbidden"

        if result == "closed":
            close_posts_in_channels(msg_dict)
            answer(call, "✅ Смена успешно закрыта досрочно!", True)
        elif result == "forbidden":
            answer(call, "⛔ Вы можете закрыть только свою смену!", True)
        else:
            answer(call, "⚠️ Эта смена уже закрыта или не найдена.", True)

        call.data = "my_active_posts"
        callback_handler(call)

    elif call.data.startswith("complain_"):
        author_id = call.data.replace("complain_", "")
        username = f"@{call.from_user.username}" if call.from_user.username else "нет юзернейма"

        answer(call, "Ваша жалоба оформлена, Мистер Крабс уже разбирается в ситуации!", True)

        try:
            complaint_text = (
                f"🚨 <b>ЖАЛОБА НА СМЕНУ / СОТРУДНИКА!</b>\n\n"
                f"• Жалующийся: {esc(username)} (ID: <code>{user_id}</code>)\n"
                f"• Автор смены ID: <code>{esc(author_id)}</code> ({esc(get_username(author_id))})\n"
                f"• Пост ID сообщения: <code>{call.message.message_id}</code>"
            )
            bot.send_message(MAIN_ADMIN_ID, complaint_text, parse_mode="HTML")
        except Exception as e:
            print(f"[LOG] Ошибка отправки жалобы админу: {e}", flush=True)

    elif call.data == "start_create_post":
        if is_night_time():
            answer(call, "🌙 Ночной режим! Бикини Боттон спит до 10:00 утра.", True)
            bot.send_message(call.message.chat.id, "💤 <b>Бикини Боттон сейчас спит (с 00:00 до 10:00).</b> Выход на смены временно недоступен.", parse_mode="HTML", reply_markup=get_persistent_keyboard())
            return
        if not check_channel_subscription(user_id):
            answer(call)
            bot.send_message(call.message.chat.id, "❌ Подпишитесь на все наши каналы Бикини Боттон!", reply_markup=get_persistent_keyboard())
            return
        if not is_user_active(user_id):
            answer(call)
            bot.send_message(call.message.chat.id, f"⛔ У вас нет активного доступа. Ваш ID: <code>{user_id}</code>", parse_mode="HTML")
            return
        cd = get_cooldown_left(user_id)
        if cd > 0:
            answer(call)
            bot.send_message(call.message.chat.id, f"⏳ Кулдаун между сменами: еще <b>{format_time(cd)}</b>.", parse_mode="HTML")
            return

        answer(call)
        user_creation_data[user_id] = {'step': 1}
        bot.edit_message_text(
            "📌 <b>Шаг 1 из 3:</b>\nВведите название платформы (например: <i>Яндекс Карты</i>, <i>Авито</i> и т.д.):",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )

    elif call.data == "confirm_publish":
        if is_night_time():
            answer(call, "🌙 Ночной режим! Бикини Боттон спит.", True)
            return
        if user_id not in user_creation_data or 'final_text' not in user_creation_data[user_id]:
            answer(call)
            bot.send_message(call.message.chat.id, "❌ Ошибка. Начните создание заново.")
            return

        answer(call)
        platform_name = user_creation_data[user_id].get('platform', 'Заказ')
        bot.edit_message_text(
            "⏱ <b>Выберите время смены по МСК (шаг 15 минут):</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_available_slots_keyboard(user_id, platform_name),
            parse_mode="HTML"
        )

    elif call.data == "confirm_force_publish":
        if not is_owner(user_id):
            return
        if user_id not in user_creation_data or 'final_text' not in user_creation_data[user_id]:
            answer(call)
            bot.send_message(call.message.chat.id, "❌ Ошибка. Начните создание заново.")
            return

        answer(call)
        c_data = user_creation_data[user_id]
        final_text = c_data['final_text']
        slot_num = c_data['slot_num']
        platform = c_data['platform']
        payment = c_data['payment']
        target_ch = c_data['target_channel']

        try:
            target_channels = CHANNELS if target_ch == "all" else [target_ch]
            published_ids = publish_shift(user_id, call.from_user.username, final_text, platform, payment, target_channels)

            register_active_post(slot_num, user_id, published_ids, platform, payment)
            save_to_history(user_id, call.from_user.username, final_text)
            update_stats(user_id, call.from_user.username, platform)

            bot.edit_message_text(
                f"🚀 <b>Смена #{slot_num} успешно опубликована вне очереди!</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            try:
                bot.edit_message_text(f"❌ Ошибка публикации: {e}", chat_id=call.message.chat.id, message_id=call.message.message_id)
            except Exception:
                pass

        if user_id in user_creation_data:
            del user_creation_data[user_id]

    elif call.data == "refresh_slots":
        answer(call, "🔄 Сетка смен обновлена!")
        try:
            platform_name = user_creation_data.get(user_id, {}).get('platform', 'Заказ')
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_available_slots_keyboard(user_id, platform_name)
            )
        except Exception:
            pass

    elif call.data == "slot_busy":
        answer(call, slot_error_text('busy', None, ''), True)

    elif call.data.startswith("slot_pcd_"):
        platform = user_creation_data.get(user_id, {}).get('platform', 'Платформа')
        try:
            parts = call.data.replace("slot_pcd_", "").split("_")
            conflict_ts = float(parts[0])
            slot_ts_cb = float(parts[1]) if len(parts) > 1 else None
            answer(call, slot_error_text('platform', conflict_ts, platform, slot_ts_cb), True)
        except Exception:
            answer(call, "⏳ Нельзя забронировать: КД этой платформы ещё не прошёл. Выберите время дальше!", True)

    elif call.data == "slot_platform_cooldown":
        # на случай старых клавиатур
        answer(call, "⏳ Нельзя забронировать: КД этой платформы ещё не прошёл. Выберите время дальше!", True)

    elif call.data == "slot_user_cooldown":
        answer(call, slot_error_text('user', None, ''), True)

    elif call.data.startswith("book_slot_"):
        if is_night_time():
            answer(call, "🌙 Ночной режим! Бикини Боттон спит.", True)
            return
        timestamp_key = call.data.replace("book_slot_", "")
        if user_id not in user_creation_data or 'final_text' not in user_creation_data[user_id]:
            answer(call)
            bot.edit_message_text("⚠️ Ошибка данных. Создайте смену заново.", chat_id=call.message.chat.id, message_id=call.message.message_id)
            return

        c_data = user_creation_data[user_id]
        try:
            slot_ts = float(timestamp_key)
        except ValueError:
            answer(call, "⚠️ Некорректный слот.", True)
            return

        # Проверка и запись брони под одним замком: два человека не займут один слот
        ok = False
        code = extra = None
        with booking_lock:
            scheduled_data = load_data(SCHEDULED_POSTS_FILE)
            if not isinstance(scheduled_data, dict):
                scheduled_data = {}
            history = load_platform_history()

            ok, code, extra = validate_slot(user_id, c_data['platform'], slot_ts, scheduled_data, history)
            if ok:
                now_ts = time.time()
                scheduled_data[str(int(slot_ts))] = {
                    "user_id": user_id,
                    "username": call.from_user.username,
                    "final_text": c_data['final_text'],
                    "slot_num": c_data['slot_num'],
                    "platform": c_data['platform'],
                    "payment": c_data['payment'],
                    "created_at": now_ts,
                    "reminded": (slot_ts - now_ts) <= REMINDER_BEFORE_SECONDS
                }
                save_data(SCHEDULED_POSTS_FILE, scheduled_data)
                history.append({
                    "platform": normalize_platform_name(c_data['platform']),
                    "ts": float(int(slot_ts)),
                    "user_id": user_id
                })
                save_data(PLATFORM_HISTORY_FILE, history)

        if not ok:
            answer(call, slot_error_text(code, extra, c_data['platform'], slot_ts), True)
            try:
                bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_available_slots_keyboard(user_id, c_data['platform'])
                )
            except Exception:
                pass
            return

        answer(call)
        dt_formatted = datetime.fromtimestamp(slot_ts, MSK).strftime('%H:%M МСК')
        bot.edit_message_text(
            f"✅ <b>Смена успешно забронирована на {dt_formatted}!</b>\nБот опубликует её автоматически на кухне в назначенное время.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_back_keyboard(),
            parse_mode="HTML"
        )

    elif call.data == "my_scheduled_posts":
        answer(call)
        scheduled_data = load_data(SCHEDULED_POSTS_FILE)
        if not isinstance(scheduled_data, dict):
            scheduled_data = {}
        user_bookings = {ts: info for ts, info in scheduled_data.items() if info.get("user_id") == user_id}

        if not user_bookings:
            bot.edit_message_text("📅 <b>У вас нет активных броней смен.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_back_keyboard(), parse_mode="HTML")
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        text = "📅 <b>Ваши активные брони смен:</b>\n\n"
        for ts, info in sorted(user_bookings.items(), key=lambda x: float(x[0])):
            dt_str = datetime.fromtimestamp(float(ts), MSK).strftime('%d.%m в %H:%M МСК')
            text += f"• Смена #{info['slot_num']} на <code>{dt_str}</code> ({esc(info['platform'])})\n"
            markup.add(types.InlineKeyboardButton(text=f"❌ Отменить бронь #{info['slot_num']}", callback_data=f"cancel_booking_{ts}"))
        markup.add(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

    elif call.data.startswith("cancel_booking_"):
        ts_to_cancel = call.data.replace("cancel_booking_", "")
        removed = None
        with booking_lock:
            scheduled_data = load_data(SCHEDULED_POSTS_FILE)
            info = scheduled_data.get(ts_to_cancel) if isinstance(scheduled_data, dict) else None
            if info and (info.get("user_id") == user_id or is_owner(user_id)):
                removed = remove_booking(ts_to_cancel, drop_history=True)

        if removed:
            answer(call, "Бронь смены отменена!")
            # уведомление владельцу брони, если её отменил админ
            if removed.get("user_id") != user_id:
                dt_str = datetime.fromtimestamp(float(ts_to_cancel), MSK).strftime('%d.%m в %H:%M МСК')
                notify_user(
                    removed["user_id"],
                    f"❌ Ваша бронь смены #{removed['slot_num']} на <code>{dt_str}</code> ({esc(removed['platform'])}) была отменена администратором."
                )
        else:
            answer(call, "⚠️ Бронь не найдена (возможно, уже опубликована или отменена).", True)

        if is_owner(user_id) and call.message.text and "Все активные брони" in call.message.text:
            call.data = "admin_manage_bookings"
        else:
            call.data = "my_scheduled_posts"
        callback_handler(call)

    elif call.data == "open_admin_panel":
        if not is_owner(user_id): return
        answer(call)
        bot.edit_message_text(ADMIN_PANEL_TEXT, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

    elif call.data == "admin_manage_bookings":
        if not is_owner(user_id): return
        answer(call)
        scheduled_data = load_data(SCHEDULED_POSTS_FILE)
        if not scheduled_data:
            bot.edit_message_text("📅 Активных броней смен в системе нет.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        text = "📅 <b>Все активные брони смен:</b>\n\n"
        for ts, info in sorted(scheduled_data.items(), key=lambda x: float(x[0])):
            dt_str = datetime.fromtimestamp(float(ts), MSK).strftime('%d.%m в %H:%M МСК')
            text += f"• Бронь #{info['slot_num']} на <code>{dt_str}</code> ({esc(info['platform'])}) — {esc(get_username(info.get('user_id')))}\n"
            markup.add(types.InlineKeyboardButton(text=f"❌ Снять бронь #{info['slot_num']} ({dt_str})", callback_data=f"cancel_booking_{ts}"))
        markup.add(types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="open_admin_panel"))
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

    elif call.data == "cancel_publish":
        answer(call)
        if user_id in user_creation_data: del user_creation_data[user_id]
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    elif call.data == "main_menu":
        answer(call)
        bot.edit_message_text("👋 Главное меню Бикини Боттон:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu_keyboard(user_id))

    elif call.data == "show_rules":
        answer(call)
        bot.edit_message_text(RULES_TEXT, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=get_back_keyboard())

    elif call.data == "my_profile":
        answer(call)
        if is_owner(user_id):
            prof_text = "👑 У вас статус <b>Владельца Бикини Боттон</b>."
        elif is_user_active(user_id):
            cd = get_cooldown_left(user_id)
            cd_str = format_time(cd) if cd > 0 else "Отсутствует"
            users = load_data(DB_FILE)
            u_info = users.get(str(user_id), {})
            posts_cnt = u_info.get('posts', 0) if isinstance(u_info, dict) else 0
            exp = u_info.get('expire', 0) if isinstance(u_info, dict) else 0
            exp_str = datetime.fromtimestamp(exp, MSK).strftime('%d.%m.%Y') if exp > 0 else "Бессрочно"
            prof_text = (
                f"👤 <b>Ваш профиль повара:</b>\n"
                f"• Кулдаун между сменами: <b>{cd_str}</b>\n"
                f"• Доступ до: <b>{exp_str}</b>\n"
                f"• Доступно смен: <b>{posts_cnt}</b>"
            )
        else:
            prof_text = "⛔ У вас нет активного доступа к сменам."
        bot.edit_message_text(prof_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_back_keyboard(), parse_mode="HTML")

    elif call.data == "report_scam":
        answer(call)
        bot.edit_message_text(
            f"🚨 Если вы столкнулись со скамом или нарушением, напишите Мистеру Крабсу (администратору): {MY_USERNAME}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_back_keyboard(),
            parse_mode="HTML"
        )


# --- ТЕКСТОВЫЕ ВВОДЫ И КОМАНДЫ ---

@bot.message_handler(commands=['cancel'])
def cancel_cmd(message):
    user_id = message.from_user.id
    if user_id in user_creation_data: del user_creation_data[user_id]
    if user_id in admin_action_data: del admin_action_data[user_id]
    bot.reply_to(message, "Действие отменено.", reply_markup=get_persistent_keyboard())


@bot.message_handler(func=lambda msg: msg.text == "📱 Главное меню")
def handle_menu_btn(message):
    remember_user(message.from_user)
    bot.send_message(message.chat.id, "👋 Главное меню Бикини Боттон:", reply_markup=get_main_menu_keyboard(message.from_user.id))


@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    remember_user(message.from_user)
    if is_banned(user_id): return

    command_args = message.text.split()
    if len(command_args) > 1 and command_args[1].startswith("spamblock_"):
        author_id_str = command_args[1].replace("spamblock_", "")
        try:
            author_id = int(author_id_str)
            user_mention = f"@{message.from_user.username}" if message.from_user.username else f"ID: <code>{user_id}</code>"

            spam_notification = (
                f"⚠️ <b>У сотрудника спам-блок!</b>\n\n"
                f"• Повар: {esc(user_mention) if message.from_user.username else user_mention} (ID: <code>{user_id}</code>)\n"
                f"Хочет взять вашу смену, но у него не отправляется ЛС из-за спам-блока. Напишите ему первыми!"
            )
            bot.send_message(author_id, spam_notification, parse_mode="HTML")
            bot.reply_to(message, "✅ Уведомление отправлено автору смены! Скоро он напишет вам сам.", reply_markup=get_persistent_keyboard())
        except Exception:
            bot.reply_to(message, "❌ Не удалось отправить уведомление автору.", reply_markup=get_persistent_keyboard())
        return

    bot.reply_to(message, "👋 Добро пожаловать в Бикини Боттон Заработок!", reply_markup=get_main_menu_keyboard(user_id))
    bot.send_message(message.chat.id, "Меню закреплено ниже.", reply_markup=get_persistent_keyboard())


@bot.message_handler(content_types=['text', 'photo', 'video', 'document'])
def text_handler(message):
    user_id = message.from_user.id
    remember_user(message.from_user)
    if is_banned(user_id): return

    txt = (message.text or "").strip()

    if is_owner(user_id) and user_id in admin_action_data:
        action_info = admin_action_data[user_id]
        act_type = action_info.get('action')
        target_ch = action_info.get('channel')

        if act_type == 'grant_days_step1':
            target_id = txt
            if not target_id.isdigit():
                bot.reply_to(message, "❌ Некорректный ID! Введите только цифры ID:")
                return
            admin_action_data[user_id]['target_id'] = target_id
            admin_action_data[user_id]['action'] = 'grant_days_step2'
            bot.reply_to(message, f"🟢 ID сотрудника <code>{target_id}</code> записан.\n\nНа сколько дней выдать доступ? (введите число, например: <code>30</code>)", parse_mode="HTML")
            return

        elif act_type == 'grant_days_step2':
            days_str = txt
            if not days_str.isdigit():
                bot.reply_to(message, "❌ Введите только число дней:")
                return
            days = int(days_str)
            target_id = action_info['target_id']

            users = load_data(DB_FILE)
            exp_time = time.time() + (days * 86400) if days > 0 else 0
            existing_posts = users.get(target_id, {}).get("posts", 0) if isinstance(users.get(target_id), dict) else 0
            users[target_id] = {"expire": exp_time, "posts": existing_posts, "channel": target_ch}
            save_data(DB_FILE, users)

            ch_label = "Все ТГК" if target_ch == "all" else f"ТГК {target_ch}"
            bot.reply_to(message, f"✅ Доступ для сотрудника <code>{target_id}</code> успешно выдан на {days} дней в {ch_label}!", parse_mode="HTML")
            try:
                bot.send_message(int(target_id), f"🎉 <b>Вам выдан доступ к сменам на {days} дней!</b> Нажмите /start", parse_mode="HTML", reply_markup=get_persistent_keyboard())
            except Exception:
                pass
            del admin_action_data[user_id]
            return

        elif act_type == 'grant_posts_step1':
            target_id = txt
            if not target_id.isdigit():
                bot.reply_to(message, "❌ Некорректный ID! Введите только цифры ID:")
                return
            admin_action_data[user_id]['target_id'] = target_id
            admin_action_data[user_id]['action'] = 'grant_posts_step2'
            bot.reply_to(message, f"📦 ID сотрудника <code>{target_id}</code> записан.\n\nСколько смен выдать? (введите число, например: <code>4</code>)", parse_mode="HTML")
            return

        elif act_type == 'grant_posts_step2':
            posts_str = txt
            if not posts_str.isdigit():
                bot.reply_to(message, "❌ Введите только число смен:")
                return
            posts = int(posts_str)
            target_id = action_info['target_id']

            users = load_data(DB_FILE)
            existing_exp = users.get(target_id, {}).get("expire", 0) if isinstance(users.get(target_id), dict) else 0
            curr_posts = users.get(target_id, {}).get("posts", 0) if isinstance(users.get(target_id), dict) else 0
            users[target_id] = {"expire": existing_exp, "posts": curr_posts + posts, "channel": target_ch}
            save_data(DB_FILE, users)

            ch_label = "Все ТГК" if target_ch == "all" else f"ТГК {target_ch}"
            bot.reply_to(message, f"✅ Сотруднику <code>{target_id}</code> успешно начислено +{posts} смен в {ch_label}! Всего доступно: {curr_posts + posts}", parse_mode="HTML")
            try:
                bot.send_message(int(target_id), f"🎉 <b>Вам начислено {posts} смен для работы!</b> Нажмите /start", parse_mode="HTML", reply_markup=get_persistent_keyboard())
            except Exception:
                pass
            del admin_action_data[user_id]
            return

        elif act_type == 'forcepost_step1':
            user_creation_data[user_id] = {'step': 1, 'platform': txt, 'target_channel': target_ch, 'is_force': True}
            admin_action_data[user_id]['action'] = 'forcepost_step2'
            bot.reply_to(message, "💵 <b>Шаг 2 из 3:</b>\nВведите сумму оплаты в рублях (например: <i>150</i>):", parse_mode="HTML")
            return

        elif act_type == 'forcepost_step2':
            user_creation_data[user_id]['payment'] = txt
            user_creation_data[user_id]['step'] = 3
            admin_action_data[user_id]['action'] = 'forcepost_step3'
            bot.reply_to(message, "📝 <b>Шаг 3 из 3:</b>\nВведите подробное описание смены:", parse_mode="HTML")
            return

        elif act_type == 'forcepost_step3':
            user_creation_data[user_id]['desc'] = txt
            slot_num = get_next_slot_id()

            c_data = user_creation_data[user_id]
            final_text = build_shift_text(c_data['platform'], c_data['payment'], c_data['desc'])

            user_creation_data[user_id]['final_text'] = final_text
            user_creation_data[user_id]['slot_num'] = slot_num

            preview_markup = types.InlineKeyboardMarkup(row_width=1)
            preview_markup.add(
                types.InlineKeyboardButton(text="🚀 Опубликовать сейчас вне очереди", callback_data="confirm_force_publish"),
                types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_publish")
            )

            bot.reply_to(message, f"👁 <b>ПРЕДПРОСМОТР СМЕНЫ (ВНЕ ОЧЕРЕДИ):</b>\n\n{final_text}\n\nВсе верно?", parse_mode="HTML", reply_markup=preview_markup)
            del admin_action_data[user_id]
            return

    if message.text and message.text.startswith('/'): return

    if user_id in user_creation_data and not user_creation_data[user_id].get('is_force'):
        if is_night_time():
            bot.reply_to(message, "🌙 Сейчас ночное время (с 00:00 до 10:00). Бикини Боттон спит, создание смен приостановлено.", reply_markup=get_persistent_keyboard())
            del user_creation_data[user_id]
            return

        step = user_creation_data[user_id].get('step', 1)

        if step == 1:
            user_creation_data[user_id]['platform'] = txt
            user_creation_data[user_id]['step'] = 2
            bot.reply_to(message, "💵 <b>Шаг 2 из 3:</b>\nВведите сумму оплаты в рублях (например: <i>150</i>):", parse_mode="HTML")

        elif step == 2:
            user_creation_data[user_id]['payment'] = txt
            user_creation_data[user_id]['step'] = 3
            bot.reply_to(message, "📝 <b>Шаг 3 из 3:</b>\nВведите подробное описание смены:", parse_mode="HTML")

        elif step == 3:
            user_creation_data[user_id]['desc'] = txt
            slot_num = get_next_slot_id()

            platform = user_creation_data[user_id]['platform']
            payment = user_creation_data[user_id]['payment']
            desc = user_creation_data[user_id]['desc']

            final_text = build_shift_text(platform, payment, desc)

            user_creation_data[user_id]['final_text'] = final_text
            user_creation_data[user_id]['slot_num'] = slot_num

            preview_markup = types.InlineKeyboardMarkup(row_width=1)
            preview_markup.add(
                types.InlineKeyboardButton(text="⏱ Выбрать время брони (до 2ч вперёд)", callback_data="confirm_publish"),
                types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_publish")
            )

            bot.reply_to(message, f"👁 <b>ПРЕДПРОСМОТР СМЕНЫ:</b>\n\n{final_text}\n\nВсе верно?", parse_mode="HTML", reply_markup=preview_markup)


# --- ЗАПУСК ПОТОКОВ ---

threading.Thread(target=auto_close_checker, daemon=True).start()
threading.Thread(target=night_mode_scheduler, daemon=True).start()
threading.Thread(target=backup_scheduler, daemon=True).start()
threading.Thread(target=scheduled_posts_checker, daemon=True).start()

if __name__ == '__main__':
    print("🚀 Бот Бикини Боттон полностью обновлен и запущен!", flush=True)
    while True:
        try:
            bot.polling(none_stop=True, timeout=30, long_polling_timeout=30, skip_pending=True)
        except Exception as e:
            print(f"❌ Сбой: {e}. Переподключение...", flush=True)
            time.sleep(5)
