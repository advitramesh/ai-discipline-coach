import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client
import google.generativeai as genai
from groq import Groq
import requests as http_requests

load_dotenv()

app = FastAPI()

# Supabase
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')
if not supabase_url or not supabase_key:
    raise ValueError("Supabase URL and key must be set as environment variables")
supabase = create_client(supabase_url, supabase_key)

# Gemini
gemini_api_key = os.getenv('GEMINI_API_KEY')
if gemini_api_key:
    genai.configure(api_key=gemini_api_key)

# Groq
groq_api_key = os.getenv('GROQ_API_KEY')
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

OLLAMA_URL = "http://localhost:11434"
SYSTEM_PROMPT = "You are a supportive and motivating AI coach. Help the user achieve their goals with clear, actionable advice."
MAX_HISTORY = 10

# In-memory conversation history keyed by session_id
conversation_history: dict[str, list] = {}


@app.get('/health')
async def health():
    providers = {}

    providers['gemini'] = bool(gemini_api_key)
    providers['groq'] = bool(groq_api_key)

    try:
        resp = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        providers['ollama'] = resp.status_code == 200
    except Exception:
        providers['ollama'] = False

    return {"providers": providers}


@app.post('/api/coach')
async def coach(request: Request):
    data = await request.json()
    user_message = data.get('message')
    session_id = data.get('session_id')

    if not user_message:
        raise HTTPException(status_code=400, detail="No message provided")
    if not session_id:
        raise HTTPException(status_code=400, detail="No session_id provided")

    coach_reply, provider = get_coach_reply(session_id, user_message)
    insert_log(session_id, user_message, coach_reply, provider)

    return {"message": coach_reply, "provider": provider}


def get_coach_reply(session_id: str, user_message: str) -> tuple[str, str]:
    history = conversation_history.setdefault(session_id, [])
    history.append({"role": "user", "content": user_message})

    # Keep only the last MAX_HISTORY messages
    if len(history) > MAX_HISTORY:
        conversation_history[session_id] = history[-MAX_HISTORY:]
        history = conversation_history[session_id]

    provider_chain = [
        ("gemini", _call_gemini),
        ("groq", _call_groq),
        ("ollama", _call_ollama),
    ]

    last_error = None
    for provider_name, provider_fn in provider_chain:
        try:
            reply = provider_fn(history)
            history.append({"role": "assistant", "content": reply})
            return reply, provider_name
        except Exception as e:
            print(f"{provider_name} failed: {e}")
            last_error = e

    raise HTTPException(status_code=503, detail=f"All providers failed. Last error: {last_error}")


def _call_gemini(history: list) -> str:
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    # Gemini uses role "model" for assistant turns; history passed to start_chat excludes current message
    gemini_history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in history[:-1]
    ]
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT,
    )
    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(history[-1]["content"])
    return response.text


def _call_groq(history: list) -> str:
    if not groq_client:
        raise RuntimeError("GROQ_API_KEY not set")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in history
    ]
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
    )
    return response.choices[0].message.content


def _call_ollama(history: list) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in history
    ]
    response = http_requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": "llama3:8b", "messages": messages, "stream": False},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def insert_log(session_id: str, user_message: str, coach_reply: str, provider: str):
    try:
        supabase.table('chat_logs').insert({
            'session_id': session_id,
            'user_message': user_message,
            'coach_reply': coach_reply,
            'provider': provider,
        }).execute()
        print(f"Log inserted successfully (provider: {provider})")
    except Exception as e:
        print(f"Error inserting log: {e}")
