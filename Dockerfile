FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WT_DATA_DIR=/app/data \
    WT_DB_PATH=/app/data/watch_together.sqlite3 \
    WT_ALLOW_PRIVATE_PROXY=0

WORKDIR /app

# ffmpeg нужен для DASH/разбора потоков, curl — для healthcheck и свежего
# yt-dlp. util-linux даёт setpriv: им сбрасываем привилегии после правки
# каталога данных (su-exec в Debian trixie больше не существует).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Бинарник yt-dlp из релиза всегда свежее, чем закреплённая в requirements версия.
# Ставим после pip, чтобы pip не перезаписал его своей копией.
ARG YTDLP_RELEASE=latest/download
RUN curl -fsSL "https://github.com/yt-dlp/yt-dlp/releases/${YTDLP_RELEASE}/yt-dlp" \
        -o /usr/local/bin/yt-dlp \
    && chmod 0755 /usr/local/bin/yt-dlp \
    && yt-dlp --version

COPY app ./app
COPY static ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Непривилегированный пользователь владеет /app и данными. Сам контейнер
# стартует от root (нужен для правки bind-mount), но entrypoint сразу
# переключается на wt — процесс приложения всегда непривилегированный.
RUN useradd --system --create-home --uid 1000 --shell /usr/sbin/nologin wt \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data /app/data/cache \
    && chown -R wt:wt /app

ENV WT_APP_USER=wt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# --proxy-headers, чтобы WS/HTTP за nginx/Caddy видел правильный X-Forwarded-Proto
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
