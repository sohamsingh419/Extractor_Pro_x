import datetime
import pytz
import re
import aiofiles
import os
import base64
import asyncio
import time
import json
import hmac
import hashlib
import random
import glob
from pyrogram import filters
from Extractor import app
from config import CHANNEL_ID, THUMB_URL
from colorama import Fore, Style, init
from termcolor import colored
from pyrogram.errors import FloodWait, RPCError
import aiohttp
from datetime import timedelta
from Extractor.core.utils import forward_to_log
import requests
import subprocess

init(autoreset=True)

appname = "Utkarsh"
txt_dump = CHANNEL_ID
MAX_RETRIES = 5
TIMEOUT = 90
UPDATE_DELAY = 5
UPDATE_INTERVAL = 15
EDIT_LOCK = asyncio.Lock()

API_DELAY = 2.5
MAX_CONCURRENT = 1
RATE_LIMIT_RETRY = 600
BATCH_PAUSE = 10
SUBJECT_PAUSE = 5
TOPIC_PAUSE = 3
CONTENT_PAUSE = 2
JITTER_MIN = 1
JITTER_MAX = 4

STATE_FILE = "./bot_state.json"
UPLOAD_STATE_FILE = "./upload_state.json"

# One interactive upload per chat. Commands below control the active job
# without interrupting a file that is already being downloaded/uploaded.
ACTIVE_UPLOADS = {}

BASE_URL = "https://api.asmultiverse.app"
DEVICE_ID = "2cfbaa6be65acdc5"
SECRET_KEY = "1mBD4OQnsBMBaN6oISWwTmryX1lHjkW9XLZhsirCOT0="
DEFAULT_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjE0NDQ2MTEzIiwiZGV2aWNlX3R5cGUiOiI0IiwidmVyc2lvbl9jb2RlIjoiMSIsImljciI6IjAiLCJpYXQiOjE3ODgyMzYwOTYsImV4cCI6MTc5MDM5NjA5Nn0.Sn6Ad_klMRf3HJmE81ETHXSIGtSs1r4FyflU_77x3wI"

