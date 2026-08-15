from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import AsyncOpenAI
from pathlib import Path
from dotenv import load_dotenv
import httpx
import uuid
import os
import re

app = FastAPI()

# ---------------------------------------------------------------------
# SECURE CONFIG CONFIGURATION LOADING (Local vs Production)
# ---------------------------------------------------------------------
try:
    base_dir = Path(__file__).resolve().parent
    dotenv_file = base_dir / ".env"
    if dotenv_file.exists():
        load_dotenv(dotenv_path=dotenv_file)
        print("💡 Local .env file loaded successfully.")
except Exception as e:
    print(f"⚠️ Skipping local file configuration setup: {e}")

# Read variables directly from active operating system environment memory
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")
MODEL = os.getenv("MODEL", "openrouter/free")  # Matches your OpenRouter model choice
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

# Initialize your core AsyncOpenAI engine client
client = AsyncOpenAI(
    base_url=API_BASE_URL,
    api_key=OPENAI_API_KEY
)

# Serve static files for Web UI
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------
# GLOBAL VARIABLE STORAGE MATCHING ORIGINAL ARCHITECTURE
# ---------------------------------------------------------------------
LANGUAGE_MAP = {
    "my": "Burmese",
    "th": "Thai",
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "fr": "French"
}

sessions = {}

class LanguageUpdate(BaseModel):
    host_lang: str
    guest_lang: str

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, session_id: str, role: str, websocket: WebSocket):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = {"host": None, "guest": None}
        self.active_connections[session_id][role] = websocket

    def disconnect(self, session_id: str, role: str):
        if session_id in self.active_connections:
            self.active_connections[session_id][role] = None
            if not self.active_connections[session_id]["host"] and not self.active_connections[session_id]["guest"]:
                del self.active_connections[session_id]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, session_id: str, sender_role: str, message: str):
        if session_id in self.active_connections:
            for role, ws in self.active_connections[session_id].items():
                if ws:
                    await ws.send_json({"sender": sender_role, "message": message})

manager = ConnectionManager()

# ---------------------------------------------------------------------
# ORIGINAL WEB APPLICATIONS ROUTES & ENDPOINTS
# ---------------------------------------------------------------------
@app.get("/")
async def get_index():
    return FileResponse("static/index.html")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/session/{session_id}/host")
async def get_host(session_id: str):
    return FileResponse("static/host.html")

@app.get("/session/{session_id}/guest")
async def get_guest(session_id: str):
    return FileResponse("static/guest.html")

@app.post("/set-language/{session_id}")
async def set_language(session_id: str, langs: LanguageUpdate):
    if session_id not in sessions:
        sessions[session_id] = {}
    sessions[session_id]["host_lang"] = langs.host_lang
    sessions[session_id]["guest_lang"] = langs.guest_lang
    return {"status": "success"}

# ---------------------------------------------------------------------
# ORIGINAL TRANSLATION ENGINE (Deterministic 1-Way for Web UI)
# ---------------------------------------------------------------------
async def translate_text(text: str, source_language: str, target_language: str):
    response = await client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {
                "role": "system",
                "content": f"You are a high-precision real-time translation engine. Translate strictly from {source_language} to {target_language}. RULES: Preserve tone, informality, and emojis. Do NOT explain, answer, comment, or apologize. Output ONLY the translated text."
            },
            {"role": "user", "content": text}
        ],
    )
    return response.choices[0].message.content.strip()

# ---------------------------------------------------------------------
# NEW TRANSLATION ENGINE (Bi-Directional for LINE Group Auto-Detect)
# ---------------------------------------------------------------------
async def translate_text_bidirectional(text: str, lang_a: str, lang_b: str):
    response = await client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {
                "role": "system",
                "content": f"""You are an invisible high-precision real-time translation server engine for a chat room.
The active matching language configurations are {lang_a} and {lang_b}.

RULES:
1. AUTO-DETECT: Analyze whether the incoming text string input is written in {lang_a} or {lang_b}.
2. TRANSLATE: 
   - If the input text is written in {lang_a}, translate it strictly to {lang_b}.
   - If the input text is written in {lang_b}, translate it strictly to {lang_a}.
3. Properties: Preserve exact contextual definitions, informal tone, and any emojis.
4. Output ONLY the raw final translated text string. Do NOT add meta commentary, status info, notes, or apologies.
5. If the language profile is ambiguous or mixed, default to translating to {lang_b} as a safe fallback option."""
            },
            {"role": "user", "content": text}
        ],
    )
    return response.choices[0].message.content.strip()

