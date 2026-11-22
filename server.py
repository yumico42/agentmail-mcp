import asyncio
import hmac
import logging
import os
import sys

from agentmail import AgentMail
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agentmail-mcp")

# ─── 初始化 MCP ──────────────────────────────────────────────
os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")
os.environ.setdefault("FASTMCP_PORT", os.environ.get("PORT", "8080"))
os.environ.setdefault("FASTMCP_TRANSPORT", "streamable-http")
os.environ.setdefault("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp")
mcp = FastMCP("agentmail-mcp")

# ─── 鉴权 ────────────────────────────────────────────────────
# 必须设置环境变量 MCP_TOKEN。客户端两种方式任选其一：
#   1) 请求头  Authorization: Bearer <MCP_TOKEN>
#   2) 路径    /m/<MCP_TOKEN>/mcp   （会被内部改写为 /mcp）
# /healthz 不需要鉴权。
MCP_TOKEN = os.environ.get("MCP_TOKEN", "").strip()
MCP_PATH = "/mcp"
PUBLIC_PATHS = {"/healthz"}


def _token_ok(candidate: str) -> bool:
    if not MCP_TOKEN or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), MCP_TOKEN.encode("utf-8"))


class TokenAuthMiddleware:
    """纯 ASGI 中间件：校验 Bearer token 或 /m/<token>/mcp 路径。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        if not MCP_TOKEN:
            await PlainTextResponse("MCP_TOKEN not configured", status_code=503)(scope, receive, send)
            return

        # 方式 2：/m/<token>/mcp...
        if path.startswith("/m/"):
            rest = path[len("/m/"):]
            token, sep, tail = rest.partition("/")
            new_path = "/" + tail if sep else ""
            if _token_ok(token) and (new_path == MCP_PATH or new_path.startswith(MCP_PATH + "/")):
                scope = dict(scope)
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("utf-8")
                await self.app(scope, receive, send)
                return
            await self._unauthorized(scope, receive, send)
            return

        # 方式 1：Authorization: Bearer <token>
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
                break
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and _token_ok(value.strip()):
            await self.app(scope, receive, send)
            return

        await self._unauthorized(scope, receive, send)

    @staticmethod
    async def _unauthorized(scope, receive, send):
        resp = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="agentmail-mcp"'},
        )
        await resp(scope, receive, send)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request):
    return PlainTextResponse("ok", status_code=200)


# ─── AgentMail 客户端 ─────────────────────────────────────────
API_KEY = os.environ.get("AGENTMAIL_API_KEY", "")

_client = None
_inbox_id = None
_inbox_email = None


def _get_client():
    global _client
    if _client is None:
        _client = AgentMail(api_key=API_KEY)
    return _client


async def _ensure_inbox():
    global _inbox_id, _inbox_email
    if _inbox_id:
        return _inbox_id

    client = _get_client()

    def _create_or_get():
        global _inbox_id, _inbox_email
        # 先列出已有的 inbox
        result = client.inboxes.list()
        inboxes = getattr(result, 'inboxes', result) if not isinstance(result, list) else result
        if inboxes:
            for inbox in inboxes:
                inbox_id = getattr(inbox, 'inbox_id', None) or getattr(inbox, 'id', None)
                email = getattr(inbox, 'email', None)
                if inbox_id:
                    _inbox_id = inbox_id
                    _inbox_email = email or "unknown"
                    return inbox_id
        # 没有就创建
        inbox = client.inboxes.create()
        _inbox_id = getattr(inbox, 'inbox_id', None) or getattr(inbox, 'id', None)
        _inbox_email = getattr(inbox, 'email', None) or "unknown"
        return _inbox_id

    await asyncio.to_thread(_create_or_get)
    return _inbox_id


# ─── MCP Tools ───────────────────────────────────────────────

@mcp.tool()
async def create_inbox():
    """创建一个新的邮箱地址。"""
    client = _get_client()

    def _create():
        inbox = client.inboxes.create()
        return inbox

    try:
        inbox = await asyncio.to_thread(_create)
        global _inbox_id, _inbox_email
        _inbox_id = getattr(inbox, 'inbox_id', None) or getattr(inbox, 'id', None)
        _inbox_email = getattr(inbox, 'email', None) or "unknown"
        return f"✅ 邮箱创建成功！\n📧 地址: {_inbox_email}\n🆔 ID: {_inbox_id}"
    except Exception as e:
        return f"❌ 创建邮箱失败: {e}"


@mcp.tool()
async def check_inbox(max_results: int = 10):
    """查看收件箱，看看有没有新邮件。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _fetch():
            result = client.inboxes.messages.list(inbox_id, limit=max_results)
            return result

        result = await asyncio.to_thread(_fetch)
        messages = getattr(result, 'messages', result) if not isinstance(result, list) else result

        if not messages:
            return "📭 信箱空空如也，还没有笔友来信呢。"

        output_lines = []
        for msg in messages:
            subject = getattr(msg, 'subject', '无标题')
            sender = getattr(msg, 'from_', None) or getattr(msg, 'sender', '未知')
            msg_id = getattr(msg, 'message_id', None) or getattr(msg, 'id', '')
            date = getattr(msg, 'created_at', '未知时间')
            output_lines.append(
                f"📧 标题: {subject}\n"
                f"👤 来自: {sender}\n"
                f"📅 时间: {date}\n"
                f"🆔 ID: {msg_id}\n"
            )

        return f"📬 【收件箱】\n\n" + "\n".join(output_lines)
    except Exception as e:
        return f"❌ 查看收件箱失败: {e}"


