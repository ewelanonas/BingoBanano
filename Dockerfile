# Para sa pag-host ng BingoBanano sa isang platform tulad ng Fly.io, Render, o
# Railway. Ito ang pinakamaaasahang paraan para makasali ang mga bisita: hindi
# na dumadaan sa Wi-Fi, router, firewall, o tunnel mo.
#
# Naka-pin ang version tag. Sa produksyon, mas mabuti ang digest pin.
FROM python:3.13-slim-bookworm

# uv para sa mabilis at reproducible na install mula sa uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Ang dependencies ay hiwalay na layer para hindi mag-rebuild kada code change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

# Hindi root. Kailangan ng write access sa /app para sa SQLite file.
RUN useradd --create-home --uid 10001 bingo && chown -R bingo:bingo /app
USER bingo

ENV BINGO_DATABASE_URL="sqlite+aiosqlite:////app/data/bingobanano.db" \
    PORT=8000

# Walang persistent volume: pansamantala lang naman ang mga round. Kung gusto
# mong mabuhay ang data sa restart, i-mount ang /app/data bilang volume.
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if b'ok' in urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/healthz', timeout=4).read() else 1)"

# `--forwarded-allow-ips=*` ay tama LANG dito. Sa loob ng platform, ang load
# balancer nila ang tanging daan papasok sa container, kaya walang ibang
# makakapagpeke ng client IP. Huwag itong gamitin sa local na makina.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