api_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_upload_state():
    if os.path.exists(UPLOAD_STATE_FILE):
        try:
            with open(UPLOAD_STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_upload_state(state):
    with open(UPLOAD_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(UPLOAD_STATE_FILE):
        os.remove(UPLOAD_STATE_FILE)


# ═══════════════════════════════════════════════════════════════
# STORAGE CLEANUP FUNCTIONS
# ═══════════════════════════════════════════════════════════════
async def cleanup_file(filepath):
    """Delete file and all related temp files immediately"""
    if not filepath:
        return
    try:
        # Delete main file
        if os.path.exists(filepath):
            os.remove(filepath)
            print(colored(f"  🗑️ Deleted: {os.path.basename(filepath)}", "green"))

        # Delete all related temp files
        base = os.path.splitext(filepath)[0]
        for ext in ['.jpg', '.part', '.ytdl', '.mp4', '.mkv', '.webm', '.pdf', '.txt', '.mp4.part', '.webm.part']:
            temp = base + ext
            if os.path.exists(temp):
                os.remove(temp)
                print(colored(f"  🗑️ Deleted temp: {os.path.basename(temp)}", "green"))
    except Exception as e:
        print(colored(f"  ⚠️ Cleanup error: {e}", "yellow"))


async def purge_downloads_folder():
    """Emergency: Delete everything in downloads folder"""
    try:
        files = glob.glob(f"{DOWNLOAD_DIR}/*")
        for f in files:
            if os.path.isfile(f):
                os.remove(f)
        print(colored(f"🗑️ Purged {len(files)} files from downloads/", "green"))
    except Exception as e:
        print(colored(f"⚠️ Purge error: {e}", "yellow"))


async def get_folder_size():
    """Get current downloads folder size in MB"""
    try:
        total = 0
        for dirpath, dirnames, filenames in os.walk(DOWNLOAD_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total / (1024 * 1024)
    except:
        return 0


def get_asset_headers():
    """Headers accepted by Utkarsh S3 assets (images/PDFs/video files)."""
    return {
        "User-Agent": "okhttp/3.9.1",
        "Accept": "*/*",
        # Do not ask for compressed bytes: a PDF must be written exactly as
        # received and some CDN proxies close compressed long-lived streams.
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }


def get_utkarsh_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://utkarshapp.com/",
        "Origin": "https://utkarshapp.com",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }


def sanitize_name(name, max_length=55):
    if not name:
        return "Unknown"
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    name = name.replace(" ", "_")
    name = "".join(c for c in name if ord(c) < 128)
    if len(name) > max_length:
        name = name[:max_length]
    return name or "Unknown"


def build_caption(index, icon, title, display_name, footer=""):
    """Build captions as literal text; never let titles become Telegram spoilers."""
    caption = f"[{str(index).zfill(3)}] {icon} {title}\n📚 Batch: {display_name}"
    if footer.strip():
        caption += f"\n{footer.strip()}"
    return caption


async def wait_for_upload_command(chat_id):
    """Wait while paused and return True when the batch was permanently stopped."""
    control = ACTIVE_UPLOADS.get(chat_id)
    if not control:
        return False
    while control["paused"].is_set() and not control["fullstop"]:
        await asyncio.sleep(1)
    return control["fullstop"]


async def smart_sleep(base_delay):
    jitter = random.uniform(JITTER_MIN, JITTER_MAX)
    total = base_delay + jitter
    await asyncio.sleep(total)


def _content_type_is_pdf(content_type="", content_disposition=""):
    """Return True when HTTP metadata identifies a PDF, even without .pdf in URL."""
    metadata = f"{content_type} {content_disposition}".lower()
    return "application/pdf" in metadata or "application/x-pdf" in metadata or ".pdf" in metadata


async def url_is_pdf(url, headers=None, timeout=30):
    """Probe a link so extension-less PDF URLs do not enter the video downloader."""
    try:
        h = headers or get_asset_headers()
        response = requests.head(url, headers=h, timeout=timeout, allow_redirects=True)
        if response.status_code < 400 and _content_type_is_pdf(
            response.headers.get("Content-Type", ""),
            response.headers.get("Content-Disposition", ""),
        ):
            return True
        # Some CDNs reject HEAD; a streamed GET still avoids downloading the body.
        if response.status_code in (403, 405) or not response.headers:
            response = requests.get(url, headers=h, timeout=timeout, stream=True)
            is_pdf = _content_type_is_pdf(
                response.headers.get("Content-Type", ""),
                response.headers.get("Content-Disposition", ""),
            )
            response.close()
            return is_pdf
    except Exception as e:
        print(colored(f"  ⚠️ PDF type probe failed: {e}", "yellow"))
    return False


def is_pdf_file(filepath):
    """Validate the PDF magic header so an HTML error page is never uploaded as PDF."""
    try:
        with open(filepath, "rb") as f:
            return f.read(5) == b"%PDF-"
    except (OSError, TypeError):
        return False


async def download_file(url, filepath, headers=None, timeout=120):
    """Download an asset safely from S3/CloudFront.

    Koyeb instances can see a transient CDN 403 or a connection that closes
    before a large PDF is complete.  This function first tries a normal
    streamed GET, then resumes the missing bytes with HTTP Range requests.
    Every response is checked and data is written to a .part file, so an HTML
    error page or truncated PDF can never be handed to Telegram.
    """
    return await asyncio.to_thread(
        _download_file_sync, url, filepath, headers, timeout
    )


def _download_file_sync(url, filepath, headers=None, timeout=120):
    last_error = "unknown download error"
    header_sets = [headers or get_asset_headers(), get_utkarsh_headers()]
    part_path = f"{filepath}.part"
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    for header_index, base_headers in enumerate(header_sets):
        h = dict(base_headers)
        h["Accept-Encoding"] = "identity"
        request_urls = [url]
        # Refresh a transient cached CloudFront 403 once; this preserves the
        # original object path and does not bypass authentication or use a proxy.
        if header_index == len(header_sets) - 1:
            separator = "&" if "?" in url else "?"
            request_urls.append(f"{url}{separator}_download_retry={int(time.time())}")
        for request_url in request_urls:
            try:
                with requests.Session() as session:
                    response = session.get(
                        request_url,
                        headers=h,
                        timeout=(15, timeout),
                        stream=True,
                        allow_redirects=True,
                    )
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        response.close()
                        # A second header profile is useful for CDNs that treat
                        # mobile and browser clients differently. It is not a
                        # proxy or an access-control bypass.
                        continue

                    expected = response.headers.get("Content-Length")
                    expected = int(expected) if expected and expected.isdigit() else None
                    written = 0
                    with open(part_path, "wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                output.write(chunk)
                                written += len(chunk)
                    response.close()

                    if written and (expected is None or written == expected):
                        os.replace(part_path, filepath)
                        return True
                    last_error = f"incomplete response ({written}/{expected or '?' } bytes)"

                    # Resume from the exact byte received. This is especially
                    # important for Koyeb's outbound connection timeout.
                    if expected and written < expected:
                        with open(part_path, "ab") as output:
                            while written < expected:
                                end = min(written + 1024 * 1024 - 1, expected - 1)
                                range_headers = dict(h)
                                range_headers["Range"] = f"bytes={written}-{end}"
                                range_response = session.get(
                                    request_url,
                                    headers=range_headers,
                                    timeout=(15, timeout),
                                    stream=True,
                                    allow_redirects=True,
                                )
                                if range_response.status_code != 206:
                                    last_error = f"range HTTP {range_response.status_code} at byte {written}"
                                    range_response.close()
                                    break
                                range_start = range_response.headers.get("Content-Range", "")
                                if not range_start.startswith(f"bytes {written}-"):
                                    last_error = f"invalid Content-Range at byte {written}"
                                    range_response.close()
                                    break
                                chunk_bytes = 0
                                for chunk in range_response.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        output.write(chunk)
                                        chunk_bytes += len(chunk)
                                range_response.close()
                                if not chunk_bytes:
                                    last_error = f"empty range response at byte {written}"
                                    break
                                written += chunk_bytes
                        if written == expected:
                            os.replace(part_path, filepath)
                            return True
            except (requests.RequestException, OSError, ValueError) as exc:
                last_error = str(exc)
            finally:
                # Never leave a corrupt file at the final path.
                if os.path.exists(part_path) and not os.path.exists(filepath):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass

    print(colored(f"  ⚠️ Direct download failed ({last_error}): {url}", "yellow"))
    return False


async def download_with_ytdlp(url, output_name, quality="720"):
    header_args = '--add-header "User-Agent:okhttp/3.9.1" --add-header "Accept:*/*" --add-header "Accept-Encoding:gzip"'
    if ".pdf" in url.lower():
        cmd = f'yt-dlp {header_args} -o "{output_name}.pdf" "{url}" -R 25 --fragment-retries 25'
    elif ".m3u8" in url.lower() or "jw" in url.lower():
        cmd = f'yt-dlp {header_args} -o "{output_name}.mp4" "{url}" -R 25 --fragment-retries 25'
    else:
        ytf = f"b[height<={quality}]/bv[height<={quality}]+ba/b/bv+ba"
        cmd = f'yt-dlp {header_args} -f "{ytf}" "{url}" -o "{output_name}.mp4" -R 25 --fragment-retries 25 --external-downloader aria2c --downloader-args "aria2c: -x 16 -j 32"'

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    for ext in [".mp4", ".mkv", ".webm", ".pdf"]:
        if os.path.exists(f"{output_name}{ext}") and os.path.getsize(f"{output_name}{ext}") > 5000:
            return f"{output_name}{ext}"
    return None


async def download_with_ffmpeg(url, output_name):
    headers = get_utkarsh_headers()
    ffmpeg_headers = ""
    for k, v in headers.items():
        ffmpeg_headers += f"{k}: {v}\r\n"
    output = f"{output_name}.mp4"
    cmd = f'ffmpeg -headers "{ffmpeg_headers}" -i "{url}" -c copy -bsf:a aac_adtstoasc -y "{output}"'
    subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    if os.path.exists(output) and os.path.getsize(output) > 10000:
        return output
    return None


async def get_video_duration(filepath):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        return float(result.stdout.strip())
    except:
        return 0


# ═══════════════════════════════════════════════════════════════
# UPLOAD WITH GUARANTEED CLEANUP
# ═══════════════════════════════════════════════════════════════
async def upload_video(bot_client, chat_id, filepath, caption, thumb_path=None, duration=0):
    downloaded_path = filepath
    try:
        if not os.path.exists(filepath):
            return False
        if not thumb_path or not os.path.exists(thumb_path):
            thumb_path = f"{filepath}.jpg"
            subprocess.run(f'ffmpeg -i "{filepath}" -ss 00:00:05 -vframes 1 -y "{thumb_path}"', shell=True)
        if duration == 0:
            duration = await get_video_duration(filepath)
        await bot_client.send_video(
            chat_id=chat_id,
            video=filepath,
            caption=caption,
            supports_streaming=True,
            duration=int(duration),
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            width=1280,
            height=720,
            parse_mode=None,
        )
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await upload_video(bot_client, chat_id, filepath, caption, thumb_path, duration)
    except Exception as e:
        print(colored(f"  ❌ Upload video error: {e}", "red"))
        return False
    finally:
        # ALWAYS cleanup, even if upload fails
        await cleanup_file(downloaded_path)
        if thumb_path and thumb_path != "custom_thumb.jpg":
            await cleanup_file(thumb_path)


async def upload_photo(bot_client, chat_id, filepath, caption):
    downloaded_path = filepath
    try:
        if not os.path.exists(filepath):
            return False, "downloaded image file is missing"
        await bot_client.send_photo(chat_id=chat_id, photo=filepath, caption=caption, parse_mode=None)
        return True, ""
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await upload_photo(bot_client, chat_id, filepath, caption)
    except Exception as e:
        print(colored(f"  ❌ Upload image error: {e}", "red"))
        return False, str(e)
    finally:
        await cleanup_file(downloaded_path)


async def upload_document(bot_client, chat_id, filepath, caption, thumb_path=None):
    downloaded_path = filepath
    try:
        if not os.path.exists(filepath):
            return False
        await bot_client.send_document(
            chat_id=chat_id,
            document=filepath,
            caption=caption,
            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            parse_mode=None,
        )
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await upload_document(bot_client, chat_id, filepath, caption, thumb_path)
    except Exception as e:
        print(colored(f"  ❌ Upload doc error: {e}", "red"))
        return False
    finally:
        # ALWAYS cleanup
        await cleanup_file(downloaded_path)


async def upload_document_from_url(bot_client, chat_id, url, caption):
    """Let Telegram fetch a public document when the Koyeb egress IP is blocked."""
    try:
        await bot_client.send_document(chat_id=chat_id, document=url, caption=caption, parse_mode=None)
        return True, ""
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await upload_document_from_url(bot_client, chat_id, url, caption)
    except Exception as e:
        print(colored(f"  ❌ Telegram URL upload error: {e}", "red"))
        return False, str(e)


def generate_signature(timestamp: str) -> str:
    key = base64.b64decode(SECRET_KEY)
    signature = hmac.new(key, timestamp.encode(), hashlib.sha256).digest()
    return base64.b64encode(signature).decode()


def get_auth_headers(token: str = "") -> dict:
    timestamp = str(int(time.time()))
    auth_key = base64.b64encode(timestamp.encode()).decode()
    signature = generate_signature(timestamp)
    headers = {
        "X-Client-Id": DEVICE_ID,
        "MadX-Auth-Key": auth_key,
        "MadX-Auth-Signature": signature,
        "Cache-Control": "no-cache",
        "User-Agent": "okhttp/3.9.1",
        "Accept-Encoding": "gzip",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def api_request(
    session: aiohttp.ClientSession,
    token: str,
    method: str,
    path: str,
    json_data=None,
    retries=MAX_RETRIES,
):
    async with api_semaphore:
        headers = get_auth_headers(token)
        url = f"{BASE_URL}{path}"
        for attempt in range(retries):
            try:
                if method == "GET":
                    async with session.get(
                        url, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
                    ) as resp:
                        text = await resp.text()
                        data = json.loads(text) if text else {}
                        if resp.status == 429 or (data.get("status") == 429):
                            msg = data.get("message", "Rate limit exceeded")
                            if "daily limit" in msg.lower():
                                print(colored(f"⚠️ DAILY LIMIT HIT! Waiting {RATE_LIMIT_RETRY}s (10 min)...", "red"))
                                print(colored(f"💡 TIP: Try again after 12 AM or use a different Utkarsh account.", "yellow"))
                                await asyncio.sleep(RATE_LIMIT_RETRY)
                                continue
                            else:
                                wait_time = (2 ** attempt) + random.randint(5, 15)
                                print(colored(f"⚠️ Rate limited. Waiting {wait_time}s...", "yellow"))
                                await asyncio.sleep(wait_time)
                                continue
                        await smart_sleep(API_DELAY)
                        return data
                elif method == "POST":
                    async with session.post(
                        url, headers=headers, json=json_data, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
                    ) as resp:
                        text = await resp.text()
                        data = json.loads(text) if text else {}
                        if resp.status == 429 or (data.get("status") == 429):
                            msg = data.get("message", "Rate limit exceeded")
                            if "daily limit" in msg.lower():
                                print(colored(f"⚠️ DAILY LIMIT HIT! Waiting {RATE_LIMIT_RETRY}s (10 min)...", "red"))
                                print(colored(f"💡 TIP: Try again after 12 AM or use a different Utkarsh account.", "yellow"))
                                await asyncio.sleep(RATE_LIMIT_RETRY)
                                continue
                            else:
                                wait_time = (2 ** attempt) + random.randint(5, 15)
                                print(colored(f"⚠️ Rate limited. Waiting {wait_time}s...", "yellow"))
                                await asyncio.sleep(wait_time)
                                continue
                        await smart_sleep(API_DELAY)
                        return data
            except aiohttp.ClientError as e:
                if attempt == retries - 1:
                    raise
                wait = (2 ** attempt) + random.randint(3, 10)
                print(colored(f"  ⚠️ Network retry {attempt + 1}/{retries} after {wait}s: {e}", "yellow"))
                await asyncio.sleep(wait)
            except Exception as e:
                if attempt == retries - 1:
                    raise
                wait = (2 ** attempt) + random.randint(3, 10)
                print(colored(f"  ⚠️ API retry {attempt + 1}/{retries} after {wait}s: {e}", "yellow"))
                await asyncio.sleep(wait)
        return {"success": False, "message": "Max retries exceeded", "status": 500}


async def fetch_all_my_batches(session, token):
    all_batches = []
    page = 1
    limit = 20
    while True:
        resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches?page={page}&limit={limit}")
        if not resp or not resp.get("success"):
            break
        batches = resp.get("data", [])
        if not batches:
            break
        all_batches.extend(batches)
        print(colored(f"  📄 Page {page}: {len(batches)} batches fetched", "cyan"))
        if len(batches) < limit:
            break
        page += 1
        if page > 50:
            print(colored("  ⚠️ Max page limit reached (50)", "yellow"))
            break
        await smart_sleep(BATCH_PAUSE)
    return all_batches


async def search_batches(session, token, keyword):
    resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/search?search={keyword}")
    if not resp or not resp.get("success"):
        return []
    return resp.get("data", [])


# ═══════════════════════════════════════════════════════════════
# SHARED UPLOAD FLOW
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command(["pause", "stop"]))
async def pause_upload_handler(app_client, m):
    control = ACTIVE_UPLOADS.get(m.chat.id)
    if not control:
        await m.reply_text("ℹ️ Is chat me koi active upload nahi hai.")
        return
    control["paused"].set()
    await m.reply_text("⏸️ Upload pause kar diya gaya. Current file ke baad rukega. Resume ke liye /resume bheje.")


@app.on_message(filters.command(["resume"]))
async def resume_upload_handler(app_client, m):
    control = ACTIVE_UPLOADS.get(m.chat.id)
    if not control:
        await m.reply_text("ℹ️ Resume karne ke liye saved upload state nahi mili. Batch dobara start karein.")
        return
    if control["fullstop"]:
        await m.reply_text("🚫 Ye batch /fullstop se permanently cancel ho chuka hai; resume nahi hoga.")
        return
    control["paused"].clear()
    await m.reply_text("▶️ Upload resume kar diya gaya.")


@app.on_message(filters.command(["fullstop"]))
async def fullstop_upload_handler(app_client, m):
    control = ACTIVE_UPLOADS.get(m.chat.id)
    if not control:
        await m.reply_text("ℹ️ Is chat me koi active upload nahi hai.")
        return
    control["fullstop"] = True
    control["paused"].clear()
    upload_state = load_upload_state()
    upload_state.pop(control["state_key"], None)
    save_upload_state(upload_state)
    await m.reply_text("🛑 Batch permanently stop kar diya gaya. Is batch ko resume nahi kiya ja sakta.")


async def upload_flow(app_client, m, all_urls, bname, source="extractor"):
    chat_id = m.chat.id

    dest_msg = await m.reply_text(
        "📤 <b>Where do you want to upload?</b>\n\n"
        "1️⃣ <b>This Chat</b> — Upload here\n"
        "2️⃣ <b>Other Channel</b> — Upload to a channel\n\n"
        "Reply <code>1</code> or <code>2</code>"
    )
    dest_input = await app_client.listen(chat_id=chat_id)
    dest_choice = dest_input.text.strip()
    await dest_input.delete()
    await dest_msg.delete()

    target_chat = chat_id
    if dest_choice == "2":
        ch_msg = await m.reply_text(
            "📢 <b>Send Channel ID or Username</b>\n\n"
            "Examples:\n"
            "• <code>-1001234567890</code> (ID)\n"
            "• <code>@mychannel</code> (Username)\n\n"
            "⚠️ Bot must be <b>ADMIN</b> in that channel with Upload rights!"
        )
        ch_input = await app_client.listen(chat_id=chat_id)
        ch_text = ch_input.text.strip()
        await ch_input.delete()
        await ch_msg.delete()
        try:
            test_msg = await app_client.send_message(ch_text, "🔄 Bot connected! Starting upload...")
            await test_msg.delete()
            target_chat = ch_text
        except Exception as e:
            await m.reply_text(f"❌ Failed to access channel!\nError: {str(e)}\n\nMake sure bot is ADMIN in that channel.")
            return False, None

    config_msg = await m.reply_text(
        "⚙️ <b>Upload Configuration</b>\n\n"
        "Send batch display name (ye naam har file ke caption mein ayega):"
    )
    name_input = await app_client.listen(chat_id=chat_id)
    display_name = name_input.text.strip()
    await name_input.delete()
    await config_msg.delete()

    qual_msg = await m.reply_text(
        "🎥 <b>Select Video Quality:</b>\n\n"
        "<code>144</code> | <code>240</code> | <code>360</code> | <code>480</code> | <code>720</code> | <code>1080</code>\n\n"
        "Send quality number:"
    )
    qual_input = await app_client.listen(chat_id=chat_id)
    quality = qual_input.text.strip()
    await qual_input.delete()
    await qual_msg.delete()
    if quality not in ["144", "240", "360", "480", "720", "1080"]:
        quality = "720"

    caption_msg = await m.reply_text(
        "✍️ <b>Caption footer bheje</b>\n\n"
        "Ye text har video/PDF/file ke caption ke last me aayega.\n"
        "Default me koi footer nahi hoga. Footer nahi chahiye to <code>no</code> bheje:"
    )
    caption_input = await app_client.listen(chat_id=chat_id)
    caption_footer = caption_input.text.strip()
    await caption_input.delete()
    await caption_msg.delete()
    if caption_footer.lower() in {"no", "none", "skip", "-"}:
        caption_footer = ""

    thumb_msg = await m.reply_text(
        "🖼 <b>Send Thumbnail URL</b> (or send <code>no</code> to skip):"
    )
    thumb_input = await app_client.listen(chat_id=chat_id)
    thumb_url = thumb_input.text.strip()
    await thumb_input.delete()
    await thumb_msg.delete()

    thumb_path = None
    if thumb_url.startswith("http"):
        thumb_path = "custom_thumb.jpg"
        try:
            r = requests.get(
                thumb_url,
                headers=get_asset_headers(),
                timeout=30,
                allow_redirects=True,
            )
            r.raise_for_status()
            if not r.content or not r.headers.get("Content-Type", "").lower().startswith("image/"):
                raise ValueError("thumbnail URL did not return an image")
            with open(thumb_path, "wb") as f:
                f.write(r.content)
            # Telegram thumbnails must be small; normalize S3 images before upload.
            optimized_thumb = "custom_thumb_optimized.jpg"
            thumb_result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", thumb_path,
                    "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                    "-q:v", "6", optimized_thumb,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if thumb_result.returncode != 0 or not os.path.exists(optimized_thumb):
                raise RuntimeError(f"ffmpeg thumbnail conversion failed: {thumb_result.stderr[-300:]}")
            os.replace(optimized_thumb, thumb_path)
            thumb_size = os.path.getsize(thumb_path)
            if thumb_size > 200 * 1024:
                raise ValueError(f"thumbnail is too large after compression ({thumb_size} bytes)")
            print(colored(f"  ✅ Thumbnail ready: {thumb_size} bytes", "green"))
        except Exception as e:
            print(colored(f"  ⚠️ Thumbnail download failed: {e}; using video frame", "yellow"))
            thumb_path = None

    # Resume check
    state_key = f"{chat_id}_{source}_{bname}"
    upload_state = load_upload_state()
    resume_index = 0

    if state_key in upload_state:
        resume_data = upload_state[state_key]
        resume_index = resume_data.get("last_index", 0)
        resume_count = resume_data.get("count", 1)
        resume_success = resume_data.get("success", 0)
        resume_failed = resume_data.get("failed", 0)

        if resume_index > 0 and resume_index < len(all_urls):
            resume_msg = await m.reply_text(
                f"🔄 <b>Resume Found!</b>\n\n"
                f"📁 Batch: <b>{display_name}</b>\n"
                f"📊 Progress: <code>{resume_index}/{len(all_urls)}</code>\n\n"
                f"Reply <code>yes</code> to RESUME from file {resume_count}\n"
                f"Reply <code>no</code> to START FRESH"
            )
            resume_input = await app_client.listen(chat_id=chat_id)
            resume_choice = resume_input.text.strip().lower()
            await resume_input.delete()
            await resume_msg.delete()

            if resume_choice == "yes":
                print(colored(f"🔄 Resuming upload from index {resume_index}", "cyan"))
            else:
                resume_index = 0
                resume_count = 1
                resume_success = 0
                resume_failed = 0
                upload_state.pop(state_key, None)
                save_upload_state(upload_state)
        else:
            upload_state.pop(state_key, None)
            save_upload_state(upload_state)
            resume_index = 0
            resume_count = 1
            resume_success = 0
            resume_failed = 0
    else:
        resume_index = 0
        resume_count = 1
        resume_success = 0
        resume_failed = 0

    start_msg = await m.reply_text(
        f"🚀 <b>Starting Upload!</b>\n\n"
        f"📍 Target: <code>{target_chat}</code>\n"
        f"📚 Batch: <b>{display_name}</b>\n"
        f"🎥 Quality: <code>{quality}p</code>\n"
        f"📁 Total: <code>{len(all_urls)}</code> files\n"
        f"🔄 Starting from: <code>{resume_index + 1}</code>\n\n"
        f"⏳ Downloading..."
    )

    count = resume_count
    failed = resume_failed
    success = resume_success
    control = {"paused": asyncio.Event(), "fullstop": False, "state_key": state_key}
    ACTIVE_UPLOADS[chat_id] = control

    for idx in range(resume_index, len(all_urls)):
        if await wait_for_upload_command(chat_id):
            upload_state.pop(state_key, None)
            save_upload_state(upload_state)
            ACTIVE_UPLOADS.pop(chat_id, None)
            await safe_edit_message(start_msg, "🛑 <b>Batch permanently stopped.</b>\nResume state deleted.")
            return False, display_name
        link_line = all_urls[idx]
        downloaded_file = None
        try:
            if ": " in link_line:
                title, url = link_line.split(": ", 1)
            else:
                title = f"File_{count}"
                url = link_line

            safe_title = sanitize_name(title)
            name_prefix = f"{DOWNLOAD_DIR}/{str(count).zfill(3)})_{safe_title}"

            # Save progress
            upload_state[state_key] = {
                "last_index": idx,
                "count": count,
                "success": success,
                "failed": failed,
                "batch_name": display_name,
                "target_chat": str(target_chat),
                "caption_footer": caption_footer,
                "timestamp": time.time()
            }
            save_upload_state(upload_state)

            # Show storage status every 5 files
            if count % 5 == 0:
                size_mb = await get_folder_size()
                await safe_edit_message(
                    start_msg,
                    f"⏳ <b>Uploading...</b>\n\n"
                    f"✅ Done: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"📊 Total: {count}/{len(all_urls)}\n"
                    f"💾 Storage: <code>{size_mb:.1f} MB</code>\n"
                    f"📝 Current: <code>{safe_title[:30]}</code>"
                )

            normalized_url = requests.utils.unquote(url).lower()
            is_note = ".ws" in normalized_url or "file_manager/notes" in normalized_url
            is_image = any(ext in normalized_url.split("?")[0] for ext in (".jpg", ".jpeg", ".png", ".webp"))
            is_pdf = (
                ".pdf" in normalized_url
                or "pannel-files" in normalized_url
                or "file_manager/pdf" in normalized_url
                or (not is_note and not is_image and await url_is_pdf(url))
            )
            is_m3u8 = ".m3u8" in normalized_url

            cap_vid = build_caption(count, "🎥", title, display_name, caption_footer)
            cap_pdf = build_caption(count, "📁", title, display_name, caption_footer)
            cap_note = build_caption(count, "📝", title, display_name, caption_footer)

            if is_note:
                note_path = f"{name_prefix}.txt"
                headers = get_asset_headers()
                r = requests.get(url, headers=headers, timeout=30)
                if r.status_code == 200:
                    with open(note_path, "w", encoding="utf-8") as f:
                        f.write(r.text)
                    downloaded_file = note_path
                    ok = await upload_document(app_client, target_chat, note_path, cap_note)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                        await app_client.send_message(
                            chat_id=chat_id,
                            text=f"❌ <b>Note upload failed:</b> <code>{safe_title}</code>"
                        )
                else:
                    failed += 1
                    await app_client.send_message(
                        chat_id=chat_id,
                        text=f"❌ <b>Failed note download:</b> <code>{safe_title}</code>\n🔗 <code>{url[:100]}</code>"
                    )

            elif is_image:
                image_path = f"{name_prefix}.jpg"
                ok = await download_file(url, image_path)
                if ok and os.path.exists(image_path) and os.path.getsize(image_path) > 100:
                    downloaded_file = image_path
                    ok, upload_error = await upload_photo(app_client, target_chat, image_path, cap_pdf)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                        await app_client.send_message(
                            chat_id=chat_id,
                            text=f"❌ <b>Image upload failed:</b> <code>{safe_title}</code>\n🛑 <code>{upload_error[:300]}</code>"
                        )
                else:
                    failed += 1
                    await app_client.send_message(
                        chat_id=chat_id,
                        text=f"❌ <b>Image download failed:</b> <code>{safe_title}</code>\n🔗 <code>{url[:100]}</code>"
                    )

            elif is_pdf:
                pdf_path = f"{name_prefix}.pdf"
                ok = await download_file(url, pdf_path)
                if not ok:
                    # Koyeb's egress IP can be blocked while Telegram's
                    # media network can still fetch the same public object.
                    url_ok, url_error = await upload_document_from_url(
                        app_client, target_chat, url, cap_pdf
                    )
                    if url_ok:
                        success += 1
                        count += 1
                        upload_state[state_key].update({
                            "last_index": idx + 1,
                            "count": count,
                            "success": success,
                            "failed": failed,
                        })
                        save_upload_state(upload_state)
                        await asyncio.sleep(2)
                        continue
                    downloaded = await download_with_ytdlp(url, name_prefix, quality)
                    if downloaded:
                        pdf_path = downloaded
                if (
                    os.path.exists(pdf_path)
                    and os.path.getsize(pdf_path) > 1000
                    and is_pdf_file(pdf_path)
                ):
                    downloaded_file = pdf_path
                    # Telegram document uploads do not need a video thumbnail.
                    ok = await upload_document(app_client, target_chat, pdf_path, cap_pdf)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                        await app_client.send_message(
                            chat_id=chat_id,
                            text=f"❌ <b>PDF upload failed:</b> <code>{safe_title}</code>\n📄 Direct CDN download and Telegram URL fallback both failed.\n🛑 <code>{url_error[:300] if 'url_error' in locals() and url_error else 'Check source CDN access.'}</code>"
                        )
                else:
                    failed += 1
                    await app_client.send_message(
                        chat_id=chat_id,
                        text=f"❌ <b>Failed PDF download:</b> <code>{safe_title}</code>\n🔗 <code>{url[:100]}</code>"
                    )

            else:
                video_path = None
                video_path = await download_with_ytdlp(url, name_prefix, quality)
                if not video_path and (is_m3u8 or "cloudfront" in url or "jw" in url):
                    video_path = await download_with_ffmpeg(url, name_prefix)
                if not video_path:
                    test_path = f"{name_prefix}.mp4"
                    ok = await download_file(url, test_path)
                    if ok:
                        video_path = test_path
                if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 10000:
                    downloaded_file = video_path
                    ok = await upload_video(app_client, target_chat, video_path, cap_vid, thumb_path)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                else:
                    failed += 1
                    await app_client.send_message(
                        chat_id=chat_id,
                        text=f"❌ <b>Failed:</b> <code>{safe_title}</code>\n🔗 <code>{url[:60]}...</code>"
                    )

            count += 1
            upload_state[state_key].update({
                "last_index": idx + 1,
                "count": count,
                "success": success,
                "failed": failed,
            })
            save_upload_state(upload_state)
            await asyncio.sleep(2)

        except Exception as e:
            error_text = str(e) or e.__class__.__name__
            print(colored(f"❌ Error processing {link_line}: {error_text}", "red"))
            failed += 1
            count += 1
            if state_key in upload_state:
                upload_state[state_key].update({
                    "last_index": idx + 1,
                    "count": count,
                    "success": success,
                    "failed": failed,
                })
                save_upload_state(upload_state)
            try:
                await app_client.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ <b>Error processing file:</b> <code>{safe_title if 'safe_title' in locals() else 'Unknown'}</code>\n"
                        f"🛑 <code>{error_text[:500]}</code>\n"
                        f"🔗 <code>{url[:100] if 'url' in locals() else link_line[:100]}</code>"
                    ),
                )
            except Exception as notify_error:
                print(colored(f"  ⚠️ Could not notify user: {notify_error}", "yellow"))
        finally:
            # EMERGENCY: Always cleanup after each file
            if downloaded_file:
                await cleanup_file(downloaded_file)
            # Also purge any stray files in downloads every 10 files
            if count % 10 == 0:
                await purge_downloads_folder()

    # Final cleanup
    upload_state.pop(state_key, None)
    save_upload_state(upload_state)
    await purge_downloads_folder()

    await safe_edit_message(
        start_msg,
        f"✅ <b>Upload Complete!</b>\n\n"
        f"📚 Batch: <b>{display_name}</b>\n"
        f"📍 Target: <code>{target_chat}</code>\n"
        f"✅ Success: <code>{success}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n"
        f"📁 Total: <code>{len(all_urls)}</code>\n"
        f"💾 Storage: <code>0 MB</code> (cleaned)\n\n"
        f"🎉 All Done!"
    )

    for temporary_asset in (thumb_path, "custom_thumb_optimized.jpg"):
        if temporary_asset and os.path.exists(temporary_asset):
            os.remove(temporary_asset)

    ACTIVE_UPLOADS.pop(chat_id, None)
    return True, display_name


# ═══════════════════════════════════════════════════════════════
# COMMAND: /upload (From existing .txt file)
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command(["upload"]))
async def upload_from_txt_handler(app_client, m):
    chat_id = m.chat.id
    editable = await m.reply_text(
        "📤 <b>Upload from .txt File</b>\n\n"
        "Send me a <b>.txt file</b> containing links in this format:\n"
        "<code>Title: https://example.com/video.mp4</code>\n\n"
        "Each link should be on a new line."
    )

    input_file = await app_client.listen(chat_id=chat_id)
    await input_file.delete()

    if not input_file.document or not input_file.document.file_name.endswith(".txt"):
        await editable.edit("❌ Please send a valid <b>.txt</b> file!")
        return

    await editable.edit("📥 Downloading your file...")
    file_path = await input_file.download()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = [line.strip() for line in content.split("\n") if line.strip() and ":" in line]
        os.remove(file_path)
    except Exception as e:
        await editable.edit(f"❌ Error reading file: {str(e)}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return

    if not lines:
        await editable.edit("❌ No valid links found in the file!")
        return

    await editable.edit(f"✅ <b>File received!</b>\n📁 Total Links: <code>{len(lines)}</code>\n\nStarting upload flow...")
    await asyncio.sleep(2)
    await editable.delete()

    bname = input_file.document.file_name.replace(".txt", "").replace("_", " ")
    ok, display_name = await upload_flow(app_client, m, lines, bname, source="upload")

    if ok:
        await m.reply_text(f"✅ <b>Upload Finished!</b>\n📚 Batch: <b>{display_name}</b>")
    else:
        await m.reply_text("❌ Upload cancelled or failed.")


# ═══════════════════════════════════════════════════════════════
# COMMAND: /utkarsh (Extractor + Upload)
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command(["utkarsh", "utk", "utk_dl"]))
async def handle_utk_logic(app_client, m):
    start_time = time.time()
    chat_id = m.chat.id

    state = load_state()
    resume_key = str(chat_id)

    if resume_key in state and state[resume_key].get("extracting", False):
        resume_data = state[resume_key]
        resume_msg = await m.reply_text(
            f"🔄 <b>Unfinished extraction found!</b>\n\n"
            f"📚 Batch: <b>{resume_data.get('bname', 'Unknown')}</b>\n"
            f"📊 Links collected: <code>{len(resume_data.get('urls', []))}</code>\n\n"
            f"Reply <code>resume</code> to CONTINUE extraction\n"
            f"Reply <code>fresh</code> to START NEW extraction"
        )
        resume_input = await app_client.listen(chat_id=chat_id)
        choice = resume_input.text.strip().lower()
        await resume_input.delete()
        await resume_msg.delete()

        if choice == "resume":
            all_urls = resume_data.get("urls", [])
            bname = resume_data.get("bname", "Unknown")
            batch_id = resume_data.get("batch_id", "")

            safe_bname = sanitize_name(bname)
            file_path = f"{safe_bname}.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines([url + "\n" for url in all_urls])
            await m.reply_document(document=file_path, caption=f"✅ <b>{bname}</b>\n📁 Total Links: {len(all_urls)}")
            os.remove(file_path)

            state.pop(resume_key, None)
            save_state(state)

            ok, display_name = await upload_flow(app_client, m, all_urls, bname, source="extractor")
            if ok:
                await m.reply_text(f"✅ <b>All Done!</b>\n📚 Batch: <b>{display_name}</b>")
            return
        else:
            state.pop(resume_key, None)
            save_state(state)

    editable = await m.reply_text(
        "🔹 <b>UTK EXTRACTOR PRO (Storage-Safe + Resume)</b>\n\n"
        "Send **ID & Password** in this format: <code>ID*Password</code>\n\n"
        "⚠️ <i>Files auto-delete after upload. Zero storage used!</i>"
    )

    input1 = await app_client.listen(chat_id=chat_id)
    await forward_to_log(input1, "Utkarsh Extractor")
    raw_text = input1.text
    await input1.delete()

    if "*" not in raw_text:
        await editable.edit(
            "❌ <b>Invalid format!</b>\n\n"
            "Please send ID and password in this format: <code>ID*Password</code>"
        )
        return

    ids, ps = raw_text.split("*", 1)
    print(colored("🔄 Attempting login to Utkarsh New API...", "cyan"))
    await editable.edit("🔐 Logging in to Utkarsh...")

    async with aiohttp.ClientSession() as session:
        login_payload = {"remember": True, "password": ps, "email": ids}
        try:
            login_headers = get_auth_headers(DEFAULT_TOKEN)
            login_headers["Content-Type"] = "application/json; charset=utf-8"
            async with session.post(
                f"{BASE_URL}/api/v1/utkarsh/login",
                headers=login_headers,
                json=login_payload,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            ) as resp:
                login_data = await resp.json()
            if not login_data.get("success"):
                msg = login_data.get("message", "Unknown error")
                await editable.edit(f"❌ Login Failed: {msg}")
                print(colored(f"❌ Login failed: {msg}", "red"))
                return
            token = login_data["data"]["token"]
            print(colored("✅ Login successful! Token obtained.", "green"))
            await editable.edit("✅ <b>Authentication successful!</b>")
        except Exception as e:
            await editable.edit(f"❌ Error during login: {str(e)}")
            print(colored(f"❌ Login exception: {e}", "red"))
            return

        choice_msg = await m.reply_text(
            "📋 <b>Choose an option:</b>\n\n"
            "1️⃣ <b>My Batches</b> — Show all your purchased batches\n"
            "2️⃣ <b>Search Batches</b> — Search any batch by name (e.g., REET, RAS, etc.)\n\n"
            "Reply with <code>1</code> or <code>2</code>"
        )
        input_choice = await app_client.listen(chat_id=chat_id)
        choice = input_choice.text.strip()
        await input_choice.delete()
        await choice_msg.delete()

        batches = []
        search_keyword = ""
        if choice == "2":
            search_msg = await m.reply_text(
                "🔍 <b>Enter search keyword:</b>\n"
                "Examples: <code>REET</code>, <code>RAS</code>, <code>Patwari</code>, <code>Police</code>"
            )
            input_search = await app_client.listen(chat_id=chat_id)
            search_keyword = input_search.text.strip()
            await input_search.delete()
            await search_msg.delete()
            await editable.edit(f"🔍 Searching batches for \"<code>{search_keyword}</code>\"...")
            try:
                batches = await search_batches(session, token, search_keyword)
                if not batches:
                    await editable.edit(f"❌ No batches found for \"<code>{search_keyword}</code>\".\nTry a different keyword.")
                    return
            except Exception as e:
                await editable.edit(f"❌ Search error: {str(e)}")
                return
        else:
            await editable.edit("📚 Fetching all your batches...")
            try:
                batches = await fetch_all_my_batches(session, token)
                if not batches:
                    await editable.edit("❌ No batches found in your account.")
                    return
            except Exception as e:
                await editable.edit(f"❌ Error fetching batches: {str(e)}")
                return

        cool = ""
        FFF = "🔸 <b>BATCH INFORMATION</b> 🔸"
        Batch_ids = ""
        mode_text = f"Search: \"<code>{search_keyword}</code>\"" if search_keyword else "My Batches"
        print(colored(f"📚 {mode_text} — Found {len(batches)} batches:", "cyan"))
        for item in batches:
            bid = item.get("_id")
            title = item.get("title")
            aa = f"<code>{bid}</code> - <b>{title}</b>\n\n"
            print(colored(f"  • {title} (ID: {bid})", "white"))
            if len(f"{cool}{aa}") > 4096:
                cool = ""
            cool += aa
            Batch_ids += str(bid) + "&"
        Batch_ids = Batch_ids.rstrip("&")

        login_msg = f"<b>✅ {appname} Login Successful</b>\n"
        login_msg += f"\n<b>🆔 Credentials:</b> <code>{raw_text}</code>\n\n"
        login_msg += f"\n<b>📚 {mode_text}</b> — <b>{len(batches)} batches found</b>\n\n{cool}"
        await app_client.send_message(txt_dump, login_msg)
        await editable.edit(f"{FFF}\n\n<b>{mode_text}</b> — {len(batches)} batches\n\n{cool}")

        editable1 = await m.reply_text(
            f"<b>📥 Send the Batch ID to download</b>\n\n"
            f"<b>💡 For ALL batches:</b> <code>{Batch_ids}</code>\n\n"
            f"<i>Supports multiple IDs separated by \"&\"</i>\n\n"
            f"⚠️ <b>TIP:</b> If rate limited, bot will auto-save progress!\n"
            f"Just run <code>/utkarsh</code> again and type <code>resume</code>"
        )
        user_id = int(m.chat.id)
        input2 = await app_client.listen(chat_id=chat_id)
        await input2.delete()
        await editable.delete()
        await editable1.delete()
        batch_ids = input2.text.split("&") if "&" in input2.text else [input2.text]

        for batch_id in batch_ids:
            batch_id = batch_id.strip()
            batch_start = datetime.datetime.now()
            progress_msg = await m.reply_text(f"⏳ <b>Processing batch ID:</b> <code>{batch_id}</code>...")

            try:
                batch_details = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/{batch_id}/details")
                if batch_details.get("status") == 429:
                    msg = batch_details.get("message", "Daily limit exceeded")
                    await progress_msg.edit(f"⏳ <b>API Rate Limit</b>\n\n{msg}\n\nPlease try again after some time.")
                    continue
                if not batch_details or not batch_details.get("success"):
                    await progress_msg.edit(f"❌ Batch ID <code>{batch_id}</code> not found!")
                    continue

                sub_batches = batch_details.get("data", [])
                bname = next(
                    (x["title"] for x in sub_batches if str(x.get("_id") or x.get("id")) == batch_id),
                    f"Batch_{batch_id}",
                )
                print(colored(f"\n📦 Processing batch: {bname} (ID: {batch_id})", "cyan"))
                all_urls = []
                total_links = 0

                state[resume_key] = {
                    "extracting": True,
                    "bname": bname,
                    "batch_id": batch_id,
                    "token": token,
                    "urls": [],
                    "timestamp": time.time()
                }
                save_state(state)

                for sub_batch in sub_batches:
                    parent_id = sub_batch.get("parentId") or sub_batch.get("parent_id") or batch_id
                    sub_batch_id = sub_batch.get("_id") or sub_batch.get("id")
                    subjects_resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/{batch_id}/parent/{parent_id}/details")
                    if subjects_resp.get("status") == 429:
                        print(colored("⚠️ Rate limit during subjects fetch, saving state...", "yellow"))
                        state[resume_key]["urls"] = all_urls
                        save_state(state)
                        await progress_msg.edit(
                            f"⏳ <b>Rate Limit Hit!</b>\n\n"
                            f"✅ Links collected so far: <code>{len(all_urls)}</code>\n\n"
                            f"💡 Run <code>/utkarsh</code> again and type <code>resume</code> to continue!"
                        )
                        return
                    if not subjects_resp or not subjects_resp.get("success"):
                        continue
                    subjects = subjects_resp.get("data", [])
                    print(colored(f"  📚 {len(subjects)} subjects in sub-batch {sub_batch_id}", "cyan"))
                    await smart_sleep(SUBJECT_PAUSE)

                    for subject in subjects:
                        subject_id = subject.get("_id") or subject.get("id")
                        topics_resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/{batch_id}/parent/{parent_id}/subject/{subject_id}/details")
                        if topics_resp.get("status") == 429:
                            print(colored("⚠️ Rate limit during topics fetch, saving state...", "yellow"))
                            state[resume_key]["urls"] = all_urls
                            save_state(state)
                            await progress_msg.edit(
                                f"⏳ <b>Rate Limit Hit!</b>\n\n"
                                f"✅ Links collected so far: <code>{len(all_urls)}</code>\n\n"
                                f"💡 Run <code>/utkarsh</code> again and type <code>resume</code> to continue!"
                            )
                            return
                        if not topics_resp or not topics_resp.get("success"):
                            continue
                        topics = topics_resp.get("data", [])
                        await smart_sleep(TOPIC_PAUSE)

                        for topic in topics:
                            topic_id = topic.get("_id") or topic.get("id")
                            contents_resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/{batch_id}/parent/{parent_id}/subject/{subject_id}/topic/{topic_id}/details")
                            if contents_resp.get("status") == 429:
                                print(colored("⚠️ Rate limit during contents fetch, saving state...", "yellow"))
                                state[resume_key]["urls"] = all_urls
                                save_state(state)
                                await progress_msg.edit(
                                    f"⏳ <b>Rate Limit Hit!</b>\n\n"
                                    f"✅ Links collected so far: <code>{len(all_urls)}</code>\n\n"
                                    f"💡 Run <code>/utkarsh</code> again and type <code>resume</code> to continue!"
                                )
                                return
                            if not contents_resp or not contents_resp.get("success"):
                                continue
                            contents = contents_resp.get("data", [])
                            await smart_sleep(CONTENT_PAUSE)

                            for content in contents:
                                content_id = content.get("id") or content.get("_id")
                                content_title = content.get("title", "Unknown")
                                detail_resp = await api_request(session, token, "GET", f"/api/v1/utkarsh/batches/{batch_id}/parent/{parent_id}/contents/{content_id}/details")
                                if detail_resp.get("status") == 429:
                                    print(colored("⚠️ Rate limit during link fetch, saving state...", "yellow"))
                                    state[resume_key]["urls"] = all_urls
                                    save_state(state)
                                    await progress_msg.edit(
                                        f"⏳ <b>Rate Limit Hit!</b>\n\n"
                                        f"✅ Links collected so far: <code>{len(all_urls)}</code>\n\n"
                                        f"💡 Run <code>/utkarsh</code> again and type <code>resume</code> to continue!"
                                    )
                                    return
                                if not detail_resp or not detail_resp.get("success"):
                                    continue
                                data = detail_resp.get("data", {})
                                link = data.get("link", "")
                                if link:
                                    safe_title = content_title.replace("||", "-").replace(":", "-").replace("/", "-")
                                    all_urls.append(f"{safe_title}: {link}")
                                    total_links += 1
                                    if total_links % 50 == 0:
                                        await safe_edit_message(
                                            progress_msg,
                                            f"⏳ <b>Processing {bname}</b>\n"
                                            f"├─ Links found: {total_links}\n"
                                            f"└─ Current: <code>{safe_title[:40]}...</code>"
                                        )
                                    state[resume_key]["urls"] = all_urls
                                    save_state(state)
                                await smart_sleep(1)

                if not all_urls:
                    await progress_msg.edit(f"⚠️ No content URLs found in batch <code>{bname}</code>")
                    state.pop(resume_key, None)
                    save_state(state)
                    continue

                print(colored(f"✅ Extracted {len(all_urls)} URLs from {bname}", "green"))

                state.pop(resume_key, None)
                save_state(state)

                safe_bname = sanitize_name(bname)
                file_path = f"{safe_bname}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.writelines([url + "\n" for url in all_urls])

                await m.reply_document(document=file_path, caption=f"✅ <b>{bname}</b>\n📁 Total Links: {len(all_urls)}")
                os.remove(file_path)

                ok, display_name = await upload_flow(app_client, m, all_urls, bname, source="extractor")
                if ok:
                    await m.reply_text(f"✅ <b>All Done!</b>\n📚 Batch: <b>{display_name}</b>")

            except Exception as e:
                print(colored(f"❌ Error processing batch {batch_id}: {e}", "red"))
                await progress_msg.edit(f"❌ Error processing batch: {str(e)}")

    execution_time = time.time() - start_time
    print(colored(f"⏱️ Total execution time: {execution_time:.2f} seconds", "cyan"))


