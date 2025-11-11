# --------------------@xeno_kakarot----------------------
import os
import asyncio
import tempfile
import aiohttp
import traceback
from pyrogram import Client, filters
from yt_dlp import YoutubeDL
from urllib.parse import urlparse

# --------------------
# CONFIG
# --------------------
API_ID = 23255238
API_HASH = "009e3d8c1bdc89d5387cdd8fd182ec15"
BOT_TOKEN = "8523059923:AAG3VrDe4uZZa-3HLzYmZ72YTiYw0fwhKDE"

TELEGRAM_MAX_BYTES = 2 * 1024**3
ALLOWED_DOMAINS = ("teraboxurl.com", "terabox.com", "terabox.io", "terabox.cn")

YTDLP_OPTS = {
    "quiet": True,
    "skip_download": True,
    "no_warnings": True,
    "socket_timeout": 30,
}

app = Client("tera_debug", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# helper to find terabox links
def find_terabox_url(text: str):
    if not text:
        return None
    tokens = text.split()
    for t in tokens:
        try:
            parsed = urlparse(t.strip())
            host = parsed.netloc.lower()
            if not host and t.startswith("www."):
                host = t.split("/")[0].lower()
            host = host.split(":")[0]
            if any(d in host for d in ALLOWED_DOMAINS):
                return t.strip()
        except Exception:
            continue
    return None


async def extract_direct_url(url: str) -> str | None:
    loop = asyncio.get_event_loop()

    def run():
        try:
            with YoutubeDL(YTDLP_OPTS) as ydl:
                info = ydl.extract_info(url, download=False)
                if isinstance(info, dict) and info.get("_type") == "playlist":
                    entries = info.get("entries") or []
                    if entries:
                        info = entries[0]
                formats = info.get("formats") or []
                for f in reversed(formats):
                    f_url = f.get("url")
                    if f_url and f_url.startswith(("http://", "https://")):
                        return f_url
                top = info.get("url")
                if top and isinstance(top, str) and top.startswith(("http://", "https://")):
                    return top
        except Exception:
            return None
        return None

    return await loop.run_in_executor(None, run)


async def stream_download_to_file(session: aiohttp.ClientSession, src_url: str, dest_path: str, progress_cb=None):
    async with session.get(src_url, timeout=aiohttp.ClientTimeout(total=None)) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            downloaded = 0
            async for chunk in resp.content.iter_chunked(1024 * 64):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        await progress_cb(downloaded)
    return dest_path


# ---- Simple command handlers ----
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    print(f"[INFO] /start from user={m.from_user and m.from_user.id} chat={m.chat.id}")
    await m.reply_text("👋 Bot chaloo hai. Send /ping to test or send a Terabox link.")


@app.on_message(filters.command("ping") & filters.private)
async def ping_cmd(c, m):
    print(f"[INFO] /ping from user={m.from_user and m.from_user.id} chat={m.chat.id}")
    await m.reply_text("pong")


# ---- Global debug logger for every update ----

async def log_all_messages(c, m):
    try:
        uid = m.from_user.id if m.from_user else None
        chat_id = m.chat.id if m.chat else None
        kind = "text" if m.text else ("photo" if m.photo else "other")
        text = (m.text or m.caption or "")[:200]
        print(f"[UPDATE] from_user={uid} chat_id={chat_id} kind={kind} text={repr(text)} msg_id={m.id}")
    except Exception:
        print("[ERROR] failed to log update:")
        traceback.print_exc()


# ---- Terabox listener (keeps original behavior) ----
@app.on_message(filters.text & ~filters.command("start") & ~filters.me)
async def auto_terabox_listener(client, message):
    try:
        uid = message.from_user.id if message.from_user else None
        chat = message.chat.id if message.chat else None
        print(f"[HANDLER] Received text from user={uid} chat={chat} text={message.text[:120]!r}")

        url = find_terabox_url(message.text or message.caption or "")
        if not url:
            print("[HANDLER] No terabox link found in message.")
            return

        status_msg = await message.reply_text("🔎 Terabox link mila — processing kar raha hoon...")

        direct = await extract_direct_url(url)
        if direct:
            await status_msg.edit_text("✅ Direct URL mil gaya, download start kar raha hoon...")
        else:
            await status_msg.edit_text("⚠️ Direct link nahi mil saka. Using original link and trying download...")
            direct = url  # fallback

        tmpf = tempfile.NamedTemporaryFile(delete=False)
        tmpf.close()
        tmp_path = tmpf.name
        print(f"[HANDLER] Download target: {direct} -> tmp: {tmp_path}")

        async with aiohttp.ClientSession() as session:
            bytes_prev = 0

            async def progress_cb(downloaded):
                nonlocal bytes_prev
                if downloaded - bytes_prev >= 1024 * 1024:
                    bytes_prev = downloaded
                    print(f"[DOWNLOAD] {downloaded/(1024*1024):.1f} MB downloaded")
                    try:
                        await status_msg.edit_text(f"⬇️ Downloading... {downloaded/(1024*1024):.1f} MB")
                    except Exception:
                        pass

            await stream_download_to_file(session, direct, tmp_path, progress_cb=progress_cb)

        size = os.path.getsize(tmp_path)
        print(f"[HANDLER] Download complete ({size/(1024*1024):.2f} MB). Preparing to upload.")

        # check file size
        if size > TELEGRAM_MAX_BYTES:
            try:
                # direct download link fallback (if valid)
                await status_msg.edit_text(
                    f"⚠️ File badi hai ({size/(1024*1024):.2f} MB)\n"
                    f"📥 Direct download link (valid for some time):\n{direct}\n\n"
                    f"Telegram pe 2 GB se zyada upload nahi hota."
                )
                print(f"[HANDLER] Skipped upload — file too large ({size} bytes).")
            except Exception as e:
                print("[HANDLER] Failed to send big-file message:", e)
            return

        await status_msg.edit_text("⏫ Uploading to Telegram...")
        try:
            await client.send_video(chat_id=message.chat.id, video=tmp_path, caption=f"From: {url}")
            print("[HANDLER] Sent as video.")
        except Exception as e:
            print("[HANDLER] send_video failed, trying send_document:", e)
            await client.send_document(chat_id=message.chat.id, document=tmp_path, caption=f"From: {url}")
            print("[HANDLER] Sent as document.")
        await status_msg.delete()

    except Exception as e:
        print("[ERROR] Exception in handler:", e)
        traceback.print_exc()
        try:
            await message.reply_text(f"❌ Error: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
                print("[CLEANUP] removed tmp file")
        except Exception:
            pass


# ---- Start the bot ----
if __name__ == "__main__":
    print("🚀 Debug Bot starting... (Termux friendly)")
    app.run()
