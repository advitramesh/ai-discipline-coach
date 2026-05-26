from __future__ import annotations
import os
import re
import json
from datetime import date, datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
import google.generativeai as genai
from groq import Groq
import requests as http_requests

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Supabase ---
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')
if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
supabase = create_client(supabase_url, supabase_key)

# --- Gemini ---
gemini_api_key = os.getenv('GEMINI_API_KEY')
if gemini_api_key:
    genai.configure(api_key=gemini_api_key)

# --- Groq ---
groq_api_key = os.getenv('GROQ_API_KEY')
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

OLLAMA_URL = "http://localhost:11434"
MAX_HISTORY = 10

DAY_ABBREVS = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}

COMMITMENT_TAG_RE = re.compile(r'<commitment>(.*?)</commitment>', re.DOTALL)

# In-memory stores
conversation_history: dict[str, list] = {}
onboarding_state: dict[str, int] = {}
onboarding_data: dict[str, dict] = {}

COACHING_SYSTEM_PROMPT = """You are an elite personal discipline coach specializing in habit formation,
motivation, and helping people recover from lapses and relapses. You combine
the science of behavior change with the empathy of a trusted mentor.

COACHING PHILOSOPHY:
- A lapse is a single slip. A relapse is a pattern. Never treat them the same.
- Shame kills progress. Curiosity heals it. Always ask why, never judge.
- Your job is not to motivate with hype. It is to help the user understand
  themselves deeply enough that motivation becomes natural.
- Progress is non-linear. Normalize struggle without excusing avoidance.

USER CONTEXT:
- Goal: {user_goal}
- Coaching style preference: {coaching_style}
- Current streak: {streak_days} days
- Last check-in: {last_checkin}
- Recent lapse history: {lapse_summary}

COMMITMENTS & TASKS:
{commitments_context}

COMMITMENT TRACKING RULES:
- When the user asks to add a commitment or task, respond helpfully AND include a machine-readable tag at the very end of your reply in this exact format:
  <commitment>{{"title": "...", "type": "do|abstain|one-time", "frequency": "daily|weekly|specific_days|one-time", "days_of_week": ["mon","tue"], "due_date": "YYYY-MM-DD or null"}}</commitment>
- Only include the tag when creating a NEW commitment. Never include it for logging, motivation, or chat.
- "do" = positive habit to build. "abstain" = thing to avoid. "one-time" = single task with optional due date.
- For specific_days, use lowercase 3-letter abbreviations: mon, tue, wed, thu, fri, sat, sun.

For MOTIVATION: Connect to deeper why. Use implementation intentions.
For LAPSE: Acknowledge, get curious about trigger, find smallest re-entry point.
For RELAPSE: Slow down, explore if goal needs adjusting, rebuild smaller.
For CHECK-IN: Acknowledge they showed up, ask one powerful question, reflect patterns.
For TASK_UPDATE: Acknowledge the update, note progress, ask about tomorrow.
For TASK_REQUEST: Confirm the new commitment warmly, then include the <commitment> tag.

TONE: tough love = direct and challenging. balanced = warm but honest.
gentle = empathetic and patient.

RESPONSE RULES:
- Under 150 words unless user is in crisis
- End with one question or one concrete action
- Never give a list. One thing done well.
- No corporate wellness speak."""

INTENT_CLASSIFIER_PROMPT = """Classify the following user message into exactly one of these intents:
checkin, motivation, lapse, relapse, task_update, task_request, general

- task_update: user is reporting progress, completion, or failure on a commitment or task
- task_request: user is asking to add, set, or create a new commitment, habit, or task

Return only the single word. Nothing else.

Message: {message}"""


# =============================================================================
# LLM PROVIDERS
# =============================================================================

def call_llm(messages: list, system_prompt: str) -> tuple[str, str]:
    """Try Gemini → Groq → Ollama in order. Returns (reply, provider_name)."""
    providers = [
        ("gemini", _call_gemini),
        ("groq", _call_groq),
        ("ollama", _call_ollama),
    ]
    last_error = None
    for name, fn in providers:
        try:
            return fn(messages, system_prompt), name
        except Exception as e:
            print(f"{name} failed: {e}")
            last_error = e
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


