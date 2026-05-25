import os
import urllib.request
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from your hidden .env file
load_dotenv()

app = FastAPI()

# Initialize Supabase Client
# Your project target URL: https://wnowxvxpyrchxiauekff.supabase.co
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase credentials in the .env file.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Free Gemini Cloud Engine Configuration
# Embed your actual key from Google AI Studio here
API_KEY = "AIzaSyBJVG_85cmJgaaan7-01BT9W5lwyM233RY"  
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

class UserMessageRequest(BaseModel):
    user_message: str
    tone: str  # "supportive", "direct", "brutal"

@app.post("/api/coach")
async def chat_with_coach(payload: UserMessageRequest):
    try:
        # 1. Fetch live user profiles safely
        response = supabase.table("user_profiles").select("*").execute()
        
        # Extract the raw list rows out of the Supabase response object
        records = getattr(response, "data", [])
        
        # Fallback check if your database table has zero records
        if not records:
            raise HTTPException(status_code=404, detail="No rows found inside your user_profiles table.")
            
        # Target your live row (ID 4) sitting inside the database
        profile = records[0]
        
        # 2. Extract context values using safe dictionary fallbacks
        streak = profile.get("current_streak", 0)
        budget = profile.get("daily_budget", 0.0)
        spent = profile.get("total_spent", 0.0)
        
        # 3. Construct dynamic system context text for the AI prompt
        system_instruction = (
            f"You are a personalized AI Life Coach. The user wants you to use a '{payload.tone}' tone. "
            f"Context: Their current abstinence streak is {streak} days. "
            f"Their daily budget is ${budget} and they have spent ${spent} today. "
            f"Respond concisely to their message keeping this live context in mind."
        )
        
        gemini_payload = {
            "contents": [{
                "parts": [
                    {"text": system_instruction},
                    {"text": payload.user_message}
                ]
            }]
        }
        
        # 4. Ship it off to Gemini Cloud
        req = urllib.request.Request(
            GEMINI_URL, 
            data=json.dumps(gemini_payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req) as res:
            result = json.loads(res.read().decode('utf-8'))
            reply = result['candidates'][0]['content']['parts'][0]['text']
            return {"coach_reply": reply.strip()}
            
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")