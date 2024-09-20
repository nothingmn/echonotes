FROM python:3.10-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    poppler-utils \
    ffmpeg \
    git
    

# Install Python packages
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir  -r /app/requirements.txt && \
    pip install --no-cache-dir openai-whisper

# Pre-download multiple Whisper models
RUN python -c "import whisper; whisper.load_model('tiny')" && \
    python -c "import whisper; whisper.load_model('base')" && \
    python -c "import whisper; whisper.load_model('small')"


# Clean up unnecessary files
RUN apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Copy app files
COPY main.py /app/main.py
WORKDIR /app

# Expose the incoming directory as a volume
VOLUME /app/incoming

# Expose the prompt file as a volume
VOLUME /app/summarize-notes.md

# Expose the config file as a volume
VOLUME /app/config.yml

CMD ["python", "/app/main.py"]
