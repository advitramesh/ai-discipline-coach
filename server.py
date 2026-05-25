import json
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client
import os

app = FastAPI()

# Initialize Supabase client using environment variables
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')

if not supabase_url or not supabase_key:
    raise ValueError("Supabase URL and key must be set as environment variables")

supabase = create_client(supabase_url, supabase_key)

@app.post('/api/coach')
async def coach(request: Request):
    data = await request.json()
    user_message = data.get('message')

    if not user_message:
        raise HTTPException(status_code=400, detail="No message provided")

    # Get coach's reply (this is a placeholder for actual logic)
    coach_reply = get_coach_reply(user_message)

    # Log the conversation
    insert_log(user_message, coach_reply)

    response = {
        "message": coach_reply
    }

    return response

def get_coach_reply(message):
    # Placeholder function to simulate getting a reply from the coach
    return f"Coach: {message}"

def insert_log(user_message, coach_reply):
    # Insert log into Supabase
    response = supabase.table('chat_logs').insert({
        'user_message': user_message,
        'coach_reply': coach_reply
    }).execute()

    if response.error:
        print(f"Error inserting log: {response.error}")
    else:
        print("Log inserted successfully")
