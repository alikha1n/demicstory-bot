#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Demic Story — Telegram orqali YouTube'ga avtomatik video yuklovchi bot.

ISHLASH SXEMASI:
  1. Telegram botga video yuborasan + caption (sarlavha) yozasan
  2. Bot videoni saqlaydi va navbatga qo'shadi
  3. Belgilangan vaqtlarda (default 14:00 va 21:00 Toshkent) navbatdan
     1 tadan YouTube'ga yuklaydi
  4. Yuklagach senga xabar beradi

KOMANDALAR (Telegram'da yoz):
  /start        — bot haqida
  /queue        — navbatda nechta video borligini ko'rsatadi
  /times        — joriy yuklash vaqtlarini ko'rsatadi
  /settime 14:00 21:00   — yuklash vaqtlarini o'zgartirish (xohlagancha vaqt)
  /skip         — navbatdagi 1-videoni o'tkazib yuborish (o'chiradi)
  /uploadnow    — navbatdagi 1-videoni HOZIROQ yuklash (test uchun)
"""

import os
import json
import logging
import asyncio
from datetime import datetime

import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import google.oauth2.credentials
import googleapiclient.discovery
import googleapiclient.http

# ============================================================
#  SOZLAMALAR — shu yerni o'zgartirasan
# ============================================================
BASE_DIR    = "/opt/demicbot"            # bot papkasi (VPS'da)
VIDEO_DIR   = os.path.join(BASE_DIR, "videos")     # videolar shu yerga saqlanadi
QUEUE_FILE  = os.path.join(BASE_DIR, "queue.json")  # navbat
CONFIG_FILE = os.path.join(BASE_DIR, "config.json") # vaqt sozlamalari
TOKEN_FILE  = os.path.join(BASE_DIR, "token.json")  # YouTube OAuth token

TIMEZONE = "Asia/Tashkent"               # vaqt mintaqasi
TELEGRAM_TOKEN = "8653561805:AAGwmJl8JDrPz_iGlcBQbxXJ1yz21jnSEWU"  # @BotFather'dan olingan
ADMIN_ID = 7434706702                    # SENING Telegram ID'ing (faqat sen boshqarasan)

DEFAULT_TIMES = ["14:00", "21:00"]       # default yuklash vaqtlari
# ============================================================

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("demicbot")

tz = pytz.timezone(TIMEZONE)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=tz)

# ---------- papkalar tayyorlash ----------
os.makedirs(VIDEO_DIR, exist_ok=True)


# ---------- yordamchi funksiyalar (fayl o'qish/yozish) ----------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"{path} o'qishda xato: {e}")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_queue():
    return load_json(QUEUE_FILE, [])


def set_queue(q):
    save_json(QUEUE_FILE, q)


def get_times():
    cfg = load_json(CONFIG_FILE, {"times": DEFAULT_TIMES})
    return cfg.get("times", DEFAULT_TIMES)


def set_times(times):
    save_json(CONFIG_FILE, {"times": times})


# ---------- YouTube'ga yuklash ----------
def upload_to_youtube(video_path, title, description=""):
    """Bitta videoni YouTube'ga Shorts sifatida yuklaydi."""
    creds_data = load_json(TOKEN_FILE, None)
    if not creds_data:
        raise RuntimeError("token.json topilmadi — YouTube ulanmagan!")

    creds = google.oauth2.credentials.Credentials(**creds_data)
    youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)

    # #Shorts teg sarlavha/tavsifda bo'lsa, Shorts deb tan olinadi
    body = {
        "snippet": {
            "title": title[:100],          # YouTube limit: 100 belgi
            "description": description,
            "categoryId": "17",            # 17 = Sport
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,   # "не для детей"
        },
    }

    media = googleapiclient.http.MediaFileUpload(
        video_path, chunksize=-1, resumable=True
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
    return response["id"]


# ---------- planlangan yuklash (scheduler chaqiradi) ----------
async def scheduled_upload():
    queue = get_queue()
    if not queue:
        log.info("Navbat bo'sh — yuklanmadi.")
        return

    item = queue[0]
    video_path = item["path"]
    title = item["title"]

    try:
        log.info(f"Yuklanmoqda: {title}")
        video_id = await asyncio.to_thread(
            upload_to_youtube, video_path, title, item.get("description", "")
        )
        url = f"https://youtube.com/shorts/{video_id}"

        # navbatdan o'chir + video faylni o'chir (joy tejash)
        queue.pop(0)
        set_queue(queue)
        try:
            os.remove(video_path)
        except OSError:
            pass

        await bot.send_message(
            ADMIN_ID,
            f"✅ YouTube'ga yuklandi!\n\n📹 {title}\n🔗 {url}\n\n📋 Navbatda qoldi: {len(queue)} ta"
        )
        log.info(f"Yuklandi: {url}")

    except Exception as e:
        log.error(f"Yuklashda xato: {e}")
        await bot.send_message(
            ADMIN_ID,
            f"❌ Yuklashda xato bo'ldi:\n{e}\n\nVideo navbatda qoldi, keyingi safar qayta urinadi."
        )


# ---------- scheduler'ni vaqtlarga sozlash ----------
def reschedule():
    scheduler.remove_all_jobs()
    for t in get_times():
        hh, mm = t.split(":")
        scheduler.add_job(
            scheduled_upload, "cron",
            hour=int(hh), minute=int(mm),
            id=f"upload_{t}", replace_existing=True
        )
    log.info(f"Vaqtlar sozlandi: {get_times()}")


# ============================================================
#  TELEGRAM KOMANDALAR
# ============================================================
def admin_only(handler):
    async def wrapper(message: types.Message, *a, **kw):
        if message.from_user.id != ADMIN_ID:
            return
        return await handler(message)
    return wrapper


@dp.message(Command("start"))
@admin_only
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Demic Story avtoyuklovchi bot\n\n"
        "Menga VIDEO yubor, caption'ga SARLAVHA yoz —\n"
        "men uni navbatga qo'shaman va belgilangan vaqtda YouTube'ga yuklayman.\n\n"
        "Komandalar:\n"
        "/queue — navbat\n"
        "/times — yuklash vaqtlari\n"
        "/settime 14:00 21:00 — vaqt o'zgartirish\n"
        "/uploadnow — hozir yuklash (test)\n"
        "/skip — navbatdagini o'tkazib yuborish"
    )


