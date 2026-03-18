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
app.config["JSON_AS_ASCII"]         = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"]    = 16 * 1024 * 1024  # 16MB upload limit

# ── משתמשים ──────────────────────────────────────
CREDENTIALS = {"Moshei1": "Admin2026"}

# ── תיקיית קבצים שהועלו ─────────────────────────
UPLOAD_DIR = Path("/tmp/mosheai_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ב-/tmp (עמיד בין requests באותו container) ─
CONFIG_FILE = Path("/tmp/mosheai_config.json")


def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config(cfg: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── טעינת API Key בעת אתחול ──────────────────────
# סדר עדיפות: env var (Vercel dashboard) → /tmp/config.json
_cfg = _load_config()
if not os.environ.get("GROQ_API_KEY") and _cfg.get("groq_api_key"):
    os.environ["GROQ_API_KEY"] = _cfg["groq_api_key"]

agent = MosheAIAgent()


def _reinit_agent():
    global agent
    agent = MosheAIAgent()


# ── before_request: שחזר API Key מה-session cookie ──
@app.before_request
def ensure_api_key():
    key = (
        os.environ.get("GROQ_API_KEY")
        or session.get("groq_key")
        or _load_config().get("groq_api_key", "")
    )
    if key and not os.environ.get("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = key
        _reinit_agent()


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
    # Reload key from config if env not set (cold start recovery)
    if not os.environ.get("GROQ_API_KEY"):
        cfg = _load_config()
        if cfg.get("groq_api_key"):
            os.environ["GROQ_API_KEY"] = cfg["groq_api_key"]
            _reinit_agent()

    api_key_set = bool(os.environ.get("GROQ_API_KEY"))
    username    = session.get("username", "")
    return render_template("index.html", api_key_set=api_key_set, username=username)


# ── API ───────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    # Ensure agent has the key (recover from cold start)
    if not os.environ.get("GROQ_API_KEY"):
        cfg = _load_config()
        if cfg.get("groq_api_key"):
            os.environ["GROQ_API_KEY"] = cfg["groq_api_key"]
            _reinit_agent()

    data    = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "הודעה ריקה"}), 400

    # Attach uploaded files context to message
    uploaded = _list_uploaded_files()
    if uploaded and not data.get("_files_appended"):
        file_list = ", ".join(f["name"] for f in uploaded[:5])
        message = f"{message}\n\n[קבצים זמינים להשתמש: {file_list}]"

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
        return jsonify({"error": "קובץ לא נמצא"}), 404
    return send_file(str(path), as_attachment=True, download_name=path.name)


# ── העלאת קבצים ──────────────────────────────────
@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "לא נשלח קובץ"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "שם קובץ ריק"}), 400

    # Allow only safe extensions
    allowed = {".xlsx", ".xls", ".csv", ".txt", ".json", ".pdf", ".docx", ".pptx"}
    suffix  = Path(f.filename).suffix.lower()
    if suffix not in allowed:
        return jsonify({"error": f"סוג קובץ לא נתמך: {suffix}"}), 400

    save_path = UPLOAD_DIR / Path(f.filename).name
    f.save(str(save_path))

    # Try to read text content for context
    content_preview = ""
    try:
        if suffix == ".csv":
            content_preview = save_path.read_text(encoding="utf-8", errors="replace")[:2000]
        elif suffix in (".txt", ".json"):
            content_preview = save_path.read_text(encoding="utf-8", errors="replace")[:2000]
        elif suffix in (".xlsx", ".xls"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(str(save_path), read_only=True, data_only=True)
                ws = wb.active
                rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 50: break
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
                content_preview = "\n".join(rows)
                wb.close()
            except ImportError:
                content_preview = "(openpyxl לא מותקן — תוכן לא נקרא)"
    except Exception as e:
        content_preview = f"(לא ניתן לקרוא: {e})"

    size_kb = save_path.stat().st_size // 1024

    return jsonify({
        "ok":      True,
        "name":    f.filename,
        "size":    f"{size_kb} KB",
        "preview": content_preview[:500] if content_preview else "",
        "message": f"✅ הקובץ '{f.filename}' הועלה בהצלחה!"
    })


@app.route("/api/uploads")
@login_required
def get_uploads():
    return jsonify(_list_uploaded_files())


def _list_uploaded_files() -> list:
    files = []
    try:
        for p in sorted(UPLOAD_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
            if p.is_file():
                files.append({
                    "name":    p.name,
                    "size":    f"{p.stat().st_size // 1024} KB",
                    "suffix":  p.suffix[1:].upper()
                })
    except Exception:
        pass
    return files


# ── הגדרות API Key ────────────────────────────────
@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "GET":
        # Also check /tmp config as fallback
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            cfg = _load_config()
            key = cfg.get("groq_api_key", "")
        masked = ("gsk_..." + key[-6:]) if len(key) > 10 else ""
        return jsonify({"api_key_set": bool(key), "masked": masked, "provider": "Groq"})

    data = request.get_json(force=True)
    key  = (data.get("api_key") or "").strip()
    if not key:
        return jsonify({"error": "מפתח ריק"}), 400
    if not key.startswith("gsk_"):
        return jsonify({"error": "מפתח Groq לא תקין (חייב להתחיל ב-gsk_)"}), 400

    # Save to env, session cookie AND /tmp config
    os.environ["GROQ_API_KEY"] = key
    session["groq_key"] = key          # travels with every request across serverless containers
    cfg = _load_config()
    cfg["groq_api_key"] = key
    _save_config(cfg)
    _reinit_agent()

    return jsonify({"ok": True, "message": "✅ Groq API Key נשמר והסוכן אותחל מחדש!"})


# ── Health check ──────────────────────────────────
@app.route("/api/health")
def health():
    key = os.environ.get("GROQ_API_KEY", "") or _load_config().get("groq_api_key", "")
    return jsonify({
        "status":  "ok",
        "api_key": bool(key),
        "model":   "llama-3.3-70b-versatile"
    })


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + "=" * 50)
    print("  MosheAI  -  Ready!")
    print("=" * 50)
    key = os.environ.get("GROQ_API_KEY", "") or _load_config().get("groq_api_key", "")
    if not key:
        print("  WARNING: GROQ_API_KEY not set!")
    else:
        print("  Groq API Key: OK ✅")
    print("  URL: http://localhost:5000")
    print("  User: Moshei1 | Pass: Admin2026")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
