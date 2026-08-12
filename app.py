import os
import json
import uuid
import random
import threading
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory

from services import openrouter

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = BASE_DIR / "data" / "backups.json"
ADMIN_EXPORT_PATH = os.environ.get("ADMIN_EXPORT_PATH", "changeme-export")
COOKIE_NAME = "cinetot_uid"

app = Flask(__name__, static_folder=None)
_lock = threading.Lock()


def _load_backups():
    if not DATA_FILE.exists():
        return {}
    with open(DATA_FILE) as f:
        return json.load(f)


def _save_backups(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _touch_backup(user_id, language=None, known_words=None, game=None):
    with _lock:
        data = _load_backups()
        entry = data.get(user_id, {"fingerprint": {}, "language": None, "known_words": None, "history": []})
        entry["fingerprint"] = {"ip": request.headers.get("X-Forwarded-For", request.remote_addr), "user_agent": request.headers.get("User-Agent")}
        if language is not None:
            entry["language"] = language
        if known_words is not None:
            entry["known_words"] = known_words
        if game is not None:
            history = entry.get("history") or []
            entry["history"] = history[-19:] + [{"at": datetime.now(timezone.utc).isoformat(), "movie": game.get("correctAnswer")}]
        data[user_id] = entry
        _save_backups(data)


@app.before_request
def ensure_user_id():
    request.new_uid = None
    if not request.cookies.get(COOKIE_NAME):
        request.new_uid = str(uuid.uuid4())


@app.after_request
def set_user_id_cookie(response):
    new_uid = getattr(request, "new_uid", None)
    if new_uid:
        response.set_cookie(COOKIE_NAME, new_uid, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
    return response


def _current_user_id():
    return request.cookies.get(COOKIE_NAME) or getattr(request, "new_uid", None)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


@app.route("/api/game", methods=["POST"])
def api_game():
    body = request.get_json(force=True) or {}
    language = body.get("language", "English")
    known_words = body.get("knownWords", "")
    try:
        game = openrouter.create_game(language, known_words)
        options = game.get("options", [])
        random.shuffle(options)
        game["options"] = options
        _touch_backup(_current_user_id(), language=language, known_words=known_words, game=game)
        return jsonify(game)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/word", methods=["POST"])
def api_word():
    body = request.get_json(force=True) or {}
    word = body.get("word", "")
    context = body.get("context", "")
    language = body.get("language", "English")
    if not word:
        return jsonify({"error": "word is required"}), 400
    try:
        analysis = openrouter.analyze_word(word, context, language)
        return jsonify(analysis)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route(f"/admin/{ADMIN_EXPORT_PATH}")
def admin_export():
    return jsonify(_load_backups())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
