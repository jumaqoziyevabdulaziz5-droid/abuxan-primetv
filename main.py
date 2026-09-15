"""
main.py — Telegram Kino bot (to'liq loyiha, bitta faylda).

Texnologiyalar: Python 3, pyTelegramBotAPI (telebot), APScheduler.
Database ISHLATILMAYDI — barcha ma'lumotlar data/ papkasidagi TXT fayllarda.
.env fayl ham ISHLATILMAYDI — token va admin ID'lar config.py ichida.
Film videolari Telegram serverida file_id orqali saqlanadi.

Ishga tushirish:
    1) config.py ichida BOT_TOKEN va ADMIN_IDS ni to'ldiring
    2) pip install -r requirements.txt
    3) python main.py
"""

import logging
import os
import sys
import threading
import time
from datetime import datetime

import telebot
from telebot import apihelper, types
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    ADMIN_IDS,
    ADMINS_FILE,
    BOT_TOKEN,
    BTN_BROADCAST,
    BTN_CANCEL,
    BTN_CHANNELS,
    BTN_DELETE,
    BTN_EDIT,
    BTN_LIST,
    BTN_SETTINGS,
    BTN_STATS,
    BTN_UPLOAD,
    BTN_USERS,
    CHANNELS_FILE,
    DAILY_STATS_FILE,
    FILMS_FILE,
    FILM_IDS_FILE,
    LOGS_DIR,
    LOG_FILE,
    NEXT_FILM_ID_FILE,
    PAGE_SIZE,
    REPORT_TIMES,
    STATISTICS_FILE,
    TIMEZONE,
    USERS_FILE,
)

# =====================================================================
#  1-QISM: LOGGING
# =====================================================================


def setup_logger():
    os.makedirs(LOGS_DIR, exist_ok=True)

    log = logging.getLogger("kino_bot")
    log.setLevel(logging.INFO)

    if log.handlers:
        return log

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    log.addHandler(file_handler)
    log.addHandler(console_handler)
    return log


logger = setup_logger()


# =====================================================================
#  2-QISM: TXT FAYL MENEJERI
#
#  - Maydonlar yozilishdan oldin escape qilinadi ('\' va '|'),
#    shuning uchun film nomi/tavsifida '|' bo'lsa ham format buzilmaydi.
#  - Yozish vaqtinchalik .tmp faylga bajariladi, so'ng os.replace() bilan
#    almashtiriladi (atomic) — fayl yarim yozilgan holda qolmaydi.
#  - Har bir fayl uchun alohida thread lock.
# =====================================================================

_locks = {}
_locks_guard = threading.Lock()


def _get_lock(path):
    with _locks_guard:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def escape_field(value):
    """Maydon ichidagi '\\', '|' va yangi qator belgilarini escape qiladi."""
    if value is None:
        value = ""
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace("|", "\\|")
    value = value.replace("\n", "\\n")
    value = value.replace("\r", "")
    return value


def unescape_field(value):
    """escape_field bilan yozilgan maydonni asl holatiga qaytaradi."""
    result = []
    i = 0
    length = len(value)
    while i < length:
        ch = value[i]
        if ch == "\\" and i + 1 < length:
            nxt = value[i + 1]
            if nxt == "|":
                result.append("|")
                i += 2
                continue
            if nxt == "n":
                result.append("\n")
                i += 2
                continue
            if nxt == "\\":
                result.append("\\")
                i += 2
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def split_escaped_line(line):
    """Qatorni '|' bo'yicha ajratadi, escape qilingan '\\|' ni ajratmaydi."""
    fields = []
    current = []
    i = 0
    length = len(line)
    while i < length:
        ch = line[i]
        if ch == "\\" and i + 1 < length:
            current.append(ch)
            current.append(line[i + 1])
            i += 2
            continue
        if ch == "|":
            fields.append(unescape_field("".join(current)))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    fields.append(unescape_field("".join(current)))
    return fields


def join_escaped_fields(fields):
    return "|".join(escape_field(f) for f in fields)


