FROM mcr.microsoft.com/playwright/python:v1.56.1-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /srv
RUN pip install --no-cache-dir playwright==1.56.1
COPY renderer.py visibility.js ./
USER pwuser
CMD ["python", "renderer.py"]
