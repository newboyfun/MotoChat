# MotoChat 项目总结

> 本文档供 AI 助手快速了解项目结构、架构和修改规范。

## 项目概述

MotoChat 是一个本地网页版 AI 角色聊天应用。用户可以创建多个 AI 角色（人设），与角色进行对话，支持文字聊天、图片识别、语音合成、自动消息、定时提醒等功能。使用 OpenAI 兼容 API 作为 LLM 后端。

## 目录结构

```
new/                        # git 仓库根目录
├── run.bat                 # 启动入口（双击运行）
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量示例（复制为 .env 填真实值，.env 不提交）
├── .gitignore              # 已忽略：.venv/ userdata/ config/* logs/ *.bak .workbuddy/
│
├── src/                    # 程序本体（代码）——更新程序只替换此目录
│   ├── run.py              # 主入口：进程锁、服务初始化、建目录、启动 uvicorn
│   ├── app/
│   │   ├── server.py       # FastAPI 应用组装：生命周期、中间件、页面路由、服务注入
│   │   ├── database.py     # SQLAlchemy 数据库层（会话表、消息表、通知表）
│   │   ├── api/            # 路由模块（按业务拆分）
│   │   │   ├── auth.py         # 认证：登录/登出/改密/查看 key（含限流）
│   │   │   ├── chat.py         # 聊天：SSE 流式/命令/TTS/图片识别/AI 生成
│   │   │   ├── ws.py           # WebSocket：/ws/chat 实时双向 + 定向广播
│   │   │   ├── config_routes.py# 配置管理/统计/日志/Token 用量
│   │   │   ├── personas.py     # 角色管理（含头像上传/获取/重置）
│   │   │   ├── user_persona.py # 用户角色库
│   │   │   ├── admin.py        # 管理员用户管理
│   │   │   └── conversations.py# 会话/历史
│   │   ├── core/
│   │   │   ├── deps.py         # 依赖注入容器 + 认证依赖（单轨制全局状态）
│   │   │   ├── responses.py    # 统一错误处理
│   │   │   ├── streaming.py    # ChunkCoalescer：SSE/WS 共用的流式合并
│   │   │   └── utils.py        # 工具函数（路径校验、key 掩码、图片魔数识别）
│   │   └── services/
│   │       ├── chat.py     # 核心聊天服务：整合 LLM/记忆/图片/命令/表情
│   │       ├── llm.py      # LLM 服务：OpenAI API 调用、上下文管理、流式输出
│   │       ├── memory.py   # 记忆服务：短期记忆(JSON) + 核心记忆摘要
│   │       ├── vision.py   # 图片识别：OpenAI Vision API
│   │       ├── tts.py      # 语音合成：Fish Audio SDK（可选依赖，Session 复用）
│   │       ├── reminder.py # 提醒服务：定时提醒 → 写入通知表 + 广播
│   │       ├── autosend.py # 自动消息：定时主动发消息（静默时段跳过）
│   │       └── emoji.py    # 表情标签：EMOTION_TAGS 唯一定义源 + clean_message()
│   ├── static/
│   │   ├── index.html      # 管理员单页应用
│   │   ├── user.html       # 普通用户单页应用
│   │   ├── login.html      # 登录页（自带内联 <style>，不依赖 style.css）
│   │   ├── app-v1.js       # 管理员端 JS（IIFE，SSE 流式接收、页面路由）
│   │   ├── app-user.js     # 普通用户端 JS
│   │   ├── auth.js / avatar.js / theme.js / login.js
│   │   ├── style.css       # 全局样式（暗色主题，CSS 变量）
│   │   ├── assets/         # 内置图片（avatar-mono/sage/default、empty-chat、login-hero、bg-ambient）
│   │   ├── prompt_base.md  # 基础 system prompt（表情规则、回复风格）
│   │   └── prompts/        # 命令模板（diary.md, memory.md, state.md, list.md 等）
│   └── data/
│       ├── config.py       # 配置加载模块（JSON 读写、校验、深合并、密码哈希迁移）
│       └── avatars/        # 内置角色模板（MONO / SAGE），首次启动复制到 userdata/
│
├── tests/                  # 测试（进仓库）
│   ├── smoke_test.py       # 全接口冒烟（61 项，期望「问题项 0」）
│   ├── regression_test.py  # 缺陷回归（61 项，期望全 PASS）
│   └── test_config.py      # 配置/密码哈希单测（pytest，9 项）
│
├── userdata/               # 用户数据 —— 不进仓库，首次启动自动创建
│   ├── database/chat.db    # SQLite（会话、消息、通知）
│   ├── avatars/{NAME}/     # 角色：avatar.md + avatar.jpg + memory/ + emojis/
│   ├── voices/             # TTS 音频（每小时清理 24h 前的）
│   └── images/temp/        # 上传图片临时存储
│
├── config/                 # 配置 —— 不进仓库（含 API Key / 密码 / secret）
│   └── config.json         # 所有配置（API key、模型、行为、密码哈希等）
│
└── logs/                   # 运行日志 —— 不进仓库，run.py 自动创建
```

