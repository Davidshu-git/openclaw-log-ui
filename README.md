# OpenClaw Session Viewer

A lightweight Streamlit web UI for browsing and debugging [OpenClaw](https://openclaw.ai) agent session logs (`.jsonl` files) in real time.

## Features

- 📂 Lists all `.jsonl` session files, sorted by last modified time
- 💬 Renders user/assistant messages as chat bubbles
- 💭 Expandable **思考过程 (Chain of Thought)** with optional Chinese translation via Google Translate
- 🛠️ Collapsible tool calls with syntax-highlighted arguments
- ✅/❌ Tool results with error highlighting and exit code detection
- ⚡ **Tail-first loading** — only reads the last 256 KB on open; older chunks loaded on demand
- 🔄 **1-second auto-refresh** — appends new lines without reloading the whole file
- 🌐 **Chinese translation toggle** for agent reasoning text (cached, non-blocking)
- nginx reverse proxy with gzip compression for faster remote access

## Quick Start

```bash
# Clone
git clone https://github.com/your-username/openclaw-ui.git
cd openclaw-ui

# Set your sessions directory (defaults to ~/.openclaw/agents/main/sessions)
export OPENCLAW_SESSIONS_DIR=~/.openclaw/agents/main/sessions

# Start
UID=$(id -u) GID=$(id -g) docker compose up -d --build

# Open http://localhost:8501
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `OPENCLAW_SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | Path to session `.jsonl` files |
| `OPENCLAW_CRON_DIR` | `~/.openclaw/cron/runs` | Path to cron run logs (optional, see compose file) |
| `LOG_DIR` | `/sessions` | Mount point inside the container |
| `CHUNK_BYTES` | `262144` (256 KB) | Bytes to read per chunk |

## Project Structure

```
openclaw-ui/
├── app.py              # Streamlit application
├── requirements.txt    # Python dependencies
├── Dockerfile          # python:3.12-slim image
├── docker-compose.yml  # streamlit + nginx services
├── nginx/
│   └── default.conf    # gzip + static asset caching + WebSocket proxy
└── .streamlit/
    └── config.toml     # Streamlit server config
```