@dp.message(Command("queue"))
@admin_only
async def cmd_queue(message: types.Message):
    q = get_queue()
    if not q:
        await message.answer("📋 Navbat bo'sh.")
        return
    lines = [f"{i+1}. {item['title']}" for i, item in enumerate(q[:20])]
    await message.answer(f"📋 Navbatda {len(q)} ta video:\n\n" + "\n".join(lines))


@dp.message(Command("times"))
@admin_only
async def cmd_times(message: types.Message):
    times = get_times()
    await message.answer(
        f"⏰ Joriy yuklash vaqtlari ({TIMEZONE}):\n" + ", ".join(times) +
        "\n\nO'zgartirish: /settime 14:00 21:00"
    )


@dp.message(Command("settime"))
@admin_only
async def cmd_settime(message: types.Message):
    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Misol: /settime 14:00 21:00")
        return
    # vaqt formatini tekshir
    valid = []
    for p in parts:
        try:
            hh, mm = p.split(":")
            assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
            valid.append(f"{int(hh):02d}:{int(mm):02d}")
        except Exception:
            await message.answer(f"❌ Noto'g'ri vaqt: {p}\nTo'g'ri format: 14:00")
            return
    set_times(valid)
    reschedule()
    await message.answer(f"✅ Yuklash vaqtlari yangilandi:\n" + ", ".join(valid))


@dp.message(Command("skip"))
@admin_only
async def cmd_skip(message: types.Message):
    q = get_queue()
    if not q:
        await message.answer("Navbat bo'sh.")
        return
    item = q.pop(0)
    set_queue(q)
    try:
        os.remove(item["path"])
    except OSError:
        pass
    await message.answer(f"⏭ O'tkazib yuborildi: {item['title']}\nNavbatda: {len(q)} ta")