def ensure_file(path, default_content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(default_content)


def read_file(path):
    ensure_file(path)
    with _get_lock(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f.readlines()]
    return [line for line in lines if line.strip() != ""]


def write_file(path, lines):
    ensure_file(path)
    tmp_path = path + ".tmp"
    with _get_lock(path):
        with open(tmp_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp_path, path)


def read_records(path):
    return [split_escaped_line(line) for line in read_file(path)]


def write_records(path, records):
    write_file(path, [join_escaped_fields(record) for record in records])


def append_record(path, record):
    ensure_file(path)
    line = join_escaped_fields(record)
    with _get_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def append_raw_line(path, value):
    """Bitta ustunli faylga qator qo'shadi (masalan film_ids.txt)."""
    ensure_file(path)
    with _get_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(str(value) + "\n")


def find_record(path, key_index, key_value):
    for record in read_records(path):
        if len(record) > key_index and record[key_index] == str(key_value):
            return record
    return None


def update_record(path, key_index, key_value, new_record):
    records = read_records(path)
    updated = False
    for i, record in enumerate(records):
        if len(record) > key_index and record[key_index] == str(key_value):
            records[i] = new_record
            updated = True
            break
    if updated:
        write_records(path, records)
    return updated


def delete_record(path, key_index, key_value):
    records = read_records(path)
    new_records = [
        r for r in records if not (len(r) > key_index and r[key_index] == str(key_value))
    ]
    deleted = len(new_records) != len(records)
    if deleted:
        write_records(path, new_records)
    return deleted


def read_single_value(path, default="0"):
    ensure_file(path, default_content=default)
    with _get_lock(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    return content if content != "" else default


def write_single_value(path, value):
    ensure_file(path)
    tmp_path = path + ".tmp"
    with _get_lock(path):
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(str(value))
        os.replace(tmp_path, path)


def ensure_all_data_files():
    for path in [
        USERS_FILE,
        FILMS_FILE,
        CHANNELS_FILE,
        ADMINS_FILE,
        STATISTICS_FILE,
        DAILY_STATS_FILE,
        FILM_IDS_FILE,
    ]:
        ensure_file(path)
    ensure_file(NEXT_FILM_ID_FILE, default_content="1")
    logger.info("Barcha TXT fayllar tekshirildi / yaratildi.")


# =====================================================================
#  3-QISM: HOLATLAR (FSM)
# =====================================================================

WAITING_VIDEO = "WAITING_VIDEO"
WAITING_NAME = "WAITING_NAME"
WAITING_DESCRIPTION = "WAITING_DESCRIPTION"
WAITING_DELETE_ID = "WAITING_DELETE_ID"
WAITING_EDIT_ID = "WAITING_EDIT_ID"
WAITING_EDIT_VIDEO = "WAITING_EDIT_VIDEO"
WAITING_EDIT_NAME = "WAITING_EDIT_NAME"
WAITING_EDIT_DESCRIPTION = "WAITING_EDIT_DESCRIPTION"
WAITING_BROADCAST = "WAITING_BROADCAST"
WAITING_CHANNEL = "WAITING_CHANNEL"

_user_states = {}


def set_state(user_id, state, data=None):
    _user_states[user_id] = {"state": state, "data": data or {}}


def get_state(user_id):
    entry = _user_states.get(user_id)
    return entry["state"] if entry else None


def get_data(user_id):
    entry = _user_states.get(user_id)
    return entry["data"] if entry else {}


def update_data(user_id, **kwargs):
    entry = _user_states.get(user_id)
    if entry is None:
        entry = {"state": None, "data": {}}
        _user_states[user_id] = entry
    entry["data"].update(kwargs)


def clear_state(user_id):
    if user_id in _user_states:
        del _user_states[user_id]


# =====================================================================
#  4-QISM: FILM SERVISI
#  films.txt formati: id|name|description|file_id|views|created_at
# =====================================================================

F_ID, F_NAME, F_DESC, F_FILE_ID, F_VIEWS, F_CREATED = range(6)


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def get_next_film_id():
    """next_film_id.txt dagi navbatdagi ID (faylni o'zgartirmaydi)."""
    value = read_single_value(NEXT_FILM_ID_FILE, default="1")
    try:
        return int(value)
    except ValueError:
        return 1


def _advance_next_film_id(used_id):
    write_single_value(NEXT_FILM_ID_FILE, used_id + 1)


def add_film(name, description, file_id):
    """Yangi film qo'shadi. ID avtomatik beriladi va hech qachon qayta ishlatilmaydi."""
    new_id = get_next_film_id()
    append_record(FILMS_FILE, [str(new_id), name, description, file_id, "0", _now_str()])
    append_raw_line(FILM_IDS_FILE, new_id)
    _advance_next_film_id(new_id)
    logger.info(f"Film qo'shildi: id={new_id}, nomi={name}")
    return new_id


def _record_to_film(record):
    return {
        "id": record[F_ID],
        "name": record[F_NAME],
        "description": record[F_DESC],
        "file_id": record[F_FILE_ID],
        "views": int(record[F_VIEWS]) if record[F_VIEWS].isdigit() else 0,
        "created_at": record[F_CREATED],
    }


def find_film(film_id):
    record = find_record(FILMS_FILE, F_ID, film_id)
    return _record_to_film(record) if record else None


def list_films():
    films = [_record_to_film(r) for r in read_records(FILMS_FILE) if len(r) >= 6]
    films.sort(key=lambda x: int(x["id"]) if x["id"].isdigit() else 0)
    return films


def delete_film(film_id):
    """films.txt dan o'chiradi. film_ids.txt va next_film_id.txt O'ZGARMAYDI."""
    deleted = delete_record(FILMS_FILE, F_ID, film_id)
    if deleted:
        logger.info(f"Film o'chirildi: id={film_id}")
    return deleted


def update_film_field(film_id, field_index, new_value):
    record = find_record(FILMS_FILE, F_ID, film_id)
    if record is None:
        return False
    record[field_index] = new_value
    update_record(FILMS_FILE, F_ID, film_id, record)
    logger.info(f"Film yangilandi: id={film_id}, maydon={field_index}")
    return True


def update_film_name(film_id, new_name):
    return update_film_field(film_id, F_NAME, new_name)


def update_film_description(film_id, new_description):
    return update_film_field(film_id, F_DESC, new_description)


def update_film_video(film_id, new_file_id):
    return update_film_field(film_id, F_FILE_ID, new_file_id)


def increment_views(film_id):
    record = find_record(FILMS_FILE, F_ID, film_id)
    if record is None:
        return False
    try:
        views = int(record[F_VIEWS])
    except ValueError:
        views = 0
    record[F_VIEWS] = str(views + 1)
    update_record(FILMS_FILE, F_ID, film_id, record)
    return True


def total_films_count():
    return len(read_records(FILMS_FILE))


def total_views_count():
    total = 0
    for record in read_records(FILMS_FILE):
        if len(record) >= 5 and record[F_VIEWS].isdigit():
            total += int(record[F_VIEWS])
    return total


def films_created_today_count():
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(
        1
        for r in read_records(FILMS_FILE)
        if len(r) >= 6 and r[F_CREATED].startswith(today)
    )


# =====================================================================
#  5-QISM: FOYDALANUVCHI SERVISI
#  users.txt formati: telegram_id|username|first_name|registered_date|status
# =====================================================================

U_ID, U_USERNAME, U_FIRST_NAME, U_REGISTERED, U_STATUS = range(5)

STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"


def register_user_if_new(telegram_id, username, first_name):
    if find_record(USERS_FILE, U_ID, telegram_id) is not None:
        return False
    append_record(
        USERS_FILE,
        [str(telegram_id), username or "", first_name or "", _now_str(), STATUS_ACTIVE],
    )
    logger.info(f"Yangi foydalanuvchi ro'yxatga olindi: {telegram_id}")
    return True


def _record_to_user(record):
    return {
        "telegram_id": record[U_ID],
        "username": record[U_USERNAME],
        "first_name": record[U_FIRST_NAME],
        "registered_date": record[U_REGISTERED],
        "status": record[U_STATUS],
    }


def get_user(telegram_id):
    record = find_record(USERS_FILE, U_ID, telegram_id)
    return _record_to_user(record) if record else None


def list_users():
    return [_record_to_user(r) for r in read_records(USERS_FILE) if len(r) >= 5]


def set_user_status(telegram_id, status):
    record = find_record(USERS_FILE, U_ID, telegram_id)
    if record is None:
        return False
    record[U_STATUS] = status
    update_record(USERS_FILE, U_ID, telegram_id, record)
    return True


def mark_blocked(telegram_id):
    return set_user_status(telegram_id, STATUS_BLOCKED)


def mark_active(telegram_id):
    return set_user_status(telegram_id, STATUS_ACTIVE)


def total_users_count():
    return len(read_records(USERS_FILE))


def active_users_count():
    return sum(1 for u in list_users() if u["status"] == STATUS_ACTIVE)


def blocked_users_count():
    return sum(1 for u in list_users() if u["status"] == STATUS_BLOCKED)


def new_users_today_count():
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for u in list_users() if u["registered_date"].startswith(today))


def all_user_ids():
    return [u["telegram_id"] for u in list_users()]


# =====================================================================
#  6-QISM: MAJBURIY OBUNA SERVISI
#  channels.txt formati: channel_id|username|name|invite_link
# =====================================================================

C_ID, C_USERNAME, C_NAME, C_LINK = range(4)


def list_channels():
    channels = []
    for record in read_records(CHANNELS_FILE):
        if len(record) < 4:
            continue
        channels.append(
            {
                "channel_id": record[C_ID],
                "username": record[C_USERNAME],
                "name": record[C_NAME],
                "invite_link": record[C_LINK],
            }
        )
    return channels


def add_channel(channel_id, username, name, invite_link):
    if find_record(CHANNELS_FILE, C_ID, channel_id) is not None:
        return False
    append_record(CHANNELS_FILE, [str(channel_id), username, name, invite_link])
    logger.info(f"Kanal qo'shildi: {channel_id} ({name})")
    return True


def remove_channel(channel_id):
    removed = delete_record(CHANNELS_FILE, C_ID, channel_id)
    if removed:
        logger.info(f"Kanal o'chirildi: {channel_id}")
    return removed


def check_user_subscribed(bot, user_id):
    """(True, []) — hammasiga obuna; (False, [kanallar]) — obuna bo'lmagan."""
    not_subscribed = []
    for channel in list_channels():
        try:
            member = bot.get_chat_member(int(channel["channel_id"]), user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(channel)
        except Exception as e:
            logger.error(
                f"Obunani tekshirishda xatolik: kanal={channel['channel_id']}, xato={e}"
            )
            not_subscribed.append(channel)
    return (len(not_subscribed) == 0, not_subscribed)


# =====================================================================
#  7-QISM: BROADCAST SERVISI
# =====================================================================


def broadcast_message(bot, from_chat_id, message_id):
    """Xabarni (text/photo/video/document/audio) barcha foydalanuvchilarga yuboradi."""
    users = all_user_ids()
    total = len(users)
    sent = 0
    failed = 0

    for user_id in users:
        try:
            bot.copy_message(int(user_id), from_chat_id, message_id)
            sent += 1
        except apihelper.ApiTelegramException as e:
            failed += 1
            if "blocked" in str(e).lower():
                mark_blocked(user_id)
            logger.error(f"Broadcast xatoligi (user={user_id}): {e}")
        except Exception as e:
            failed += 1
            logger.error(f"Broadcast noma'lum xatolik (user={user_id}): {e}")
        time.sleep(0.05)  # flood-limitga tushmaslik uchun

    logger.info(f"Broadcast yakunlandi: jami={total}, yuborildi={sent}, xato={failed}")
    return {"total": total, "sent": sent, "failed": failed}


# =====================================================================
#  8-QISM: STATISTIKA SERVISI
# =====================================================================


def get_overall_stats():
    return {
        "total_users": total_users_count(),
        "active_users": active_users_count(),
        "blocked_users": blocked_users_count(),
        "total_films": total_films_count(),
        "total_views": total_views_count(),
        "new_users_today": new_users_today_count(),
        "new_films_today": films_created_today_count(),
    }


def record_daily_snapshot():
    """Bugungi statistikani daily_stats.txt ga yozadi."""
    today = datetime.now().strftime("%Y-%m-%d")
    stats = get_overall_stats()
    append_record(DAILY_STATS_FILE, [today, "users", str(stats["new_users_today"])])
    append_record(DAILY_STATS_FILE, [today, "views", str(stats["total_views"])])
    append_record(DAILY_STATS_FILE, [today, "films", str(stats["new_films_today"])])
    return stats


def format_overall_stats_text():
    s = get_overall_stats()
    return (
        "📊 BOT STATISTIKASI\n\n"
        f"👥 Jami foydalanuvchilar: {s['total_users']}\n"
        f"🟢 Faol: {s['active_users']}\n"
        f"🔴 Bloklagan: {s['blocked_users']}\n\n"
        f"🎬 Jami filmlar: {s['total_films']}\n\n"
        f"👁 Jami film ko'rilishi: {s['total_views']}\n\n"
        f"📅 Bugun yangi foydalanuvchilar: {s['new_users_today']}\n"
        f"🎬 Bugun qo'shilgan filmlar: {s['new_films_today']}\n"
    )


def format_daily_report_text():
    s = get_overall_stats()
    now = datetime.now()
    return (
        "📊 BOT HISOBOTI\n\n"
        f"📅 {now.strftime('%d.%m.%Y')}\n"
        f"⏰ {now.strftime('%H:%M')}\n\n"
        f"👥 Jami foydalanuvchilar: {s['total_users']}\n"
        f"🟢 Faol foydalanuvchilar: {s['active_users']}\n"
        f"🔴 Bloklaganlar: {s['blocked_users']}\n\n"
        f"🎬 Jami filmlar: {s['total_films']}\n\n"
        f"👁 Jami ko'rilishlar: {s['total_views']}\n\n"
        f"📈 Bugungi ko'rilishlar: {s['total_views']}\n"
        f"👤 Bugungi yangi foydalanuvchilar: {s['new_users_today']}\n"
        f"🎬 Bugun qo'shilgan filmlar: {s['new_films_today']}\n"
    )


# =====================================================================
#  9-QISM: KLAVIATURALAR
# =====================================================================


def admin_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(BTN_UPLOAD, BTN_DELETE)
    markup.add(BTN_EDIT, BTN_LIST)
    markup.add(BTN_STATS, BTN_BROADCAST)
    markup.add(BTN_CHANNELS, BTN_USERS)
    markup.add(BTN_SETTINGS)
    return markup


def cancel_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(BTN_CANCEL)
    return markup


def subscription_keyboard(channels):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for channel in channels:
        link = channel["invite_link"] or f"https://t.me/{channel['username'].lstrip('@')}"
        markup.add(types.InlineKeyboardButton(text=f"📢 {channel['name']}", url=link))
    markup.add(
        types.InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription")
    )
    return markup


def confirm_cancel_keyboard(confirm_data, cancel_data="cancel_action"):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=confirm_data),
        types.InlineKeyboardButton(text="❌ Bekor qilish", callback_data=cancel_data),
    )
    return markup


