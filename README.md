# MotoChat

AI 角色聊天平台 — 多角色、多用户、多模态，支持本地运行和服务器部署。

```
双击 run.bat → 浏览器打开 → 配好 API key → 开始聊天
```

## 能力

- **角色引擎** — 预设角色开箱即用，支持自创、AI 生成、重命名、性格覆盖
- **双层记忆** — 短期对话上下文 + LLM 自动摘要核心记忆，跨会话持久化
- **多模态** — 文本对话、图片识别（Vision）、语音合成（TTS，可选）
- **用户体系** — 管理员面板 + 用户端分离，每人独立 API key 和数据空间
- **通信方式** — REST、SSE 流式输出、WebSocket 实时双向
- **自动化** — 定时主动消息、提醒通知、静默时段、指令系统
- **性格模板库** — 管理员创建公开模板，用户一键应用

## 内置角色

| 角色 | 定位 | 风格 |
|------|------|------|
| MONO | 贴心 AI 伙伴 | 温暖细腻、善解人意、偶有俏皮 |
| SAGE | 博学 AI 顾问 | 睿智沉稳、擅长引导思考、温文尔雅 |

## 快速开始

1. [Python 3.11+](https://www.python.org/downloads/)
2. 双击 `run.bat`，浏览器打开 http://127.0.0.1:7860
3. 设置 → LLM API 填入 API Key（支持 OpenAI 兼容接口）
4. 首次启动自动生成配置、数据库和角色文件

默认账号：
- 管理员 `admin` / `admin123`
- 用户 `user` / `user1234`

## 部署到服务器

### 一行启动

```bash
git clone https://github.com/newboyfun/MotoChat.git && cd MotoChat
pip install -r requirements.txt
MOTOCHAT_HOST=0.0.0.0 python src/run.py
```

### systemd 自启

```ini
[Unit]
Description=MotoChat
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/MotoChat
Environment=MOTOCHAT_HOST=0.0.0.0
ExecStart=/opt/MotoChat/.venv/bin/python src/run.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now motochat
```

服务器需放行 7860 端口。

## 目录结构

```
├── requirements.txt
├── run.bat
└── src/
    ├── run.py                # 进程锁、目录初始化、启动 uvicorn
    ├── app/
    │   ├── server.py         # FastAPI 路由、WebSocket、SSE
    │   ├── database.py       # SQLite 持久化
    │   └── services/         # 聊天、LLM、记忆、视觉、TTS 等
    ├── data/
    │   ├── config.py         # 配置加载/校验/热保存
    │   └── avatars/          # 内置角色
    └── static/               # 前端 SPA
```