# ---------------------------------------------------------------------
# ORIGINAL WEB UI WEBSOCKET ROUTING HANDLER LOOP
# ---------------------------------------------------------------------
@app.websocket("/ws/{session_id}/{role}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, role: str):
    await manager.connect(session_id, role, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("/"):
                if data == "/end":
                    await manager.broadcast(session_id, "system", "Session ended by user.")
                    break
                elif data.startswith("/lang "):
                    parts = data.split(" ", 2)
                    if len(parts) == 3:
                        await manager.broadcast(session_id, "system", f"Language updated to {parts[1]} and {parts[2]}")
                    continue
            
            session_config = sessions.get(session_id, {"host_lang": "English", "guest_lang": "Thai"})
            source_lang = session_config["host_lang"] if role == "host" else session_config["guest_lang"]
            target_lang = session_config["guest_lang"] if role == "host" else session_config["host_lang"]
            
            translated = await translate_text(data, source_lang, target_lang)
            await manager.broadcast(session_id, role, f"{data} | {translated}")
    except WebSocketDisconnect:
        manager.disconnect(session_id, role)

# ---------------------------------------------------------------------
# NEW LIVE LINE OFFICIAL ACCOUNT WEBHOOK ENTRY HANDLER
# ---------------------------------------------------------------------
@app.post("/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    events = payload.get("events", [])

    for event in events:
        if event.get("type") == "message" and event["message"]["type"] == "text":
            user_text = event["message"]["text"].strip()
            reply_token = event["replyToken"]
            
            # Map unique identifier based on incoming source scope space (Group vs Individual)
            source_id = event["source"].get("groupId") or event["source"].get("roomId") or event["source"]["userId"]

            # Command 1: View Supported Code Menu Matrix (/? )
            if user_text == "/?":
                help_text = "🌐 Supported Language Codes:\n" + "\n".join([f"• /{code} : {name}" for code, name in LANGUAGE_MAP.items()])
                help_text += "\n\nSet your group chat routing via: /en-th or /my-th"
                background_tasks.add_task(send_line_group_reply, reply_token, help_text)
                continue

            # Command 2: Dynamic Pair Reconfiguration (e.g., /my-th)
            command_match = re.match(r"^/([a-z]{2})-([a-z]{2})$", user_text.lower())
            if command_match:
                src, tgt = command_match.group(1), command_match.group(2)
                if src in LANGUAGE_MAP and tgt in LANGUAGE_MAP:
                    sessions[source_id] = {
                        "host_lang": LANGUAGE_MAP[src],
                        "guest_lang": LANGUAGE_MAP[tgt],
                        "is_line_group": True
                    }
                    confirm_msg = f"✅ Translation configured: {LANGUAGE_MAP[src]} ↔️ {LANGUAGE_MAP[tgt]}"
                else:
                    confirm_msg = "❌ Invalid language configuration path. Send /? to view active options."
                background_tasks.add_task(send_line_group_reply, reply_token, confirm_msg)
                continue

            # Default text input: Dispatched onto stateless translation loops
            background_tasks.add_task(handle_line_group_stream, source_id, user_text, reply_token)

    return "OK", 200

async def handle_line_group_stream(source_id: str, text: str, reply_token: str):
    try:
        # Default session fallback to Burmese <-> Thai if no group config command run yet
        group_config = sessions.get(source_id, {
            "host_lang": "Burmese",
            "guest_lang": "Thai"
        })
        
        lang_a = group_config["host_lang"]
        lang_b = group_config["guest_lang"]

        # Call the new bi-directional engine to translate fluently in both directions
        translated_text = await translate_text_bidirectional(text, lang_a, lang_b)
        
        # Format visually compact inline string subtitle payload matching design choices
        combined_payload = f"{text.strip()} | {translated_text.strip()}"

        print(f"[LINE Group] Intercepted ({text}) -> Dispatched ({combined_payload})")
        await send_line_group_reply(reply_token, combined_payload)

    except Exception as e:
        print(f"Error executing group processing pipeline: {e}")

async def send_line_group_reply(reply_token: str, text_content: str):
    if not LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_ACCESS_TOKEN == "None":
        print("❌ ERROR: Cannot reply to LINE. LINE_CHANNEL_ACCESS_TOKEN variable is missing or None inside environment.")
        return

    line_url = "https://api.line.me/v2/bot/message/reply"
    clean_token = str(LINE_CHANNEL_ACCESS_TOKEN).strip()
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {clean_token}"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text_content}]
    }

    async with httpx.AsyncClient(follow_redirects=True) as client_http:
        response = await client_http.post(line_url, json=data, headers=headers)
        print(f"[LINE Outbound] Status Code: {response.status_code} - Body: {response.text}")