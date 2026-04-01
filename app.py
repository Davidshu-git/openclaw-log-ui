import os
import json
import glob
from datetime import datetime, timezone, timedelta, date as date_type
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from deep_translator import GoogleTranslator


@st.cache_data(max_entries=2048)
def translate_to_chinese(text: str) -> str:
    """Translate English reasoning text to Chinese. Returns original on failure."""
    if not text or not text.strip():
        return text
    try:
        return GoogleTranslator(source="auto", target="zh-CN").translate(text)
    except Exception:
        return text

# ── Config ──────────────────────────────────────────────────────────────────
LOG_DIR = os.environ.get("LOG_DIR", "./sessions")
EXTRA_LOG_DIR = os.environ.get("EXTRA_LOG_DIR", "")  # optional second scan root
AUTO_REFRESH_INTERVAL = int(os.environ.get("AUTO_REFRESH_INTERVAL", "5"))  # seconds
CHUNK_BYTES = int(os.environ.get("CHUNK_BYTES", str(256 * 1024)))  # 256 KB per chunk


def _session_files(log_dir: str) -> set[str]:
    """Return all session file paths under log_dir: .jsonl, .jsonl.reset.*, .jsonl.deleted.*"""
    patterns = [
        os.path.join(log_dir, "**", "*.jsonl"),
        os.path.join(log_dir, "**", "*.jsonl.reset.*"),
        os.path.join(log_dir, "**", "*.jsonl.deleted.*"),
    ]
    return {f for p in patterns for f in glob.glob(p, recursive=True)}