## 核心架构

### 请求流程

```
浏览器 → FastAPI (server.py)
  ├── GET /              → 按角色返回 index.html（管理员）或 user.html（普通用户）
  ├── GET /static/*      → 静态文件（JS/CSS/prompt 模板）
  ├── POST /api/chat/stream → SSE 流式聊天（api/chat.py）
  │     └── ChatService.handle_stream()
  │           ├── LLMService.get_response_stream()  → OpenAI API（流式）
  │           ├── MemoryService.add_conversation()   → 保存到 JSON
  │           └── database.save_message()            → 保存到 SQLite
  ├── POST /api/chat     → 普通聊天（非流式）
  ├── WebSocket /ws/chat  → WebSocket 聊天（api/ws.py，实时双向 + 广播）
  ├── GET/POST /api/config → 配置读写（api/config_routes.py）
  ├── GET /api/personas   → 角色列表
  ├── POST /api/auth/*    → 认证（api/auth.py）
  └── 其他 API（stats/logs/tts/images/notifications/admin/conversations）
```

### 关键服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `ChatService` | `chat.py` | 核心调度：接收消息 → 调 LLM → 存记忆 → 返回回复。支持流式和非流式 |
| `LLMService` | `llm.py` | OpenAI API 封装：上下文管理、`_build_messages()` 构建消息列表、`_filter()` 过滤 think 标签 |
| `MemoryService` | `memory.py` | 双层记忆：短期记忆（JSON 文件，最近 N 轮）+ 核心记忆（LLM 生成的摘要） |
| `VisionService` | `vision.py` | 图片识别：base64 编码 → OpenAI Vision API |
| `TTSService` | `tts.py` | 语音合成：Fish Audio SDK（可选，未安装则跳过），Session 复用 + 每小时清理过期文件 |
| `ReminderService` | `reminder.py` | 定时提醒：后台线程轮询到期提醒 → 调 ChatService 生成回复 → 广播通知 |
| `AutoSendService` | `autosend.py` | 自动消息：定时器触发 → 检查静默时段 → 调 ChatService 发送主动消息 |
| `emoji`（模块函数） | `emoji.py` | 表情标签：`EMOTION_TAGS`/`EMOTION_RE` 是全项目唯一定义源，`clean_message()` 是无状态清理函数 |
| `ChunkCoalescer` | `core/streaming.py` | 流式合并：SSE 与 WS 共用，阈值 24 字符 / 50ms（与前端 50ms 批量渲染对齐） |

### 全局状态（单轨制）

所有服务实例统一注册在 `app/core/deps.py` 的 `_services` 容器中：

```python
from app.core.deps import get_chat_service, get_tts_service, get_reminder_service, get_autosend_service
```

`server.py` 的 `inject()` 只做一件事：把服务写进这个容器。**不要**再在 `server.py` 或其他地方维护模块级的 `_chat` 全局变量（旧双轨制已废弃）。

### 数据库（SQLAlchemy + SQLite）

```python
# 三张表
Conversation  # 会话：id, title, avatar, created_at
Message       # 消息：id, conversation_id, role, content_type, content, created_at
Notification  # 通知：id, conversation_id, title, content, is_read, created_at
```

使用 `get_db()` context manager 管理 session，自动 commit/rollback/close。

### 配置系统

`data/config.py` 加载 `config/config.json`：
- 深合并默认值和用户配置
- 校验所有必填字段、类型、范围
- 支持 `Config(config_dir=...)` 指定配置目录
- 通过 `config.update("llm.model", "xxx")` 更新并自动保存
- 自动哈希旧格式明文密码（启动时）

### 前端

两个独立 SPA（无框架，原生 JS）：
- **管理员端** `index.html` + `app-v1.js`（IIFE，SSE 流式接收、页面路由、用户管理、系统设置）
- **普通用户端** `user.html` + `app-user.js`（IIFE，专注聊天）
- **路由**：通过 `.nav-btn` 的 `data-page` 属性切换页面
- **聊天**：SSE 流式接收（`fetch` + `ReadableStream`），50ms 批量渲染（不是每 token 都写 DOM）
- **缓存**：HTML 里的 `?v=` 参数控制 JS/CSS 缓存版本

## 安全模型

