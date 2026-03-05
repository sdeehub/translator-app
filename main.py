from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import AsyncOpenAI
import uuid
import os

app = FastAPI()

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# -------------------------
# CONFIG
# -------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")

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
    "km": "Central Khmer"
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
        model="gpt-4o",  # Best balance for real-time translation
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

                await websocket.send_json({
                    "system": f"Your language changed to {full_language}"
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

                guest_socket = sessions[session_id]["guest"]
                if guest_socket:
                    await guest_socket.send_json({
                        "system": f"Your language was set to {full_language} by host"
                    })

                await websocket.send_json({
                    "system": f"Guest language changed to {full_language}"
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
