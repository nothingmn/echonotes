
# EchoNotes

EchoNotes is a Python-based application that monitors a folder for new files, extracts the content (text, audio, video), summarizes it using a local instance of an LLM model, and saves the summarized output back to disk. It supports offline operation and can handle multiple file formats, including PDFs, Word documents, text files, video/audio files.

## Features

- **Monitors a directory** for new files (PDF, DOCX, TXT, common audio formats, and video formats).
- **Text Extraction**:
  - PDF files (via PyPDF2 and Tesseract for OCR)
  - Word documents (via python-docx)
  - Plain text files
  - Audio files such as MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, AIFF, MP2, AMR, and AC3
  - Video files such as MP4, AVI, MOV, MKV, WEBM, and M4V
  - Non-MP3 audio is normalized to MP3 via FFmpeg before transcription
- **Summarization**: 
  - Supports explicit LLM providers instead of assuming Ollama-style APIs.
  - Supported providers: Open WebUI, Ollama, OpenAI, Claude/Anthropic, OpenRouter, and a legacy generic generate endpoint.
  - Supports customizable markdown prompts.
- **Offline Operation**: 
  - All processing (text extraction, transcription, summarization) can be done offline.
  - Pre-downloads WhisperX ASR models and handles everything locally.
- **Background Worker Pool**:
  - Files are queued immediately by the watcher and processed by persistent workers.
  - Each worker keeps its model loaded to avoid per-file startup costs.
  - The container is intended to run continuously while monitoring the mounted `incoming/` folder.
- **Automatic Transcript Formatting**:
  - Audio and video transcripts can be reformatted into readable Markdown before saving.
  - Formatting falls back to the raw transcript if the formatter fails.
- **Speaker Labels**:
  - WhisperX diarization can label timestamped transcript lines as `Speaker 1`, `Speaker 2`, and so on.
  - Speaker diarization requires a Hugging Face token for the pyannote diarization model.
- **Automatic Chunking**:
  - Large transcripts are chunked and reduced automatically so long meetings do not overflow model context windows.
- **Obsidian Export**:
  - Audio/video jobs can copy the final MP3, transcript, summary, and an Obsidian note into `vault/`.
  - The Obsidian note template is loaded from the same area as the summarization prompt, with a built-in fallback.
- **Logging**: Extensive logging to help track operations and errors.
- **Ingestion Hardening**:
  - Partial-copy files are held until their size stabilizes.
  - Temporary files and hidden dot-paths such as `.obsidian` and `.stfolder` are ignored.

## Requirements

### Quick Start via Docker

Create a config directory and place your runtime files there:

```bash
mkdir -p config incoming vault model-cache
cp config.sample.yml config/config.yml
cp summarize-notes.md config/summarize-notes.md
```

Edit `config/config.yml` for your LLM endpoint, model, tokens, and any diarization settings.

Run the persistent worker container:

```bash
docker run -d --name echonotes --init \
  -v /path/to/incoming:/app/incoming \
  -v /path/to/vault:/app/vault \
  -v /path/to/config:/app/config \
  -v /path/to/model-cache:/app/model-cache \
  echonotes:latest
```

Example from the repo root:

```bash
docker run -d --name echonotes --init \
  -v "$(pwd)/incoming:/app/incoming" \
  -v "$(pwd)/vault:/app/vault" \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/model-cache:/app/model-cache" \
  echonotes:latest
```

The model cache mount is optional but recommended for local/dev use. If `/app/model-cache` is empty, EchoNotes downloads the configured WhisperX model at startup. If `whisper_model` is not configured, it defaults to `base` on CPU and `small` on GPU.

## Installation from source, via docker.

### Docker Setup

1. **Build the Docker Image**:

   Clone the repository and build the Docker image:
   ```bash
   docker build -t echonotes .
   ```

2. **Run the Docker Container**:

   Run the long-lived worker container with the three runtime mounts:
   ```bash
   docker run -d --name echonotes --init \
     -v /path/to/incoming:/app/incoming \
     -v /path/to/vault:/app/vault \
     -v /path/to/config:/app/config \
     -v /path/to/model-cache:/app/model-cache \
     echonotes:latest
   ```

3. **Optional Local Model Cache Warmup**:

   Use the helper script to build the image and warm a local model cache only when the cache directory is empty:
   ```bash
   ./build.sh --model-cache-dir ./model-cache
   ```

   For a GPU-capable runtime:
   ```bash
   ./build.sh --gpu --model-cache-dir ./model-cache
   ```

### Docker Compose Example

You can use Docker Compose to manage the container:

```yaml
version: '3.8'
services:
  echonotes:
    image: echonotes:latest
    init: true
    volumes:
      - ./incoming:/app/incoming
      - ./vault:/app/vault
      - ./config:/app/config
      - ./model-cache:/app/model-cache
    restart: unless-stopped
```

Run the service with:

```bash
docker-compose up -d
```

## Usage

EchoNotes monitors the `/app/incoming` directory continuously. When it detects a new file, it processes it according to the file type:

