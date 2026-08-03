import os
import asyncio
import time
import re
from datetime import datetime
from config import *
from pyrogram import Client, filters
from pyrogram.types import Message
from pyromod import listen
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64
from motor.motor_asyncio import AsyncIOMotorClient

# --- MongoDB Setup (for owner-configurable branding/caption) ---
# This is optional infrastructure: if MONGO_URI is unset or the DB is
# unreachable, branding/caption features silently no-op. Core txt-to-html
# conversion never depends on Mongo and keeps working regardless.
_mongo_client = None
_settings_collection = None
if MONGO_URI:
    try:
        _mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _settings_collection = _mongo_client[MONGO_DB_NAME]["bot_settings"]
    except Exception:
        _mongo_client = None
        _settings_collection = None

async def get_bot_settings() -> dict:
    """Fetch the single settings document. Returns {} on any failure
    (no Mongo configured, connection error, no document yet)."""
    if _settings_collection is None:
        return {}
    try:
        doc = await _settings_collection.find_one({"_id": "config"})
        return doc or {}
    except Exception:
        return {}

async def update_bot_settings(fields: dict) -> bool:
    """Upsert given fields into the single settings document.
    Returns True on success, False if Mongo is unavailable/errored."""
    if _settings_collection is None:
        return False
    try:
        await _settings_collection.update_one(
            {"_id": "config"}, {"$set": fields}, upsert=True
        )
        return True
    except Exception:
        return False

def is_admin(user_id: int) -> bool:
    """Owner or configured admins only -- used to gate /setbranding and
    /setcaption so regular users can't touch bot-wide branding."""
    return user_id == OWNER_ID or user_id in ADMINS

# --- AES Encryption ---
def aes_encrypt_auto_prefix(data: str) -> str:
    try:
        key = b'ThisIsASecretKey'
        cipher = AES.new(key, AES.MODE_CBC)
        ct_bytes = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
        encrypted_data = base64.b64encode(cipher.iv + ct_bytes).decode('utf-8')
        return encrypted_data
    except Exception as e:
        return data

# --- Player URL Generator ---
def get_player_url(url: str) -> str:
    # Genuinely DRM-protected streams still need the external player, since
    # decrypting real Widevine/PlayReady content requires its license
    # server. Everything else plays natively in the browser, so we return
    # the original link directly instead of wrapping it.
    if "drm" in url and "playlist.m3u8" in url:
        encrypted = aes_encrypt_auto_prefix(url)
        return f"https://itsgolu-v1player.vercel.app/?url={encrypted}"
    elif 'zip' in url:
        return f'https://video.pablocoder.eu.org/appx-zip?url={url}'
    elif 'brightcove' in url:
        bcov = 'bcov_auth=eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJpYXQiOjE3Mjg3MDIyMDYsImNvbiI6eyJpc0FkbWluIjpmYWxzZSwiYXVzZXIiOiJVMFZ6TkdGU2NuQlZjR3h5TkZwV09FYzBURGxOZHowOSIsImlkIjoiT0dweFpuWktabVl3WVdwRlExSXJhV013WVdvMlp6MDkiLCJmaXJzdF9uYW1lIjoiU0hCWVJFc3ZkbVJ0TVVSR1JqSk5WamN3VEdoYVp6MDkiLCJlbWFpbCI6ImNXbE5NRTVoTUd4NloxbFFORmx4UkhkWVV6bFhjelJTWWtwSlVVcHNSM0JDVTFKSWVGQXpRM2hsT0QwPSIsInBob25lIjoiYVhReWJ6TTJkWEJhYzNRM01uQjZibEZ4ZGxWR1p6MDkiLCJhdmF0YXIiOiJLM1ZzY1M4elMwcDBRbmxrYms4M1JEbHZla05pVVQwOSIsInJlZmVycmFsX2NvZGUiOiJla3RHYjJoYWRtcENXSFo0YTFsV2FEVlBaM042ZHowOSIsImRldmljZV90eXBlIjoiYW5kcm9pZCIsImRldmljZV92ZXJzaW9uIjoidXBwZXIgdGhhbiAzMSIsImRldmljZV9tb2RlbCI6IlhpYW9NaSBNMjAwN0oxN0MiLCJyZW1vdGVfYWRkciI6IjQ0LjIyMi4yNTMuODUifX0.k_419KObeIVpLO6BqHcg8MpnvEwDgm54UxPnY7rTUEu_SIjOaE7FOzez5NL9LS7LdI_GawTeibig3ILv5kWuHhDqAvXiM8sQpTkhQoGEYybx8JRFmPw_fyNsiwNxTZQ4P4RSF9DgN_yiQ61aFtYpcfldT0xG1AfamXK4JlneJpVOJ8aG_vOLm6WkiY-XG4PCj5u4C3iyur0VM1-j-EhwHmNXVCiCz5weXDsv6ccV6SqNW2j_Cbjia16ghgX61XeIyyEkp07Nyrp7GN4eXuxxHeKcoBJB-YsQ0OopSWKzOQNEjlGgx7b54BkmU8PbiwElYgMGpjRT9bLTf3EYnTJ_wA'
        return url.split("bcov_auth")[0] + bcov
    else:
        # .m3u8 (non-DRM), .mp4, or any other direct link -- browsers play
        # these natively, no external wrapping needed.
        return url

