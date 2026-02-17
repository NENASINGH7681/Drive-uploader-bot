import os
import time
import math
import asyncio
import shutil
import re
import json
import subprocess
import google.auth.transport.requests
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import BotCommand
from google.oauth2 import service_account
from googleapiclient.discovery import build

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
bot = Client("aria2_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global Variables
user_data = {}
STOP_PROCESS = False
FOLDER_INDEX = []
SKIP_UNTIL_NAME = None 
FOUND_START_FILE = False 

# --- CONFIG MANAGEMENT ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f: json.dump(data, f)

# --- HELPER FUNCTIONS ---

def get_video_attributes(file_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,duration",
               "-of", "default=noprint_wrappers=1:nokey=1", file_path]
        output = subprocess.check_output(cmd).decode("utf-8").strip().split("\n")
        width = int(output[0]) if len(output) > 0 and output[0].isdigit() else 0
        height = int(output[1]) if len(output) > 1 and output[1].isdigit() else 0
        duration = 0
        if len(output) > 2:
            try: duration = int(float(output[2]))
            except: pass
        return width, height, duration
    except: return 0, 0, 0

async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    try:
        await asyncio.to_thread(subprocess.run, 
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

async def progress(current, total, message, start_time, status_text):
    now = time.time()
    diff = now - start_time
    if round(diff % 5.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        if speed == 0: speed = 1 
        time_to_completion = round((total - current) / speed) * 1000
        
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

async def count_total_files(service, folder_id):
    total = 0
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        results = await asyncio.to_thread(
            service.files().list(q=query, fields="nextPageToken, files(id, mimeType)", pageToken=page_token).execute
        )
        items = results.get('files', [])
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                total += await count_total_files(service, item['id']) 
            else:
                total += 1
        page_token = results.get('nextPageToken')
        if not page_token: break
    return total

async def check_file_in_channel(client, chat_id, file_name):
    try:
        query = file_name
        async for message in client.search_messages(chat_id, query=query, limit=5):
            if message.caption and file_name in message.caption: return True
            if message.document and message.document.file_name == file_name: return True
            if message.video and message.video.file_name == file_name: return True
    except: return False
    return False

# --- GOOGLE DRIVE & ARIA2 ---
def get_gdrive_service():
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), creds

def get_file_id_from_url(url):
    if "id=" in url: return url.split("id=")[1].split("&")[0]
    elif "/folders/" in url: return url.split("/folders/")[1].split("?")[0]
    elif "/d/" in url: return url.split("/d/")[1].split("/")[0]
    return url

async def download_with_aria2(file_id, original_name, message, creds):
    temp_filename = f"temp_{file_id}" 
    file_path = f"./{temp_filename}"
    
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    token = creds.token
    download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    
    start_time = time.time()
    await message.edit(f"⬇️ **Starting Aria2c:**\n`{original_name}`")

    cmd = [
        "aria2c", f"--header=Authorization: Bearer {token}",
        "-x16", "-s16", "-j16", "-k1M",
        "--out", temp_filename, download_url
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    
    while process.returncode is None:
        if STOP_PROCESS:
            process.terminate()
            raise Exception("Stopped by User")
        
        if os.path.exists(file_path):
            current_size = os.path.getsize(file_path)
            if current_size > 0 and time.time() - start_time > 3:
                try:
                    speed = current_size / (time.time() - start_time)
                    await message.edit(
                        f"⬇️ **Downloading:**\n`{original_name}`\n\n"
                        f"📦 **Downloaded:** {humanbytes(current_size)}\n"
                        f"🚀 **Speed:** {humanbytes(speed)}/s"
                    )
                except: pass
        
        await asyncio.sleep(2)
        if process.returncode is not None: break

    await process.wait()
    if process.returncode != 0: raise Exception("Aria2c Download Failed")
    return file_path

# --- UPLOAD ---
async def upload_file(client, file_path, display_name, chat_id, caption, message, is_part=False):
    start_time = time.time()
    status_text = f"⬆️ **Uploading:**\n`{display_name}`"
    if is_part: status_text = f"⬆️ **Uploading Part:**\n`{display_name}`"
    
    thumb_path = None
    width, height, duration = 0, 0, 0

    if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')) and not is_part:
        status_text = f"⚙️ **Processing Video:**\n`{display_name}`"
        try:
            await message.edit(status_text)
            thumb_path = await generate_thumbnail(file_path)
            width, height, duration = get_video_attributes(file_path)
        except: pass
        status_text = f"⬆️ **Uploading:**\n`{display_name}`"

    try:
        if display_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
             await client.send_video(
                chat_id, video=file_path, caption=caption, file_name=display_name,
                thumb=thumb_path, width=width, height=height, duration=duration,
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
        await client.send_document(chat_id, document=file_path, caption=caption, file_name=display_name)
    finally:
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)

# --- RECURSIVE CORE ---
async def recursive_process(client, service, creds, folder_id, user_id, message, parent_path="", is_root_selection=False):
    global STOP_PROCESS, FOLDER_INDEX, SKIP_UNTIL_NAME, FOUND_START_FILE
    if STOP_PROCESS: return

    config = load_config()
    chat_id = config.get("channel_id")
    
    query = f"'{folder_id}' in parents and trashed = false"
    results = await asyncio.to_thread(service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType)").execute)
    items = results.get('files', [])

    if not items: return
    items.sort(key=lambda x: natural_sort_key(x['name']))

    for item in items:
        if STOP_PROCESS: return

        original_name = item['name']
        file_id = item['id']
        mime_type = item['mimeType']

        if SKIP_UNTIL_NAME and not FOUND_START_FILE:
            if mime_type == 'application/vnd.google-apps.folder': pass 
            else:
                if original_name.strip() == SKIP_UNTIL_NAME.strip():
                    FOUND_START_FILE = True
                    await client.send_message(user_id, f"✅ **Resuming Download:** {original_name}")
                else: continue

        if mime_type == 'application/vnd.google-apps.folder':
            full_folder_name = f"📂 {parent_path}{original_name}"
            
            if not SKIP_UNTIL_NAME or FOUND_START_FILE:
                sent_msg = await client.send_message(chat_id, f"**{full_folder_name}**")
                
                # PIN ONLY ROOT SELECTION
                if is_root_selection:
                    try: await client.pin_chat_message(chat_id, sent_msg.id)
                    except: pass

                clean_cid = str(chat_id).replace("-100", "")
                msg_link = f"https://t.me/c/{clean_cid}/{sent_msg.id}"
                FOLDER_INDEX.append(f"[{original_name}]({msg_link})")

            await recursive_process(client, service, creds, file_id, user_id, message, parent_path + original_name + " / ", is_root_selection=False)
        else:
            if SKIP_UNTIL_NAME and not FOUND_START_FILE: continue 

            msg = await message.reply_text(f"⏳ **Queued:** {original_name}")
            try:
                temp_path = await download_with_aria2(file_id, original_name, msg, creds)
                
                final_caption = (f"📂 {parent_path}\n🎥 **{original_name}**")
                
                # --- RAM SAFE SPLITTING ---
                f_size = os.path.getsize(temp_path)
                LIMIT = 1.9 * 1024 * 1024 * 1024 
                CHUNK_SIZE = 10 * 1024 * 1024 # 10MB Chunks to save RAM
                
                if f_size <= LIMIT:
                    await upload_file(client, temp_path, original_name, chat_id, final_caption, msg)
                else:
                    await msg.edit(f"✂️ **Splitting File:** {humanbytes(f_size)}")
                    part_num = 1
                    
                    with open(temp_path, 'rb') as f:
                        while True:
                            if STOP_PROCESS: raise Exception("Stopped")
                            
                            part_temp = f"{temp_path}_part{part_num}"
                            part_name = f"{original_name}.part{part_num}"
                            current_part_size = 0
                            
                            # Streaming Write (Low RAM)
                            with open(part_temp, 'wb') as p:
                                while current_part_size < LIMIT:
                                    chunk = f.read(CHUNK_SIZE)
                                    if not chunk: break
                                    p.write(chunk)
                                    current_part_size += len(chunk)
                            
                            # If part is empty (EOF), delete and break
                            if os.path.getsize(part_temp) == 0:
                                os.remove(part_temp)
                                break
                                
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

async def set_commands(client):
    commands = [
        BotCommand("start", "Start Bot"),
        BotCommand("setchannel", "Set Channel"),
        BotCommand("stop", "Stop"),
        BotCommand("removeid", "Remove Channel ID")
    ]
    await client.set_bot_commands(commands)

@bot.on_message(filters.command("setchannel") & filters.private)
async def set_channel(client, message):
    if len(message.command) < 2: return await message.reply_text("Usage: `/setchannel -100xxxxxxx`")
    try:
        cid = int(message.command[1])
        save_config({"channel_id": cid})
        await message.reply_text(f"✅ Channel ID set to: `{cid}`")
    except: await message.reply_text("❌ Invalid ID format.")

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
    await set_commands(client)
    user_data[message.from_user.id] = {'step': 'idle'}
    config = load_config()
    if not config.get("channel_id"):
        await message.reply_text("👋 **Welcome!**\nFirst, set channel: `/setchannel -100xxxxxxxxx`")
    else:
        await message.reply_text(f"✅ **Channel Configured:** `{config['channel_id']}`\nSend me a Link.")

@bot.on_message(filters.text & filters.private)
async def handle_inputs(client, message):
    text = message.text.strip()
    uid = message.from_user.id
    if text.startswith("/"): return

    config = load_config()
    if not config.get("channel_id"):
        return await message.reply_text("❌ Please set channel first using `/setchannel`")

    current_step = user_data.get(uid, {}).get('step', 'idle')

    if current_step == 'idle' or "drive.google.com" in text:
        try:
            folder_id = get_file_id_from_url(text)
            service, creds = get_gdrive_service()
            msg = await message.reply_text("🔍 **Scanning for folders...**")
            
            query = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
            results = await asyncio.to_thread(service.files().list(q=query, fields="files(id, name)").execute)
            folders = results.get('files', [])
            folders.sort(key=lambda x: natural_sort_key(x['name']))

            if not folders:
                await msg.edit("❌ No folders found. (Files in root are skipped in selective mode).")
                return

            folder_map = {f['name']: f['id'] for f in folders}
            user_data[uid] = {'step': 'ask_selection', 'folder_map': folder_map}

            list_text = "**Found Folders:**\n\n"
            for f in folders:
                list_text += f"`{f['name']}`\n"
            
            list_text += "\n👇 **Copy & Send names of folders to download.**"
            await msg.edit(list_text)

        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")

    elif current_step == 'ask_selection':
        folder_map = user_data[uid].get('folder_map', {})
        selected_names = text.split('\n')
        valid_folders = []

        for name in selected_names:
            clean_name = name.strip()
            if clean_name in folder_map:
                valid_folders.append((clean_name, folder_map[clean_name]))
        
        if not valid_folders:
            await message.reply_text("❌ No valid folder names found. Copy exactly from list.")
            return
        
        user_data[uid]['valid_folders'] = valid_folders
        user_data[uid]['step'] = 'ask_start_file'
        
        await message.reply_text(
            f"✅ Selected {len(valid_folders)} folders.\n\n"
            "❓ **Do you want to start from a specific file?**\n"
            "- Send **File Name** to start from there.\n"
            "- Send **NO** to download everything."
        )

    elif current_step == 'ask_start_file':
        start_from = text
        valid_folders = user_data[uid]['valid_folders']
        
        global STOP_PROCESS, FOLDER_INDEX, SKIP_UNTIL_NAME, FOUND_START_FILE
        STOP_PROCESS = False
        FOLDER_INDEX = []
        
        if start_from.upper() == "NO":
            SKIP_UNTIL_NAME = None
            FOUND_START_FILE = True 
            await message.reply_text("🚀 **Starting Full Download...**")
        else:
            SKIP_UNTIL_NAME = start_from
            FOUND_START_FILE = False
            await message.reply_text(f"🚀 **Searching for '{start_from}'...**")

        service, creds = get_gdrive_service()
        progress_msg = await message.reply_text("⚙️ **Initializing Aria2c...**")

        for name, fid in valid_folders:
            if STOP_PROCESS: break
            await recursive_process(client, service, creds, fid, uid, progress_msg, parent_path="", is_root_selection=True)
        
        if not STOP_PROCESS:
            if FOLDER_INDEX:
                index_text = "📑 **Index:**\n\n" + "\n".join(FOLDER_INDEX)
                if len(index_text) > 4000: index_text = index_text[:4000] + "..."
                await client.send_message(load_config().get("channel_id"), index_text)
            await message.reply_text("✅ **All Selected Tasks Completed!**")
        
        user_data[uid] = {'step': 'idle'}

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
