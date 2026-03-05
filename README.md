# Real-Time Translator App

A lightweight real-time chat translator built with:

- FastAPI
- WebSockets
- LiteLLM
- HTML/CSS (Custom UI)
- Deployable on Render

## Features

- Host / Guest session model
- Real-time WebSocket communication
- Automatic message translation
- ISO language switching (`/lang fr`)
- End session with `/end`
- Mobile responsive full-screen chat UI

## Commands

| Command      | Description |
|-------------|------------|
| `/lang fr`  | Change your language (ISO code) |
| `/guest fr` | Change guest language (host only) |
| `/end`      | End the session |

## Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8010
```

Visit:

```
http://localhost:8010
```

## Deployment

Configured for deployment on Render using:

- `render.yaml`
- `requirements.txt`

## Environment Variables

Set the following environment variables:

OPENAI_API_KEY=your_api_key
API_BASE_URL=https://your-api-base-url

## Architecture

- `/` → Host
- `/session/{id}/guest` → Guest
- WebSocket endpoint per session
- Stateless backend session handling

## License

MIT
