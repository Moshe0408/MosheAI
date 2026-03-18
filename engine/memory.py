"""מערכת זיכרון ולמידה עבור MosheAI - מותאם ל-Vercel (/tmp)"""

import json
import datetime
from pathlib import Path

# Vercel: /tmp is the only writable directory
MEMORY_FILE = Path("/tmp/moshe_memory.json")


def load() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "sessions": [],
        "errors": [],
        "improvements": [],
        "stats": {"total": 0, "success": 0, "failed": 0}
    }


def save(mem: dict):
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # Silent fail on read-only environments


def record_success(mem: dict, task: str, outputs: list):
    mem["stats"]["total"] += 1
    mem["stats"]["success"] += 1
    mem["sessions"].append({
        "ts": _now(), "task": task[:200], "status": "success", "outputs": outputs
    })
    mem["sessions"] = mem["sessions"][-60:]
    save(mem)


def record_error(mem: dict, task: str, error: str):
    mem["stats"]["total"] += 1
    mem["stats"]["failed"] += 1
    mem["errors"].append({"ts": _now(), "task": task[:200], "error": error[:400]})
    mem["errors"] = mem["errors"][-30:]
    save(mem)


def get_lessons(mem: dict) -> str:
    if not mem["errors"]:
        return ""
    lines = ["⚠️ לקחים מטעויות קודמות - הימנע מהם:"]
    for e in mem["errors"][-5:]:
        lines.append(f"• {e['task'][:80]} ← {e['error'][:100]}")
    return "\n".join(lines)


def _now() -> str:
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