def edit_film_options_keyboard(film_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            text="🎬 Videoni almashtirish", callback_data=f"edit_video_{film_id}"
        ),
        types.InlineKeyboardButton(
            text="📝 Nomini o'zgartirish", callback_data=f"edit_name_{film_id}"
        ),
        types.InlineKeyboardButton(
            text="📄 Tavsifni o'zgartirish", callback_data=f"edit_desc_{film_id}"
        ),
        types.InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action"),
    )
    return markup


def pagination_keyboard(prefix, page, total_pages):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    if page > 0:
        buttons.append(
            types.InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_{page - 1}")
        )
    buttons.append(
        types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page < total_pages - 1:
        buttons.append(
            types.InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_{page + 1}")
        )
    if buttons:
        markup.row(*buttons)
    return markup


def channels_keyboard(channels):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="add_channel"))
    for ch in channels:
        markup.add(
            types.InlineKeyboardButton(
                text=f"🗑 {ch['name']}", callback_data=f"remove_channel_{ch['channel_id']}"
            )
        )
    return markup


# =====================================================================
#  10-QISM: YORDAMCHI FUNKSIYALAR
# =====================================================================


def is_admin(user_id):
    return user_id in ADMIN_IDS


def format_film_list_page(films, page):
    chunk = films[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]
    lines = ["🎞 KINOLAR\n"]
    for film in chunk:
        lines.append(f"🆔 {film['id']} — {film['name']}")
    return "\n".join(lines)


