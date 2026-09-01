
import requests
import datetime
import pytz
import re
import aiofiles
import os
import base64
import asyncio
import time
import json
from pyrogram import filters
from Extractor import app
from config import CHANNEL_ID, THUMB_URL
from colorama import Fore, Style, init
from termcolor import colored
from pyrogram.errors import FloodWait, RPCError
import aiohttp
from datetime import timedelta
from Extractor.core.utils import forward_to_log

init(autoreset=True)

appname = "Utkarsh"
txt_dump = CHANNEL_ID
MAX_RETRIES = 5
TIMEOUT = 90
UPDATE_DELAY = 5
UPDATE_INTERVAL = 15
EDIT_LOCK = asyncio.Lock()

# ═══════════════════════════════════════════════════════════════
# NEW API CONFIGURATION (api.asmultiverse.app)
# ═══════════════════════════════════════════════════════════════
BASE_URL = "https://api.asmultiverse.app/api/v1/utkarsh"
CLIENT_ID = "2cfbaa6be65acdc5"

# Default Bearer token used for the LOGIN endpoint.
# This token is hardcoded in the Utkarsh app and is required
# to call /login. It may expire — update if login fails.
DEFAULT_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjE0NDQ2MTEzIiwiZGV2aWNlX3R5cGUiOiI0IiwidmVyc2lvbl9jb2RlIjoiMSIsImljciI6IjAiLCJpYXQiOjE3ODgyMzYwOTYsImV4cCI6MTc5MDM5NjA5Nn0.Sn6Ad_klMRf3HJmE81ETHXSIGtSs1r4FyflU_77x3wI"

# ═══════════════════════════════════════════════════════════════
# ⚠️  CRITICAL TODO: MadX-Auth-Signature Generation
# ═══════════════════════════════════════════════════════════════
# The API requires a `MadX-Auth-Signature` header on EVERY request.
# Without a valid signature, all calls return 401/403.
#
# What we know:
#   • MadX-Auth-Key = base64(timestamp)  ✓ confirmed
#   • Signature changes per request        ✓ confirmed
#   • It is NOT any of these (tested & failed):
#       – HMAC-SHA256(key="MadXABhi", msg=method+path+timestamp)
#       – HMAC-SHA256(key=CLIENT_ID, msg=path+timestamp)
#       – HMAC-SHA256(key=JWT_token, msg=path+timestamp)
#       – HMAC-SHA256(key=JWT_payload.id, msg=path+timestamp)
#       – HMAC-SHA256(key="", msg=path+timestamp)
#       – SHA256/SHA1/MD5 of various combinations
#
# HOW TO FIND THE REAL ALGORITHM:
#   1. Decompile Utkarsh APK (com.utkarsh.ABhi) with JADX
#   2. Search strings: "MadX-Auth-Signature", "generateSignature",
#      "signRequest", "getSignature"
#   3. If the logic is in a native .so library, use Frida to hook
#      the signing function at runtime
#   4. The signature is most likely HMAC-SHA256 with a static key
#      hidden somewhere in the app assets / native code.
#
# Once you find it, replace the body of generate_signature() below.
# ═══════════════════════════════════════════════════════════════
def generate_signature(method: str, path: str, timestamp: str) -> str:
    """
    Generate MadX-Auth-Signature for API requests.
    ⛔ THIS IS A PLACEHOLDER — Replace with the real algorithm.
    """
    # TODO: Replace with actual signature algorithm extracted from APK
    return ""


def get_auth_headers(token: str, method: str, path: str) -> dict:
    """Build the complete header set for api.asmultiverse.app."""
    timestamp = str(int(time.time()))
    auth_key = base64.b64encode(timestamp.encode()).decode()
    signature = generate_signature(method, path, timestamp)
    return {
        "authorization": f"Bearer {token}",
        "X-Client-Id": CLIENT_ID,
        "MadX-Auth-Signature": signature,
        "X-Auth-Key": "",
        "Cache-Control": "no-cache",
        "MadX-Auth-Key": auth_key,
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Host": "api.asmultiverse.app",
        "Accept-Encoding": "gzip",
        "User-Agent": "okhttp/3.9.1",
    }