def _call_gemini(messages: list, system_prompt: str) -> str:
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    gemini_history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in messages[:-1]
    ]
    model = genai.GenerativeModel(model_name="gemini-2.5-flash", system_instruction=system_prompt)
    chat = model.start_chat(history=gemini_history)
    return chat.send_message(messages[-1]["content"]).text


def _call_groq(messages: list, system_prompt: str) -> str:
    if not groq_client:
        raise RuntimeError("GROQ_API_KEY not set")
    full = [{"role": "system", "content": system_prompt}] + messages
    response = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=full)
    return response.choices[0].message.content


def _call_ollama(messages: list, system_prompt: str) -> str:
    full = [{"role": "system", "content": system_prompt}] + messages
    response = http_requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": "llama3:8b", "messages": full, "stream": False},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# =============================================================================
# INTENT CLASSIFICATION
# =============================================================================

def classify_intent(message: str) -> str:
    valid = {"checkin", "motivation", "lapse", "relapse", "task_update", "task_request", "general"}
    try:
        reply, _ = call_llm(
            [{"role": "user", "content": INTENT_CLASSIFIER_PROMPT.format(message=message)}],
            "You are a message classifier. Return only the classification word.",
        )
        intent = reply.strip().lower()
        return intent if intent in valid else "general"
    except Exception:
        return "general"


# =============================================================================
# SUPABASE HELPERS
# =============================================================================