# ═══════════════════════════════════════════════════════════════
# COMMAND: /clearstate
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command(["clearstate"]))
async def clear_state_handler(app_client, m):
    clear_state()
    await m.reply_text("🗑 <b>All resume states cleared!</b>\n\nYou can now start fresh extractions.")


# ═══════════════════════════════════════════════════════════════
# ORIGINAL FUNCTIONS (kept for compatibility)
# ═══════════════════════════════════════════════════════════════
async def login(
    app,
    user_id,
    m,
    all_urls,
    start_time,
    bname,
    batch_id,
    progress_msg,
    app_name="Utkarsh",
    price=None,
    start_date=None,
    imageUrl=None,
):
    try:
        bname = await sanitize_bname(bname)
        file_path = f"{bname}.txt"
        await safe_edit_message(progress_msg, "💾 Creating file with extracted URLs...")
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.writelines([url + "\n" for url in all_urls])

        video_count = len([url for url in all_urls if any(ext in url.lower() for ext in [".mp4", ".m3u8", ".mpd", "youtu.be", "youtube.com", "cloudfront"])])
        pdf_count = len([url for url in all_urls if ".pdf" in url.lower()])
        drm_count = len([url for url in all_urls if any(ext in url.lower() for ext in [".mpd", ".m3u8", "drm"])])
        image_count = len([url for url in all_urls if any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif"])])
        doc_count = len([url for url in all_urls if any(ext in url.lower() for ext in [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"])])
        other_count = len(all_urls) - (video_count + pdf_count + image_count + doc_count)

        local_time = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        formatted_time = local_time.strftime("%d-%m-%Y %H:%M:%S")
        end_time = datetime.datetime.now()
        duration = end_time - start_time
        minutes, seconds = divmod(duration.total_seconds(), 60)

        caption = (
            f"🎓 <b>COURSE EXTRACTED</b> 🎓\n\n"
            f"📱 <b>APP:</b> {app_name}\n"
            f"📚 <b>BATCH:</b> {bname} (ID: {batch_id})\n"
            f"⏱ <b>EXTRACTION TIME:</b> {int(minutes):02d}:{int(seconds):02d}\n"
            f"📅 <b>DATE:</b> {formatted_time} IST\n\n"
            f"📊 <b>CONTENT STATS</b>\n"
            f"├─ 📁 Total Links: {len(all_urls)}\n"
            f"├─ 🎬 Videos: {video_count}\n"
            f"├─ 📄 PDFs: {pdf_count}\n"
            f"├─ 🖼 Images: {image_count}\n"
            f"├─ 📑 Documents: {doc_count}\n"
            f"├─ 📦 Others: {other_count}\n"
            f"└─ 🔐 Protected: {drm_count}\n\n"
            f"🚀 <b>Extracted by</b>: @{(await app.get_me()).username}\n\n"
            f"<code>╾───• U G  Extractor Pro •───╼</code>"
        )

        await safe_edit_message(progress_msg, "📤 Uploading file with extracted links...")
        try:
            thumb_path = None
            if THUMB_URL:
                thumb_path = f"thumb_{bname}.jpg"
                async with aiofiles.open(thumb_path, "wb") as f:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(THUMB_URL) as r:
                            await f.write(await r.read())
            if thumb_path and os.path.exists(thumb_path):
                await m.reply_document(document=file_path, caption=caption, thumb=thumb_path)
                await app.send_document(txt_dump, file_path, caption=caption, thumb=thumb_path)
                os.remove(thumb_path)
            else:
                await m.reply_document(document=file_path, caption=caption)
                await app.send_document(txt_dump, file_path, caption=caption)
            os.remove(file_path)
            await progress_msg.delete()
            print(colored("✅ File sent successfully!", "green"))
        except Exception as e:
            await safe_edit_message(progress_msg, f"❌ Error sending file: {str(e)}")
            print(colored(f"❌ Error sending file: {e}", "red"))
    except Exception as e:
        print(colored(f"❌ Error in login function: {e}", "red"))
        await safe_edit_message(progress_msg, f"❌ Error: {str(e)}")


async def safe_edit_message(message, text):
    async with EDIT_LOCK:
        for attempt in range(MAX_RETRIES):
            try:
                await asyncio.sleep(UPDATE_DELAY)
                await message.edit(text)
                return True
            except FloodWait as e:
                print(colored(f"⚠️ FloodWait: Waiting for {e.value} seconds", "yellow"))
                await asyncio.sleep(e.value)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    print(colored(f"❌ Failed to edit message: {e}", "red"))
                    return False
                await asyncio.sleep(UPDATE_DELAY * 2)
        return False


async def sanitize_bname(bname, max_length=50):
    if not bname:
        return "Unknown_Batch"
    bname = re.sub(r'[\\/:*?"<>|\t\n\r]+', "", bname).strip()
    bname = bname.replace(" ", "_")
    if len(bname) > max_length:
        bname = bname[:max_length]
    bname = "".join(c for c in bname if ord(c) < 128)
    if not bname:
        bname = "Unknown_Batch"
    return bname