async def api_request(
    session: aiohttp.ClientSession,
    token: str,
    method: str,
    path: str,
    json_data=None,
    retries=MAX_RETRIES,
):
    """Make an authenticated API request with exponential back-off."""
    headers = get_auth_headers(token, method, path)
    url = f"{BASE_URL}{path}"

    for attempt in range(retries):
        try:
            if method == "GET":
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
                ) as resp:
                    text = await resp.text()
                    return json.loads(text) if text else {}
            elif method == "POST":
                async with session.post(
                    url,
                    headers=headers,
                    json=json_data,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as resp:
                    text = await resp.text()
                    return json.loads(text) if text else {}
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(colored(f"  ⚠️ API retry {attempt + 1}/{retries} after {wait}s: {e}", "yellow"))
            await asyncio.sleep(wait)
    return None


# ═══════════════════════════════════════════════════════════════
# MAIN HANDLER
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command(["utkarsh", "utk", "utk_dl"]))
async def handle_utk_logic(app_client, m):
    start_time = time.time()
    editable = await m.reply_text(
        "🔹 <b>UTK EXTRACTOR PRO (New API v2)</b> 🔹\n\n"
        "Send **ID & Password** in this format: <code>ID*Password</code>"
    )

    # ── 1. Read credentials ──────────────────────────────────
    input1 = await app_client.listen(chat_id=m.chat.id)
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
        # ── 2. Login ───────────────────────────────────────────
        login_payload = {"remember": True, "password": ps, "email": ids}
        try:
            login_headers = get_auth_headers(DEFAULT_TOKEN, "POST", "/login")
            login_headers["Content-Type"] = "application/json; charset=utf-8"

            async with session.post(
                f"{BASE_URL}/login",
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

        # ── 3. Fetch batch list ────────────────────────────────
        await editable.edit("📚 Fetching your batches...")
        try:
            batches_resp = await api_request(
                session, token, "GET", "/batches?page=1&limit=100"
            )
            if not batches_resp or not batches_resp.get("success"):
                await editable.edit("❌ Failed to fetch batches from API.")
                return

            batches = batches_resp.get("data", [])
            if not batches:
                await editable.edit("❌ No batches found in your account.")
                return
        except Exception as e:
            await editable.edit(f"❌ Error fetching batches: {str(e)}")
            return

        # ── 4. Show batches to user ────────────────────────────
        cool = ""
        FFF = "🔸 <b>BATCH INFORMATION</b> 🔸"
        Batch_ids = ""

        print(colored(f"📚 Found {len(batches)} batches:", "cyan"))
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
        login_msg += f"\n\n<b>📚 Available Batches</b>\n\n{cool}"

        await app_client.send_message(txt_dump, login_msg)
        await editable.edit(f"{FFF}\n\n{cool}")

        # ── 5. Ask for batch ID ────────────────────────────────
        editable1 = await m.reply_text(
            f"<b>📥 Send the Batch ID to download</b>\n\n"
            f"<b>💡 For ALL batches:</b> <code>{Batch_ids}</code>\n\n"
            f"<i>Supports multiple IDs separated by '&'</i>"
        )

        user_id = int(m.chat.id)
        input2 = await app_client.listen(chat_id=m.chat.id)
        await input2.delete()
        await editable.delete()
        await editable1.delete()

        batch_ids = input2.text.split("&") if "&" in input2.text else [input2.text]

        # ── 6. Process each selected batch ─────────────────────
        for batch_id in batch_ids:
            batch_id = batch_id.strip()
            batch_start = datetime.datetime.now()
            progress_msg = await m.reply_text(
                f"⏳ <b>Processing batch ID:</b> <code>{batch_id}</code>..."
            )

            try:
                # 6a. Get sub-batches
                batch_details = await api_request(
                    session, token, "GET", f"/batches/{batch_id}/details"
                )
                if not batch_details or not batch_details.get("success"):
                    await progress_msg.edit(
                        f"❌ Batch ID <code>{batch_id}</code> not found!"
                    )
                    continue

                sub_batches = batch_details.get("data", [])
                bname = next(
                    (
                        x["title"]
                        for x in sub_batches
                        if str(x["_id"]) == batch_id
                    ),
                    f"Batch_{batch_id}",
                )
                print(colored(f"\n📦 Processing batch: {bname} (ID: {batch_id})", "cyan"))

                all_urls = []

                # 6b. Iterate sub-batches → subjects → topics → contents
                for sub_batch in sub_batches:
                    parent_id = sub_batch.get("parentId", batch_id)

                    # Subjects
                    subjects_resp = await api_request(
                        session,
                        token,
                        "GET",
                        f"/batches/{batch_id}/parent/{parent_id}/details",
                    )
                    if not subjects_resp or not subjects_resp.get("success"):
                        continue
                    subjects = subjects_resp.get("data", [])
                    print(colored(
                        f"  📚 {len(subjects)} subjects in sub-batch {sub_batch.get('_id')}",
                        "cyan",
                    ))

                    for subject in subjects:
                        subject_id = subject.get("_id")

                        # Topics
                        topics_resp = await api_request(
                            session,
                            token,
                            "GET",
                            f"/batches/{batch_id}/parent/{parent_id}/subject/{subject_id}/details",
                        )
                        if not topics_resp or not topics_resp.get("success"):
                            continue
                        topics = topics_resp.get("data", [])

                        for topic in topics:
                            topic_id = topic.get("_id")

                            # Contents list
                            contents_resp = await api_request(
                                session,
                                token,
                                "GET",
                                f"/batches/{batch_id}/parent/{parent_id}/subject/{subject_id}/topic/{topic_id}/details",
                            )
                            if not contents_resp or not contents_resp.get("success"):
                                continue
                            contents = contents_resp.get("data", [])

                            for content in contents:
                                content_id = content.get("id")
                                content_title = content.get("title", "Unknown")

                                # Actual link
                                detail_resp = await api_request(
                                    session,
                                    token,
                                    "GET",
                                    f"/batches/{batch_id}/parent/{parent_id}/contents/{content_id}/details",
                                )
                                if not detail_resp or not detail_resp.get("success"):
                                    continue

                                data = detail_resp.get("data", {})
                                link = data.get("link", "")
                                if link:
                                    safe_title = (
                                        content_title.replace("||", "-")
                                        .replace(":", "-")
                                    )
                                    all_urls.append(f"{safe_title}: {link}")

                if all_urls:
                    print(colored(
                        f"✅ Extracted {len(all_urls)} URLs from {bname}", "green"
                    ))
                    await login(
                        app_client,
                        user_id,
                        m,
                        all_urls,
                        batch_start,
                        bname,
                        batch_id,
                        progress_msg,
                        app_name="Utkarsh",
                    )
                else:
                    await progress_msg.edit(
                        f"⚠️ No content URLs found in batch <code>{bname}</code>"
                    )

            except Exception as e:
                print(colored(f"❌ Error processing batch {batch_id}: {e}", "red"))
                await progress_msg.edit(f"❌ Error processing batch: {str(e)}")

    execution_time = time.time() - start_time
    print(colored(f"⏱️ Total execution time: {execution_time:.2f} seconds", "cyan"))


# ═══════════════════════════════════════════════════════════════
# FILE CREATION & SENDING (unchanged from original)
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

        # Content stats
        video_count = len([
            url for url in all_urls
            if any(ext in url.lower() for ext in [
                ".mp4", ".m3u8", ".mpd", "youtu.be", "youtube.com", "cloudfront"
            ])
        ])
        pdf_count = len([url for url in all_urls if ".pdf" in url.lower()])
        drm_count = len([
            url for url in all_urls
            if any(ext in url.lower() for ext in [".mpd", ".m3u8", "drm"])
        ])
        image_count = len([
            url for url in all_urls
            if any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif"])
        ])
        doc_count = len([
            url for url in all_urls
            if any(ext in url.lower() for ext in [
                ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"
            ])
        ])
        other_count = len(all_urls) - (video_count + pdf_count + image_count + doc_count)

        # Timestamps
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

            print(colored("\n📊 EXTRACTION SUMMARY:", "cyan"))
            print(colored(f"📚 Batch: {bname}", "white"))
            print(colored(f"📁 Total Links: {len(all_urls)}", "white"))
            print(colored(f"🎬 Videos: {video_count}", "white"))
            print(colored(f"📄 PDFs: {pdf_count}", "white"))
            print(colored(f"🖼 Images: {image_count}", "white"))
            print(colored(f"📑 Documents: {doc_count}", "white"))
            print(colored(f"📦 Others: {other_count}", "white"))
            print(colored(f"🔐 Protected: {drm_count}", "white"))
            print(colored(f"⏱️ Process took: {int(minutes):02d}:{int(seconds):02d}", "white"))

        except Exception as e:
            await safe_edit_message(progress_msg, f"❌ Error sending file: {str(e)}")
            print(colored(f"❌ Error sending file: {e}", "red"))

    except Exception as e:
        print(colored(f"❌ Error in login function: {e}", "red"))
        await safe_edit_message(progress_msg, f"❌ Error: {str(e)}")


async def safe_edit_message(message, text):
    """Safely edit a message with retry logic and delay."""
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
    """Clean batch name for safe file operations."""
    if not bname:
        return "Unknown_Batch"
    bname = re.sub(r"[\\/:*?"<>|	
]+", "", bname).strip()
    bname = bname.replace(" ", "_")
    if len(bname) > max_length:
        bname = bname[:max_length]
    bname = "".join(c for c in bname if ord(c) < 128)
    if not bname:
        bname = "Unknown_Batch"
    return bname
