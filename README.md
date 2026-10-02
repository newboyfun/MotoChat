# MotoChat

一个**本地运行的网页版 AI 角色聊天应用**。创建多个 AI 角色（人设），与它们聊天，支持流式回复、图片识别、语音合成、自动消息和定时提醒。后端对接任意 **OpenAI 兼容 API**（官方 OpenAI、DeepSeek、通义、本地 Ollama / vLLM 等均可）。

数据全部保存在本机，界面通过浏览器访问，不需要注册任何账号。

## 功能

- **多角色人设** —— 内置角色模板，可自建/上传头像；管理员可为每个用户单独设置角色覆盖
- **SSE 流式对话** —— 逐字输出，前端 50ms 批量渲染；客户端断开可取消生成，不占用线程
- **WebSocket 实时通道** —— 聊天与通知推送，定向投递（不会串台）
- **双层记忆** —— 短期记忆（最近 N 轮对话）+ 核心记忆（用 LLM 自动摘要）
- **图片识别** —— 发送图片由 Vision 模型识别后回复
- **语音合成** —— 可选，接入 Fish Audio；未配置则自动跳过
- **自动消息 / 定时提醒** —— 角色主动找你，支持静默时段
- **命令系统** —— `/diary` 日记、`/state` 状态、`/list` 备忘录、`/mem` 查看记忆、`/clear` 清空记忆
- **多用户** —— 管理员端 + 普通用户端两套界面，支持权限、审批、Token 用量统计
- **安全** —— PBKDF2-SHA256 密码哈希、HttpOnly Cookie 认证、登录限流、API Key 只回显掩码

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ / FastAPI / Uvicorn |
| 数据库 | SQLAlchemy + SQLite |
| LLM | OpenAI 兼容 API（流式 `run_in_executor` + `asyncio.Queue`） |
| 前端 | 原生 JS（无框架，两个 IIFE 单页应用）+ CSS 变量暗色主题 |

## 快速开始

### Windows（推荐）

双击 `run.bat`。脚本会自动创建虚拟环境、安装依赖并启动服务。

### 手动

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python src/run.py
```

启动后浏览器打开 **http://127.0.0.1:7860**。

> **首次启动会打印一次管理员随机密码**（形如 `账号 [admin] 已生成随机密码: xxxx`），请立刻记下，登录后尽快修改。密码只显示这一次。

## 配置

三种方式，优先级从高到低：

1. **环境变量**（`.env`）—— 复制 `.env.example` 为 `.env` 并填写，`llm.api_key` 等字段会被覆盖
2. **管理端界面** —— 登录后进「系统设置」直接改，最方便
3. **`config/config.json`** —— 首次启动自动生成

> `.env` 和 `config/` 都已被 `.gitignore` 忽略，**不要**把它们提交到 Git。

### 接本地模型（如 Ollama）

```
MOTOCHAT_LLM_BASE_URL=http://localhost:11434/v1
MOTOCHAT_LLM_MODEL=qwen2.5:7b
MOTOCHAT_LLM_API_KEY=ollama        # 本地服务随便填，占位即可
```

> 注意：本地推理模型（如 gemma 系列）会先消耗 token 做隐藏推理，`max_tokens` 太小会导致回复为空，建议 1500 以上。

## 目录结构

```
new/
├── run.bat                 # 启动入口
├── requirements.txt        # 运行依赖
├── requirements-dev.txt    # 测试依赖
├── src/                    # 程序代码
│   ├── run.py              # 主入口：进程锁 / 初始化 / 启动 uvicorn
│   ├── app/                # FastAPI 应用
│   │   ├── server.py       # 应用组装、生命周期、服务注入
│   │   ├── database.py     # SQLAlchemy 模型
│   │   ├── api/            # 路由：auth / chat / ws / personas / admin / config ...
│   │   ├── core/           # 依赖注入、错误处理、流式合并、工具
│   │   └── services/       # 业务服务：chat / llm / memory / vision / tts / reminder / autosend
│   ├── static/             # 前端 + 图片 + prompt 模板
│   └── data/               # 配置模块 + 内置角色模板
├── tests/                  # 冒烟 / 回归 / 单测
├── userdata/               # 运行时数据（不入库，首次启动自动创建）
└── config/                 # 配置（不入库）
```

## 测试

测试需要服务已在运行（`python src/run.py`）。

```bash
pip install -r requirements-dev.txt

python tests/smoke_test.py admin123        # 全接口冒烟，期望「问题项 0」
python tests/regression_test.py admin123   # 缺陷回归，期望全 PASS
python -m pytest tests/test_config.py -q   # 配置 / 密码哈希单测
```

> 参数是管理员密码。`admin123` 只是本地测试常用的默认值，请替换成你自己的。

## 安全说明

- 认证：HttpOnly Cookie（`kc_token`）或 `Authorization: Bearer` header，**不支持 URL query token**（防止访问日志泄露凭据）
- 密码：PBKDF2-SHA256，31 万次迭代；默认密码缺失时生成随机密码而非弱口令
- 限流：登录 / 验密 / 查看 key 为 10 次/分钟，聊天接口 30 次/分钟
- **提交前务必确认** `config/`、`.env`、`userdata/` 都未被跟踪：
  ```bash
  git status --short
  git diff --cached | grep -i "api_key\|password\|secret"
  ```

## License

[MIT](LICENSE) © 2026 newboyfun
