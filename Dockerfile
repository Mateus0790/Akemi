# Núcleo da Akemi (mente, memória, chat da live) para rodar 24h num servidor
# Docker, ex. ZimaOS. Microfone, tela e voz ficam no PC (akemi-corpo).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    TZ=America/Sao_Paulo

WORKDIR /app

# Dependências primeiro: mudar o código não reinstala tudo.
COPY pyproject.toml README.md LICENSE ./
COPY akemi ./akemi
RUN pip install ".[servidor]"

VOLUME ["/app/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/saude', timeout=4)"

CMD ["akemi-servidor"]