@dp.message(Command("uploadnow"))
@admin_only
async def cmd_uploadnow(message: types.Message):
    await message.answer("⏳ Hozir yuklayapman...")
    await scheduled_upload()


# ---------- VIDEO qabul qilish ----------
@dp.message(F.video | F.document)
@admin_only
async def handle_video(message: types.Message):
    # video yoki document (ba'zan video document sifatida keladi)
    file_obj = message.video or message.document
    if not file_obj:
        return

    caption = (message.caption or "").strip()
    if not caption:
        await message.answer(
            "⚠️ Sarlavha yo'q!\n\nVideoni QAYTA yubor va caption'ga sarlavha yoz.\n"
            "Misol:\nLower Back Pain Fix 🔥 #fitness #backpain #workout"
        )
        return

    await message.answer("⏳ Video yuklab olinmoqda...")

    # faylni VPS'ga saqlash
    ts = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}.mp4"
    save_path = os.path.join(VIDEO_DIR, filename)

    file = await bot.get_file(file_obj.file_id)
    await bot.download_file(file.file_path, save_path)

    # navbatga qo'shish
    q = get_queue()
    q.append({
        "path": save_path,
        "title": caption,
        "description": caption,   # tavsif sifatida ham sarlavhani ishlatamiz
        "added": ts,
    })
    set_queue(q)

    times = get_times()
    await message.answer(
        f"✅ Navbatga qo'shildi!\n\n"
        f"📹 {caption}\n\n"
        f"📋 Navbatda: {len(q)} ta video\n"
        f"⏰ Yuklash vaqtlari: {', '.join(times)}"
    )


# ============================================================
#  INSTAGRAM HAVOLA QABUL QILISH (yt-dlp bilan)
# ============================================================
def download_instagram(url, save_path):
    """Instagram videoni yuklab oladi va caption qaytaradi."""
    import yt_dlp
    # URL'ni tozalash (?igsh= va boshqa parametrlarni olib tashlash)
    clean_url = url.split("?")[0]
    opts = {
        "outtmpl": save_path,
        "format": "mp4/best",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(clean_url, download=True)
        caption = info.get("description") or info.get("title") or "Demic Story"
    return caption


@dp.message(F.text.startswith("http"))
@admin_only
async def handle_link(message):
    url = message.text.strip()
    if "instagram.com" not in url and "tiktok.com" not in url:
        await message.answer("⚠️ Bu Instagram yoki TikTok havolasi emas.\n\nInstagram/TikTok video havolasini tashla yoki to'g'ridan-to'g'ri video yubor.")
        return

    await message.answer("⏳ Video yuklab olinmoqda... (biroz kutib tur)")

    ts = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    save_path = os.path.join(VIDEO_DIR, f"{ts}.mp4")

    try:
        caption = await asyncio.to_thread(download_instagram, url, save_path)
    except Exception as e:
        log.error(f"Instagram yuklab olishda xato: {e}")
        await message.answer(f"❌ Yuklab olishda xato:\n{e}\n\nHavola to'g'rimi? Qayta urinib ko'r.")
        return

    if not os.path.exists(save_path):
        await message.answer("❌ Video yuklanmadi. Havolani tekshir.")
        return

    # caption tozalash va sarlavha/tavsif tayyorlash
    caption = caption.strip()
    title = caption[:100] if caption else "Demic Story"

    q = get_queue()
    q.append({
        "path": save_path,
        "title": title,
        "description": caption,
        "added": ts,
    })
    set_queue(q)

    await message.answer(
        f"✅ Navbatga qo'shildi!\n\n"
        f"📹 {title}\n\n"
        f"📋 Navbatda: {len(q)} ta video\n"
        f"⏰ Yuklash vaqtlari: {', '.join(get_times())}"
    )


# ============================================================
#  ISHGA TUSHIRISH
# ============================================================
async def main():
    reschedule()
    scheduler.start()
    log.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