- **认证**：HttpOnly Cookie (`kc_token`) 或 `Authorization: Bearer` header，**不支持 URL query token**（防止 access_log 泄露凭据）
- **API Key**：后端**不下发明文**，只返回掩码；查看需走 `/api/auth/reveal-key`（密码确认）
- **限流**：`/login`、`/verify-password`、`/reveal-key` 同款滑动窗口限流（10 次/分钟）；聊天 API 单独限流（30 次/分钟）
- **密码**：PBKDF2-SHA256 31 万次迭代；默认密码缺失时生成随机密码打印到控制台（仅一次），不会重置回弱口令
- **SSE/WS 取消**：客户端断连时通过 `cancel_event` 通知 producer 停止 LLM 生成，防止线程池被占满

## 修改规范

### 修改代码时

1. **路径引用**：程序代码在 `src/`，用户数据在 `userdata/`，配置在 `config/`
   - `src/app/server.py` 的 `ROOT` 指向 `src/`
   - `os.path.dirname(ROOT)` 才是项目根目录 `new/`
   - 角色头像在 `userdata/avatars/{name}/avatar.md`
   - 记忆文件在 `userdata/avatars/{name}/memory/{user_id}/`
   - 数据库在 `userdata/database/chat.db`
   - 配置文件在 `config/config.json`

2. **添加新 API 端点**：在对应的 `src/app/api/*.py` 路由模块中添加
   - 服务通过 `from app.core.deps import get_chat_service` 等获取（**不要** import server 的全局变量）
   - 认证用 `Depends(require_user)` / `Depends(require_admin)` / `Depends(get_current_user)`

3. **添加新服务**：在 `src/app/services/` 中创建新文件
   - 在 `src/run.py` 的 `main()` 中实例化
   - 通过 `inject()` 注入到 `deps._services` 容器

4. **修改前端**：编辑 `src/static/` 下的文件
   - `app-v1.js` / `app-user.js` 都是 IIFE 包裹，所有函数在闭包内
   - 修改后更新对应 HTML 里的 `?v=` 缓存版本号
   - 大括号必须严格匹配，多了或少了会导致整个页面白屏

### 部署/备份

- **更新程序**：替换 `src/` 目录
- **备份数据**：备份 `userdata/` + `config/`（这两者不进 git）
- **首次运行**：双击 `run.bat` —— 自动建 `.venv`、装 `requirements.txt`、从 `src/data/avatars/`
  初始化内置角色到 `userdata/avatars/`、自动创建 `logs/` 与 `userdata/database/`
- **迁移**：复制整个 `new/` 目录（`userdata/` + `config/` 必须一起带走）

### 提交到 Git 的注意（重要）

`.gitignore` 已忽略：`.venv/`、`userdata/`、`config/*`、`logs/`、`backup-*/`、`outputs/`、
`.backup/`、`.workbuddy/`、`__pycache__/`、`*.pyc`、`*.bak`、`*.before-cleanup`、`.env`。

**务必**：`config/` 下任何形式的备份（`config.json.bak` 等）都含明文 API Key 与密码，
`*.bak` / `*.before-cleanup` 规则就是为了兜住它们 —— 历史上曾因只忽略 `config/config.json`、
漏掉 `config.json.bak` 而导致密钥会随提交公开泄露。提交前用
`git diff --cached | grep -i "api_key\|password\|secret"` 快速自查一遍。

### 注意事项

- **端口锁**：程序用 `socket.bind(19876)` 做单例检测，同时只能运行一个实例
- **异步模型**：LLM 调用是同步阻塞的（openai SDK），通过 `run_in_executor` 放到线程池
- **SSE 流**：生产者（LLM 线程）通过 `asyncio.run_coroutine_threadsafe` 往 `asyncio.Queue` 写数据（带 5s 超时背压），消费者（async generator）从队列读数据；客户端断连时 `cancel_event` 通知生产者停止
- **save_to_db 参数**：`ChatService.handle()` 和 `_chat()` 的 `save_to_db=False` 用于提醒/自动消息，防止混入对话历史
- **异常处理**：所有 bare `except: pass` 都应该加 `logger.debug()` 日志
- **Python 版本**：项目使用 Python 3.13（.venv），注意 `__pycache__` 缓存可能跨版本冲突
- **TTS 清理**：`TTSService.cleanup_old()` 由 lifespan 每小时调度一次，删除 24 小时前的 `voice_*.mp3`
- **角色头像**：`GET /api/personas/{name}/avatar` 统一入口——用户上传的 `userdata/avatars/{name}/avatar.jpg` 优先，无则 302 到 `src/static/assets/` 内置图（MONO/SAGE 专属，其余默认玻璃星球）。上传走 `POST`（multipart，Pillow 重编码 512x512 JPEG），重置走 `DELETE`。前端用 `avatar.js` 的 `MotoAvatars` 获取 URL，上传后调 `MotoAvatars.bust(name)` 刷新缓存
- **推理模型注意**：本地 gemma4 等推理模型会先消耗 token 做隐藏推理，`max_tokens` 太小会导致 `content` 为空（非流式调用至少给 1500+）
