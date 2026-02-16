import os
import time
import math
import asyncio
import shutil
import re
import json
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
CONFIG_FILE = "config.json"

# --- INITIALIZE BOT ---
bot = Client("pro_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global Variables
user_data = {}
STOP_PROCESS = False
FILE_COUNTER = 0  # Global index counter
FOLDER_INDEX = [] # To store folder name and msg link

# --- CONFIG MANAGEMENT (Problem 2) ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# --- HELPER FUNCTIONS ---

# Problem 1: Get Video Attributes for Perfect Size
def get_video_attributes(file_path):
    try:
        # FFPROBE se Width, Height, Duration nikalenge
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        output = subprocess.check_output(cmd).decode("utf-8").strip().split("\n")
        
        width = int(output[0]) if len(output) > 0 and output[0].isdigit() else 0
        height = int(output[1]) if len(output) > 1 and output[1].isdigit() else 0
        # Duration seconds me hoti hai, int me badalna padega
        duration = 0
        if len(output) > 2:
            try:
                duration = int(float(output[2]))
            except:
                pass
                
        return width, height, duration
    except Exception as e:
        print(f"Metadata Error: {e}")
        return 0, 0, 0

async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-ss", "00:00:02", "-vframes", "1", thumb_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        if os.path.exists(thumb_path): return thumb_path
    except: pass
    return None

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power: size /= power; n += 1
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

# Problem 3: Fancy Progress Bar
async def progress(current, total, message, start_time, status_text):
    now = time.time()
    diff = now - start_time
    if round(diff % 5.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        if speed == 0: speed = 1 
        time_to_completion = round((total - current) / speed) * 1000
        
        # Fancy Bar: ▰▰▰▱▱▱
        filled_length = int(10 * percentage // 100)
        bar = '▰' * filled_length + '▱' * (10 - filled_length)
        
        tmp = (
            f"{status_text}\n\n"
            f"**{bar}** {round(percentage, 1)}%\n"
            f"📦 **Size:** {humanbytes(current)} / {humanbytes(total)}\n"
            f"🚀 **Speed:** {humanbytes(speed)}/s\n"
            f"⏱ **ETA:** {time_formatter(time_to_completion)}"
        )
        try: await message.edit(tmp)
        except: pass

# Problem 5: Pre-Count Files
async def count_total_files(service, folder_id):
    total = 0
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        results = service.files().list(q=query, fields="nextPageToken, files(id, mimeType)", pageToken=page_token).execute()
        items = results.get('files', [])
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                total += await count_total_files(service, item['id']) # Recursive count
            else:
                total += 1
        page_token = results.get('nextPageToken')
        if not page_token: break
    return total

# --- GOOGLE DRIVE & DOWNLOAD ---
def get_gdrive_service():
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
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
                await progress(int(status.resumable_progress), total_size, message, start_time, f"⬇️ **Downloading:**\n`{original_name}`")
    return file_path

# --- UPLOAD ---
async def upload_file(client, file_path, display_name, chat_id, caption, message, is_part=False):
    start_time = time.time()
    status_text = f"⬆️ **Uploading:**\n`{display_name}`"
    if is_part: status_text = f"⬆️ **Uploading Part:**\n`{display_name}`"
    
    thumb_path = None
    width, height, duration = 0, 0, 0

    # Video Processing
    if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')) and not is_part:
        status_text = f"⚙️ **Processing Video:**\n`{display_name}`"
        try:
            await message.edit(status_text)
            thumb_path = await generate_thumbnail(file_path)
            # Metadata fetch
            width, height, duration = get_video_attributes(file_path)
        except: pass
        status_text = f"⬆️ **Uploading:**\n`{display_name}`"

    try:
        if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
             await client.send_video(
                chat_id, video=file_path, caption=caption, file_name=display_name,
                thumb=thumb_path, width=width, height=height, duration=duration, # <-- Attributes added
                progress=progress, progress_args=(message, start_time, status_text),
                supports_streaming=True
            )
        else:
            await client.send_document(
                chat_id, document=file_path, caption=caption, file_name=display_name,
                progress=progress, progress_args=(message, start_time, status_text)
            )
    except Exception as e:
        print(f"Upload Error: {e}")
        # Fallback to document
        await client.send_document(chat_id, document=file_path, caption=caption, file_name=display_name)
    finally:
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)

# --- RECURSIVE CORE ---
async def recursive_process(client, service, folder_id, user_id, message, parent_path=""):
    global STOP_PROCESS, FILE_COUNTER, FOLDER_INDEX
    if STOP_PROCESS: return

    config = load_config()
    chat_id = config.get("channel_id")
    
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType)").execute()
    items = results.get('files', [])

    if not items: return
    items.sort(key=lambda x: natural_sort_key(x['name']))

    for item in items:
        if STOP_PROCESS: return

        original_name = item['name']
        file_id = item['id']
        mime_type = item['mimeType']

        if mime_type == 'application/vnd.google-apps.folder':
            # Folder Name send karna and store karna index ke liye
            full_folder_name = f"📂 {parent_path}{original_name}"
            sent_msg = await client.send_message(chat_id, f"**{full_folder_name}**")
            
            # Problem 7: Index Link Storage
            # Format: Name -> Link (https://t.me/c/CHANNEL_ID/MSG_ID)
            # Channel ID start with -100, remove -100 for link
            clean_cid = str(chat_id).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_cid}/{sent_msg.id}"
            FOLDER_INDEX.append(f"[{original_name}]({msg_link})")

            await recursive_process(client, service, file_id, user_id, message, parent_path + original_name + " / ")
        else:
            FILE_COUNTER += 1 # Increment Counter
            msg = await message.reply_text(f"⏳ **Queued:** {original_name}")
            try:
                temp_path = await download_file_gdrive(service, file_id, original_name, msg)
                
                # Problem 6: Auto Caption with Index
                # Format: #1 \n FolderPath \n FileName
                final_caption = (
                    f"#{FILE_COUNTER}\n"
                    f"📂 {parent_path}\n"
                    f"🎥 **{original_name}**"
                )

                f_size = os.path.getsize(temp_path)
                LIMIT = 1.9 * 1024 * 1024 * 1024 
                
                if f_size <= LIMIT:
                    await upload_file(client, temp_path, original_name, chat_id, final_caption, msg)
                else:
                    await msg.edit(f"✂️ **Splitting File:** {humanbytes(f_size)}")
                    part_num = 1
                    with open(temp_path, 'rb') as f:
                        while True:
                            if STOP_PROCESS: raise Exception("Stopped")
                            chunk = f.read(int(LIMIT))
                            if not chunk: break
                            
                            part_temp = f"{temp_path}_part{part_num}"
                            part_name = f"{original_name}.part{part_num}"
                            with open(part_temp, 'wb') as p: p.write(chunk)
                            
                            p_cap = f"{final_caption}\n\n**Part {part_num}**"
                            await upload_file(client, part_temp, part_name, chat_id, p_cap, msg, is_part=True)
                            if os.path.exists(part_temp): os.remove(part_temp)
                            part_num += 1

                if os.path.exists(temp_path): os.remove(temp_path)
                await msg.delete()
            except Exception as e:
                if "Stopped" in str(e): return
                await client.send_message(user_id, f"❌ Error: {original_name}\n{str(e)}")
                if os.path.exists(f"./temp_{file_id}"): os.remove(f"./temp_{file_id}")
                await msg.delete()

