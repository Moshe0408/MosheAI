"""MosheAI — Flask Web App, Vercel-ready"""

import json
import os
import functools
from pathlib import Path
from flask import (Flask, render_template, request, Response,
                   jsonify, send_file, session, redirect, url_for)

from engine.agent import MosheAIAgent
from engine.tools import list_outputs, OUTPUT_DIR

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mosheai-secret-2026-xk9")
app.config["JSON_AS_ASCII"]        = False
app.config["TEMPLATES_AUTO_RELOAD"] = True

# ── משתמשים ──────────────────────────────────────
CREDENTIALS = {
    "Moshei1": "Admin2026"
}

# ── טעינת API Key מ-env ──────────────────────────
# ב-Vercel מגדירים GROQ_API_KEY ב-Environment Variables
# בסביבה מקומית: set GROQ_API_KEY=gsk_...

agent = MosheAIAgent()


def _reinit_agent():
    global agent
    agent = MosheAIAgent()


# ── הגנה ─────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ── דפים ─────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if CREDENTIALS.get(username) == password:
            session["logged_in"] = True
            session["username"]  = username
            return redirect(url_for("index"))
        error = "שם משתמש או סיסמה שגויים"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    api_key_set = bool(os.environ.get("GROQ_API_KEY"))
    username    = session.get("username", "")
    return render_template("index.html", api_key_set=api_key_set, username=username)


# ── API ───────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data    = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "הודעה ריקה"}), 400

    def generate():
        for chunk in agent.stream_response(message):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/memory")
@login_required
def get_memory():
    return jsonify(agent.get_memory_summary())


@app.route("/api/files")
@login_required
def get_files():
    return jsonify(list_outputs())


@app.route("/api/file/<path:filename>")
@login_required
def download_file(filename):
    path = OUTPUT_DIR / Path(filename).name
    if not path.exists():
        return jsonify({"error": "קובץ לא נמצא — ייתכן שהשרת הופעל מחדש"}), 404
    return send_file(str(path), as_attachment=True, download_name=path.name)


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "GET":
        key    = os.environ.get("GROQ_API_KEY", "")
        masked = ("gsk_..." + key[-6:]) if len(key) > 10 else ""
        return jsonify({"api_key_set": bool(key), "masked": masked, "provider": "Groq"})

    data = request.get_json(force=True)
    key  = (data.get("api_key") or "").strip()
    if not key:
        return jsonify({"error": "מפתח ריק"}), 400
    if not key.startswith("gsk_"):
        return jsonify({"error": "מפתח Groq לא תקין (חייב להתחיל ב-gsk_)"}), 400

    os.environ["GROQ_API_KEY"] = key
    _reinit_agent()
    return jsonify({"ok": True, "message": "✅ Groq API Key נשמר והסוכן אותחל מחדש!"})


# ── Health check ──────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({
        "status":  "ok",
        "api_key": bool(os.environ.get("GROQ_API_KEY")),
        "model":   "llama-3.3-70b-versatile"
    })


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + "=" * 50)
    print("  MosheAI  -  Ready!")
    print("=" * 50)
    if not os.environ.get("GROQ_API_KEY"):
        print("  WARNING: GROQ_API_KEY not set!")
        print("  set GROQ_API_KEY=gsk_...")
    else:
        print("  Groq API Key: OK ✅")
    print("  URL: http://localhost:5000")
    print("  User: Moshei1 | Pass: Admin2026")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
