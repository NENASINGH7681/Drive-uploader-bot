import os
import time
import math
import asyncio
import shutil
import re
import subprocess
from aiohttp import web
from pyrogram import Client, filters, idle
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CREDENTIALS_JSON = os.environ.get("GDRIVE_CREDENTIALS_JSON")

if CREDENTIALS_JSON:
    with open("credentials.json", "w") as f:
        f.write(CREDENTIALS_JSON)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# --- INITIALIZE BOT ---
bot = Client("render_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_data = {}
STOP_PROCESS = False

# --- HELPER FUNCTIONS ---

# 1. Natural Sorting Key (Taaki 1 ke baad 2 aaye, 10 nahi)
def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]

# 2. Thumbnail Generator
async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    try:
        # FFMPEG se 2nd second ka snapshot lenge
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-ss", "00:00:02", "-vframes", "1", thumb_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        print(f"Thumb Error: {e}")
    return None

def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

def time_formatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return ((str(days) + "d, ") if days else "") + \
        ((str(hours) + "h, ") if hours else "") + \
        ((str(minutes) + "m, ") if minutes else "") + \
        ((str(seconds) + "s, ") if seconds else "")

async def progress(current, total, message, start_time, status_text):
    now = time.time()
    diff = now - start_time
    if round(diff % 5.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        elapsed_time = round(diff) * 1000
        if speed == 0: speed = 1 
        time_to_completion = round((total - current) / speed) * 1000
        
        progress_str = "[{0}{1}] {2}%\n".format(
            ''.join(["●" for i in range(math.floor(percentage / 10))]),
            ''.join(["○" for i in range(10 - math.floor(percentage / 10))]),
            round(percentage, 2))
            
        tmp = f"{status_text}\n\n{progress_str}" + \
            f"📦 **Size:** {humanbytes(current)} / {humanbytes(total)}\n" + \
            f"🚀 **Speed:** {humanbytes(speed)}/s\n" + \
            f"⏱ **ETA:** {time_formatter(time_to_completion)}"
        try:
            await message.edit(tmp)
        except:
            pass

# --- GOOGLE DRIVE FUNCTIONS ---
def get_gdrive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def get_file_id_from_url(url):
    if "id=" in url: return url.split("id=")[1].split("&")[0]
    elif "/folders/" in url: return url.split("/folders/")[1].split("?")[0]
    elif "/d/" in url: return url.split("/d/")[1].split("/")[0]
    return url

async def download_file_gdrive(service, file_id, original_name, message):
    temp_filename = f"temp_{file_id}" 
    file_path = f"./{temp_filename}"
    
    request = service.files().get_media(fileId=file_id)
    file_metadata = service.files().get(fileId=file_id, fields="size").execute()
    total_size = int(file_metadata.get('size', 0))
    start_time = time.time()
    
    with open(file_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=50 * 1024 * 1024)
        done = False
        while done is False:
            if STOP_PROCESS: raise Exception("Stopped by User")
            status, done = downloader.next_chunk()
            if status:
                await progress(int(status.resumable_progress), total_size, message, start_time, f"⬇️ **Downloading:** {original_name}")
                
    return file_path

# --- UPLOAD & SPLIT ---
async def upload_file(client, file_path, display_name, chat_id, caption, message, is_part=False):
    start_time = time.time()
    status_text = f"⬆️ **Uploading:** {display_name}"
    if is_part: status_text = f"⬆️ **Uploading Part:** {display_name}"

    thumb_path = None
    
    # Video Thumbnail Logic
    if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')) and not is_part:
        status_text = f"🖼 **Generating Thumbnail:** {display_name}"
        try:
            await message.edit(status_text)
            thumb_path = await generate_thumbnail(file_path)
            status_text = f"⬆️ **Uploading:** {display_name}"
        except:
            pass

    try:
        if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
             await client.send_video(
                chat_id, 
                video=file_path, 
                caption=caption,
                file_name=display_name,
                thumb=thumb_path,  # <-- Thumbnail added here
                progress=progress, 
                progress_args=(message, start_time, status_text),
                supports_streaming=True
            )
        else:
            await client.send_document(
                chat_id, 
                document=file_path, 
                caption=caption,
                file_name=display_name,
                progress=progress, 
                progress_args=(message, start_time, status_text)
            )
    except Exception as e:
        print(f"Upload Error: {e}")
        await client.send_document(
            chat_id, 
            document=file_path, 
            caption=caption,
            file_name=display_name 
        )
    finally:
        # Clean up thumbnail
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

async def recursive_process(client, service, folder_id, user_id, message, parent_path=""):
    global STOP_PROCESS
    if STOP_PROCESS: return

    chat_id = user_data[user_id]['channel_id']
    custom_caption = user_data[user_id]['caption']
    
    query = f"'{folder_id}' in parents and trashed = false"
    # Note: orderBy hataya nahi hai, par hum Python me re-sort karenge
    results = service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType)").execute()
    items = results.get('files', [])

    if not items: return

    # --- CRITICAL FIX: NATURAL SORTING ---
    # Python me sort kar rahe hain taaki 1, 2, 10 wala issue fix ho jaye
    items.sort(key=lambda x: natural_sort_key(x['name']))

    for item in items:
        if STOP_PROCESS: 
            await client.send_message(user_id, "🛑 **Process Stopped by User.**")
            return

        original_name = item['name']
        file_id = item['id']
        mime_type = item['mimeType']

        if mime_type == 'application/vnd.google-apps.folder':
            await client.send_message(chat_id, f"📂 **{parent_path}{original_name}**")
            await recursive_process(client, service, file_id, user_id, message, parent_path + original_name + " / ")
        else:
            msg = await message.reply_text(f"⏳ **Queued:** {original_name}")
            try:
                temp_path = await download_file_gdrive(service, file_id, original_name, msg)
                
                final_caption = original_name if custom_caption == "SKIP" else f"{custom_caption}\n\n{original_name}"
                
                f_size = os.path.getsize(temp_path)
                LIMIT = 1.9 * 1024 * 1024 * 1024 
                
                if f_size <= LIMIT:
                    await upload_file(client, temp_path, original_name, chat_id, final_caption, msg)
                else:
                    await msg.edit(f"✂️ **Splitting File:** {humanbytes(f_size)}")
                    part_num = 1
                    with open(temp_path, 'rb') as f:
                        while True:
                            if STOP_PROCESS: raise Exception("Stopped during split")
                            
                            chunk = f.read(int(LIMIT))
                            if not chunk: break
                            
                            part_temp_path = f"{temp_path}_part{part_num}"
                            part_display_name = f"{original_name}.part{part_num}"
                            
                            with open(part_temp_path, 'wb') as p: p.write(chunk)
                            
                            part_caption = f"{final_caption}\n\n**Part {part_num}**"
                            await upload_file(client, part_temp_path, part_display_name, chat_id, part_caption, msg, is_part=True)
                            
                            if os.path.exists(part_temp_path): os.remove(part_temp_path)
                            part_num += 1

                if os.path.exists(temp_path): os.remove(temp_path)
                await msg.delete()
                
            except Exception as e:
                error_str = str(e)
                if "Stopped by User" in error_str:
                    await msg.delete()
                    return 
                else:
                    await client.send_message(user_id, f"❌ **Error with {original_name}:**\n{error_str}")
                    if os.path.exists(f"./temp_{file_id}"): os.remove(f"./temp_{file_id}")
                    await msg.delete()

