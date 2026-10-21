FROM python:3.11-slim

WORKDIR /app

# 无状态服务：agentmail 数据全在云端，本地不需要持久卷
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# FastMCP streamable-http，默认挂载路径 /mcp
CMD ["python", "server.py"]
