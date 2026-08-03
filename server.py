import asyncio
import os

from agentmail import AgentMail
from fastmcp import FastMCP

# ─── 初始化 MCP ──────────────────────────────────────────────
os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")
os.environ.setdefault("FASTMCP_PORT", os.environ.get("PORT", "8080"))
mcp = FastMCP("agentmail-mcp")

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
if __name__ == "__main__":
    mcp.run(
        transport="sse",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080"))
    )
