FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/model-cache/hf \
    XDG_CACHE_HOME=/app/model-cache/xdg

WORKDIR /app

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    poppler-utils \
    tesseract-ocr \
    tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

RUN mkdir -p /app/config-defaults /app/incoming /app/vault /app/config /app/model-cache/hf /app/model-cache/xdg

COPY main.py README.md /app/
COPY config.sample.yml /app/config-defaults/config.yml
COPY summarize-notes.md /app/config-defaults/summarize-notes.md

VOLUME ["/app/incoming", "/app/vault", "/app/config", "/app/model-cache"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/main.py"]
