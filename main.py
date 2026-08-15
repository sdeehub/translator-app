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

base_dir = Path(__file__).resolve().parent
load_dotenv(dotenv_path=base_dir / ".env")

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# -------------------------
# CONFIG
# -------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")
MODEL = os.getenv("MODEL", "gpt-4o")  # fallback to gpt-4o if not set

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

client = AsyncOpenAI(
    base_url=API_BASE_URL,
    api_key=OPENAI_API_KEY
)

# -------------------------
# IN-MEMORY SESSION STORE
# -------------------------

sessions = {}

# -------------------------
# ISO Language Map
# -------------------------

LANGUAGE_MAP = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "th": "Thai",
    "ms": "Malay",
    "id": "Indonesian",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "pt": "Portuguese",
    "ru": "Russian",
    "hu": "Hungarian",
    "lo": "Lao",
    "km": "Central Khmer",
    "my": "Burmese"
}

# -------------------------
# HOME PAGE
# -------------------------

@app.get("/", response_class=HTMLResponse)
async def home():
    return FileResponse("static/index.html")

# -------------------------
# HEALTH CHECK (optional)
# -------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}

# -------------------------
# CREATE SESSION
# -------------------------

@app.post("/create-session")
async def create_session():
    session_id = str(uuid.uuid4())[:8]

    sessions[session_id] = {
        "host": None,
        "guest": None,
        "status": "active",
        "host_lang": "Thai",       # Default host language
        "guest_lang": "Spanish"    # Default guest language
    }

    return {"session_id": session_id}

# -------------------------
# SERVE SESSION UI
# -------------------------

@app.get("/session/{session_id}/{role}", response_class=HTMLResponse)
async def serve_session(session_id: str, role: str):

    if role not in ["guest"]:
        return HTMLResponse("Invalid role", status_code=400)

    if session_id not in sessions:
        return HTMLResponse("Invalid session ID", status_code=400)

    with open("static/index.html", encoding="utf-8") as f:
        html = f.read()

    return (
        html.replace("SESSION_ID", session_id)
            .replace("ROLE", role)
    )

# -------------------------
# UPDATE LANGUAGE
# -------------------------

class LanguageUpdate(BaseModel):
    role: str
    language: str

@app.post("/set-language/{session_id}")
async def set_language(session_id: str, payload: LanguageUpdate):

    if session_id not in sessions:
        return {"error": "Invalid session"}

    if payload.role not in ["host", "guest"]:
        return {"error": "Invalid role"}

    sessions[session_id][f"{payload.role}_lang"] = payload.language

    return {"status": "updated"}

# -------------------------
# HIGH-PRECISION TRANSLATION
# -------------------------

async def translate_text(text: str, source_language: str, target_language: str):
    """
    Deterministic translation engine for real-time chat.
    """

    response = await client.chat.completions.create(
        model=MODEL,  # Set as env var
        temperature=0.0,
        messages=[
            {
                "role": "system",
                "content": f"""
You are a high-precision real-time translation engine.

Translate strictly from {source_language} to {target_language}.

RULES:
- Preserve tone and informality.
- Preserve emojis.
- Do NOT explain.
- Do NOT answer.
- Do NOT add commentary.
- Do NOT apologize.
- Do NOT mention training data.
- Do NOT change meaning.
- Output ONLY the translated text.
- If slang appears, translate naturally.
"""
            },
            {
                "role": "user",
                "content": text
            }
        ],
    )

    return response.choices[0].message.content.strip()

# -------------------------
# WEBSOCKET ENDPOINT
# -------------------------

