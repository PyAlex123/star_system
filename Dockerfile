FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY app.py ./

# Приложение пишет только в /app/data — его монтируют томом с хоста.
# Работаем не от root: если контейнер взломают, прав будет минимум.
RUN useradd --create-home --uid 10001 stars \
    && mkdir -p /app/data/uploads/avatars /app/data/uploads/prizes \
    && chown -R stars:stars /app

USER stars

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4)"

# --proxy-headers нужен за nginx: без него приложение считает соединение
# незащищённым и ставит cookie учителя без флага secure.
CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
