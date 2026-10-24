FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --system --uid 10001 --create-home mcp
COPY app ./app
USER mcp
EXPOSE 8000
CMD ["sh", "-c", "python -m app.manage init-db && exec uvicorn app.server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log"]