@app.websocket("/ws/{session_id}/{role}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, role: str):

    await websocket.accept()

    if session_id not in sessions:
        await websocket.send_json({"error": "Invalid session"})
        await websocket.close()
        return

    if role not in ["host", "guest"]:
        await websocket.send_json({"error": "Invalid role"})
        await websocket.close()
        return

    sessions[session_id][role] = websocket

    try:
        while True:
            data = await websocket.receive_text()
            command = data.strip().lower()

            # =========================
            # /end
            # =========================
            if command == "/end":
                sessions[session_id]["status"] = "closed"

                other_role = "guest" if role == "host" else "host"
                other_socket = sessions[session_id][other_role]

                if other_socket:
                    await other_socket.send_json({
                        "system": "Session ended."
                    })
                    await other_socket.close()

                await websocket.send_json({
                    "system": "Session ended."
                })
                await websocket.close()
                break
            
            # =========================
            # /lang <iso_code>
            # =========================
            if command.startswith("/lang "):
                code = data.split(" ", 1)[1].strip().lower()

                if code not in LANGUAGE_MAP:
                    await websocket.send_json({
                        "system": "Invalid language code."
                    })
                    continue

                full_language = LANGUAGE_MAP[code]
                sessions[session_id][f"{role}_lang"] = full_language

                host_lang  = sessions[session_id]["host_lang"]
                guest_lang = sessions[session_id]["guest_lang"]

                # Notify the sender
                await websocket.send_json({
                    "system": f"Your language changed to {full_language}",
                    "lang_update": {"host_lang": host_lang, "guest_lang": guest_lang}
                })

                # Sync the other side so their badge updates too
                other_role   = "guest" if role == "host" else "host"
                other_socket = sessions[session_id][other_role]
                if other_socket:
                    await other_socket.send_json({
                        "system": f"{'Host' if role == 'host' else 'Guest'} language changed to {full_language}",
                        "lang_update": {"host_lang": host_lang, "guest_lang": guest_lang}
                    })
                continue

            # =========================
            # /guest <iso_code>
            # =========================
            if command.startswith("/guest ") and role == "host":
                code = data.split(" ", 1)[1].strip().lower()

                if code not in LANGUAGE_MAP:
                    await websocket.send_json({
                        "system": "Invalid language code."
                    })
                    continue

                full_language = LANGUAGE_MAP[code]
                sessions[session_id]["guest_lang"] = full_language

                host_lang  = sessions[session_id]["host_lang"]
                guest_lang = sessions[session_id]["guest_lang"]

                guest_socket = sessions[session_id]["guest"]
                if guest_socket:
                    await guest_socket.send_json({
                        "system": f"Your language was set to {full_language} by host",
                        "lang_update": {"host_lang": host_lang, "guest_lang": guest_lang}
                    })

                await websocket.send_json({
                    "system": f"Guest language changed to {full_language}",
                    "lang_update": {"host_lang": host_lang, "guest_lang": guest_lang}
                })
                continue

            # =========================
            # Ignore if closed
            # =========================
            if sessions[session_id]["status"] != "active":
                continue

            other_role = "guest" if role == "host" else "host"
            other_socket = sessions[session_id][other_role]

            if not other_socket:
                continue

            # Determine languages
            if role == "host":
                source_lang = sessions[session_id]["host_lang"]
                target_lang = sessions[session_id]["guest_lang"]
            else:
                source_lang = sessions[session_id]["guest_lang"]
                target_lang = sessions[session_id]["host_lang"]

            # Translate
            translated = await translate_text(
                data,
                source_lang,
                target_lang
            )

            payload = {
                "original": data,
                "translated": translated,
                "from": role
            }

            await other_socket.send_json(payload)
            await websocket.send_json(payload)

    except WebSocketDisconnect:
        sessions[session_id][role] = None

        # Notify the other party if session is still active
        if sessions[session_id]["status"] == "active":
            other_role   = "guest" if role == "host" else "host"
            other_socket = sessions[session_id][other_role]

            if other_socket:
                try:
                    await other_socket.send_json({
                        "system": "Session ended."
                    })
                except Exception:
                    pass

@app.post("/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    events = payload.get("events", [])

    for event in events:
        if event.get("type") == "message" and event["message"]["type"] == "text":
            user_text = event["message"]["text"].strip()
            reply_token = event["replyToken"]
            
            # Map unique identifier based on incoming source scope space
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
                
                # Check authorization matching indices against your existing LANGUAGE_MAP dict
                if src in LANGUAGE_MAP and tgt in LANGUAGE_MAP:
                    # Save configuration safely using structural keys that won't conflict with Web UI logic
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

            # Default: Run Stateless Group Translation
            background_tasks.add_task(handle_line_group_stream, source_id, user_text, reply_token)

    return "OK", 200

async def handle_line_group_stream(source_id: str, text: str, reply_token: str):
    try:
        # Default session fallback if they haven't run commands yet
        group_config = sessions.get(source_id, {
            "host_lang": "Burmese",
            "guest_lang": "Thai"
        })
        
        lang_a = group_config["host_lang"]
        lang_b = group_config["guest_lang"]

        # 1. Call the bi-directional engine to translate fluently
        translated_text = await translate_text_bidirectional(text, lang_a, lang_b)

        # 2. FORMAT TRANSMISSION: Single compact inline format
        # Uses standard strip() to remove any trailing system line breaks
        combined_payload = f"{text.strip()} | {translated_text.strip()}"

        # 3. Deliver back to the LINE group chat
        await send_line_group_reply(reply_token, combined_payload)

    except Exception as e:
        print(f"Error executing inline group translation layout: {e}")

async def translate_text_bidirectional(text: str, lang_a: str, lang_b: str):
    """
    Bi-directional translator that auto-detects language inputs 
    and handles two-way group chat communication out of the box.
    """
    response = await client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {
                "role": "system",
                "content": f"""
You are an invisible high-precision real-time translation server engine for a chat room.
The active matching language configurations are {lang_a} and {lang_b}.

RULES:
1. AUTO-DETECT: Analyze whether the incoming text string input is written in {lang_a} or {lang_b}.
2. TRANSLATE: 
   - If the input text is written in {lang_a}, translate it strictly to {lang_b}.
   - If the input text is written in {lang_b}, translate it strictly to {lang_a}.
3. Properties: Preserve exact contextual definitions, informal tone, and any emojis.
4. Output ONLY the raw final translated text string. Do NOT add meta commentary, status info, notes, or apologies.
5. If the language profile is ambiguous or mixed, default to translating to {lang_b} as a safe fallback option.
"""
            },
            {"role": "user", "content": text}
        ],
    )
    return response.choices[0].message.content.strip()


async def send_line_group_reply(reply_token: str, text_content: str):
    line_url = "https://line.me"
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
        await client_http.post(line_url, json=data, headers=headers)
