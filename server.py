import asyncio
import hmac
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from agentmail import AgentMail
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agentmail-mcp")

# ─── 初始化 MCP ──────────────────────────────────────────────
DEFAULT_PORT = 8000  # 与 Dockerfile 的 EXPOSE / ENV PORT 保持一致
PORT = int(os.environ.get("PORT") or DEFAULT_PORT)
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
# 可选：固定使用的共享邮箱。设置 INBOX_USERNAME 后会使用/创建 <INBOX_USERNAME>@<INBOX_DOMAIN>
# （INBOX_DOMAIN 默认 agentmail.to）；不设置则使用账号下第一个 inbox，没有就新建。
INBOX_USERNAME = os.environ.get("INBOX_USERNAME", "").strip()
INBOX_DOMAIN = os.environ.get("INBOX_DOMAIN", "").strip()

# ─── 北京时间（固定 UTC+8，镜像里没有 tzdata，不依赖 zoneinfo）────
BEIJING_TZ = timezone(timedelta(hours=8), name="UTC+8")


def _fmt_time(value) -> str:
    """把 AgentMail 返回的时间（datetime 或 ISO 字符串）转成北京时间。"""
    if value is None or value == "":
        return "未知时间"
    dt = value
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    if not isinstance(dt, datetime):
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S") + " (北京时间)"


def _inbox_fields(inbox):
    inbox_id = getattr(inbox, 'inbox_id', None) or getattr(inbox, 'id', None)
    email = getattr(inbox, 'email', None) or (inbox_id if inbox_id and "@" in str(inbox_id) else None)
    return inbox_id, email


def _create_request(username=None, domain=None):
    from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest
    kwargs = {}
    if username:
        kwargs["username"] = username
    if domain:
        kwargs["domain"] = domain
    return CreateInboxRequest(**kwargs) if kwargs else None

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

    def _list_all():
        out, token = [], None
        for _ in range(20):  # 最多翻 20 页，防止死循环
            result = client.inboxes.list(page_token=token) if token else client.inboxes.list()
            items = getattr(result, 'inboxes', result) if not isinstance(result, list) else result
            out.extend(items or [])
            token = getattr(result, 'next_page_token', None)
            if not token:
                break
        return out

    def _create_or_get():
        global _inbox_id, _inbox_email
        inboxes = _list_all()
        if INBOX_USERNAME:
            # 指定了共享邮箱：按地址精确匹配
            want_user = INBOX_USERNAME.lower()
            want_domain = (INBOX_DOMAIN or "agentmail.to").lower()
            want = f"{want_user}@{want_domain}"
            for inbox in inboxes:
                inbox_id, email = _inbox_fields(inbox)
                if inbox_id and (str(email).lower() == want or str(inbox_id).lower() == want):
                    _inbox_id, _inbox_email = inbox_id, email or want
                    return _inbox_id
            inbox = client.inboxes.create(request=_create_request(INBOX_USERNAME, INBOX_DOMAIN or None))
            _inbox_id, email = _inbox_fields(inbox)
            _inbox_email = email or want
            log.info("created shared inbox %s", _inbox_email)
            return _inbox_id
        # 未指定：用第一个已有 inbox
        for inbox in inboxes:
            inbox_id, email = _inbox_fields(inbox)
            if inbox_id:
                _inbox_id, _inbox_email = inbox_id, email or "unknown"
                return inbox_id
        # 没有就创建
        inbox = client.inboxes.create(request=_create_request(None, INBOX_DOMAIN or None))
        _inbox_id, email = _inbox_fields(inbox)
        _inbox_email = email or "unknown"
        return _inbox_id

    await asyncio.to_thread(_create_or_get)
    return _inbox_id


# ─── MCP Tools ───────────────────────────────────────────────

@mcp.tool()
async def create_inbox(username: str = "", domain: str = ""):
    """创建一个新的（额外的）邮箱地址。不会替换共享/默认邮箱，收发信仍使用默认邮箱。"""
    client = _get_client()

    def _create():
        return client.inboxes.create(
            request=_create_request(username.strip() or None, domain.strip() or INBOX_DOMAIN or None)
        )

    try:
        inbox = await asyncio.to_thread(_create)
        new_id, new_email = _inbox_fields(inbox)
        return (
            f"✅ 邮箱创建成功！\n📧 地址: {new_email or 'unknown'}\n🆔 ID: {new_id}\n"
            f"ℹ️ 默认收发邮箱未改变。"
        )
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
            date = _fmt_time(getattr(msg, 'created_at', None) or getattr(msg, 'timestamp', None))
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
        date = _fmt_time(getattr(msg, 'created_at', None) or getattr(msg, 'timestamp', None))

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
        port=PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
