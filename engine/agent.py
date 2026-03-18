"""ליבת הסוכן MosheAI - מתחבר ל-Groq API"""

import json
import os
from groq import Groq

from engine import memory as mem_module
from engine.tools import TOOLS_SCHEMA_GROQ, run_tool

MODEL = "llama-3.3-70b-versatile"

SYSTEM = """אתה MosheAI - עוזר AI מקצועי חכם של Verifone ישראל. אתה עוזר לצוות ה-TIER2.

🎯 אתה יכול לעשות הכל:
• לענות על כל שאלה - טכנית, עסקית, כללית
• לנתח נתוני שיחות ו-Tickets של מוקד שירות
• ליצור מצגות PowerPoint מקצועיות
• לכתוב דוחות Word עם טבלאות
• ליצור גרפים וסטטיסטיקות
• לנתח קבצי Excel/CSV

📊 מבנה נתוני TIER2 שאתה מכיר:
קבצי Calls (CSV): CAMPAIGN, CALL TYPE, AGENT NAME, DISPOSITION, TALK TIME, HANDLE TIME, QUEUE WAIT TIME, SKILL, DATE, HOUR, TRANSFERS, Survey
קבצי Tickets (XLSX): id, ticketType, owner, state, open/close times, response times, message counts, duration, ticketSummary

כשמנתחים קבצי Calls:
- ספור שיחות לפי סוג (Inbound/Manual/Outbound)
- חשב ממוצע TALK TIME לפי נציג
- נתח DISPOSITION breakdown
- זהה שיחות ללא מענה (SIP 480)
- צור גרפים: עמודות לנציגים, עוגה ל-dispositions

כשמנתחים קבצי Tickets:
- חשב זמני תגובה (agentAssignToResponseTimeNet)
- נתח לפי סוג (ticketType)
- בדוק open/close rates

עקרונות:
• ענה תמיד בעברית
• היה ידידותי ומקצועי
• אם מבקשים קובץ - צור אותו עם הכלים
• אם שואלים שאלה - ענה ישירות בלי ליצור קבצים מיותרים
• למד מטעויות קודמות
"""


class MosheAIAgent:
    def __init__(self):
        self.memory = mem_module.load()
        api_key = os.environ.get("GROQ_API_KEY", "")
        self.client = Groq(api_key=api_key) if api_key else None

    def stream_response(self, user_message: str):
        """
        Generator — מניב dict-ים בפורמט SSE:
          {"type": "text",       "content": "..."}
          {"type": "tool_start", "tool": "...", "label": "..."}
          {"type": "tool_done",  "tool": "...", "result": {...}}
          {"type": "done",       "outputs": [...]}
          {"type": "error",      "content": "..."}
        """
        if not self.client:
            yield {"type": "error", "content": "❌ GROQ_API_KEY לא מוגדר. עבור להגדרות והגדר את ה-API Key."}
            return

        all_outputs = []
        system = SYSTEM

        lessons = mem_module.get_lessons(self.memory)
        if lessons:
            system += f"\n\n{lessons}"

        full_messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message}
        ]

        try:
            while True:
                stream = self.client.chat.completions.create(
                    model=MODEL,
                    messages=full_messages,
                    tools=TOOLS_SCHEMA_GROQ,
                    tool_choice="auto",
                    stream=True,
                    max_tokens=8192,
                )

                current_text   = ""
                tool_calls_acc = {}   # index → {id, name, arguments}

                for chunk in stream:
                    choice = chunk.choices[0]
                    delta  = choice.delta

                    if delta.content:
                        current_text += delta.content
                        yield {"type": "text", "content": delta.content}

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id":        tc.id or f"call_{idx}",
                                    "name":      "",
                                    "arguments": ""
                                }
                            if tc.function.name:
                                tool_calls_acc[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments

                # אין כלים → סיימנו
                if not tool_calls_acc:
                    break

                assistant_tool_calls = [
                    {
                        "id":   tool_calls_acc[i]["id"],
                        "type": "function",
                        "function": {
                            "name":      tool_calls_acc[i]["name"],
                            "arguments": tool_calls_acc[i]["arguments"]
                        }
                    }
                    for i in sorted(tool_calls_acc)
                ]

                full_messages.append({
                    "role":       "assistant",
                    "content":    current_text or None,
                    "tool_calls": assistant_tool_calls
                })

                for tc in assistant_tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = {}

                    yield {"type": "tool_start", "tool": name, "label": _tool_label(name)}

                    result = run_tool(name, args, self.memory)

                    if result.get("path"):
                        all_outputs.append(result["path"])

                    # Don't send large base64 in SSE for non-chart files — send separately
                    sse_result = {k: v for k, v in result.items() if k not in ("data_b64",)}
                    if result.get("type") == "png":
                        sse_result["thumbnail"] = result.get("thumbnail", "")
                        sse_result["data_b64"]  = result.get("data_b64", "")
                    else:
                        sse_result["data_b64"] = result.get("data_b64", "")

                    yield {"type": "tool_done", "tool": name, "result": sse_result}

                    full_messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      json.dumps({k: v for k, v in result.items() if k != "data_b64"}, ensure_ascii=False)
                    })

            mem_module.record_success(self.memory, user_message, all_outputs)
            yield {"type": "done", "outputs": all_outputs}

        except Exception as e:
            err = str(e)
            if any(k in err.lower() for k in ("auth", "api_key", "invalid_api_key", "401")):
                err = "❌ API Key שגוי. עבור להגדרות ועדכן את מפתח Groq."
            mem_module.record_error(self.memory, user_message, err)
            yield {"type": "error", "content": f"שגיאה: {err}"}

    def get_memory_summary(self) -> dict:
        m = mem_module.load()
        return {
            "stats":           m["stats"],
            "recent_sessions": m["sessions"][-8:],
            "recent_errors":   m["errors"][-5:]
        }


def _tool_label(name: str) -> str:
    return {
        "create_presentation": "יוצר מצגת PowerPoint",
        "create_word_report":  "כותב דוח Word",
        "create_chart":        "מייצר גרף סטטיסטי",
        "recall_memory":       "קורא זיכרון"
    }.get(name, name)