- **PDF**: Extracts text using PyPDF2 or OCR via Tesseract if needed.
- **Word Documents (DOCX)**: Extracts text using `python-docx`.
- **Text Files (TXT)**: Reads the plain text.
- **Audio Files**: Common audio inputs are converted to MP3 with FFmpeg when needed, then transcribed with WhisperX.
- **Video Files**: Extracts audio using FFmpeg, then transcribes it with WhisperX.

Once the text is extracted, it can be summarized by sending the text and a customizable markdown prompt to a configured LLM provider. If no LLM provider is configured, EchoNotes will still extract and transcribe files, but it will skip LLM-based formatting and summarization.

Files placed in hidden folders or dot-paths such as `.obsidian`, `.stfolder`, or `.syncthing*` are ignored. Temporary partial-download files such as `.part`, `.tmp`, and `.crdownload` are also ignored.

## Configuration

The application is configured via `/app/config/config.yml`. The image also includes baked defaults in `/app/config-defaults`, so if a prompt file is missing from the mounted config directory EchoNotes falls back to the image default where available. An example configuration file is shown below:

```yaml
path_to_watch: "/app/incoming"

llm:
  provider: "openwebui"
  model: "gpt-4o-mini"
  base_url: "http://openwebui:3000/api"
  api_key: "your_api_token_here"
  timeout_seconds: null
  max_tokens: 2048

whisper_model: "base" # Optional; defaults to 'base' on CPU and 'small' on GPU when omitted
worker_count: 2 # Number of background workers to run concurrently; on GPU start with 1
diarization_enabled: true # Enable WhisperX speaker diarization when configured
diarization_hf_token: "" # Required for speaker labels via pyannote diarization
diarization_model_name: "" # Optional; leave blank for WhisperX default
diarization_num_speakers: null # Optional exact speaker count
diarization_min_speakers: null # Optional lower bound
diarization_max_speakers: null # Optional upper bound
format_transcripts: true # Format audio/video transcripts into readable Markdown before summarization
transcript_format_prompt_path: "/app/config/format-transcript.md" # Optional; built-in prompt is used if missing
summary_prompt_path: "/app/config/summarize-notes.md" # Optional; falls back to /app/config-defaults/summarize-notes.md
vault_path: "/app/vault" # Folder where Obsidian-ready artifacts are copied
obsidian_template_path: "/app/config/obsidian-template.md" # Optional; defaults next to summarize-notes.md or a built-in template

chunking:
  enabled: true
  max_input_chars: 24000
  target_chunk_chars: 16000
  overlap_chars: 400
```

### Markdown Prompt Customization

Put your custom runtime files in the mounted `/app/config` directory:
- `config.yml`
- `summarize-notes.md`
- `format-transcript.md`
- `obsidian-template.md`

If you mount `/app/model-cache`, WhisperX downloads are reused across container rebuilds and restarts. This is especially useful for local/dev Docker workflows.

The summarization prompt (`summarize-notes.md`) is used to prepend instructions for summaries. If you want to customize transcript formatting, place `format-transcript.md` in the same config directory and point `transcript_format_prompt_path` at it. If no transcript-format prompt exists there, EchoNotes uses a built-in transcript-formatting prompt.

If you mount an Obsidian vault folder at `vault_path`, EchoNotes also copies audio-ready artifacts there for audio and video jobs:
- The final MP3
- The full transcript markdown
- The summary markdown
- An Obsidian note markdown file

If `obsidian_template_path` is not provided, EchoNotes looks for `obsidian-template.md` next to the summarization prompt. If that file is missing, it uses a built-in plain template.

For speaker labels in audio/video transcripts, configure `diarization_hf_token`. When diarization is available, EchoNotes writes labels like `Speaker 1` and `Speaker 2` into the timestamped transcript lines.

### LLM Providers

`llm.provider` is optional. If it is empty, EchoNotes does not call any LLM provider.

- `openwebui`: `base_url` should usually look like `http://host:3000/api`
- `ollama`: `base_url` should usually look like `http://host:11434/api`
- `openai`: `base_url` should usually look like `https://api.openai.com/v1`
- `claude` or `anthropic`: `base_url` should usually look like `https://api.anthropic.com`
- `openrouter`: `base_url` should usually look like `https://openrouter.ai/api/v1`
- `legacy_generate`: keeps compatibility with the older single-endpoint `api_url` style config

Chunking settings under `chunking:` apply to LLM-based transcript formatting and summarization.

Set `llm.timeout_seconds: null` (or `0`) to disable the HTTP timeout and let slow local models run until they finish.

## Logging

The application logs all activities and errors to help with debugging and tracking its operations. The log includes details about:
- Files processed
- Errors encountered
- Summaries generated

## Folder Structure

- **incoming**: Monitored input mount where new files are placed for processing.
- **working**: Temporary folder where files are processed.
- **completed**: Once processed, files and generated artifacts are moved under `incoming/completed`.
- **vault**: Output mount where final MP3, transcript, summary, and Obsidian note are copied.
- **config**: Mounted runtime config directory for `config.yml` and prompt/template overrides.
- **model-cache**: Optional mounted cache directory for WhisperX, Hugging Face, and related model downloads.

## Contributing

We welcome contributions to EchoNotes! Please fork the repository and submit a pull request with your changes.

## License

EchoNotes is licensed under the MIT License.
