# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deploy & Restart

```bash
# Build and start (or rebuild after changes to requirements.txt, Dockerfile, nginx/*)
UID=$(id -u) GID=$(id -g) docker compose up -d --build

# Restart without rebuild (app.py is bind-mounted, changes take effect after restart)
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

**Data flow:** Host filesystem (`~/.openclaw/agents/main/sessions`) → bind-mounted read-only at `/sessions` inside the `streamlit` container → `app.py` reads session files → nginx proxies browser requests (port 8501 on host → nginx :80 → streamlit :8501).

**Session file types:** OpenClaw writes three kinds of files to the sessions directory:
- `<uuid>.jsonl` — active session, currently being written
- `<uuid>.jsonl.reset.<timestamp>` — session archived by compaction (context limit hit); contains the full pre-compaction history
- `<uuid>.jsonl.deleted.<timestamp>` — session pruned by the maintenance cycle

All three types are scanned by `_session_files(log_dir)` and included in both the sidebar file list and token statistics.

**Session log format:** Each file is one line per JSON record. Records have a `type` field:
- `"message"` — conversation turns; `message.role` is `"user"`, `"assistant"`, or `"toolResult"`
- `"session"`, `"model_change"`, `"thinking_level_change"`, `"custom"` — metadata events

**Key functions in `app.py`:**
- `_session_files(log_dir)` — returns all session file paths (all three suffixes) via glob
- `list_jsonl_files()` — sidebar file listing, sorted newest-first by mtime
- `detect_session_type()` — classifies a file as `main` / `cron` / `subagent` by reading the first user message
- `_fmt_file_label()` — renders compact sidebar labels for reset/deleted files (e.g. `abc12345 ↩ [04-01 10:42]`)
- `tail_load()` / `load_older_chunk()` / `append_new_lines()` — chunked, incremental file I/O using byte offsets in `st.session_state.file_cache`
- `render_records()` — dispatches each record to `render_user_message()`, `render_assistant_message()`, `render_tool_result()`, or `render_meta_record()`
- `render_content_block()` — handles content blocks within assistant messages (`text`, `thinking`, `toolCall`, unknown)
- `scan_token_stats()` — scans all session files, aggregates `input + output + cacheRead + cacheWrite` per day (Asia/Shanghai)
- `render_token_stats()` — renders today / 近7天 / 近30天 / all-time metric cards + 30-day bar chart + detail table

**Token statistics logic:**
- Only `type=message`, `role=assistant`, `usage.input > 0` records are counted (one record = one API call)
- `total = input + output + cacheRead + cacheWrite` (matches OpenClaw's `totalTokens` field definition)
- Cache key (`fingerprint`) is `sorted([(filename, size)])` — invalidated whenever any file grows

**Caching:**
- `translate_to_chinese()` — `@st.cache_data(max_entries=2048)`, avoids re-translating identical strings
- `detect_session_type()` — `@st.cache_data(max_entries=500)`, keyed on `(filepath, mtime)`
- `scan_token_stats()` — `@st.cache_data`, keyed on directory fingerprint

**Auto-refresh:** `st_autorefresh(interval=1000)` triggers Streamlit reruns every second; `append_new_lines()` reads only new bytes since `tail_offset`, making reruns cheap.

## Configuration

Controlled via environment variables in `docker-compose.yml`:

| Variable | Default | Notes |
|---|---|---|
| `OPENCLAW_SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | Host path mounted as `/sessions` |
| `CHUNK_BYTES` | `262144` (256 KB) | Set in `app.py`; not in compose by default |
| `AUTO_REFRESH_INTERVAL` | `5` | Passed to container but UI hardcodes 1 s |

To also expose cron run logs, uncomment the `OPENCLAW_CRON_DIR` volume line in `docker-compose.yml`.
