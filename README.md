# agentmail-mcp
AgentMail MCP 服务 - AI邮箱收发，用于笔友通信

## 部署

- 启动命令：`python server.py`
- MCP transport：Streamable HTTP
- MCP 地址：`https://你的域名/mcp`
- 必需环境变量：`AGENTMAIL_API_KEY`
- 端口：自动读取部署平台提供的 `PORT`，默认 `8080`
