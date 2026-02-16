import os
import time
import math
import asyncio
import shutil
# High speed event loop policy check
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from pyrogram import Client, filters
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
# tgcrypto will be used automatically if installed
bot = Client("speedy_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_data = {}

# --- HELPER FUNCTIONS ---

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
        if speed == 0: speed = 1 # avoid zero division
        time_to_completion = round((total - current) / speed) * 1000
        estimated_total_time = elapsed_time + time_to_completion
        
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

# --- GOOGLE DRIVE OPTIMIZED DOWNLOAD ---

def get_gdrive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def get_file_id_from_url(url):
    if "id=" in url: return url.split("id=")[1].split("&")[0]
    elif "/folders/" in url: return url.split("/folders/")[1].split("?")[0]
    elif "/d/" in url: return url.split("/d/")[1].split("/")[0]
    return url

async def download_file_gdrive(service, file_id, file_name, message):
    request = service.files().get_media(fileId=file_id)
    file_path = f"./{file_name}"
    
    file_metadata = service.files().get(fileId=file_id, fields="size").execute()
    total_size = int(file_metadata.get('size', 0))
    
    start_time = time.time()
    
    with open(file_path, "wb") as fh:
        # HIGH SPEED SETTING: Chunk size increased to 50MB
        # This reduces HTTP requests overhead significantly
        downloader = MediaIoBaseDownload(fh, request, chunksize=50 * 1024 * 1024)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            if status:
                await progress(int(status.resumable_progress), total_size, message, start_time, f"⬇️ **Downloading:** {file_name}")
                
    return file_path

# --- UPLOAD & LOGIC ---

async def upload_file(client, file_path, file_name, chat_id, caption, message, is_part=False):
    start_time = time.time()
    status_text = f"⬆️ **Uploading:** {file_name}"
    if is_part: status_text = f"⬆️ **Uploading Part:** {file_name}"

    try:
        if file_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
             await client.send_video(
                chat_id, video=file_path, caption=caption, 
                progress=progress, progress_args=(message, start_time, status_text),
                supports_streaming=True
            )
        else:
            await client.send_document(
                chat_id, document=file_path, caption=caption, 
                progress=progress, progress_args=(message, start_time, status_text)
            )
    except Exception as e:
        print(f"Upload Error: {e}")
        await client.send_document(chat_id, document=file_path, caption=caption)

async def recursive_process(client, service, folder_id, user_id, message, parent_path=""):
    chat_id = user_data[user_id]['channel_id']
    custom_caption = user_data[user_id]['caption']
    
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, orderBy="name", fields="nextPageToken, files(id, name, mimeType)").execute()
    items = results.get('files', [])

    if not items: return

    for item in items:
        file_name = item['name']
        file_id = item['id']
        mime_type = item['mimeType']

        if mime_type == 'application/vnd.google-apps.folder':
            await client.send_message(chat_id, f"📂 **{parent_path}{file_name}**")
            await recursive_process(client, service, file_id, user_id, message, parent_path + file_name + " / ")
        else:
            try:
                msg = await message.reply_text(f"⏳ **Queued:** {file_name}")
                file_path = await download_file_gdrive(service, file_id, file_name, msg)
                
                final_caption = file_name if custom_caption == "SKIP" else f"{custom_caption}\n\n{file_name}"
                
                # Check Size for Split
                f_size = os.path.getsize(file_path)
                LIMIT = 1.9 * 1024 * 1024 * 1024 
                
                if f_size <= LIMIT:
                    await upload_file(client, file_path, file_name, chat_id, final_caption, msg)
                else:
                    await msg.edit(f"✂️ **Splitting File:** {humanbytes(f_size)}")
                    part_num = 1
                    with open(file_path, 'rb') as f:
                        while True:
                            chunk = f.read(int(LIMIT))
                            if not chunk: break
                            part_name = f"{file_name}.part{part_num}"
                            part_path = f"./{part_name}"
                            with open(part_path, 'wb') as p: p.write(chunk)
                            
                            part_caption = f"{final_caption}\n\n**Part {part_num}**"
                            await upload_file(client, part_path, part_name, chat_id, part_caption, msg, is_part=True)
                            os.remove(part_path)
                            part_num += 1

                if os.path.exists(file_path): os.remove(file_path)
                await msg.delete()
                
            except Exception as e:
                await client.send_message(user_id, f"❌ Error: {str(e)}")

# --- COMMANDS ---

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_data[message.from_user.id] = {'step': 'ask_channel'}
    await message.reply_text("👋 **High Speed Drive Bot**\n\n1. Send Target Channel ID (e.g., `-100xxxx`).\n(Make sure I am Admin there).")

@bot.on_message(filters.text & filters.private)
async def handle_inputs(client, message):
    uid = message.from_user.id
    text = message.text.strip()
    
    if uid not in user_data: return await message.reply_text("/start first.")
    step = user_data[uid].get('step')

    if step == 'ask_channel':
        if text.startswith("-100"):
            user_data[uid]['channel_id'] = int(text)
            user_data[uid]['step'] = 'ask_caption'
            await message.reply_text("✅ Channel Set.\n\n2. Send **Custom Caption** (or `SKIP`).")
        else:
            await message.reply_text("❌ Invalid ID. Starts with -100...")

    elif step == 'ask_caption':
        user_data[uid]['caption'] = text
        user_data[uid]['step'] = 'ask_link'
        await message.reply_text("✅ Caption Set.\n\n3. Send **Google Drive Link**.")

    elif step == 'ask_link':
        try:
            folder_id = get_file_id_from_url(text)
            service = get_gdrive_service()
            await message.reply_text(f"🚀 **Processing Started...**")
            await recursive_process(client, service, folder_id, uid, message)
            await message.reply_text("✅ **Task Completed!**")
            del user_data[uid]
        except Exception as e:
            await message.reply_text(f"Error: {e}")

if __name__ == "__main__":
    print("Bot Started...")
    bot.run()
