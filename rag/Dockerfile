FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/rag
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --system --uid 10002 --create-home rag && \
    mkdir -p /model-cache && chown rag:rag /model-cache
COPY ragcore ./ragcore
USER rag
ENTRYPOINT ["python", "-m", "ragcore"]
CMD ["--help"]
