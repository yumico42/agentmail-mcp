import asyncio
import os
from datetime import datetime

from agentmail import AgentMail
from mcp.server.fastmcp import FastMCP

# ─── 初始化 MCP ──────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8000))
mcp = FastMCP("agentmail-mcp", host="0.0.0.0", port=PORT)

# ─── AgentMail 客户端 ─────────────────────────────────────────
API_KEY = os.environ.get("AGENTMAIL_API_KEY", "")
INBOX_USERNAME = os.environ.get("INBOX_USERNAME", "yeqi")
INBOX_DOMAIN = os.environ.get("INBOX_DOMAIN", "agentmail.to")

_client = None
_inbox_id = None


def _get_client():
    global _client
    if _client is None:
        _client = AgentMail(api_key=API_KEY)
    return _client


async def _ensure_inbox():
    global _inbox_id
    if _inbox_id:
        return _inbox_id

    client = _get_client()

    def _create_or_get():
        inboxes = client.inboxes.list()
        for inbox in inboxes:
            if hasattr(inbox, 'email') and inbox.email == f"{INBOX_USERNAME}@{INBOX_DOMAIN}":
                return inbox.id
        inbox = client.inboxes.create(
            username=INBOX_USERNAME,
            domain=INBOX_DOMAIN
        )
        return inbox.id

    _inbox_id = await asyncio.to_thread(_create_or_get)
    return _inbox_id


# ─── MCP Tools ───────────────────────────────────────────────

@mcp.tool()
async def create_inbox(username: str = "", domain: str = "agentmail.to"):
    """创建一个新的邮箱地址。不传 username 则使用默认配置。"""
    client = _get_client()
    uname = username or INBOX_USERNAME
    dom = domain or INBOX_DOMAIN

    def _create():
        inbox = client.inboxes.create(username=uname, domain=dom)
        return inbox

    try:
        inbox = await asyncio.to_thread(_create)
        global _inbox_id
        _inbox_id = inbox.id
        return f"✅ 邮箱创建成功！\n📧 地址: {inbox.email}\n🆔 ID: {inbox.id}"
    except Exception as e:
        return f"❌ 创建邮箱失败: {e}"


@mcp.tool()
async def check_inbox(max_results: int = 10):
    """查看收件箱，看看有没有新邮件。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _fetch():
            threads = client.threads.list(inbox_id=inbox_id)
            return threads

        threads = await asyncio.to_thread(_fetch)

        if not threads:
            return "📭 信箱空空如也，还没有笔友来信呢。"

        output_lines = []
        count = 0
        for thread in threads:
            if count >= max_results:
                break
            subject = getattr(thread, 'subject', '无标题')
            thread_id = getattr(thread, 'id', '未知')
            last_message_at = getattr(thread, 'last_message_at', '未知时间')
            output_lines.append(
                f"📧 标题: {subject}\n"
                f"🆔 Thread ID: {thread_id}\n"
                f"📅 最后消息: {last_message_at}\n"
            )
            count += 1

        return f"📬 【收件箱】共 {len(threads)} 封\n\n" + "\n".join(output_lines)
    except Exception as e:
        return f"❌ 查看收件箱失败: {e}"


@mcp.tool()
async def read_thread(thread_id: str):
    """读取一个邮件线程的所有消息。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _read():
            messages = client.messages.list(inbox_id=inbox_id, thread_id=thread_id)
            return messages

        messages = await asyncio.to_thread(_read)

        if not messages:
            return "📭 这个线程里没有消息。"

        output_lines = []
        for msg in messages:
            sender = getattr(msg, 'from_', '未知')
            subject = getattr(msg, 'subject', '无标题')
            body = getattr(msg, 'text', '') or getattr(msg, 'body', '无法解析正文')
            date = getattr(msg, 'created_at', '未知时间')
            msg_id = getattr(msg, 'id', '')
            output_lines.append(
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 来自: {sender}\n"
                f"📧 标题: {subject}\n"
                f"📅 时间: {date}\n"
                f"🆔 Message ID: {msg_id}\n"
                f"📄 正文:\n{body}\n"
            )

        return "\n".join(output_lines)
    except Exception as e:
        return f"❌ 读取邮件失败: {e}"


@mcp.tool()
async def send_email(to_email: str, subject: str, content: str):
    """发送邮件给指定地址。用来给笔友写信！"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _send():
            message = client.messages.create(
                inbox_id=inbox_id,
                to=to_email,
                subject=subject,
                text=content
            )
            return message

        message = await asyncio.to_thread(_send)
        return f"✅ 邮件已发送至 {to_email}！\n📧 标题: {subject}\n🆔 Message ID: {getattr(message, 'id', '未知')}"
    except Exception as e:
        return f"❌ 发送失败: {e}"


@mcp.tool()
async def reply_email(thread_id: str, content: str, to_email: str = ""):
    """回复邮件线程中的消息。"""
    try:
        inbox_id = await _ensure_inbox()
        client = _get_client()

        def _reply():
            kwargs = {
                "inbox_id": inbox_id,
                "thread_id": thread_id,
                "text": content,
            }
            if to_email:
                kwargs["to"] = to_email
            message = client.messages.create(**kwargs)
            return message

        message = await asyncio.to_thread(_reply)
        return f"✅ 回信已发送！\n🆔 Message ID: {getattr(message, 'id', '未知')}"
    except Exception as e:
        return f"❌ 回信失败: {e}"


@mcp.tool()
async def get_my_address():
    """查看我的邮箱地址。"""
    try:
        await _ensure_inbox()
        return f"📧 我的邮箱地址: {INBOX_USERNAME}@{INBOX_DOMAIN}"
    except Exception as e:
        return f"❌ 获取地址失败: {e}"


# ─── 启动 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