# --- COMMANDS ---

@bot.on_message(filters.command("stop") & filters.private)
async def stop_command(client, message):
    global STOP_PROCESS
    STOP_PROCESS = True
    await message.reply_text("🛑 **Stopping process...**")

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    global STOP_PROCESS
    STOP_PROCESS = False
    user_data[message.from_user.id] = {'step': 'ask_channel'}
    await message.reply_text("👋 **Drive to Telegram Bot**\n\n1. Send Target Channel ID (e.g., `-100xxxx`).")

@bot.on_message(filters.text & filters.private)
async def handle_inputs(client, message):
    uid = message.from_user.id
    text = message.text.strip()
    if text.startswith("/"): return
    if uid not in user_data: return await message.reply_text("/start first.")
    step = user_data[uid].get('step')

    if step == 'ask_channel':
        if text.startswith("-100"):
            user_data[uid]['channel_id'] = int(text)
            user_data[uid]['step'] = 'ask_caption'
            await message.reply_text("✅ Channel Set.\n\n2. Send **Caption** (or `SKIP`).")
        else:
            await message.reply_text("❌ Invalid ID.")
    elif step == 'ask_caption':
        user_data[uid]['caption'] = text
        user_data[uid]['step'] = 'ask_link'
        await message.reply_text("✅ Caption Set.\n\n3. Send **Drive Link**.")
    elif step == 'ask_link':
        try:
            global STOP_PROCESS
            STOP_PROCESS = False
            folder_id = get_file_id_from_url(text)
            service = get_gdrive_service()
            await message.reply_text(f"🚀 **Processing Started...**\nSorting files correctly (1, 2, 3...)")
            await recursive_process(client, service, folder_id, uid, message)
            if not STOP_PROCESS:
                await message.reply_text("✅ **All Files Uploaded Successfully!**")
            if uid in user_data: del user_data[uid]
        except Exception as e:
            await message.reply_text(f"Error: {e}")

# --- WEB SERVER & MAIN LOOP ---
async def web_server():
    async def handle(request):
        return web.Response(text="Bot is running!")
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web Server started on port {port}")

async def main():
    await web_server()
    await bot.start()
    print("Bot Started...")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