def get_user_profile(session_id: str) -> dict | None:
    try:
        result = supabase.table("user_profiles").select("*").eq("session_id", session_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Error fetching profile: {e}")
        return None


def save_user_profile(session_id: str, user_goal: str, coaching_style: str):
    try:
        supabase.table("user_profiles").insert({
            "session_id": session_id,
            "user_goal": user_goal,
            "coaching_style": coaching_style,
            "streak_days": 0,
            "last_checkin": None,
        }).execute()
    except Exception as e:
        print(f"Error saving profile: {e}")


def update_profile_streak(session_id: str, streak_days: int):
    try:
        supabase.table("user_profiles").update({
            "streak_days": streak_days,
            "last_checkin": datetime.now(timezone.utc).isoformat(),
        }).eq("session_id", session_id).execute()
    except Exception as e:
        print(f"Error updating streak: {e}")


def log_chat(session_id: str, user_message: str, coach_reply: str, provider: str, intent: str):
    try:
        supabase.table("chat_logs").insert({
            "session_id": session_id,
            "user_message": user_message,
            "coach_reply": coach_reply,
            "provider_used": provider,
            "intent": intent,
        }).execute()
    except Exception as e:
        print(f"Error logging chat: {e}")


def load_history_from_db(session_id: str) -> list:
    try:
        result = (
            supabase.table("chat_logs")
            .select("user_message, coach_reply, created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        if not result.data:
            return []
        messages = []
        for row in reversed(result.data):
            messages.append({"role": "user",      "content": row["user_message"]})
            messages.append({"role": "assistant",  "content": row["coach_reply"]})
        print(f"Loaded {len(messages)} messages from DB for session {session_id}")
        return messages
    except Exception as e:
        print(f"Error loading history from DB: {e}")
        return []


def calculate_streak(session_id: str) -> int:
    try:
        result = (
            supabase.table("chat_logs")
            .select("created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .execute()
        )
        if not result.data:
            return 0
        days_with_messages = {row["created_at"][:10] for row in result.data}
        streak = 0
        check = date.today()
        while str(check) in days_with_messages:
            streak += 1
            check -= timedelta(days=1)
        return streak
    except Exception as e:
        print(f"Error calculating streak: {e}")
        return 0


def get_lapse_summary(session_id: str) -> str:
    try:
        result = (
            supabase.table("chat_logs")
            .select("intent, created_at")
            .eq("session_id", session_id)
            .in_("intent", ["lapse", "relapse"])
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        if not result.data:
            return "None"
        return "; ".join(f"{r['intent']} on {r['created_at'][:10]}" for r in result.data)
    except Exception as e:
        print(f"Error fetching lapse summary: {e}")
        return "Unknown"


# =============================================================================
# COMMITMENT HELPERS
# =============================================================================

def is_due_today(commitment: dict) -> bool:
    freq = commitment.get("frequency")
    today = date.today()
    if freq == "daily":
        return True
    if freq == "one-time":
        due = commitment.get("due_date")
        return due is not None and str(due) == str(today)
    if freq == "weekly":
        return today.weekday() == 0  # Monday by default
    if freq == "specific_days":
        days = commitment.get("days_of_week") or []
        return DAY_ABBREVS[today.weekday()] in days
    return False


def get_todays_commitments(session_id: str) -> list:
    try:
        result = (
            supabase.table("commitments")
            .select("*")
            .eq("session_id", session_id)
            .eq("active", True)
            .execute()
        )
        if not result.data:
            return []
        return [c for c in result.data if is_due_today(c)]
    except Exception as e:
        print(f"Error fetching commitments: {e}")
        return []


def calculate_commitment_streak(commitment_id: str) -> int:
    try:
        result = (
            supabase.table("commitment_logs")
            .select("date, status")
            .eq("commitment_id", commitment_id)
            .order("date", desc=True)
            .execute()
        )
        if not result.data:
            return 0
        logged_days = {row["date"]: row["status"] for row in result.data}
        streak = 0
        check = date.today()
        while True:
            key = str(check)
            if key not in logged_days:
                # Allow today to be missing (not yet logged)
                if check == date.today():
                    check -= timedelta(days=1)
                    continue
                break
            if logged_days[key] == "completed":
                streak += 1
                check -= timedelta(days=1)
            else:
                break
        return streak
    except Exception as e:
        print(f"Error calculating commitment streak: {e}")
        return 0


def get_abstinence_streak(commitment_id: str) -> int:
    """Days since last lapse for abstain-type commitments."""
    try:
        result = (
            supabase.table("commitment_logs")
            .select("date, status")
            .eq("commitment_id", commitment_id)
            .in_("status", ["lapsed", "skipped"])
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            # No lapses ever — streak from commitment creation
            c_result = (
                supabase.table("commitments")
                .select("created_at")
                .eq("id", commitment_id)
                .execute()
            )
            if c_result.data:
                created = date.fromisoformat(c_result.data[0]["created_at"][:10])
                return (date.today() - created).days
            return 0
        last_lapse = date.fromisoformat(result.data[0]["date"])
        return (date.today() - last_lapse).days
    except Exception as e:
        print(f"Error calculating abstinence streak: {e}")
        return 0


def build_commitments_context(session_id: str) -> str:
    try:
        result = (
            supabase.table("commitments")
            .select("*")
            .eq("session_id", session_id)
            .eq("active", True)
            .execute()
        )
        if not result.data:
            return "No active commitments yet."

        today_commitments = [c for c in result.data if is_due_today(c)]
        other_commitments = [c for c in result.data if not is_due_today(c)]

        lines = []

        if today_commitments:
            lines.append("DUE TODAY:")
            for c in today_commitments:
                if c["type"] == "abstain":
                    streak = get_abstinence_streak(c["id"])
                    lines.append(f"  - [{c['type'].upper()}] {c['title']} — {streak}d clean")
                else:
                    streak = calculate_commitment_streak(c["id"])
                    lines.append(f"  - [{c['type'].upper()}] {c['title']} — {streak}d streak")

        if other_commitments:
            lines.append("OTHER ACTIVE COMMITMENTS:")
            for c in other_commitments:
                freq = c.get("frequency", "")
                lines.append(f"  - [{c['type'].upper()}] {c['title']} ({freq})")

        return "\n".join(lines) if lines else "No active commitments yet."
    except Exception as e:
        print(f"Error building commitments context: {e}")
        return "Could not load commitments."


def extract_and_save_commitment(session_id: str, reply: str) -> str:
    """Parse <commitment> tag from reply, save to DB, return cleaned reply."""
    match = COMMITMENT_TAG_RE.search(reply)
    if not match:
        return reply

    raw = match.group(1).strip()
    clean_reply = COMMITMENT_TAG_RE.sub("", reply).strip()

    try:
        data = json.loads(raw)
        supabase.table("commitments").insert({
            "session_id": session_id,
            "title": data.get("title", "Untitled"),
            "type": data.get("type", "do"),
            "frequency": data.get("frequency"),
            "days_of_week": data.get("days_of_week"),
            "due_date": data.get("due_date") if data.get("due_date") not in (None, "null", "") else None,
            "active": True,
        }).execute()
        print(f"Auto-saved commitment for {session_id}: {data.get('title')}")
    except Exception as e:
        print(f"Error saving auto-commitment: {e}")

    return clean_reply


# =============================================================================
# ONBOARDING
# =============================================================================

_STYLE_KEYWORDS = {
    "tough": "tough love",
    "tough love": "tough love",
    "balanced": "balanced",
    "gentle": "gentle",
}


def parse_coaching_style(message: str) -> str:
    lower = message.lower()
    for keyword, style in _STYLE_KEYWORDS.items():
        if keyword in lower:
            return style
    return "balanced"


def handle_onboarding(session_id: str, user_message: str) -> dict | None:
    step = onboarding_state.get(session_id, 0)

    if step == 0:
        onboarding_state[session_id] = 1
        return {
            "reply": "What's the one goal you're committed to working on? Be specific.",
            "provider_used": "system",
            "intent": "onboarding",
            "streak_days": 0,
        }

    if step == 1:
        onboarding_data.setdefault(session_id, {})["goal"] = user_message
        onboarding_state[session_id] = 2
        return {
            "reply": (
                "Got it. How do you want me to coach you?\n\n"
                "- **Tough love** — direct, no fluff, I'll challenge you\n"
                "- **Balanced** — warm but honest\n"
                "- **Gentle** — patient and compassionate\n\n"
                "Which style fits you best?"
            ),
            "provider_used": "system",
            "intent": "onboarding",
            "streak_days": 0,
        }

    if step == 2:
        style = parse_coaching_style(user_message)
        goal = onboarding_data.get(session_id, {}).get("goal", "Not specified")
        save_user_profile(session_id, goal, style)
        onboarding_state[session_id] = 3
        onboarding_data.pop(session_id, None)
        return None  # fall through to coaching flow


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.get("/health")
async def health():
    providers = {
        "gemini": bool(gemini_api_key),
        "groq": bool(groq_api_key),
    }
    try:
        resp = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        providers["ollama"] = resp.status_code == 200
    except Exception:
        providers["ollama"] = False
    return {"providers": providers}


@app.post("/api/coach")
async def coach(request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    user_message = data.get("message")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required")

    profile = get_user_profile(session_id)

    if not profile:
        onboarding_response = handle_onboarding(session_id, user_message)
        if onboarding_response is not None:
            return onboarding_response
        profile = get_user_profile(session_id)
        if not profile:
            raise HTTPException(status_code=500, detail="Failed to create user profile")

    intent = classify_intent(user_message)

    if session_id not in conversation_history:
        conversation_history[session_id] = load_history_from_db(session_id)

    history = conversation_history[session_id]
    history.append({"role": "user", "content": user_message})
    if len(history) > MAX_HISTORY:
        conversation_history[session_id] = history[-MAX_HISTORY:]
        history = conversation_history[session_id]

    streak = calculate_streak(session_id)
    lapse_summary = get_lapse_summary(session_id)
    commitments_context = build_commitments_context(session_id)

    last_checkin = profile.get("last_checkin") or "Never"
    if last_checkin != "Never":
        last_checkin = last_checkin[:10]

    system_prompt = COACHING_SYSTEM_PROMPT.format(
        user_goal=profile.get("user_goal", "Not set"),
        coaching_style=profile.get("coaching_style", "balanced"),
        streak_days=streak,
        last_checkin=last_checkin,
        lapse_summary=lapse_summary,
        commitments_context=commitments_context,
    )

    try:
        reply, provider = call_llm(history, system_prompt)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Auto-save any commitment the coach created
    if intent == "task_request":
        reply = extract_and_save_commitment(session_id, reply)

    history.append({"role": "assistant", "content": reply})

    update_profile_streak(session_id, streak)
    log_chat(session_id, user_message, reply, provider, intent)

    return {
        "reply": reply,
        "provider_used": provider,
        "intent": intent,
        "streak_days": streak,
    }


@app.post("/api/commitments")
async def create_commitment(request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    try:
        result = supabase.table("commitments").insert({
            "session_id": session_id,
            "title": data.get("title", "Untitled"),
            "type": data.get("type", "do"),
            "frequency": data.get("frequency"),
            "days_of_week": data.get("days_of_week"),
            "due_date": data.get("due_date"),
            "active": True,
        }).execute()
        return {"commitment": result.data[0] if result.data else {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/commitments/{session_id}")
async def list_commitments(session_id: str):
    try:
        result = (
            supabase.table("commitments")
            .select("*")
            .eq("session_id", session_id)
            .eq("active", True)
            .order("created_at", desc=False)
            .execute()
        )
        commitments = result.data or []

        enriched = []
        for c in commitments:
            due_today = is_due_today(c)
            if c["type"] == "abstain":
                streak = get_abstinence_streak(c["id"])
            else:
                streak = calculate_commitment_streak(c["id"])
            enriched.append({**c, "due_today": due_today, "streak": streak})

        return {"commitments": enriched}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/commitments/{commitment_id}/log")
async def log_commitment(commitment_id: str, request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    status = data.get("status")  # completed | skipped | lapsed
    note = data.get("note", "")
    log_date = data.get("date", str(date.today()))

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if status not in ("completed", "skipped", "lapsed"):
        raise HTTPException(status_code=400, detail="status must be completed, skipped, or lapsed")

    try:
        # Upsert: one log per commitment per day
        existing = (
            supabase.table("commitment_logs")
            .select("id")
            .eq("commitment_id", commitment_id)
            .eq("date", log_date)
            .execute()
        )
        if existing.data:
            result = (
                supabase.table("commitment_logs")
                .update({"status": status, "note": note})
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            result = supabase.table("commitment_logs").insert({
                "commitment_id": commitment_id,
                "session_id": session_id,
                "date": log_date,
                "status": status,
                "note": note,
            }).execute()
        return {"log": result.data[0] if result.data else {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/commitments/{commitment_id}")
async def delete_commitment(commitment_id: str, request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    try:
        supabase.table("commitments").update({"active": False}).eq("id", commitment_id).eq("session_id", session_id).execute()
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dashboard/{session_id}")
async def dashboard(session_id: str):
    try:
        profile = get_user_profile(session_id)
        streak = calculate_streak(session_id)
        lapse_summary = get_lapse_summary(session_id)

        commitments_result = (
            supabase.table("commitments")
            .select("*")
            .eq("session_id", session_id)
            .eq("active", True)
            .execute()
        )
        commitments = commitments_result.data or []

        today_due = []
        for c in commitments:
            if is_due_today(c):
                if c["type"] == "abstain":
                    s = get_abstinence_streak(c["id"])
                else:
                    s = calculate_commitment_streak(c["id"])
                today_due.append({**c, "streak": s})

        return {
            "profile": profile,
            "streak_days": streak,
            "lapse_summary": lapse_summary,
            "todays_commitments": today_due,
            "total_active": len(commitments),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chase/{session_id}")
async def chase(session_id: str):
    """Return commitments due today that have no log entry yet."""
    try:
        today = str(date.today())
        commitments_result = (
            supabase.table("commitments")
            .select("*")
            .eq("session_id", session_id)
            .eq("active", True)
            .execute()
        )
        all_active = commitments_result.data or []
        due_today = [c for c in all_active if is_due_today(c)]

        if not due_today:
            return {"unlogged": []}

        ids = [c["id"] for c in due_today]
        logs_result = (
            supabase.table("commitment_logs")
            .select("commitment_id")
            .in_("commitment_id", ids)
            .eq("date", today)
            .execute()
        )
        logged_ids = {row["commitment_id"] for row in (logs_result.data or [])}
        unlogged = [c for c in due_today if c["id"] not in logged_ids]

        return {"unlogged": unlogged, "date": today}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