def format_users_page(users, page):
    chunk = users[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]
    lines = ["👤 Foydalanuvchilar\n"]
    for u in chunk:
        icon = "🟢" if u["status"] == STATUS_ACTIVE else "🔴"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{u['telegram_id']} — {uname} — {icon}")
    return "\n".join(lines)


def format_channels_text(channels):
    if not channels:
        return "📢 Majburiy obuna kanallari hozircha qo'shilmagan."
    lines = ["📢 MAJBURIY OBUNA KANALLARI\n"]
    for ch in channels:
        lines.append(f"• {ch['name']} ({ch['username']}) — id: {ch['channel_id']}")
    return "\n".join(lines)


# =====================================================================
#  11-QISM: BOT VA HANDLERLAR
# =====================================================================


def create_bot():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. config.py faylida BOT_TOKEN qiymatini to'g'ri kiriting."
        )
    return telebot.TeleBot(BOT_TOKEN, parse_mode=None)


def register_all_handlers(bot):
    """Handlerlar ro'yxatdan o'tkaziladi.

    TARTIB MUHIM: holatga bog'liq (aniqroq) handlerlar avval,
    oddiy foydalanuvchining film-kod handleri esa eng oxirida bo'lishi shart.
    """

    # ---------------- /START ----------------

    @bot.message_handler(commands=["start"])
    def handle_start(message):
        user = message.from_user
        register_user_if_new(user.id, user.username or "", user.first_name or "")

        if is_admin(user.id):
            bot.send_message(
                message.chat.id, "👑 Admin panelga xush kelibsiz!", reply_markup=admin_main_menu()
            )
            logger.info(f"Admin /start bosdi: {user.id}")
            return

        channels = list_channels()
        if not channels:
            bot.send_message(
                message.chat.id, "🎬 Kino botga xush kelibsiz!\n\n🎬 Film kodini yuboring:"
            )
            return

        bot.send_message(
            message.chat.id,
            "🎬 Kino botga xush kelibsiz!\n\n"
            "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
            reply_markup=subscription_keyboard(channels),
        )
        logger.info(f"Foydalanuvchi /start bosdi: {user.id}")

    # ---------------- OBUNANI TEKSHIRISH ----------------

    @bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
    def handle_check_subscription(call):
        user_id = call.from_user.id
        subscribed, _ = check_user_subscribed(bot, user_id)

        if subscribed:
            bot.answer_callback_query(call.id, "✅ Obuna tasdiqlandi!")
            bot.edit_message_text(
                "✅ Obuna tasdiqlandi!\n\n🎬 Film kodini yuboring:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
            )
            logger.info(f"Foydalanuvchi obunani tasdiqladi: {user_id}")
        else:
            bot.answer_callback_query(
                call.id, "❌ Siz hali barcha kanallarga obuna bo'lmagansiz.", show_alert=True
            )
            try:
                bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=subscription_keyboard(list_channels()),
                )
            except Exception:
                pass

    # ---------------- KINO YUKLASH ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_UPLOAD)
    def start_upload(message):
        set_state(message.from_user.id, WAITING_VIDEO)
        bot.send_message(message.chat.id, "🎬 Film videosini yuboring:", reply_markup=cancel_menu())

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_VIDEO,
        content_types=["video"],
    )
    def receive_video(message):
        update_data(message.from_user.id, file_id=message.video.file_id)
        set_state(message.from_user.id, WAITING_NAME, get_data(message.from_user.id))
        bot.send_message(message.chat.id, "🎬 Film nomini yuboring:", reply_markup=cancel_menu())

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_VIDEO,
        content_types=["text"],
    )
    def receive_video_wrong_type(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return
        bot.send_message(message.chat.id, "❌ Iltimos, video fayl yuboring.")

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_NAME,
        content_types=["text"],
    )
    def receive_name(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return
        update_data(message.from_user.id, name=message.text.strip())
        set_state(message.from_user.id, WAITING_DESCRIPTION, get_data(message.from_user.id))
        bot.send_message(message.chat.id, "📝 Film tavsifini yuboring:", reply_markup=cancel_menu())

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id)
        and get_state(m.from_user.id) == WAITING_DESCRIPTION,
        content_types=["text"],
    )
    def receive_description(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return
        data = get_data(message.from_user.id)
        film_id = add_film(data["name"], message.text.strip(), data["file_id"])
        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id,
            "✅ Film muvaffaqiyatli qo'shildi!\n\n"
            f"🎬 Nomi: {data['name']}\n"
            f"🆔 Film kodi: {film_id}",
            reply_markup=admin_main_menu(),
        )

    # ---------------- KINO O'CHIRISH ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_DELETE)
    def start_delete(message):
        set_state(message.from_user.id, WAITING_DELETE_ID)
        bot.send_message(
            message.chat.id,
            "🆔 O'chirmoqchi bo'lgan film kodini yuboring:",
            reply_markup=cancel_menu(),
        )

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_DELETE_ID,
        content_types=["text"],
    )
    def receive_delete_id(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return

        code = message.text.strip()
        if not code.isdigit():
            bot.send_message(message.chat.id, "❌ Film kodi faqat raqamdan iborat bo'lishi kerak.")
            return

        film = find_film(code)
        if film is None:
            bot.send_message(message.chat.id, f"❌ {code} kodli film topilmadi.")
            return

        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id,
            f"🎬 {film['name']}\n🆔 {film['id']}\n\nHaqiqatan ham o'chirmoqchimisiz?",
            reply_markup=confirm_cancel_keyboard(f"delete_confirm_{film['id']}"),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("delete_confirm_"))
    def confirm_delete(call):
        if not is_admin(call.from_user.id):
            return
        film_id = call.data.replace("delete_confirm_", "")
        deleted = delete_film(film_id)
        text = (
            f"✅ {film_id} kodli film o'chirildi."
            if deleted
            else f"❌ {film_id} kodli film topilmadi."
        )
        bot.edit_message_text(
            text, chat_id=call.message.chat.id, message_id=call.message.message_id
        )
        if deleted:
            logger.info(f"Admin filmni o'chirdi: id={film_id}, admin={call.from_user.id}")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_action")
    def cancel_action(call):
        clear_state(call.from_user.id)
        bot.edit_message_text(
            "❌ Bekor qilindi.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
        bot.answer_callback_query(call.id)

    # ---------------- KINO TAHRIRLASH ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_EDIT)
    def start_edit(message):
        set_state(message.from_user.id, WAITING_EDIT_ID)
        bot.send_message(message.chat.id, "🆔 Film kodini yuboring:", reply_markup=cancel_menu())

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_EDIT_ID,
        content_types=["text"],
    )
    def receive_edit_id(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return

        code = message.text.strip()
        if not code.isdigit():
            bot.send_message(message.chat.id, "❌ Film kodi faqat raqamdan iborat bo'lishi kerak.")
            return

        film = find_film(code)
        if film is None:
            bot.send_message(message.chat.id, f"❌ {code} kodli film topilmadi.")
            return

        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id,
            f"🎬 {film['name']}\n\nNimani o'zgartirmoqchisiz?",
            reply_markup=edit_film_options_keyboard(film["id"]),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("edit_video_"))
    def edit_video_start(call):
        if not is_admin(call.from_user.id):
            return
        film_id = call.data.replace("edit_video_", "")
        set_state(call.from_user.id, WAITING_EDIT_VIDEO, {"film_id": film_id})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id, "🎬 Yangi videoni yuboring:", reply_markup=cancel_menu()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("edit_name_"))
    def edit_name_start(call):
        if not is_admin(call.from_user.id):
            return
        film_id = call.data.replace("edit_name_", "")
        set_state(call.from_user.id, WAITING_EDIT_NAME, {"film_id": film_id})
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "📝 Yangi nomni yuboring:", reply_markup=cancel_menu())

    @bot.callback_query_handler(func=lambda call: call.data.startswith("edit_desc_"))
    def edit_desc_start(call):
        if not is_admin(call.from_user.id):
            return
        film_id = call.data.replace("edit_desc_", "")
        set_state(call.from_user.id, WAITING_EDIT_DESCRIPTION, {"film_id": film_id})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id, "📄 Yangi tavsifni yuboring:", reply_markup=cancel_menu()
        )

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_EDIT_VIDEO,
        content_types=["video"],
    )
    def receive_edit_video(message):
        data = get_data(message.from_user.id)
        update_film_video(data["film_id"], message.video.file_id)
        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id, "✅ Film videosi yangilandi.", reply_markup=admin_main_menu()
        )

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_EDIT_NAME,
        content_types=["text"],
    )
    def receive_edit_name(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return
        data = get_data(message.from_user.id)
        update_film_name(data["film_id"], message.text.strip())
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "✅ Film nomi yangilandi.", reply_markup=admin_main_menu())

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id)
        and get_state(m.from_user.id) == WAITING_EDIT_DESCRIPTION,
        content_types=["text"],
    )
    def receive_edit_description(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return
        data = get_data(message.from_user.id)
        update_film_description(data["film_id"], message.text.strip())
        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id, "✅ Film tavsifi yangilandi.", reply_markup=admin_main_menu()
        )

    # ---------------- KINOLAR RO'YXATI ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_LIST)
    def show_film_list(message):
        films = list_films()
        if not films:
            bot.send_message(message.chat.id, "🎞 Hozircha filmlar yo'q.")
            return
        total_pages = max(1, (len(films) + PAGE_SIZE - 1) // PAGE_SIZE)
        markup = pagination_keyboard("filmlist", 0, total_pages) if total_pages > 1 else None
        bot.send_message(message.chat.id, format_film_list_page(films, 0), reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("filmlist_"))
    def paginate_film_list(call):
        if not is_admin(call.from_user.id):
            return
        page = int(call.data.replace("filmlist_", ""))
        films = list_films()
        total_pages = max(1, (len(films) + PAGE_SIZE - 1) // PAGE_SIZE)
        bot.edit_message_text(
            format_film_list_page(films, page),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=pagination_keyboard("filmlist", page, total_pages),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "noop")
    def noop_callback(call):
        bot.answer_callback_query(call.id)

    # ---------------- XABAR YUBORISH (BROADCAST) ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_BROADCAST)
    def start_broadcast(message):
        set_state(message.from_user.id, WAITING_BROADCAST)
        bot.send_message(
            message.chat.id,
            "📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.",
            reply_markup=cancel_menu(),
        )

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_BROADCAST,
        content_types=["text", "photo", "video", "document", "audio"],
    )
    def receive_broadcast(message):
        if message.content_type == "text" and message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return

        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "⏳ Xabar yuborilmoqda...")
        result = broadcast_message(bot, message.chat.id, message.message_id)
        bot.send_message(
            message.chat.id,
            "📊 YUBORISH YAKUNI\n\n"
            f"👥 Jami: {result['total']}\n"
            f"✅ Yuborildi: {result['sent']}\n"
            f"❌ Yuborilmadi: {result['failed']}",
            reply_markup=admin_main_menu(),
        )

    # ---------------- STATISTIKA ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_STATS)
    def show_stats(message):
        bot.send_message(message.chat.id, format_overall_stats_text())

    # ---------------- FOYDALANUVCHILAR ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_USERS)
    def show_users(message):
        users = list_users()
        if not users:
            bot.send_message(message.chat.id, "👤 Hozircha foydalanuvchilar yo'q.")
            return
        total_pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
        markup = pagination_keyboard("userslist", 0, total_pages) if total_pages > 1 else None
        bot.send_message(message.chat.id, format_users_page(users, 0), reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("userslist_"))
    def paginate_users(call):
        if not is_admin(call.from_user.id):
            return
        page = int(call.data.replace("userslist_", ""))
        users = list_users()
        total_pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
        bot.edit_message_text(
            format_users_page(users, page),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=pagination_keyboard("userslist", page, total_pages),
        )
        bot.answer_callback_query(call.id)

    # ---------------- MAJBURIY OBUNA (KANALLAR) ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_CHANNELS)
    def show_channels(message):
        channels = list_channels()
        bot.send_message(
            message.chat.id, format_channels_text(channels), reply_markup=channels_keyboard(channels)
        )

    @bot.callback_query_handler(func=lambda call: call.data == "add_channel")
    def add_channel_start(call):
        if not is_admin(call.from_user.id):
            return
        set_state(call.from_user.id, WAITING_CHANNEL)
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "📢 Kanal ma'lumotlarini quyidagi formatda yuboring:\n\n"
            "channel_id|username|nomi|invite_link\n\n"
            "Masalan:\n-100123456789|@kino_channel|Kino Channel|https://t.me/kino_channel",
            reply_markup=cancel_menu(),
        )

    @bot.message_handler(
        func=lambda m: is_admin(m.from_user.id) and get_state(m.from_user.id) == WAITING_CHANNEL,
        content_types=["text"],
    )
    def receive_channel(message):
        if message.text == BTN_CANCEL:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())
            return

        parts = message.text.strip().split("|")
        if len(parts) != 4:
            bot.send_message(
                message.chat.id,
                "❌ Format noto'g'ri. Quyidagicha yuboring:\n\nchannel_id|username|nomi|invite_link",
            )
            return

        channel_id, username, name, invite_link = [p.strip() for p in parts]
        add_channel(channel_id, username, name, invite_link)
        clear_state(message.from_user.id)
        bot.send_message(
            message.chat.id, f"✅ Kanal qo'shildi: {name}", reply_markup=admin_main_menu()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("remove_channel_"))
    def remove_channel_cb(call):
        if not is_admin(call.from_user.id):
            return
        channel_id = call.data.replace("remove_channel_", "")
        removed = remove_channel(channel_id)
        bot.answer_callback_query(call.id, "✅ Kanal o'chirildi." if removed else "❌ Topilmadi.")
        channels = list_channels()
        bot.edit_message_text(
            format_channels_text(channels),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=channels_keyboard(channels),
        )

    # ---------------- SOZLAMALAR ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_SETTINGS)
    def show_settings(message):
        bot.send_message(
            message.chat.id,
            "⚙️ SOZLAMALAR\n\n"
            f"👑 Adminlar soni: {len(ADMIN_IDS)}\n"
            f"📢 Majburiy obuna kanallari: {len(list_channels())}\n"
            f"🆔 Keyingi film kodi: {get_next_film_id()}\n",
        )

    # ---------------- BEKOR QILISH (umumiy) ----------------

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == BTN_CANCEL)
    def cancel_flow(message):
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "❌ Bekor qilindi.", reply_markup=admin_main_menu())

    # ---------------- ODDIY FOYDALANUVCHI: FILM KODI ----------------
    # ENG OXIRGI handler bo'lishi shart!

    @bot.message_handler(
        func=lambda m: not is_admin(m.from_user.id) and get_state(m.from_user.id) is None,
        content_types=["text"],
    )
    def handle_film_code(message):
        if message.text.startswith("/"):
            return

        user_id = message.from_user.id
        channels = list_channels()

        if channels:
            subscribed, _ = check_user_subscribed(bot, user_id)
            if not subscribed:
                bot.send_message(
                    message.chat.id,
                    "❌ Siz hali barcha kanallarga obuna bo'lmagansiz.\n\n"
                    "Kanallarga obuna bo'lib, qaytadan tekshiring.",
                    reply_markup=subscription_keyboard(channels),
                )
                return

        code = message.text.strip()

        if not code.isdigit():
            bot.send_message(
                message.chat.id,
                "❌ Film kodi faqat raqamdan iborat bo'lishi kerak.\n\nMasalan:\n126",
            )
            return

        film = find_film(code)
        if film is None:
            bot.send_message(message.chat.id, f"❌ {code} kodli film topilmadi.")
            return

        try:
            bot.send_video(
                message.chat.id,
                film["file_id"],
                caption=f"🎬 {film['name']}\n\n{film['description']}",
            )
            increment_views(film["id"])
            logger.info(f"Film yuborildi: id={film['id']}, user={user_id}")
        except Exception as e:
            logger.error(f"Film yuborishda xatolik: id={film['id']}, xato={e}")
            bot.send_message(
                message.chat.id,
                "❌ Filmni yuborishda xatolik yuz berdi, qaytadan urinib ko'ring.",
            )