st.set_page_config(
    page_title="OpenClaw 日志查看器",
    page_icon="🦞",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* 减少主区域顶部空白 */
.block-container { padding-top: 1rem !important; padding-bottom: 0.5rem !important; }

/* 缩小侧边栏顶部空白 */
[data-testid="stSidebar"] > div:first-child { padding-top: 0.75rem !important; }

/* 侧边栏标题紧凑化 */
[data-testid="stSidebar"] h3 { margin-bottom: 0.1rem !important; }

/* 压缩 info/warning 框的内边距 */
[data-testid="stAlert"] { padding: 0.5rem 0.75rem !important; }

/* 减少 divider 上下间距 */
hr { margin: 0.4rem 0 !important; }

/* chat message 减少上下 padding */
[data-testid="stChatMessage"] { padding: 0.4rem 0.6rem !important; }

/* caption 字体略小一点 */
[data-testid="stCaptionContainer"] p { font-size: 0.76rem !important; color: #888 !important; }
</style>
""", unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────────────────────

def list_jsonl_files(log_dir: str) -> list[dict]:
    """Return list of dicts with path/name/mtime, sorted newest first.
    Includes .jsonl, .jsonl.reset.*, and .jsonl.deleted.* files."""
    result = []
    for f in _session_files(log_dir):
        try:
            mtime = os.path.getmtime(f)
            result.append({"path": f, "name": os.path.relpath(f, log_dir), "mtime": mtime})
        except OSError:
            pass
    result.sort(key=lambda x: x["mtime"], reverse=True)
    return result


@st.cache_data(max_entries=500)
def detect_session_type(filepath: str, mtime: float) -> str:
    """Return 'main', 'cron', or 'subagent'. mtime is used as cache-invalidation key."""
    try:
        with open(filepath, "rb") as fh:
            fh.readline()  # skip first line (session metadata)
            for _ in range(60):
                raw = fh.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("type") == "message" and rec.get("message", {}).get("role") == "user":
                        content = rec["message"]["content"]
                        text = ""
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block["text"]
                                    break
                        elif isinstance(content, str):
                            text = content
                        if "[cron:" in text:
                            return "cron"
                        if "[Subagent Context]" in text:
                            return "subagent"
                        return "main"
                except Exception:
                    pass
    except Exception:
        pass
    return "subagent"


_TYPE_BADGE = {"main": "主", "cron": "C", "subagent": "子"}


def _fmt_file_label(name: str) -> str:
    """Return a compact display name for a session file, handling reset/deleted suffixes."""
    basename = os.path.basename(name)
    uid = basename[:8]

    def _parse_suffix_ts(suffix_str: str) -> str:
        """Parse '2026-04-01T02-42-57.325Z' → '04-01 10:42' (Asia/Shanghai)."""
        try:
            # Time portion uses hyphens instead of colons: T02-42-57
            date_part, time_part = suffix_str.split("T", 1)
            time_part = time_part.replace("-", ":", 2).split(".")[0]  # HH:MM:SS
            dt = datetime.fromisoformat(f"{date_part}T{time_part}+00:00").astimezone(TZ)
            return dt.strftime("%m-%d %H:%M")
        except Exception:
            return ""

    if ".reset." in basename:
        ts = _parse_suffix_ts(basename.split(".reset.")[-1])
        return f"{uid} ↩  [{ts}]" if ts else f"{uid} ↩reset"
    if ".deleted." in basename:
        ts = _parse_suffix_ts(basename.split(".deleted.")[-1])
        return f"{uid} 🗑  [{ts}]" if ts else f"{uid} 🗑deleted"
    return uid


def _parse_range(fh, start: int, end: int) -> tuple[list[dict], int, int]:
    """Read lines from fh between [start, end) bytes. Returns (records, actual_start, actual_end)."""
    fh.seek(start)
    if start > 0:
        fh.readline()           # skip potentially incomplete first line
    actual_start = fh.tell()
    records = []
    while fh.tell() < end:
        raw = fh.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records, actual_start, fh.tell()


def tail_load(filepath: str) -> dict:
    """Initial load: read only the last CHUNK_BYTES of the file.
    Returns cache dict with keys: path, records, head_offset, tail_offset, file_size, fully_loaded."""
    try:
        file_size = os.path.getsize(filepath)
        start = max(0, file_size - CHUNK_BYTES)
        with open(filepath, "rb") as fh:
            records, actual_start, tail_offset = _parse_range(fh, start, file_size)
        return {
            "path": filepath,
            "records": records,
            "head_offset": actual_start,
            "tail_offset": tail_offset,
            "file_size": file_size,
            "fully_loaded": actual_start == 0,
        }
    except (OSError, PermissionError) as exc:
        st.error(f"无法读取文件: {exc}")
        return {"path": filepath, "records": [], "head_offset": 0,
                "tail_offset": 0, "file_size": 0, "fully_loaded": True}


def load_older_chunk(cache: dict) -> dict:
    """Prepend one more CHUNK_BYTES of older records to the cache."""
    filepath = cache["path"]
    end = cache["head_offset"]
    if end == 0:
        return {**cache, "fully_loaded": True}
    start = max(0, end - CHUNK_BYTES)
    try:
        with open(filepath, "rb") as fh:
            records, actual_start, _ = _parse_range(fh, start, end)
        return {
            **cache,
            "records": records + cache["records"],
            "head_offset": actual_start,
            "fully_loaded": actual_start == 0,
        }
    except (OSError, PermissionError) as exc:
        st.error(f"无法读取文件: {exc}")
        return cache


def append_new_lines(cache: dict) -> tuple[dict, int]:
    """Read any lines appended since last load. Returns (updated_cache, new_count).
    Does NOT skip the first line — tail_offset is always at a line boundary."""
    filepath = cache["path"]
    try:
        file_size = os.path.getsize(filepath)
        if file_size <= cache["tail_offset"]:
            return cache, 0
        records = []
        new_tail = cache["tail_offset"]
        with open(filepath, "rb") as fh:
            fh.seek(cache["tail_offset"])
            for raw in fh:
                new_tail = fh.tell()
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        updated = {**cache, "records": cache["records"] + records,
                   "tail_offset": new_tail, "file_size": file_size}
        return updated, len(records)
    except (OSError, PermissionError) as exc:
        st.error(f"无法读取文件: {exc}")
        return cache, 0


def fmt_ts(ts_str: str) -> str:
    """ISO timestamp → Asia/Shanghai time string."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone(TZ).strftime("%H:%M:%S")
    except Exception:
        return ts_str


def has_error(obj) -> bool:
    """Recursively check whether any value looks like an error."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("error", "exception", "iserror") and v and v is not False:
                return True
            if has_error(v):
                return True
    elif isinstance(obj, list):
        return any(has_error(i) for i in obj)
    return False


# ── Rendering ────────────────────────────────────────────────────────────────

def render_content_block(block: dict, translate: bool = False):
    """Render a single content block from an assistant or user message."""
    btype = block.get("type", "")

    if btype == "text":
        text = block.get("text", "")
        if text.strip():
            out = translate_to_chinese(text) if translate else text
            st.markdown(_safe_str(out))

    elif btype == "thinking":
        thinking_text = block.get("thinking", "")
        with st.expander("💭 思考过程", expanded=True):
            out = translate_to_chinese(thinking_text) if translate else thinking_text
            st.markdown(_safe_str(out))

    elif btype == "toolCall":
        tool_name = block.get("name", "unknown")
        tool_id = block.get("id", "")
        arguments = block.get("arguments", {})
        label = f"🛠️ 工具调用: `{tool_name}`"
        if tool_id:
            label += f" <span style='color:gray;font-size:0.8em'>({tool_id})</span>"
        with st.expander(label, expanded=False):
            st.caption("参数")
            if isinstance(arguments, dict):
                # Pretty-print each argument; show 'command' as shell code block
                for key, val in arguments.items():
                    st.caption(f"**{key}**")
                    if key in ("command", "script", "code") and isinstance(val, str):
                        lang = "bash" if key == "command" else "python"
                        st.code(val, language=lang)
                    elif isinstance(val, (dict, list)):
                        st.code(json.dumps(val, indent=2, ensure_ascii=False), language="json")
                    else:
                        st.code(str(val))
            else:
                st.code(json.dumps(arguments, indent=2, ensure_ascii=False), language="json")

    else:
        # Unknown block type — show raw JSON
        with st.expander(f"📦 {btype}", expanded=False):
            st.code(json.dumps(block, indent=2, ensure_ascii=False), language="json")


def render_tool_result(record: dict):
    """Render a toolResult message (role == 'toolResult')."""
    msg = record.get("message", {})
    tool_name = msg.get("toolName", "unknown")
    tool_id = msg.get("toolCallId", "")
    content = msg.get("content", [])
    details = msg.get("details", {})
    is_error = msg.get("isError", False) or has_error(details)
    ts = fmt_ts(record.get("timestamp", ""))

    exit_code = details.get("exitCode")
    duration_ms = details.get("durationMs")

    # Build expander label with status hint
    status_icon = "❌" if is_error or (exit_code is not None and exit_code != 0) else "✅"
    label = f"{status_icon} 工具结果: `{tool_name}`"
    if ts:
        label += f"  ·  {ts}"
    if duration_ms is not None:
        label += f"  ·  {duration_ms}ms"

    with st.expander(label, expanded=is_error):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    if is_error or (exit_code is not None and exit_code != 0):
                        st.error(text)
                    else:
                        st.code(text, language="")
            elif isinstance(block, str):
                st.code(block, language="")

        if details and details != {"status": "completed"}:
            st.caption("详情")
            st.code(json.dumps(details, indent=2, ensure_ascii=False), language="json")

        if tool_id:
            st.caption(f"调用 ID: `{tool_id}`")


def _abbr(n: int) -> str:
    """Abbreviate large token counts: 53168 → 53.2k, 1234567 → 1.2M, 423 → 423."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def render_assistant_message(record: dict, translate: bool = False):
    """Render an assistant message — text visible, tool calls / thinking collapsible."""
    msg = record.get("message", {})
    content = msg.get("content", [])
    usage = msg.get("usage", {})
    ts = fmt_ts(record.get("timestamp", ""))

    with st.chat_message("assistant"):
        # Header row: timestamp left, token pill or local badge right
        if usage and usage.get("input", 0):
            inp   = _abbr(usage.get("input", 0))
            out   = _abbr(usage.get("output", 0))
            cache = usage.get("cacheRead", 0)
            cache_str = f" · cache:{_abbr(cache)}" if cache else ""
            right_html = (
                f"<span style='float:right;"
                f"font-size:0.68rem;color:#4a9eff;"
                f"background:rgba(74,158,255,0.1);border:1px solid rgba(74,158,255,0.25);"
                f"border-radius:4px;padding:1px 6px;line-height:1.8'>"
                f"in:{inp} · out:{out}{cache_str}"
                f"</span>"
            )
        else:
            right_html = (
                f"<span style='float:right;"
                f"font-size:0.68rem;color:#999;"
                f"background:rgba(150,150,150,0.1);border:1px solid rgba(150,150,150,0.2);"
                f"border-radius:4px;padding:1px 6px;line-height:1.8'>"
                f"⚙️ 本地"
                f"</span>"
            )
        ts_html = f"<span style='font-size:0.76rem;color:#888'>{ts}</span>" if ts else ""
        st.markdown(f"{ts_html}{right_html}<div style='clear:both'></div>", unsafe_allow_html=True)
        for block in content:
            render_content_block(block, translate=translate)


def _safe_str(text: str) -> str:
    """Remove lone surrogate characters that cause UnicodeEncodeError in Streamlit."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _split_metadata(text: str) -> tuple[str, str, str]:
    """Split into (before_meta, meta_block, after_meta).
    Detects 'Conversation info (untrusted metadata):' injected by Telegram.
    Both before and after text are shown; only the metadata block is folded."""
    marker = "Conversation info (untrusted metadata):"
    idx = text.find(marker)
    if idx == -1:
        return text, "", ""

    before = text[:idx].strip()
    rest = text[idx:]

    # The metadata contains two ```json...``` fences (Conversation info + Sender).
    # Split on ``` to find where metadata ends and user text resumes.
    parts = rest.split("```")
    if len(parts) >= 5:
        # parts: [0]=intro, [1]=json1, [2]=between, [3]=json2, [4]=after
        meta = "```".join(parts[:4]) + "```"
        after = parts[4].strip()
    else:
        meta = rest
        after = ""

    return before, meta, after


def render_user_message(record: dict):
    """Render a user message."""
    msg = record.get("message", {})
    content = msg.get("content", [])
    ts = fmt_ts(record.get("timestamp", ""))

    with st.chat_message("user"):
        if ts:
            st.caption(ts)
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    before, meta, after = _split_metadata(block.get("text", ""))
                    if before:
                        st.markdown(_safe_str(before))
                    if meta:
                        with st.expander("📋 消息元数据", expanded=False):
                            st.code(meta, language="")
                    if after:
                        st.markdown(_safe_str(after))
                else:
                    with st.expander(f"📦 {btype}", expanded=False):
                        st.code(json.dumps(block, indent=2, ensure_ascii=False), language="json")
            elif isinstance(block, str):
                before, meta, after = _split_metadata(block)
                if before:
                    st.markdown(_safe_str(before))
                if meta:
                    with st.expander("📋 消息元数据", expanded=False):
                        st.code(meta, language="")
                if after:
                    st.markdown(_safe_str(after))


def render_meta_record(record: dict):
    """Render session-level metadata events (model_change, session info, etc.)."""
    rtype = record.get("type", "")
    ts = fmt_ts(record.get("timestamp", ""))

    if rtype == "session":
        version = record.get("version", "")
        session_id = record.get("id", "")
        cwd = record.get("cwd", "")
        st.info(f"📂 会话开始  ·  id: `{session_id}`  ·  cwd: `{cwd}`  ·  v{version}  ·  {ts}")

    elif rtype == "model_change":
        provider = record.get("provider", "")
        model_id = record.get("modelId", "")
        st.info(f"🤖 模型: **{model_id}** ({provider})  ·  {ts}")

    elif rtype == "thinking_level_change":
        level = record.get("thinkingLevel", "")
        st.info(f"🧠 思考深度: **{level}**  ·  {ts}")

    elif rtype == "custom":
        custom_type = record.get("customType", rtype)
        data = record.get("data", {})
        with st.expander(f"⚙️ {custom_type}  ·  {ts}", expanded=False):
            st.code(json.dumps(data, indent=2, ensure_ascii=False), language="json")

    else:
        with st.expander(f"📋 {rtype}  ·  {ts}", expanded=False):
            st.code(json.dumps(record, indent=2, ensure_ascii=False), language="json")


@st.cache_data
def scan_token_stats(fingerprint: str) -> list[dict]:
    """Scan all .jsonl / .jsonl.reset.* / .jsonl.deleted.* files in LOG_DIR and aggregate
    token usage by date (Asia/Shanghai).
    fingerprint is a str of sorted (filename, size) tuples — used as cache key."""
    daily: dict[str, dict] = {}
    for filepath in _session_files(LOG_DIR):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") != "message":
                        continue
                    msg = rec.get("message", {})
                    if msg.get("role") != "assistant":
                        continue
                    usage = msg.get("usage", {})
                    if not usage or not usage.get("input", 0):
                        continue
                    ts_str = rec.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        day_str = dt.astimezone(TZ).strftime("%Y-%m-%d")
                    except Exception:
                        continue
                    if day_str not in daily:
                        daily[day_str] = {"date": day_str, "input": 0, "output": 0, "cache_read": 0, "total": 0, "calls": 0}
                    inp = usage.get("input", 0)
                    out = usage.get("output", 0)
                    daily[day_str]["input"] += inp
                    daily[day_str]["output"] += out
                    daily[day_str]["cache_read"] += usage.get("cacheRead", 0)
                    daily[day_str]["total"] += inp + out + usage.get("cacheRead", 0) + usage.get("cacheWrite", 0)
                    daily[day_str]["calls"] += 1
        except Exception:
            continue
    return sorted(daily.values(), key=lambda x: x["date"])


def render_token_stats():
    """Render the Token Statistics tab."""
    # Build directory fingerprint for cache invalidation
    file_info = []
    for f in _session_files(LOG_DIR):
        try:
            file_info.append((os.path.basename(f), os.path.getsize(f)))
        except OSError:
            pass
    fingerprint = str(sorted(file_info))

    stats = scan_token_stats(fingerprint)

    if not stats:
        st.info("暂无 Token 使用数据。请确保 sessions 目录下有含有 usage 字段的会话日志。")
        return

    today = datetime.now(tz=TZ).date()
    day7_start  = today - timedelta(days=6)
    day30_start = today - timedelta(days=29)

    def sum_period(predicate):
        totals = {"total": 0, "output": 0, "input": 0, "cache_read": 0, "calls": 0}
        for row in stats:
            d = date_type.fromisoformat(row["date"])
            if predicate(d):
                totals["total"]      += row["total"]
                totals["output"]     += row["output"]
                totals["input"]      += row["input"]
                totals["cache_read"] += row["cache_read"]
                totals["calls"]      += row["calls"]
        return totals

    today_t = sum_period(lambda d: d == today)
    week_t  = sum_period(lambda d: day7_start <= d <= today)
    month_t = sum_period(lambda d: day30_start <= d <= today)
    all_t   = sum_period(lambda d: True)

    # Top metric cards — show total, with output as sub-caption
    c1, c2, c3, c4 = st.columns(4)
    for col, label, t in [
        (c1, "今日", today_t),
        (c2, "近 7 天", week_t),
        (c3, "近 30 天", month_t),
        (c4, "全部时间", all_t),
    ]:
        with col:
            st.metric(f"{label} Total Tokens", _abbr(t['total']))
            st.caption(f"out:{t['output']:,}  ·  API {t['calls']} 次")

    st.divider()

    # Stacked bar chart: recent 30 calendar days (consistent with metric cards)
    day30_str = day30_start.isoformat()
    chart_data = [row for row in stats if row["date"] >= day30_str]
    if chart_data:
        df_chart = (
            pd.DataFrame(chart_data)[["date", "output", "input", "cache_read"]]
            .rename(columns={"output": "输出", "input": "输入", "cache_read": "缓存命中"})
            .set_index("date")
        )
        st.caption("近 30 天每日 Token 构成（输出 / 输入 / 缓存命中）")
        st.bar_chart(df_chart, y=["输出", "输入", "缓存命中"])

    st.divider()

    # Detail table sorted newest-first
    if stats:
        df = pd.DataFrame(list(reversed(stats)))
        df = df.rename(columns={
            "date": "日期",
            "input": "输入",
            "output": "输出",
            "cache_read": "缓存命中",
            "total": "总计",
            "calls": "调用次数",
        })
        # Format numeric columns with thousands separator
        for col in ["输入", "输出", "缓存命中", "总计", "调用次数"]:
            df[col] = df[col].apply(lambda x: f"{x:,}")
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_records(records: list[dict], translate: bool = False):
    """Dispatch each record to the appropriate renderer."""
    for record in records:
        if not isinstance(record, dict):
            continue
        rtype = record.get("type", "")

        if rtype != "message":
            render_meta_record(record)
            continue

        msg = record.get("message", {})
        role = msg.get("role", "")

        if role == "user":
            render_user_message(record)
        elif role == "assistant":
            render_assistant_message(record, translate=translate)
        elif role == "toolResult":
            render_tool_result(record)
        else:
            with st.expander(f"❓ 未知消息类型 [role={role}]", expanded=False):
                st.code(json.dumps(record, indent=2, ensure_ascii=False), language="json")


# ── Sidebar ──────────────────────────────────────────────────────────────────

SESSION_LIST_PAGE_SIZE = 5


def sidebar() -> str | None:
    """Render sidebar and return selected file path (or None)."""
    with st.sidebar:
        st.markdown("### 🦞 OpenClaw 日志")
        st.divider()

        log_mode = st.radio(
            "日志类型",
            ["💬 对话日志", "⏰ 定时任务"],
            horizontal=True,
            label_visibility="collapsed",
            key="log_mode",
        )
        scan_dir = LOG_DIR if log_mode == "💬 对话日志" else EXTRA_LOG_DIR
        if not scan_dir or (log_mode == "⏰ 定时任务" and not os.path.isdir(scan_dir)):
            st.warning("定时任务日志目录未配置或不存在")
            return None

        files = list_jsonl_files(scan_dir)
        if not files:
            st.warning(f"在 `{LOG_DIR}` 中未找到 `.jsonl` 文件")
            return None

        # Type filter (only for conversation logs where all three types coexist)
        file_types: dict[str, str] = {}
        if log_mode == "💬 对话日志":
            for f in files:
                file_types[f["path"]] = detect_session_type(f["path"], f["mtime"])
            _FILTER_OPTIONS = ["全部", "🏠 主会话", "⏰ Cron", "🤖 子Agent"]
            _FILTER_MAP = {"全部": None, "🏠 主会话": "main", "⏰ Cron": "cron", "🤖 子Agent": "subagent"}
            type_filter = st.selectbox(
                "type_filter",
                _FILTER_OPTIONS,
                index=1,  # default to 🏠 主会话
                label_visibility="collapsed",
                key="type_filter",
            )
            filter_key = _FILTER_MAP[type_filter]
            if filter_key:
                files = [f for f in files if file_types.get(f["path"]) == filter_key]

        total = len(files)
        total_pages = max(1, -(-total // SESSION_LIST_PAGE_SIZE))
        sp = st.session_state.get("session_page", 1)
        sp = max(1, min(sp, total_pages))

        st.subheader(f"会话列表（{total}）")

        # Pagination controls — only shown when there's more than one page
        if total_pages > 1:
            cols = st.columns([1, 2, 1])
            with cols[0]:
                if st.button("◀", disabled=(sp <= 1), use_container_width=True, key="sp_prev"):
                    st.session_state.session_page = sp - 1
                    st.rerun()
            with cols[1]:
                st.markdown(
                    f"<div style='text-align:center;font-size:0.76rem;color:#888;padding-top:0.4rem'>"
                    f"{sp} / {total_pages} 页</div>",
                    unsafe_allow_html=True,
                )
            with cols[2]:
                if st.button("▶", disabled=(sp >= total_pages), use_container_width=True, key="sp_next"):
                    st.session_state.session_page = sp + 1
                    st.rerun()

        start = (sp - 1) * SESSION_LIST_PAGE_SIZE
        page_files = files[start: start + SESSION_LIST_PAGE_SIZE]

        options = {f["name"]: f["path"] for f in page_files}
        labels = list(options.keys())

        def label_with_time(name):
            f = next(x for x in page_files if x["name"] == name)
            mtime_str = datetime.fromtimestamp(f["mtime"], tz=TZ).strftime("%m-%d %H:%M")
            badge = _TYPE_BADGE.get(file_types.get(f["path"], ""), "")
            prefix = f"[{badge}] " if badge else ""
            return f"{prefix}{_fmt_file_label(name)}  [{mtime_str}]"

        selected_label = st.radio(
            "Select session",
            labels,
            format_func=label_with_time,
            label_visibility="collapsed",
        )

        st.divider()

        auto_refresh = st.toggle("🔄 自动刷新（1s）", value=True)
        refresh_interval = 1
        translate = st.toggle("🌐 中文翻译思考过程", value=True)
        page_size = 10

        return options.get(selected_label), auto_refresh, refresh_interval, page_size, translate




# ── Main ─────────────────────────────────────────────────────────────────────

def render_session(page_size: int):
    cache = st.session_state.file_cache
    records = cache["records"]
    total = len(records)
    fully_loaded = cache["fully_loaded"]

    page = st.session_state.get("page", 1)
    # How many pages are covered by loaded records; add 1 if there are more to load
    loaded_pages = max(1, -(-total // page_size))
    max_page = loaded_pages if fully_loaded else loaded_pages + 1
    page = min(page, max_page)

    # Newest-first slice
    reversed_records = list(reversed(records))
    start = (page - 1) * page_size
    visible = reversed_records[start: start + page_size]

    new_count = st.session_state.get("last_new_count", 0)

    # Pagination controls
    cols = st.columns([1, 2, 1])
    with cols[0]:
        st.markdown("<style>[data-testid='column']:nth-child(1) div[data-testid='stButton'] button{width:100%}</style>", unsafe_allow_html=True)
        if st.button("◀ 更新", disabled=(page <= 1), use_container_width=True):
            st.session_state.page = page - 1
            st.rerun()
    with cols[1]:
        st.markdown(
            f"<div style='text-align:center;font-size:0.76rem;color:#888;padding-top:0.4rem'>"
            f"第 {page}/{max_page} 页 · {start + 1}–{min(start + page_size, total)} / {total} 条"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[2]:
        at_last_loaded_page = page >= loaded_pages
        older_label = "⏳ 加载更多" if (at_last_loaded_page and not fully_loaded) else "更早 ▶"
        if st.button(older_label, disabled=(page >= max_page), use_container_width=True):
            if at_last_loaded_page and not fully_loaded:
                st.session_state.file_cache = load_older_chunk(cache)
            st.session_state.page = page + 1
            st.rerun()

    render_records(visible, translate=st.session_state.get("translate", True))



def main():
    result = sidebar()
    if result and result[1]:  # auto_refresh on
        st_autorefresh(interval=1000, key="autorefresh")

    if result is None:
        st.info("未找到会话文件，请检查 LOG_DIR 配置。")
        return

    selected_path, auto_refresh, refresh_interval, page_size, translate = result
    st.session_state.translate = translate

    # Reset session list page when switching log mode or type filter
    new_mode = st.session_state.get("log_mode")
    new_filter = st.session_state.get("type_filter", "全部")
    if (st.session_state.get("_prev_log_mode") != new_mode
            or st.session_state.get("_prev_type_filter") != new_filter):
        st.session_state.session_page = 1
        st.session_state._prev_log_mode = new_mode
        st.session_state._prev_type_filter = new_filter

    if not selected_path:
        st.info("请从左侧选择一个会话。")
        return

    # Header
    rel = os.path.relpath(selected_path, LOG_DIR)
    st.markdown(f"#### 🦞 `{rel}`")
    st.divider()

    # Tail-first load: on file change do a fast tail load; on rerun just append new lines
    cache = st.session_state.get("file_cache", {})
    if cache.get("path") != selected_path:
        st.session_state.file_cache = tail_load(selected_path)
        st.session_state.page = 1
        st.session_state.last_new_count = 0
    else:
        updated, new_count = append_new_lines(cache)
        st.session_state.file_cache = updated
        st.session_state.last_new_count = new_count

    tab_log, tab_stats = st.tabs(["💬 会话日志", "📊 Token 统计"])

    with tab_log:
        if not st.session_state.file_cache["records"]:
            st.warning("文件为空或不包含有效的 JSON 行。")
        else:
            render_session(page_size)

    with tab_stats:
        render_token_stats()


if __name__ == "__main__":
    main()