client = Client("itsgolu_html_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@client.on_message(filters.command("start") & filters.private)
async def start_command(_, message: Message):
    settings = await get_bot_settings()
    branding_text = settings.get("branding_text")
    branding_link = settings.get("branding_link")

    text = (
        f"🎐 **Welcome {message.from_user.first_name}!**\n"
        "✨ **TXT ➝ HTML Bot** ✨\n"
        "📌 **Features:**\n"
        "• Direct Video/PDF Playback\n"
        "• Fixed Grid & Index\n"
        "• Smart Search\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎨 **Themes:**\n"
        "🔓 /modern → Minimal Light\n"
        "🔓 /neumorphic → Soft Grey\n"
        "🔓 /brutalist → Dark Elegant\n"
        "🔓 /glassmorphism → Glass Effect\n"
        "🔓 /cyberpunk → Soft Pastel\n"
        "🔓 /hub → Hub (drill-down + player)\n"
    )
    # Only append the "By:" line if branding has actually been configured.
    if branding_text:
        text += "━━━━━━━━━━━━━━━━━━\n"
        if branding_link:
            text += f"👑 [{branding_text}]({branding_link})"
        else:
            text += f"👑 {branding_text}"

    await message.reply_text(text)

@client.on_message(filters.command("setbranding") & filters.private)
async def cmd_setbranding(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.reply("❌ Only the owner/admins can use this command.")
        return
    if _settings_collection is None:
        await message.reply("❌ MongoDB is not configured, so branding can't be saved. Set `MONGO_URI` first.")
        return

    settings = await get_bot_settings()
    current_text = settings.get("branding_text")
    current_link = settings.get("branding_link")
    if current_text:
        current_display = f"'{current_text}'" + (f" → {current_link}" if current_link else " (no link)")
        await message.reply(f"ℹ️ Current branding: {current_display}\n\n📝 Send the new branding text (e.g. `Join Now` or `Made by Alex`). Send `clear` to remove branding entirely.")
    else:
        await message.reply("📝 Send the branding text you want to show (e.g. `Join Now` or `Made by Alex`). Send `clear` to cancel.")

    try:
        text_msg: Message = await client.listen(user_id, timeout=300)
    except asyncio.TimeoutError:
        await message.reply("⏰ Timeout! No changes made.")
        return

    new_text = (text_msg.text or "").strip()
    if not new_text:
        await text_msg.reply("❌ Empty text isn't allowed.")
        return
    if new_text.lower() == "clear":
        await update_bot_settings({"branding_text": None, "branding_link": None})
        await text_msg.reply("✅ Branding removed.")
        return

    await text_msg.reply("🔗 Now send the channel link for it to be a hyperlink, or send `skip` to show it as plain text (no link).")
    try:
        link_msg: Message = await client.listen(user_id, timeout=300)
    except asyncio.TimeoutError:
        await message.reply("⏰ Timeout! No changes made.")
        return

    new_link = (link_msg.text or "").strip()
    if new_link.lower() == "skip":
        new_link = None
    elif not new_link.startswith("http"):
        await link_msg.reply("❌ That doesn't look like a valid link (must start with `http`). Branding not saved -- run /setbranding again.")
        return

    ok = await update_bot_settings({"branding_text": new_text, "branding_link": new_link})
    if ok:
        preview = f"[{new_text}]({new_link})" if new_link else new_text
        await link_msg.reply(f"✅ Branding saved! `/start` will now show: 👑 {preview}")
    else:
        await link_msg.reply("❌ Failed to save (database error). Please try again.")

@client.on_message(filters.command("setcaption") & filters.private)
async def cmd_setcaption(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.reply("❌ Only the owner/admins can use this command.")
        return
    if _settings_collection is None:
        await message.reply("❌ MongoDB is not configured, so this can't be saved. Set `MONGO_URI` first.")
        return

    settings = await get_bot_settings()
    current_caption = settings.get("caption_name")
    if current_caption:
        await message.reply(f"ℹ️ Current caption name: '{current_caption}'\n\n📝 Send the new name to show as \"Made by: X\" on generated HTML files. Send `clear` to remove it.")
    else:
        await message.reply("📝 Send the name to show as \"Made by: X\" on generated HTML files. Send `clear` to cancel.")

    try:
        name_msg: Message = await client.listen(user_id, timeout=300)
    except asyncio.TimeoutError:
        await message.reply("⏰ Timeout! No changes made.")
        return

    new_name = (name_msg.text or "").strip()
    if not new_name:
        await name_msg.reply("❌ Empty text isn't allowed.")
        return
    if new_name.lower() == "clear":
        await update_bot_settings({"caption_name": None})
        await name_msg.reply("✅ Caption name removed. Future files will show just the theme name.")
        return

    ok = await update_bot_settings({"caption_name": new_name})
    if ok:
        await name_msg.reply(f"✅ Saved! Future HTML files will show: Made by: {new_name}")
    else:
        await name_msg.reply("❌ Failed to save (database error). Please try again.")

@client.on_message(filters.command("neumorphic") & filters.private)
async def cmd_neumorphic(client, message: Message): await process_txt_to_html(client, message, "neumorphic")
@client.on_message(filters.command("brutalist") & filters.private)
async def cmd_brutalist(client, message: Message): await process_txt_to_html(client, message, "brutalist")
@client.on_message(filters.command("modern") & filters.private)
async def cmd_modern(client, message: Message): await process_txt_to_html(client, message, "modern")
@client.on_message(filters.command("glassmorphism") & filters.private)
async def cmd_glassmorphism(client, message: Message): await process_txt_to_html(client, message, "glassmorphism")
@client.on_message(filters.command("cyberpunk") & filters.private)
async def cmd_cyberpunk(client, message: Message): await process_txt_to_html(client, message, "cyberpunk")
@client.on_message(filters.command("hub") & filters.private)
async def cmd_hub(client, message: Message): await process_txt_to_html(client, message, "hub")

async def process_txt_to_html(client: Client, message: Message, theme: str):
    user_id = message.from_user.id
    await message.reply(f"🕹️ **Generating `{theme}`...**\n📤 Please send `.txt` file.")
    try:
        msg: Message = await client.listen(user_id, timeout=300)
    except asyncio.TimeoutError:
        await message.reply("⏰ Timeout!")
        return

    if not msg.document or not msg.document.file_name.endswith(".txt"):
        await msg.reply("❌ Only `.txt` files allowed.")
        return

    file_path = await msg.download()
    # Fix Filename
    original_name = msg.document.file_name.replace(".txt", "")
    output_path = f"{original_name}_{user_id}.html" 
    await msg.reply("⏳ Processing...")

    try:
        if theme == "neumorphic": await extract_links_neumorphic(file_path, output_path)
        elif theme == "brutalist": await extract_links_dark_elegant(file_path, output_path)
        elif theme == "modern": await extract_links_minimal(file_path, output_path)
        elif theme == "glassmorphism": await extract_links_glassmorphism(file_path, output_path)
        elif theme == "cyberpunk": await extract_links_pastel(file_path, output_path)
        elif theme == "hub": await extract_links_hub(file_path, output_path)
        else: raise ValueError("Invalid theme")

        settings = await get_bot_settings()
        caption_name = settings.get("caption_name")
        caption = f"✅ Theme: `{theme}`"
        if caption_name:
            caption += f" | Made by: {caption_name}"
        await msg.reply_document(document=output_path, file_name=f"{original_name}.html", caption=caption)
    except Exception as e:
        await msg.reply(f"❌ Error: `{str(e)}`")
    finally:
        for f in [file_path, output_path]:
            if os.path.exists(f): os.remove(f)

# --- Common Parsing ---
def parse_line(line):
    line = line.strip()
    if not line or ':' not in line:
        return None

    # URL is always the part starting at the LAST "http" occurrence, so we
    # split there instead of the first ':' -- this keeps titles that
    # contain ':' safe too.
    http_pos = line.rfind('http')
    if http_pos == -1:
        return None
    title_part = line[:http_pos]
    url = line[http_pos:].strip()
    title_part = title_part.rstrip(': \t')

    # Support "A || B || C" style multi-segment titles (new batch export
    # format) in addition to the older single-line "Title: URL" format.
    if '||' in title_part:
        segments = [seg.strip() for seg in title_part.split('||') if seg.strip()]
    else:
        segments = [title_part.strip()]

    subject = "General"
    category = None
    clean_title = segments[-1] if segments else title_part

    matches = re.findall(r'\[([^\]]+)\]|\(([^)]+)\)', clean_title)
    if matches:
        subject = matches[0][0] if matches[0][0] else matches[0][1]
        subject = subject.strip()
        if len(matches) > 1:
            category = matches[1][0] if matches[1][0] else matches[1][1]
            category = category.strip()
        clean_title = re.sub(r'\[([^\]]+)\]|\(([^)]+)\)', '', clean_title).strip()

    # If the bracket-based subject repeats across multiple classes (e.g.
    # "[Number System]" for Class-01, Class-02, Class-03...), merge in the
    # "Class - XX" segment so each class gets its own group instead of all
    # of them collapsing into one "Number System" bucket.
    class_segment = None
    for seg in segments:
        if re.match(r'(?i)^class\s*-?\s*\d+', seg.strip()):
            class_segment = seg.strip()
            break

    if matches and class_segment:
        subject = f"{subject} {class_segment}"
    elif subject == "General" and len(segments) >= 2:
        subject = segments[-2].strip()

    if not clean_title:
        clean_title = segments[-1] if segments else "Untitled"

    url_path = url.split('?')[0].split('#')[0]
    is_pdf = url_path.lower().endswith('.pdf')
    is_image = any(url_path.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])

    return {
        "subject": subject,
        "category": category,
        "title": clean_title,
        "url": url,
        "is_pdf": is_pdf,
        "is_image": is_image
    }

def make_subject_id(subject: str, index: int) -> str:
    """HTML-safe, guaranteed-unique id for a subject group. Using the
    subject name alone as the id caused duplicate ids (and broken
    show/hide toggling) whenever two different classes shared the same
    bracket subject; the index makes every group's id unique."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', subject).strip('_')
    return f"subj_{index}_{slug}"

# --- Common JS (Modal Player & Search) ---
COMMON_JS = """
<script>
    // Tab Switching
    function showContent(tabName) {
        document.querySelectorAll('.content-section').forEach(s=>s.classList.remove('active'));
        document.querySelectorAll('.nav-item, .tab').forEach(t=>t.classList.remove('active'));
        document.getElementById(tabName).classList.add('active');
        if(event && event.target) event.target.classList.add('active');
        
        // Update breadcrumbs
        const breadcrumb = document.querySelector('.breadcrumb span.active');
        if(breadcrumb) breadcrumb.textContent = tabName.charAt(0).toUpperCase() + tabName.slice(1);
    }
    
    // Toggle Folders (Subjects)
    function toggleVideos(subject) {
        const el = document.getElementById(subject);
        const icon = el.previousElementSibling.querySelector('.fa-chevron-down');
        if(el.classList.toggle('active')){
            icon.style.transform = 'rotate(180deg)';
        } else {
            icon.style.transform = 'rotate(0deg)';
        }
    }

    // --- SEARCH LOGIC ---
    function searchContent() {
        var input = document.getElementById('searchInput');
        var filter = input.value.toLowerCase();
        var items = document.querySelectorAll('.searchable-item');
        var subjects = document.querySelectorAll('.subject-card');

        // Reset all visibility first
        subjects.forEach(sub => sub.style.display = '');

        items.forEach(function(item) {
            var text = item.textContent || item.innerText;
            if (text.toLowerCase().indexOf(filter) > -1) {
                item.style.display = ""; // Show item
            } else {
                item.style.display = "none"; // Hide item
            }
        });

        // Smart Hide: If a subject has no visible items, hide subject too
        subjects.forEach(function(subject) {
            var listId = subject.getAttribute('onclick').match(/'([^']+)'/)[1];
            var list = document.getElementById(listId);
            var visibleItems = list.querySelectorAll('.searchable-item[style=""]');
            var hasVisibleItems = false;
            
            visibleItems.forEach(function(i) {
                if(i.offsetParent !== null) hasVisibleItems = true;
            });

            if (filter !== "" && !hasVisibleItems && subject.textContent.toLowerCase().indexOf(filter) === -1) {
                subject.style.display = "none";
            } else if (filter === "") {
                subject.style.display = ""; 
            }
        });
    }

    // --- VIDEO PLAYER MODAL ---
    function openPlayer(url) {
        document.getElementById('playerModal').style.display = "flex";
        document.getElementById('videoFrame').src = url;
    }
    function closePlayer() {
        document.getElementById('playerModal').style.display = "none";
        document.getElementById('videoFrame').src = "";
    }

    // PDF Viewer -- open directly in a new tab. The Mozilla pdf.js iframe
    // approach was removed because most PDF hosts block cross-origin
    // fetches (CORS), which left the viewer blank ("0 of 0" pages).
    // Opening the link directly lets the browser handle it natively,
    // exactly like it already does when the URL is opened by itself.
    function openPdf(url) {
        window.open(url, '_blank');
    }
    
    // Mobile Sidebar
    function toggleSidebar() {
        document.getElementById('sidebar').classList.toggle('active');
    }
</script>
"""

COMMON_PLAYER_MODAL = """
<div id="playerModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.9);z-index:9999;align-items:center;justify-content:center;">
    <div style="position:absolute;top:20px;right:20px;z-index:10000;">
        <button onclick="closePlayer()" style="background:#ef4444;color:white;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:18px;font-weight:bold;">✕ Close Player</button>
    </div>
    <iframe id="videoFrame" style="width:100%;height:100%;border:none;max-width:1200px;max-height:80vh;" allowfullscreen></iframe>
</div>
"""

COMMON_PDF_MODAL = ""  # PDFs now open directly in a new tab (see openPdf), modal no longer needed

# --- THEME 1: MINIMAL LIGHT ---
async def extract_links_minimal(input_file, output_file):
    video_links_by_subject = {}
    pdf_links = []
    image_links = []
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            if data['is_pdf']: pdf_links.append(data)
            elif data['is_image']: image_links.append(data)
            else:
                sub = data['subject']
                if sub not in video_links_by_subject: video_links_by_subject[sub] = []
                video_links_by_subject[sub].append(data)

    total_videos = sum(len(v) for v in video_links_by_subject.values())
    total_pdfs = len(pdf_links)
    total_images = len(image_links)

    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Minimal</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        :root {{ --bg: #ffffff; --surface: #f7f8fa; --border: #e5e7eb; --text: #111827; --text-muted: #6b7280; --accent: #2563eb; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 28px; }}
        h1 {{ font-size: 2.2rem; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 18px; }}
        #searchInput {{ width: 100%; max-width: 420px; padding: 13px 20px; border-radius: 12px; border: 1px solid var(--border); outline: none; background: var(--surface); color: var(--text); font-size: 0.95rem; }}
        #searchInput:focus {{ border-color: var(--accent); }}
        .tabs {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 28px; }}
        .tab {{ padding: 9px 22px; border-radius: 10px; cursor: pointer; font-weight: 500; font-size: 0.9rem; background: var(--surface); border: 1px solid var(--border); transition: 0.2s; }}
        .tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
        .content {{ display: none; }}
        .content.active {{ display: block; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .subject {{ padding: 18px 20px; border-radius: 14px; cursor: pointer; margin-bottom: 14px; background: var(--surface); border: 1px solid var(--border); font-weight: 600; }}
        .video-list {{ display: none; grid-column: 1 / -1; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .video-list.active {{ display: grid; }}
        .card {{ padding: 16px 18px; border-radius: 12px; background: var(--bg); border: 1px solid var(--border); transition: 0.2s; display: flex; align-items: center; gap: 14px; text-decoration: none; color: inherit; }}
        .card:hover {{ border-color: var(--accent); background: var(--surface); }}
        .card i {{ font-size: 1.2rem; color: var(--accent); flex-shrink: 0; }}
        .card span {{ font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .pdf-btn {{ margin-left: auto; padding: 6px 14px; background: var(--accent); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 0.82rem; flex-shrink: 0; }}
    </style></head><body><div class="container">
        <div class="header"><h1>Minimal</h1><input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="searchContent()"></div>
        <div class="tabs"><div class="tab active" onclick="showContent('videos')">Videos</div><div class="tab" onclick="showContent('pdfs')">PDFs</div></div>
        <div id="videos" class="content active"><div class="grid">"""
    for idx, (sub, vids) in enumerate(video_links_by_subject.items()):
        sid = make_subject_id(sub, idx)
        html_content += f'<div class="subject" onclick="toggleVideos(\'{sid}\')">{sub}</div><div id="{sid}" class="video-list">'
        for v in vids:
            p_url = get_player_url(v['url'])
            html_content += f'<a href="{p_url}" target="_blank" class="card searchable-item"><i class="fas fa-play-circle"></i><span>{v["title"]}</span></a>'
        html_content += '</div>'
    html_content += """</div></div><div id="pdfs" class="content"><div class="grid">"""
    for p in pdf_links:
        html_content += f'<div class="card searchable-item"><i class="fas fa-file-pdf"></i><span>{p["title"]}</span><button class="pdf-btn" onclick="openPdf(\'{p["url"]}\')">View</button></div>'
    html_content += f"""</div></div>{COMMON_PDF_MODAL}{COMMON_JS}</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

# --- THEME 2: NEUMORPHIC ---
async def extract_links_neumorphic(input_file, output_file):
    video_links_by_subject = {}
    pdf_links = []
    image_links = []
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            if data['is_pdf']: pdf_links.append(data)
            elif data['is_image']: image_links.append(data)
            else:
                sub = data['subject']
                if sub not in video_links_by_subject: video_links_by_subject[sub] = []
                video_links_by_subject[sub].append(data)
    total_videos = sum(len(v) for v in video_links_by_subject.values())
    
    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Neumorphic</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        :root {{ --bg: #e0e5ec; --card: #e0e5ec; --text: #4a5568; --accent: #6c5ce7; --shadow-light: #ffffff; --shadow-dark: #a3b1c6; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 20px; min-height: 100vh; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        h1 {{ font-size: 2.5rem; color: var(--accent); }}
        #searchInput {{ width: 100%; max-width: 400px; padding: 15px 25px; border-radius: 50px; border: none; outline: none; background: var(--card); box-shadow: 6px 6px 12px var(--shadow-dark), -6px -6px 12px var(--shadow-light); color: var(--text); margin-bottom: 20px; }}
        .tabs {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; }}
        .tab {{ padding: 10px 25px; border-radius: 15px; cursor: pointer; font-weight: 600; background: var(--card); box-shadow: 5px 5px 10px var(--shadow-dark), -5px -5px 10px var(--shadow-light); transition: 0.3s; }}
        .tab.active {{ box-shadow: inset 5px 5px 10px var(--shadow-dark), inset -5px -5px 10px var(--shadow-light); color: var(--accent); }}
        .content {{ display: none; }}
        .content.active {{ display: block; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 25px; }}
        .subject {{ padding: 20px; border-radius: 20px; cursor: pointer; margin-bottom: 20px; background: var(--card); box-shadow: 8px 8px 16px var(--shadow-dark), -8px -8px 16px var(--shadow-light); }}
        .video-list {{ display: none; grid-column: 1 / -1; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }}
        .video-list.active {{ display: grid; }}
        .card {{ padding: 20px; border-radius: 15px; background: var(--card); box-shadow: 6px 6px 12px var(--shadow-dark), -6px -6px 12px var(--shadow-light); transition: 0.3s; display: flex; align-items: center; gap: 15px; text-decoration: none; color: inherit; }}
        .card:hover {{ box-shadow: inset 4px 4px 8px var(--shadow-dark), inset -4px -4px 8px var(--shadow-light); }}
        .card i {{ font-size: 1.5rem; color: var(--accent); }}
        .pdf-btn {{ margin-left: auto; padding: 5px 10px; background: var(--accent); color: white; border: none; border-radius: 10px; cursor: pointer; }}
    </style></head><body><div class="container">
        <div class="header"><h1>Neumorphic</h1><input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="searchContent()"></div>
        <div class="tabs"><div class="tab active" onclick="showContent('videos')">Videos</div><div class="tab" onclick="showContent('pdfs')">PDFs</div></div>
        <div id="videos" class="content active"><div class="grid">"""
    for idx, (sub, vids) in enumerate(video_links_by_subject.items()):
        sid = make_subject_id(sub, idx)
        html_content += f'<div class="subject" onclick="toggleVideos(\'{sid}\')"><h3>{sub}</h3></div><div id="{sid}" class="video-list">'
        for v in vids: 
            p_url = get_player_url(v['url'])
            html_content += f'<a href="{p_url}" target="_blank" class="card searchable-item"><i class="fas fa-play-circle"></i><span>{v["title"]}</span></a>'
        html_content += '</div>'
    html_content += """</div></div><div id="pdfs" class="content"><div class="grid">"""
    for p in pdf_links:
        html_content += f'<div class="card searchable-item"><i class="fas fa-file-pdf"></i><span>{p["title"]}</span><button class="pdf-btn" onclick="openPdf(\'{p["url"]}\')">View</button></div>'
    html_content += f"""</div></div>{COMMON_PDF_MODAL}{COMMON_JS}</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

# --- THEME 3: DARK ELEGANT ---
async def extract_links_dark_elegant(input_file, output_file):
    video_links_by_subject = {}
    pdf_links = []
    image_links = []
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            if data['is_pdf']: pdf_links.append(data)
            elif data['is_image']: image_links.append(data)
            else:
                sub = data['subject']
                if sub not in video_links_by_subject: video_links_by_subject[sub] = []
                video_links_by_subject[sub].append(data)

    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Dark Elegant</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        :root {{ --bg: #0f0f11; --surface: #1a1a1d; --border: #2a2a2e; --text: #f4f4f5; --text-muted: #9a9aa0; --accent: #d4af37; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 28px; }}
        h1 {{ font-size: 2.3rem; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 18px; color: var(--accent); }}
        #searchInput {{ width: 100%; max-width: 420px; padding: 13px 20px; border-radius: 10px; border: 1px solid var(--border); outline: none; background: var(--surface); color: var(--text); font-size: 0.95rem; }}
        #searchInput:focus {{ border-color: var(--accent); }}
        .tabs {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 28px; }}
        .tab {{ padding: 9px 22px; border-radius: 8px; cursor: pointer; font-weight: 500; font-size: 0.9rem; background: var(--surface); border: 1px solid var(--border); transition: 0.2s; }}
        .tab.active {{ background: var(--accent); border-color: var(--accent); color: #0f0f11; font-weight: 700; }}
        .content {{ display: none; }}
        .content.active {{ display: block; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .subject {{ padding: 18px 20px; border-radius: 10px; cursor: pointer; margin-bottom: 14px; background: var(--surface); border: 1px solid var(--border); font-weight: 600; }}
        .video-list {{ display: none; grid-column: 1 / -1; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .video-list.active {{ display: grid; }}
        .card {{ padding: 16px 18px; border-radius: 10px; background: var(--surface); border: 1px solid var(--border); transition: 0.2s; display: flex; align-items: center; gap: 14px; text-decoration: none; color: inherit; }}
        .card:hover {{ border-color: var(--accent); }}
        .card i {{ font-size: 1.2rem; color: var(--accent); flex-shrink: 0; }}
        .card span {{ font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }}
        .pdf-btn {{ margin-left: auto; padding: 6px 14px; background: var(--accent); color: #0f0f11; border: none; border-radius: 8px; cursor: pointer; font-size: 0.82rem; font-weight: 600; flex-shrink: 0; }}
    </style></head><body><div class="container">
        <div class="header"><h1>Dark Elegant</h1><input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="searchContent()"></div>
        <div class="tabs"><div class="tab active" onclick="showContent('videos')">Videos</div><div class="tab" onclick="showContent('pdfs')">PDFs</div></div>
        <div id="videos" class="content active"><div class="grid">"""
    for idx, (sub, vids) in enumerate(video_links_by_subject.items()):
        sid = make_subject_id(sub, idx)
        html_content += f'<div class="subject" onclick="toggleVideos(\'{sid}\')">{sub}</div><div id="{sid}" class="video-list">'
        for v in vids:
            p_url = get_player_url(v['url'])
            html_content += f'<a href="{p_url}" target="_blank" class="card searchable-item"><i class="fas fa-play-circle"></i><span>{v["title"]}</span></a>'
        html_content += '</div>'
    html_content += """</div></div><div id="pdfs" class="content"><div class="grid">"""
    for p in pdf_links:
        html_content += f'<div class="card searchable-item"><i class="fas fa-file-pdf"></i><span>{p["title"]}</span><button class="pdf-btn" onclick="openPdf(\'{p["url"]}\')">View</button></div>'
    html_content += f"""</div></div>{COMMON_PDF_MODAL}{COMMON_JS}</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

# --- THEME 4: GLASSMORPHISM ---
async def extract_links_glassmorphism(input_file, output_file):
    video_links_by_subject = {}
    pdf_links = []
    image_links = []
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            if data['is_pdf']: pdf_links.append(data)
            elif data['is_image']: image_links.append(data)
            else:
                sub = data['subject']
                if sub not in video_links_by_subject: video_links_by_subject[sub] = []
                video_links_by_subject[sub].append(data)

    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Glass</title><link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;500;600&display=swap" rel="stylesheet"><style>
        body{{margin:0;padding:0;background:linear-gradient(45deg,#1a1a2e,#16213e);background-attachment:fixed;color:#fff;font-family:'Poppins',sans-serif;min-height:100vh;}}
        .container{{max-width:1100px;margin:0 auto;padding:20px;}}
        .glass{{background:rgba(255,255,255,0.05);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.1);border-radius:15px;}}
        h1{{text-align:center;margin-bottom:20px;}}
        #searchInput{{width:100%;padding:15px;border-radius:30px;background:rgba(255,255,255,0.1);border:none;color:#fff;margin-bottom:20px;outline:none;}}
        .tabs{{display:flex;justify-content:center;gap:15px;margin-bottom:30px;}}
        .tab{{padding:10px 25px;border-radius:20px;cursor:pointer;background:rgba(255,255,255,0.1);transition:0.3s;}}
        .tab.active{{background:#ff0055;}}
        .content{{display:none;}}
        .content.active{{display:block;}}
        .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px;}}
        .card{{padding:20px;text-decoration:none;color:#fff;display:block;transition:0.3s;}}
        .card:hover{{background:rgba(255,255,255,0.1);}}
        .subject{{padding:20px;cursor:pointer;margin-bottom:15px;font-weight:bold;}}
        .video-list{{display:none;grid-column:1/-1;}}
        .video-list.active{{display:grid;}}
        .pdf-btn{{float:right;color:#ff0055;font-weight:bold;cursor:pointer;border:none;background:none;}}
    </style></head><body><div class="container">
        <div class="glass" style="padding:20px;margin-bottom:20px;"><h1>Glass View</h1></div>
        <div class="glass" style="padding:15px;margin-bottom:20px;"><input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="searchContent()"></div>
        <div class="tabs"><div class="tab active" onclick="showContent('videos')">Videos</div><div class="tab" onclick="showContent('pdfs')">PDFs</div></div>
        <div id="videos" class="content active"><div class="grid">"""
    for idx, (sub, vids) in enumerate(video_links_by_subject.items()):
        sid = make_subject_id(sub, idx)
        html_content += f'<div class="glass subject" onclick="toggleVideos(\'{sid}\')">{sub}</div><div id="{sid}" class="video-list">'
        for v in vids: 
            p_url = get_player_url(v['url'])
            html_content += f'<a href="{p_url}" target="_blank" class="glass card searchable-item">▶ {v["title"]}</a>'
        html_content += '</div>'
    html_content += """</div></div><div id="pdfs" class="content"><div class="grid">"""
    for p in pdf_links:
        html_content += f'<div class="glass card searchable-item">📄 {p["title"]}<button class="pdf-btn" onclick="openPdf(\'{p["url"]}\')">View PDF</button></div>'
    html_content += f"""</div></div>{COMMON_PDF_MODAL}{COMMON_JS}</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

# --- THEME 5: SOFT PASTEL ---
async def extract_links_pastel(input_file, output_file):
    video_links_by_subject = {}
    pdf_links = []
    image_links = []
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            if data['is_pdf']: pdf_links.append(data)
            elif data['is_image']: image_links.append(data)
            else:
                sub = data['subject']
                if sub not in video_links_by_subject: video_links_by_subject[sub] = []
                video_links_by_subject[sub].append(data)

    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Soft Pastel</title><link href="https://fonts.googleapis.com/css2?family=Quicksand:wght@400;500;600;700&display=swap" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        :root {{ --bg: #fdfbff; --surface: #f4effa; --border: #e8ddf5; --text: #3f3355; --text-muted: #8a7fa3; --accent: #9b6bd6; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Quicksand', sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 28px; }}
        h1 {{ font-size: 2.4rem; font-weight: 700; margin-bottom: 18px; color: var(--accent); }}
        #searchInput {{ width: 100%; max-width: 420px; padding: 13px 20px; border-radius: 20px; border: 1.5px solid var(--border); outline: none; background: var(--surface); color: var(--text); font-size: 0.95rem; font-family: 'Quicksand', sans-serif; }}
        #searchInput:focus {{ border-color: var(--accent); }}
        .tabs {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 28px; }}
        .tab {{ padding: 9px 22px; border-radius: 16px; cursor: pointer; font-weight: 600; font-size: 0.9rem; background: var(--surface); border: 1.5px solid var(--border); transition: 0.2s; }}
        .tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
        .content {{ display: none; }}
        .content.active {{ display: block; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .subject {{ padding: 18px 20px; border-radius: 18px; cursor: pointer; margin-bottom: 14px; background: var(--surface); border: 1.5px solid var(--border); font-weight: 600; }}
        .video-list {{ display: none; grid-column: 1 / -1; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
        .video-list.active {{ display: grid; }}
        .card {{ padding: 16px 18px; border-radius: 16px; background: var(--surface); border: 1.5px solid var(--border); transition: 0.2s; display: flex; align-items: center; gap: 14px; text-decoration: none; color: inherit; }}
        .card:hover {{ border-color: var(--accent); background: #fff; }}
        .card i {{ font-size: 1.2rem; color: var(--accent); flex-shrink: 0; }}
        .card span {{ font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .pdf-btn {{ margin-left: auto; padding: 6px 14px; background: var(--accent); color: white; border: none; border-radius: 14px; cursor: pointer; font-size: 0.82rem; font-weight: 600; flex-shrink: 0; }}
    </style></head><body><div class="container">
        <div class="header"><h1>Soft Pastel</h1><input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="searchContent()"></div>
        <div class="tabs"><div class="tab active" onclick="showContent('videos')">Videos</div><div class="tab" onclick="showContent('pdfs')">PDFs</div></div>
        <div id="videos" class="content active"><div class="grid">"""
    for idx, (sub, vids) in enumerate(video_links_by_subject.items()):
        sid = make_subject_id(sub, idx)
        html_content += f'<div class="subject" onclick="toggleVideos(\'{sid}\')">{sub}</div><div id="{sid}" class="video-list">'
        for v in vids:
            p_url = get_player_url(v['url'])
            html_content += f'<a href="{p_url}" target="_blank" class="card searchable-item"><i class="fas fa-play-circle"></i><span>{v["title"]}</span></a>'
        html_content += '</div>'
    html_content += """</div></div><div id="pdfs" class="content"><div class="grid">"""
    for p in pdf_links:
        html_content += f'<div class="card searchable-item"><i class="fas fa-file-pdf"></i><span>{p["title"]}</span><button class="pdf-btn" onclick="openPdf(\'{p["url"]}\')">View</button></div>'
    html_content += f"""</div></div>{COMMON_PDF_MODAL}{COMMON_JS}</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

# --- THEME 6: HUB (subject drill-down + in-page player) ---
async def extract_links_hub(input_file, output_file):
    video_links_by_subject = {}
    pdf_links_by_subject = {}
    with open(input_file, 'r', encoding='utf-8', errors='replace') as file:
        for line in file:
            data = parse_line(line)
            if not data: continue
            sub = data['subject']
            if data['is_pdf']:
                pdf_links_by_subject.setdefault(sub, []).append(data)
            elif data['is_image']:
                continue
            else:
                video_links_by_subject.setdefault(sub, []).append(data)

    # All subjects that have at least one video or PDF, in first-seen order.
    all_subjects = []
    for sub in list(video_links_by_subject.keys()) + list(pdf_links_by_subject.keys()):
        if sub not in all_subjects:
            all_subjects.append(sub)

    import json as _json
    subjects_payload = []
    for idx, sub in enumerate(all_subjects):
        vids = video_links_by_subject.get(sub, [])
        pdfs = pdf_links_by_subject.get(sub, [])
        subjects_payload.append({
            "id": make_subject_id(sub, idx),
            "name": sub,
            "videos": [{"title": v["title"], "url": get_player_url(v["url"])} for v in vids],
            "pdfs": [{"title": p["title"], "url": p["url"]} for p in pdfs],
        })
    subjects_json = _json.dumps(subjects_payload, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Hub</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.13/hls.min.js"></script>
<style>
    :root {{ --bg: #f5f6f8; --surface: #ffffff; --border: #e5e7eb; --text: #111827; --text-muted: #6b7280; --accent: #4f46e5; }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding-bottom: 40px; }}
    .container {{ max-width: 720px; margin: 0 auto; padding: 16px; }}
    .header {{ text-align: center; margin-bottom: 16px; }}
    h1 {{ font-size: 1.6rem; font-weight: 700; }}
    #playerWrap {{ position: sticky; top: 0; background: #000; z-index: 50; border-radius: 0 0 12px 12px; overflow: hidden; display: none; }}
    #playerWrap.active {{ display: block; }}
    #playerTitle {{ color: #fff; font-size: 0.85rem; padding: 8px 12px; background: #111; }}
    video {{ width: 100%; max-height: 50vh; display: block; background: #000; }}
    #searchInput {{ width: 100%; padding: 12px 18px; border-radius: 10px; border: 1px solid var(--border); outline: none; background: var(--surface); font-size: 0.95rem; margin-bottom: 12px; }}
    #searchInput:focus {{ border-color: var(--accent); }}
    .backBtn {{ display: none; align-items: center; gap: 6px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px; cursor: pointer; font-size: 0.85rem; margin-bottom: 12px; }}
    .backBtn.active {{ display: inline-flex; }}
    .subjectTitle {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; display: none; }}
    .subjectTitle.active {{ display: block; }}
    .tabs {{ display: flex; gap: 8px; margin-bottom: 14px; }}
    .tab {{ flex: 1; padding: 10px; border-radius: 10px; text-align: center; cursor: pointer; background: var(--surface); border: 1px solid var(--border); font-size: 0.85rem; font-weight: 500; }}
    .tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    .list {{ display: flex; flex-direction: column; gap: 10px; }}
    .row {{ display: flex; align-items: center; gap: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; cursor: pointer; text-decoration: none; color: inherit; }}
    .row.playing {{ background: #eef0ff; border-color: var(--accent); }}
    .row.playing i.fas:first-child {{ color: var(--accent); }}
    .row i {{ color: var(--accent); font-size: 1.1rem; flex-shrink: 0; }}
    .row span {{ font-size: 0.92rem; flex: 1; }}
    .row .type {{ font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; flex-shrink: 0; }}
    .row .arrow {{ color: var(--text-muted); flex-shrink: 0; }}
</style></head><body>
<div id="playerWrap"><div id="playerTitle"></div><video id="hubVideo" controls></video></div>
<div class="container">
    <div class="header"><h1>Hub</h1></div>
    <div class="backBtn" id="backBtn" onclick="showSubjectList()"><i class="fas fa-arrow-left"></i> Back</div>
    <div class="subjectTitle" id="subjectTitle"></div>
    <input type="text" id="searchInput" placeholder="🔍 Search..." onkeyup="onSearch()">
    <div class="tabs" id="tabs">
        <div class="tab active" data-type="all" onclick="setFilter('all')">All</div>
        <div class="tab" data-type="video" onclick="setFilter('video')">Videos</div>
        <div class="tab" data-type="pdf" onclick="setFilter('pdf')">PDFs</div>
    </div>
    <div class="list" id="list"></div>
</div>
<script>
const SUBJECTS = {subjects_json};
let currentSubject = null;
let currentFilter = 'all';
let hlsInstance = null;
let currentPlayingUrl = null;

function renderSubjectList(filterText) {{
    const list = document.getElementById('list');
    list.innerHTML = '';
    const q = (filterText || '').toLowerCase();
    SUBJECTS.forEach(s => {{
        if (q && s.name.toLowerCase().indexOf(q) === -1) return;
        const total = s.videos.length + s.pdfs.length;
        const row = document.createElement('div');
        row.className = 'row';
        row.onclick = () => openSubject(s.id);
        row.innerHTML = `<i class="fas fa-folder"></i><span>${{s.name}}</span><span class="type">${{total}} items</span><i class="fas fa-chevron-right arrow"></i>`;
        list.appendChild(row);
    }});
}}

function openSubject(id) {{
    currentSubject = SUBJECTS.find(s => s.id === id);
    if (!currentSubject) return;
    document.getElementById('backBtn').classList.add('active');
    document.getElementById('subjectTitle').classList.add('active');
    document.getElementById('subjectTitle').textContent = currentSubject.name;
    document.getElementById('searchInput').value = '';
    document.getElementById('searchInput').placeholder = 'Search in this subject...';
    currentFilter = 'all';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector('.tab[data-type="all"]').classList.add('active');
    updateTabCounts();
    renderItems('');
}}

function showSubjectList() {{
    currentSubject = null;
    document.getElementById('backBtn').classList.remove('active');
    document.getElementById('subjectTitle').classList.remove('active');
    document.getElementById('searchInput').value = '';
    document.getElementById('searchInput').placeholder = '🔍 Search...';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector('.tab[data-type="all"]').classList.add('active');
    document.getElementById('tabs').style.display = 'none';
    renderSubjectList('');
}}

function updateTabCounts() {{
    if (!currentSubject) return;
    document.querySelector('.tab[data-type="all"]').textContent = `All (${{currentSubject.videos.length + currentSubject.pdfs.length}})`;
    document.querySelector('.tab[data-type="video"]').textContent = `Videos (${{currentSubject.videos.length}})`;
    document.querySelector('.tab[data-type="pdf"]').textContent = `PDFs (${{currentSubject.pdfs.length}})`;
    document.getElementById('tabs').style.display = 'flex';
}}

function renderItems(filterText) {{
    const list = document.getElementById('list');
    list.innerHTML = '';
    if (!currentSubject) return;
    const q = (filterText || '').toLowerCase();
    let items = [];
    if (currentFilter === 'all' || currentFilter === 'video') {{
        currentSubject.videos.forEach(v => items.push({{type: 'video', title: v.title, url: v.url}}));
    }}
    if (currentFilter === 'all' || currentFilter === 'pdf') {{
        currentSubject.pdfs.forEach(p => items.push({{type: 'pdf', title: p.title, url: p.url}}));
    }}
    items.forEach(it => {{
        if (q && it.title.toLowerCase().indexOf(q) === -1) return;
        const row = document.createElement('div');
        row.className = 'row' + (it.type === 'video' && it.url === currentPlayingUrl ? ' playing' : '');
        const icon = it.type === 'video' ? 'fa-play-circle' : 'fa-file-pdf';
        row.innerHTML = `<i class="fas ${{icon}}"></i><span>${{it.title}}</span><span class="type">${{it.type}}</span><i class="fas fa-chevron-right arrow"></i>`;
        row.onclick = () => it.type === 'video' ? playVideo(it.url, it.title, currentSubject.name) : openPdf(it.url);
        list.appendChild(row);
    }});
}}

function setFilter(type) {{
    currentFilter = type;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[data-type="${{type}}"]`).classList.add('active');
    renderItems(document.getElementById('searchInput').value);
}}

function onSearch() {{
    const val = document.getElementById('searchInput').value;
    if (currentSubject) renderItems(val); else renderSubjectList(val);
}}

// Play in the sticky in-page player. Falls back to opening the URL in a
// new tab if the stream can't be loaded in-page (e.g. HLS blocked by the
// source server) so playback still works exactly like before.
function playVideo(url, title, subjectName) {{
    const wrap = document.getElementById('playerWrap');
    const video = document.getElementById('hubVideo');
    const titleEl = document.getElementById('playerTitle');
    wrap.classList.add('active');
    titleEl.textContent = subjectName ? (subjectName + ' — ' + title) : title;
    wrap.scrollIntoView({{behavior: 'smooth', block: 'start'}});

    currentPlayingUrl = url;
    renderItems(document.getElementById('searchInput').value);

    if (hlsInstance) {{ hlsInstance.destroy(); hlsInstance = null; }}

    function giveUp() {{
        wrap.classList.remove('active');
        if (currentPlayingUrl === url) {{
            currentPlayingUrl = null;
            renderItems(document.getElementById('searchInput').value);
        }}
        window.open(url, '_blank');
    }}

    let fallbackTimer = setTimeout(giveUp, 8000);
    function clearFallback() {{ clearTimeout(fallbackTimer); }}

    if (url.toLowerCase().includes('.m3u8') && window.Hls && Hls.isSupported()) {{
        hlsInstance = new Hls();
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, clearFallback);
        hlsInstance.on(Hls.Events.ERROR, (evt, data) => {{
            if (data.fatal) {{ clearFallback(); giveUp(); }}
        }});
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(video);
        video.play().catch(() => {{}});
    }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
        video.src = url;
        video.addEventListener('loadedmetadata', clearFallback, {{once: true}});
        video.addEventListener('error', () => {{ clearFallback(); giveUp(); }}, {{once: true}});
        video.play().catch(() => {{}});
    }} else {{
        video.src = url;
        video.addEventListener('loadedmetadata', clearFallback, {{once: true}});
        video.addEventListener('error', () => {{ clearFallback(); giveUp(); }}, {{once: true}});
        video.play().catch(() => {{}});
    }}
}}

function openPdf(url) {{
    window.open(url, '_blank');
}}

document.getElementById('tabs').style.display = 'none';
renderSubjectList('');
</script>
</body></html>"""
    with open(output_file, 'w', encoding='utf-8') as file: file.write(html_content)

if __name__ == "__main__":
    print("✅ Bot is starting...")
    client.run()
