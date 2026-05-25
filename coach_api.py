import json
from flask import Flask, request, jsonify
from supabase import create_client

app = Flask(__name__)

# Initialize Supabase client
supabase_url = "your_supabase_url"
supabase_key = "your_supabase_key"
supabase = create_client(supabase_url, supabase_key)

@app.route('/api/coach', methods=['POST'])
def coach():
    data = request.get_json()
    user_message = data.get('message')

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    # Get coach's reply (this is a placeholder for actual logic)
    coach_reply = get_coach_reply(user_message)

    # Log the conversation
    insert_log(user_message, coach_reply)

    response = {
        "message": coach_reply
    }

    return jsonify(response), 200

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

if __name__ == '__main__':
    app.run(debug=True)
