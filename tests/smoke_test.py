"""
MotoChat 冒烟测试 — 遍历所有 API 端点，检查是否返回预期状态码。

用法: python tests/smoke_test.py [admin_password]
默认尝试 admin123。
"""
import sys
import os
import json
import requests

BASE = "http://127.0.0.1:7860"
ADMIN_PW = sys.argv[1] if len(sys.argv) > 1 else "admin123"

# 本机测试必须绕过系统代理，否则会被代理拦截返回 502
SESSION = requests.Session()
SESSION.trust_env = False
SESSION.proxies = {}

results = []


def check(name, method, path, expect, **kw):
    url = BASE + path
    try:
        r = SESSION.request(method, url, timeout=30, **kw)
    except Exception as e:
        results.append((name, method, path, "EXC", str(e)[:90]))
        return None
    ok = (r.status_code in expect) if isinstance(expect, (list, tuple, set)) else (r.status_code == expect)
    body = ""
    if not ok:
        try:
            body = json.dumps(r.json(), ensure_ascii=False)[:200]
        except Exception:
            body = (r.text or "")[:200]
    results.append((name, method, path, r.status_code if ok else f"{r.status_code} != {expect}", body))
    return r


# ---------- 匿名可访问 ----------
check("健康检查", "GET", "/api/health", 200)
check("登录页", "GET", "/login", 200)
check("根路径(未登录->登录页)", "GET", "/", 200)
check("admin页(未登录->跳转)", "GET", "/admin", [200, 302])
check("favicon", "GET", "/favicon.ico", 204)
check("静态CSS", "GET", "/static/style.css", 200)
check("匿名访问需鉴权接口", "GET", "/api/config", 401)

# ---------- 登录 ----------
r = check("管理员登录", "POST", "/api/auth/login", 200,
          json={"username": "admin", "password": ADMIN_PW})
if not r or not r.ok or not r.json().get("success"):
    print("!! 管理员登录失败，无法继续鉴权测试。")
    print("   请传入正确密码: python tests/smoke_test.py <admin_password>")
    for row in results:
        print(row)
    sys.exit(2)

check("当前用户", "GET", "/api/auth/me", 200)
check("错误密码登录", "POST", "/api/auth/login", 401,
      json={"username": "admin", "password": "__wrong__"})
check("普通用户错误密码", "POST", "/api/auth/login", 401,
      json={"username": "user", "password": "__wrong__"})

# ---------- 管理端读接口 ----------
check("角色列表", "GET", "/api/personas", 200)
check("角色详情", "GET", "/api/personas/MONO", 200)
check("角色提示词", "GET", "/api/personas/MONO/prompt", 200)
check("角色头像", "GET", "/api/personas/MONO/avatar", [200, 302])
# requests 默认跟随 302 -> 拿到内置默认头像图片，故为 200
check("角色头像(默认回退)", "GET", "/api/personas/__nope__/avatar", [200, 302])
check("非法角色名", "GET", "/api/personas/..%2F..%2Fconfig", [400, 404])
check("统计", "GET", "/api/stats", 200)
check("日志", "GET", "/api/logs?limit=5", 200)
check("配置读取", "GET", "/api/config", 200)
check("管理员token用量", "GET", "/api/admin/token-usage", 200)
check("token用量", "GET", "/api/token-usage", 200)
check("用户列表", "GET", "/api/admin/users", 200)
check("用户API配置列表", "GET", "/api/admin/users/api-configs", 200)
check("用户消息", "GET", "/api/admin/users/user/messages?limit=5", 200)
check("用户记忆", "GET", "/api/admin/users/user/memory", 200)
check("用户角色详情", "GET", "/api/admin/users/user/persona/MONO", 200)
check("用户API配置", "GET", "/api/admin/users/user/api-config", 200)
check("性格库", "GET", "/api/admin/persona-library", 200)
check("会话列表", "GET", "/api/conversations", 200)
check("历史记录", "GET", "/api/history/admin?limit=5", 200)
check("历史记录(旧路径)", "GET", "/api/history/admin?limit=5", 200)
check("用户首页统计", "GET", "/api/user/home-stats", 200)
check("用户角色库", "GET", "/api/user/personas", 200)
check("待审批", "GET", "/api/user/pending-overrides", 200)
check("性格库(用户)", "GET", "/api/user/persona-library", 200)
check("通知", "GET", "/api/conversations/notifications/admin", 200)
check("通知(旧路径)", "GET", "/api/notifications/admin", [200, 307])
check("路径穿越(traversal)", "GET", "/api/personas/../../config/config.json", [400, 404])

# ---------- 写接口（用不产生副作用或可回滚的参数） ----------
check("配置保存(非法路径)", "POST", "/api/config/save", 400,
      json={"evil.path": 1})
check("配置保存(掩码key)", "POST", "/api/config/save", 400,
      json={"llm.api_key": "***abcd"})
check("配置保存(类型错误)", "POST", "/api/config/save", 400,
      json={"llm.max_tokens": "not-a-number"})
check("修改不存在的用户", "POST", "/api/admin/users/update", 404,
      json={"username": "__no_such_user__"})
check("删除不存在的用户", "POST", "/api/admin/users/delete", 404,
      json={"username": "__no_such_user__"})
check("创建重名用户", "POST", "/api/admin/users/create", 400,
      json={"username": "user", "password": "abcd1234"})
check("创建非法用户名", "POST", "/api/admin/users/create", 400,
      json={"username": "../evil", "password": "abcd1234"})
check("set-persona 路径穿越", "POST", "/api/admin/users/set-persona", 400,
      json={"username": "user", "persona": "../../config"})
check("角色切换(不存在)", "POST", "/api/personas/switch", 404,
      json={"name": "__nope__"})
check("创建角色(非法名)", "POST", "/api/personas/create", 400,
      json={"name": "../evil"})
check("删除角色(不存在)", "POST", "/api/personas/__nope__/delete", 404, json={})
# 用空文本触发服务端本地短路（clean_text("") 为空 → 直接返回失败），
# 避免装好 fish-audio-sdk 后冒烟测试真的去调外部 TTS API（消耗额度且结果不确定）
check("TTS(空文本本地拒绝)", "POST", "/api/chat/tts", [500, 503], json={"text": ""})
check("图片识别(空文件)", "POST", "/api/chat/images/recognize", 400,
      files={"file": ("a.jpg", b"", "image/jpeg")})
check("图片识别(非图片)", "POST", "/api/chat/images/recognize", 415,
      files={"file": ("a.txt", b"hello world", "text/plain")})
check("AI生成性格(空参数)", "POST", "/api/chat/generate-personality", 400, json={})

# ---------- 登出 ----------
check("登出", "POST", "/api/auth/logout", 200)
check("登出后访问", "GET", "/api/config", 401)

# ---------- 输出 ----------
fails = [x for x in results if x[3] != 200 and not str(x[3]).startswith(("200", "204", "307", "400", "401", "404", "415", "500", "503"))]
print(f"\n共 {len(results)} 项检查\n")
bad = []
for name, method, path, status, body in results:
    mark = "OK "
    if isinstance(status, str) and "!=" in status:
        mark = "FAIL"
    elif status == "EXC":
        mark = "EXC "
    print(f"[{mark}] {name:<28} {method:<6} {path:<50} -> {status} {body}")
    if mark != "OK ":
        bad.append(name)

print(f"\n问题项 {len(bad)}: {bad}")
