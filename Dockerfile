FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Cloud Run injects $PORT (defaults to 8080). We bind to it.
ENV PORT=8080
EXPOSE 8080

# The MCP server runs as a subprocess of the backend, so one container hosts
# the site, the CX webhook, the playbook tools, and the banking tools together.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT}"]