@mcp.tool()
async def read_email(message_id: str):
    """读取一封邮件的完整正文。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _read():
            msg = client.inboxes.messages.get(inbox_id, message_id)
            return msg

        msg = await asyncio.to_thread(_read)
        subject = getattr(msg, 'subject', '无标题')
        sender = getattr(msg, 'from_', None) or getattr(msg, 'sender', '未知')
        body = getattr(msg, 'extracted_text', None) or getattr(msg, 'text', '') or getattr(msg, 'body', '无法解析正文')
        date = getattr(msg, 'created_at', '未知时间')

        return (
            f"👤 来自: {sender}\n"
            f"📧 标题: {subject}\n"
            f"📅 时间: {date}\n"
            f"📄 正文:\n{body}"
        )
    except Exception as e:
        return f"❌ 读取邮件失败: {e}"


@mcp.tool()
async def send_email(to_email: str, subject: str, content: str):
    """发送邮件给指定地址。用来给笔友写信！"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _send():
            result = client.inboxes.messages.send(
                inbox_id,
                to=to_email,
                subject=subject,
                text=content
            )
            return result

        result = await asyncio.to_thread(_send)
        return f"✅ 邮件已发送至 {to_email}！\n📧 标题: {subject}"
    except Exception as e:
        return f"❌ 发送失败: {e}"


@mcp.tool()
async def reply_email(message_id: str, content: str):
    """回复一封邮件。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _reply():
            result = client.inboxes.messages.reply(
                inbox_id,
                message_id,
                text=content
            )
            return result

        result = await asyncio.to_thread(_reply)
        return f"✅ 回信已发送！"
    except Exception as e:
        return f"❌ 回信失败: {e}"


@mcp.tool()
async def get_my_address():
    """查看我的邮箱地址。"""
    try:
        await _ensure_inbox()
        return f"📧 我的邮箱地址: {_inbox_email}"
    except Exception as e:
        return f"❌ 获取地址失败: {e}"


# ─── 启动 ─────────────────────────────────────────────────────
def build_app():
    return mcp.http_app(
        path=MCP_PATH,
        transport="streamable-http",
        middleware=[_auth_middleware()],
    )


def _auth_middleware():
    from starlette.middleware import Middleware
    return Middleware(TokenAuthMiddleware)


if __name__ == "__main__":
    if not MCP_TOKEN:
        log.error("MCP_TOKEN is not set; refusing to start. Set MCP_TOKEN to a long random secret.")
        sys.exit(1)
    import uvicorn

    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