# =====================================================================
#  12-QISM: SCHEDULER — KUNIGA 2 MARTA HISOBOT (09:00 va 21:00)
# =====================================================================


def send_daily_report(bot):
    record_daily_snapshot()
    text = format_daily_report_text()
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception as e:
            logger.error(f"Adminga hisobot yuborilmadi ({admin_id}): {e}")
    logger.info("Kunlik hisobot barcha adminlarga yuborildi.")


def start_scheduler(bot):
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    for hour, minute in REPORT_TIMES:
        scheduler.add_job(
            send_daily_report,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
            args=[bot],
            id=f"daily_report_{hour}_{minute}",
            replace_existing=True,
        )
    scheduler.start()
    logger.info(f"Scheduler ishga tushdi. Hisobot vaqtlari: {REPORT_TIMES} ({TIMEZONE})")
    return scheduler


# =====================================================================
#  13-QISM: ISHGA TUSHIRISH
# =====================================================================


def main():
    ensure_all_data_files()

    bot = create_bot()
    register_all_handlers(bot)
    scheduler = start_scheduler(bot)

    logger.info("Bot ishga tushdi.")
    try:
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    except apihelper.ApiTelegramException as e:
        logger.error(f"Telegram API xatoligi: {e}")
    except Exception as e:
        logger.error(f"Kutilmagan xatolik: {e}")
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    main()
