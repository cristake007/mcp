FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/rag
# CPU-only OCR; tessdata-fast languages are packaged by Debian.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-ron && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --system --uid 10002 --create-home rag && \
    mkdir -p /model-cache && chown rag:rag /model-cache
COPY ragcore ./ragcore
USER rag
ENTRYPOINT ["python", "-m", "ragcore"]
CMD ["--help"]
