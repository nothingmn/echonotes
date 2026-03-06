FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    poppler-utils \
    tesseract-ocr \
    tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Pre-download the most common CPU WhisperX ASR models so startup stays fast.
RUN python -c "import whisperx; whisperx.load_model('tiny', 'cpu', compute_type='int8')" && \
    python -c "import whisperx; whisperx.load_model('base', 'cpu', compute_type='int8')" && \
    python -c "import whisperx; whisperx.load_model('small', 'cpu', compute_type='int8')"

RUN mkdir -p /app/config-defaults /app/incoming /app/vault /app/config

COPY main.py README.md /app/
COPY config.sample.yml /app/config-defaults/config.yml
COPY summarize-notes.md /app/config-defaults/summarize-notes.md

VOLUME ["/app/incoming", "/app/vault", "/app/config"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/main.py"]
