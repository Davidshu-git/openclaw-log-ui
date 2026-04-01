# OpenClaw 日志查看器

用于实时浏览和调试 [OpenClaw](https://openclaw.ai) Agent 会话日志的轻量级 Streamlit Web UI。

## 功能特性

- 📂 列出所有会话文件（含压缩前的 `.reset` / 已清理的 `.deleted` 文件），按最后修改时间倒序排列
- 🏷️ 会话类型自动识别并过滤：主会话 / Cron 定时任务 / 子 Agent
- 💬 用户/助手消息以聊天气泡形式渲染
- 💭 可展开的**思考过程**，支持一键切换中文翻译
- 🛠️ 工具调用折叠展示，参数语法高亮
- ✅/❌ 工具结果自动标注状态，错误内容红色高亮
- ⚡ **尾部优先加载**——打开时只读最后 256KB，旧内容按需加载
- 🔄 **1 秒自动刷新**——增量追加新行，无需重载整个文件
- 🌐 Agent 推理文本**中文翻译**（Google Translate，结果缓存，不重复翻译）
- 📋 Telegram 消息元数据自动折叠，正文始终可见
- 📊 **Token 统计**——按日汇总 input / output / cacheRead / cacheWrite，展示今日 / 近 7 天 / 近 30 天 / 全部时间四个维度
- nginx 反向代理，开启 gzip 压缩，适合远程访问

## 快速开始

```bash
git clone https://github.com/Davidshu-git/openclaw-log-ui.git
cd openclaw-log-ui

# 设置会话日志目录（默认为 ~/.openclaw/agents/main/sessions）
export OPENCLAW_SESSIONS_DIR=~/.openclaw/agents/main/sessions

UID=$(id -u) GID=$(id -g) docker compose up -d --build
# 访问 http://localhost:8501
```

## 配置说明

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `OPENCLAW_SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | 会话日志目录（host 路径） |
| `OPENCLAW_CRON_DIR` | `~/.openclaw/cron/runs` | 定时任务日志目录（可选，见 compose 文件） |
| `LOG_DIR` | `/sessions` | 容器内挂载路径 |
| `CHUNK_BYTES` | `262144`（256KB） | 每次读取的块大小 |

## 目录结构

```
openclaw-log-ui/
├── app.py              # Streamlit 应用主文件
├── requirements.txt    # Python 依赖
├── Dockerfile          # 基于 python:3.12-slim
├── docker-compose.yml  # streamlit + nginx 双服务
├── nginx/
│   └── default.conf    # gzip 压缩 + 静态资源缓存 + WebSocket 代理
└── .streamlit/
    └── config.toml     # Streamlit 服务配置
```
