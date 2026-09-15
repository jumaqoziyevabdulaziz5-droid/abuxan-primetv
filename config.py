"""
config.py — Kino bot sozlamalari.

Barcha sozlamalar shu faylda. .env fayl ISHLATILMAYDI —
token va admin ID'lar to'g'ridan-to'g'ri shu yerda yoziladi.

Database ham ishlatilmaydi — barcha ma'lumotlar data/ papkasidagi TXT fayllarda.
"""

import os

# =====================================================================
#  SHU YERNI TO'LDIRING
# =====================================================================

# @BotFather dan olingan token
BOT_TOKEN = "8892389501:AAFe-FP4oQKRCCE9NCGbG75LejikGn9DBjw"

# Adminlarning Telegram ID lari (bir nechta bo'lishi mumkin)
# O'z ID ingizni @userinfobot dan bilib olasiz
ADMIN_IDS = {
    7170382261,
}

# Vaqt mintaqasi (kunlik hisobot uchun)
TIMEZONE = "Asia/Tashkent"

# =====================================================================
#  PAPKALAR
# =====================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# =====================================================================
#  TXT FAYLLAR
# =====================================================================

USERS_FILE = os.path.join(DATA_DIR, "users.txt")
FILMS_FILE = os.path.join(DATA_DIR, "films.txt")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.txt")
ADMINS_FILE = os.path.join(DATA_DIR, "admins.txt")
STATISTICS_FILE = os.path.join(DATA_DIR, "statistics.txt")
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.txt")
FILM_IDS_FILE = os.path.join(DATA_DIR, "film_ids.txt")
NEXT_FILM_ID_FILE = os.path.join(DATA_DIR, "next_film_id.txt")

LOG_FILE = os.path.join(LOGS_DIR, "bot.log")

# =====================================================================
#  BOSHQA SOZLAMALAR
# =====================================================================

# Kunlik hisobot vaqtlari: (soat, daqiqa)
REPORT_TIMES = [(9, 0), (21, 0)]

# Pagination — bir sahifada nechta element ko'rsatilsin
PAGE_SIZE = 10

# =====================================================================
#  ADMIN TUGMALARI
# =====================================================================

BTN_UPLOAD = "🎬 Kino yuklash"
BTN_DELETE = "🗑 Kino o'chirish"
BTN_EDIT = "✏️ Kino tahrirlash"
BTN_LIST = "🎞 Kinolar ro'yxati"
BTN_STATS = "📊 Statistika"
BTN_BROADCAST = "📢 Xabar yuborish"
BTN_CHANNELS = "📢 Majburiy obuna"
BTN_USERS = "👥 Foydalanuvchilar"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_CANCEL = "❌ Bekor qilish"
