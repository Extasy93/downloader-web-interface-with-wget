import os
import uuid
import json
import subprocess
import requests
import functools
from flask import Flask, request, render_template, jsonify, session, redirect, url_for
from flask_socketio import SocketIO
from threading import Thread
from dotenv import load_dotenv
from datetime import datetime

print = functools.partial(print, flush=True)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
app.config['DEBUG'] = True
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True
)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

APP_PASSWORD        = os.environ.get("APP_PASSWORD", "changeme")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
BASE_DOWNLOAD_PATH  = "/downloads"
HISTORY_FILE        = "download_history.json"
MAX_HISTORY_ITEMS   = 15

active_downloads = {}

def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"[ERROR] Erreur lors du chargement de l'historique: {e}")
    return []

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Erreur lors de la sauvegarde de l'historique: {e}")

download_history = load_history()

print(f"[INIT] APP_PASSWORD={'*'*len(APP_PASSWORD)}  DISCORD_WEBHOOK_URL set?={'Yes' if DISCORD_WEBHOOK_URL else 'No'}  BASE_DOWNLOAD_PATH={BASE_DOWNLOAD_PATH}")

@app.route("/", methods=["GET", "POST"])
def index():
    print(f"[TRACE] → index() method={request.method}  session.authenticated={session.get('authenticated')}")
    error = None
    if session.get("authenticated"):
        print("[TRACE]    déjà authentifié, render main")
        return render_template("index.html", password_ok=True)
    if request.method == "POST":
        pwd = request.form.get("password")
        print(f"[TRACE]    tentative login pwd={pwd}")
        if pwd == APP_PASSWORD:
            session["authenticated"] = True
            print("[TRACE]    login OK → redirect")
            return redirect(url_for("index"))
        else:
            error = "Mot de passe incorrect"
            print("[TRACE]    login FAILED")
    print(f"[TRACE]    render login page error={error}")
    return render_template("index.html", password_ok=False, error=error)

@app.route("/logout")
def logout():
    print("[TRACE] → logout(), clearing session")
    session.clear()
    return redirect(url_for("index"))

@app.route("/get_files")
def get_files():
    print(f"[TRACE] → get_files()  authenticated={session.get('authenticated')}")
    if not session.get("authenticated"):
        print("[TRACE]    unauthorized get_files → 403")
        return jsonify({"error": "Non autorisé"}), 403
    def build_tree(path):
        items = []
        try:
            for entry in sorted(os.listdir(path)):
                full = os.path.join(path, entry)
                if os.path.isdir(full):
                    items.append({
                        "name": entry,
                        "path": full,
                        "children": build_tree(full)
                    })
        except Exception as e:
            print(f"[ERROR]    build_tree('{path}') error: {e}")
        return items
    tree = build_tree(BASE_DOWNLOAD_PATH)
    print(f"[TRACE]    get_files returning {len(tree)} roots")
    return jsonify({"files": tree})

@app.route("/get_history")
def get_history():
    print(f"[TRACE] → get_history()  authenticated={session.get('authenticated')}")
    if not session.get("authenticated"):
        print("[TRACE]    unauthorized get_history → 403")
        return jsonify({"error": "Non autorisé"}), 403
    return jsonify({"history": download_history})

def send_discord_notification(content):
    print(f"[TRACE] → send_discord_notification('{content}')")
    if not DISCORD_WEBHOOK_URL:
        print("[TRACE]    no webhook configured")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        print(f"[TRACE]    webhook status={resp.status_code}")
    except Exception as e:
        print(f"[ERROR]    webhook exception: {e}")

@socketio.on('connect')
def handle_connect():
    socketio.emit('feed_update', {'downloads': active_downloads})

def download_file(url, folder, download_id):
    print(f"[TRACE] → download_file id={download_id} url={url}")
    filename = os.path.basename(url.split("?")[0])
    output = os.path.join(folder, filename)
    print(f"[TRACE]    saving to {output}")
    active_downloads[download_id] = {
        'filename': filename,
        'folder': folder,
        'progress': 0,
        'status': 'starting',
        'timestamp': datetime.now().isoformat()
    }
    socketio.emit('feed_update', {'downloads': active_downloads})
    proc = subprocess.Popen(
        ["wget", "-O", output, url],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1
    )
    last = -1
    for line in iter(proc.stdout.readline, ""):
        print(f"[DEBUG][{download_id}] {line.strip()}")
        if "%" in line:
            try:
                part = next(p for p in line.split() if p.endswith("%"))
                percent = int(part.strip("%"))
                if percent != last:
                    print(f"[TRACE][{download_id}] {percent}%")
                    socketio.emit("progress", {"id": download_id, "progress": percent})
                    active_downloads[download_id]['progress'] = percent
                    active_downloads[download_id]['status'] = 'downloading'
                    socketio.emit('feed_update', {'downloads': active_downloads})
                    socketio.sleep(0)
                    last = percent
            except Exception as e:
                print(f"[ERROR]    parse progress: {e}")
    proc.wait()
    print(f"[TRACE]    download complete id={download_id}")
    socketio.emit("progress", {"id": download_id, "progress": 100})
    active_downloads[download_id]['progress'] = 100
    active_downloads[download_id]['status'] = 'completed'
    socketio.emit('feed_update', {'downloads': active_downloads})
    download_history.insert(0, {
        'filename': filename,
        'folder': folder,
        'timestamp': datetime.now().isoformat(),
        'status': 'completed'
    })
    if len(download_history) > MAX_HISTORY_ITEMS:
        download_history.pop()
    save_history(download_history)
    send_discord_notification(f"Téléchargement terminé: **{filename}** dans `{folder}`.")

@app.route("/download", methods=["POST"])
def handle_download():
    print(f"[TRACE] → POST /download  authenticated={session.get('authenticated')}")
    if not session.get("authenticated"):
        print("[TRACE]    unauthorized → 403")
        return jsonify({"error": "Non autorisé"}), 403
    url    = request.form.get("url")
    folder = request.form.get("folder")
    print(f"[TRACE]    params url={url}, folder={folder}")
    if not url or not folder:
        print("[TRACE]    missing params → 400")
        return jsonify({"error": "Données manquantes"}), 400
    absf = os.path.abspath(folder)
    if not absf.startswith(BASE_DOWNLOAD_PATH):
        print(f"[TRACE]    invalid folder {absf} → 400")
        return jsonify({"error": "Chemin non autorisé"}), 400
    filename = os.path.basename(url.split("?")[0])
    output = os.path.join(folder, filename)
    for d in active_downloads.values():
        if d['filename'] == filename and d['folder'] == folder and d['status'] != 'completed':
            print(f"[TRACE]    téléchargement déjà en cours pour {output}")
            return jsonify({"error": "Téléchargement déjà en cours"}), 409
    did = str(uuid.uuid4())[:8]
    print(f"[TRACE]    starting thread id={did}")
    Thread(target=download_file, args=(url, folder, did), daemon=True).start()
    return jsonify({"id": did})

if __name__ == "__main__":
    print("[TRACE] Starting server on 0.0.0.0:80")
    socketio.run(app, host="0.0.0.0", port=80, debug=True, allow_unsafe_werkzeug=True, use_reloader=True)