# --- COMMANDS ---

@bot.on_message(filters.command("setchannel") & filters.private)
async def set_channel(client, message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/setchannel -100xxxxxxx`")
    try:
        cid = int(message.command[1])
        save_config({"channel_id": cid})
        await message.reply_text(f"✅ Channel ID set to: `{cid}`")
    except:
        await message.reply_text("❌ Invalid ID format.")

@bot.on_message(filters.command("removeid") & filters.private)
async def remove_channel(client, message):
    save_config({})
    await message.reply_text("🗑 Channel ID removed.")

@bot.on_message(filters.command("stop") & filters.private)
async def stop_cmd(client, message):
    global STOP_PROCESS
    STOP_PROCESS = True
    await message.reply_text("🛑 Stopping...")

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    config = load_config()
    if not config.get("channel_id"):
        await message.reply_text(
            "👋 **Welcome!**\n\n"
            "First, set the channel using:\n"
            "`/setchannel -100xxxxxxxxx`\n\n"
            "(Make sure I am Admin in that channel)"
        )
    else:
        await message.reply_text(
            f"✅ **Channel Configured:** `{config['channel_id']}`\n"
            "Send me a **Google Drive Link** to start."
        )

@bot.on_message(filters.text & filters.private)
async def handle_link(client, message):
    text = message.text.strip()
    if text.startswith("/"): return

    config = load_config()
    chat_id = config.get("channel_id")
    if not chat_id:
        return await message.reply_text("❌ Please set channel first using `/setchannel`")

    try:
        global STOP_PROCESS, FILE_COUNTER, FOLDER_INDEX
        STOP_PROCESS = False
        FILE_COUNTER = 0
        FOLDER_INDEX = []

        folder_id = get_file_id_from_url(text)
        service = get_gdrive_service()

        status_msg = await message.reply_text("🔍 **Scanning Drive... Please Wait...**")
        
        # Problem 5: Counting Files
        total_files = await count_total_files(service, folder_id)
        
        # Get Root Folder Name for Pinning
        root_meta = service.files().get(fileId=folder_id, fields="name").execute()
        root_name = root_meta.get('name', 'Drive Folder')
        
        await status_msg.edit(
            f"📂 **Folder Found:** `{root_name}`\n"
            f"📄 **Total Files:** `{total_files}`\n\n"
            "🚀 Starting in 10 seconds..."
        )
        
        # Problem 4: Pin First Message in Channel
        root_msg = await client.send_message(chat_id, f"💿 **Drive Upload Started:**\n`{root_name}`")
        try: await client.pin_chat_message(chat_id, root_msg.id)
        except: pass

        await asyncio.sleep(10)
        await status_msg.delete()
        
        progress_msg = await message.reply_text("🚀 **Starting Download...**")
        
        await recursive_process(client, service, folder_id, message.from_user.id, progress_msg)
        
        if not STOP_PROCESS:
            # Problem 7: Index Message
            if FOLDER_INDEX:
                index_text = "📑 **Index of Uploaded Folders:**\n\n" + "\n".join(FOLDER_INDEX)
                # Split if too long
                if len(index_text) > 4000:
                    index_text = index_text[:4000] + "\n...(truncated)"
                await client.send_message(chat_id, index_text)

            await message.reply_text("✅ **Task Completed Successfully!**")

    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")

# --- WEB SERVER ---
async def web_server():
    async def handle(request): return web.Response(text="Bot Running")
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await web_server()
    await bot.start()
    print("Bot Started...")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
