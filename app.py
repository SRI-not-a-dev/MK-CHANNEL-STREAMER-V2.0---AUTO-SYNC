from flask import Flask, render_template, request, send_file, jsonify
from telegram import Update
from telegram.ext import Application, ChannelPostHandler, ContextTypes
import requests, os, sqlite3, uuid, threading, subprocess

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID") # -100xxxxxxxx
DB = "videos.db"
DOWNLOAD_DIR = "downloads"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# INIT DB
conn = sqlite3.connect(DB)
conn.execute('''CREATE TABLE IF NOT EXISTS videos
    (id INTEGER PRIMARY KEY, file_id TEXT, title TEXT, file_url TEXT, uid TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
conn.close()

def save_video(file_id, title, file_url):
    uid = str(uuid.uuid4())
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO videos (file_id, title, file_url, uid) VALUES (?,?,?,?)", (file_id, title, file_url, uid))
    conn.commit()
    conn.close()
    # Start conversion in background
    threading.Thread(target=process_video, args=(file_id, file_url, uid)).start()

def process_video(file_id, file_url, uid):
    folder = os.path.join(DOWNLOAD_DIR, uid)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "video.mkv")

    r = requests.get(file_url, stream=True)
    with open(file_path, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            f.write(chunk)

    # Convert to HLS with all audio tracks
    cmd = [
        'ffmpeg', '-i', file_path,
        '-map', '0:v', '-map', '0:a',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-var_stream_map', 'v:0,a:0,name:video',
        '-master_pl_name', 'master.m3u8',
        '-hls_time', '6', '-hls_list_size', '0',
        f'{folder}/stream_%v.m3u8'
    ]
    subprocess.run(cmd)

# TELEGRAM BOT
async def channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if msg.video or msg.document:
        file = msg.video or msg.document
        file_id = file.file_id
        title = msg.caption or file.file_name or "Untitled"
        file_obj = await context.bot.get_file(file_id)
        file_url = file_obj.file_path
        save_video(file_id, title, file_url)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=f"✅ Added to Website: {title}")

def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(ChannelPostHandler(channel_handler))
    application.run_polling()

# FLASK ROUTES
@app.route('/')
def home():
    conn = sqlite3.connect(DB)
    videos = conn.execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('index.html', videos=videos)

@app.route('/watch/<uid>')
def watch(uid):
    conn = sqlite3.connect(DB)
    video = conn.execute("SELECT * FROM videos WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not video: return "Not found"
    return render_template('player.html', stream_url=f"/hls/{uid}/master.m3u8", download_url=video[3], title=video[2])

@app.route('/hls/<uid>/<path:filename>')
def hls_files(uid, filename):
    return send_file(os.path.join(DOWNLOAD_DIR, uid, filename))

if __name__ == '__main__':
    threading.Thread(target=run_bot).start() # Run bot in background
    app.run(host='0.0.0.0', port=5000)
