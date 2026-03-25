# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deploy & Restart

```bash
# Build and start (or rebuild after changes to app.py, requirements.txt, Dockerfile, nginx/*)
UID=$(id -u) GID=$(id -g) docker compose up -d --build

# Restart without rebuild (e.g. after editing app.py, which is bind-mounted)
docker compose restart streamlit

# View logs
docker compose logs -f streamlit
docker compose logs -f nginx

# Stop
docker compose down
```

`app.py` is bind-mounted read-only into the container, so changes to it take effect after a container restart (no rebuild needed). Changes to `requirements.txt`, `Dockerfile`, or `nginx/default.conf` require a full `--build`.

## Architecture

The app is a single Python file (`app.py`) that runs as a Streamlit server inside Docker, fronted by an nginx reverse proxy.

**Data flow:** Host filesystem (`~/.openclaw/agents/main/sessions`) → bind-mounted read-only at `/sessions` inside the `streamlit` container → `app.py` reads `.jsonl` files → nginx proxies browser requests (port 8501 on host → nginx :80 → streamlit :8501).

**Session log format:** Each `.jsonl` file is one line per JSON record. Records have a `type` field:
- `"message"` — conversation turns; `message.role` is `"user"`, `"assistant"`, or `"toolResult"`
- `"session"`, `"model_change"`, `"thinking_level_change"`, `"custom"` — metadata events

**Rendering pipeline in `app.py`:**
1. `list_jsonl_files()` — sidebar file listing, sorted newest-first by mtime
2. `tail_load()` / `load_older_chunk()` / `append_new_lines()` — chunked, incremental file I/O using byte offsets stored in `st.session_state.file_cache`
3. `render_records()` — dispatches each record to `render_user_message()`, `render_assistant_message()`, `render_tool_result()`, or `render_meta_record()`
4. `render_content_block()` — handles individual content blocks within assistant messages (`text`, `thinking`, `toolCall`, unknown)

**Caching:** `translate_to_chinese()` is decorated with `@st.cache_data(max_entries=2048)` to avoid re-translating identical strings across reruns.

**Auto-refresh:** `st_autorefresh(interval=1000)` triggers Streamlit reruns every second; `append_new_lines()` reads only new bytes since `tail_offset`, making reruns cheap.

## Configuration

Controlled via environment variables in `docker-compose.yml`:

| Variable | Default | Notes |
|---|---|---|
| `OPENCLAW_SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | Host path mounted as `/sessions` |
| `CHUNK_BYTES` | `262144` (256 KB) | Set in `app.py`; not in compose by default |
| `AUTO_REFRESH_INTERVAL` | `5` | Passed to container but UI hardcodes 1 s |

To also expose cron run logs, uncomment the `OPENCLAW_CRON_DIR` volume line in `docker-compose.yml`.
