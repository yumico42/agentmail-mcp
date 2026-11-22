# agentmail-mcp
AgentMail MCP 服务 - AI邮箱收发，用于笔友通信

## 部署

- 启动命令：`python server.py`
- MCP transport：Streamable HTTP
- 端口：读取环境变量 `PORT`，默认 `8000`（与 Dockerfile 一致）
- 健康检查：`GET /healthz` → `200 ok`（无需鉴权）

### 环境变量

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `AGENTMAIL_API_KEY` | 是 | AgentMail API key |
| `MCP_TOKEN` | 是 | 访问 `/mcp` 的密钥。**未设置时服务拒绝启动。** 用足够长的随机串，例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `PORT` | 否 | 监听端口，默认 `8000` |
| `INBOX_USERNAME` | 否 | 固定使用的共享邮箱用户名。设置后使用（不存在则创建）`<INBOX_USERNAME>@<INBOX_DOMAIN>` |
| `INBOX_DOMAIN` | 否 | 共享邮箱的域名，默认 `agentmail.to`（自定义域名需先在 AgentMail 验证） |

不设置 `INBOX_USERNAME` 时，使用账号下第一个已有 inbox，没有就自动创建一个。

## 鉴权（连接 MCP）

`/mcp` 必须携带 `MCP_TOKEN`，两种方式任选其一：

1. 请求头（推荐，客户端支持自定义 header 时使用）：
   - MCP 地址：`https://你的域名/mcp`
   - Header：`Authorization: Bearer <MCP_TOKEN>`
2. 路径带 token（客户端无法设置 header 时使用，如部分网页版连接器）：
   - MCP 地址：`https://你的域名/m/<MCP_TOKEN>/mcp`

token 错误或缺失返回 `401`。路径方式会把 token 写进 URL，可能出现在访问日志里，注意不要外泄；泄露后更换 `MCP_TOKEN` 并重启即可。

## 工具说明

- `get_my_address` / `check_inbox` / `read_email` / `send_email` / `reply_email`：都使用共享/默认邮箱。
- `create_inbox(username?, domain?)`：额外新建一个邮箱，**不会**替换默认邮箱。
- 邮件时间统一显示为北京时间（固定 UTC+8）。
