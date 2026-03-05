
# EchoNotes

EchoNotes is a Python-based application that monitors a folder for new files, extracts the content (text, audio, video), summarizes it using a local instance of an LLM model, and saves the summarized output back to disk. It supports offline operation and can handle multiple file formats, including PDFs, Word documents, text files, video/audio files.

## Features

- **Monitors a directory** for new files (PDF, DOCX, TXT, MP4, MP3 formats).
- **Text Extraction**:
  - PDF files (via PyPDF2 and Tesseract for OCR)
  - Word documents (via python-docx)
  - Plain text files
  - Audio files (via WhisperX for speech-to-text)
  - Video files (audio extracted via FFmpeg and transcribed using WhisperX)
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
- **Automatic Transcript Formatting**:
  - Audio and video transcripts can be reformatted into readable Markdown before saving.
  - Formatting falls back to the raw transcript if the formatter fails.
- **Automatic Chunking**:
  - Large transcripts are chunked and reduced automatically so long meetings do not overflow model context windows.
- **Obsidian Export**:
  - Audio/video jobs can copy the final MP3, transcript, summary, and an Obsidian note into `vault/`.
  - The Obsidian note template is loaded from the same area as the summarization prompt, with a built-in fallback.
- **Logging**: Extensive logging to help track operations and errors.

## Requirements

### Quick Start via Docker

   ```base
   cp config.sample.yml config.yml
   ```
   Edit config.yml and make sure you enter your correct Ollama endpoint, API token, etc.
   
   Then:

   ```bash
   docker run -v /path/to/incoming:/app/incoming -v /path/to/config.yml:/app/config.yml -v /path/to/summarize-notes.md:/app/summarize-notes.md -v /path/to/vault:/app/vault echonotes
   ```

   For example

   ```bash
    docker run --rm -v "$(pwd)//incoming:/app/incoming" \
            -v "$(pwd)/config.yml:/app/config.yml" \
            -v "$(pwd)//summarize-notes.md:/app/summarize-notes.md" \
            -v "$(pwd)//vault:/app/vault" \
            echonotes:latest
   ```




## Installation from source, via docker.

### Docker Setup

1. **Build the Docker Image**:

   Clone the repository and build the Docker image:
   ```bash
   docker build -t echonotes .
   ```

2. **Run the Docker Container**:

   Run the Docker container, mounting the appropriate volumes:
   ```bash
   docker run -v /path/to/incoming:/app/incoming -v /path/to/config.yml:/app/config.yml -v /path/to/summarize-notes.md:/app/summarize-notes.md -v /path/to/vault:/app/vault echonotes
   ```

3. **Pre-Download WhisperX Models (Optional)**:

   The WhisperX ASR models are automatically downloaded, but you can pre-download them by running:
   ```bash
   docker exec -it <container_id> python -c "import whisperx; whisperx.load_model('base', 'cpu', compute_type='int8')"
   ```

### Docker Compose Example

You can use Docker Compose to manage the container:

```yaml
version: '3.8'
services:
  echonotes:
    image: echonotes:latest
    volumes:
      - ./incoming:/app/incoming
      - ./config.yml:/app/config.yml
      - ./summarize-notes.md:/app/summarize-notes.md
      - ./vault:/app/vault
    restart: unless-stopped
```

Run the service with:

```bash
docker-compose up -d
```

## Usage

EchoNotes monitors the `/app/incoming` directory for new files. When it detects a new file, it processes it according to the file type:

- **PDF**: Extracts text using PyPDF2 or OCR via Tesseract if needed.
- **Word Documents (DOCX)**: Extracts text using `python-docx`.
- **Text Files (TXT)**: Reads the plain text.
- **Audio Files (MP3)**: Transcribes speech to text using WhisperX.
- **Video Files (MP4)**: Extracts audio using FFmpeg, then transcribes it with WhisperX.

Once the text is extracted, it can be summarized by sending the text and a customizable markdown prompt to a configured LLM provider. If no LLM provider is configured, EchoNotes will still extract and transcribe files, but it will skip LLM-based formatting and summarization.

## Configuration

The application is configured via a `config.yml` file mounted into the Docker container. An example configuration file is shown below:

```yaml
llm:
  provider: "openwebui"
  model: "gpt-4o-mini"
  base_url: "http://openwebui:3000/api"
  api_key: "your_api_token_here"
  timeout_seconds: 120
  max_tokens: 2048

whisper_model: "base" # Specify the WhisperX ASR model to use ('tiny', 'base', 'small', 'medium', 'large')
worker_count: 2 # Number of background workers to run concurrently; on GPU start with 1
format_transcripts: true # Format audio/video transcripts into readable Markdown before summarization
transcript_format_prompt_path: "/app/format-transcript.md" # Optional; built-in prompt is used if missing
summary_prompt_path: "/app/summarize-notes.md" # Optional; defaults to /app/summarize-notes.md
vault_path: "/app/vault" # Folder where Obsidian-ready artifacts are copied
obsidian_template_path: "/app/obsidian-template.md" # Optional; defaults next to summarize-notes.md or a built-in template

chunking:
  enabled: true
  max_input_chars: 24000
  target_chunk_chars: 16000
  overlap_chars: 400
```

### Markdown Prompt Customization

The prompt file (`summarize-notes.md`) is used to prepend any instructions for summarization. Update it as you see fit.

If you want to customize transcript formatting, you can optionally mount a separate prompt file and point `transcript_format_prompt_path` at it. If no file exists there, EchoNotes uses a built-in transcript-formatting prompt.

If you mount an Obsidian vault folder at `vault_path`, EchoNotes also copies audio-ready artifacts there for MP3 and video jobs:
- The final MP3
- The full transcript markdown
- The summary markdown
- An Obsidian note markdown file

If `obsidian_template_path` is not provided, EchoNotes looks for `obsidian-template.md` next to the summarization prompt. If that file is missing, it uses a built-in plain template.

### LLM Providers

`llm.provider` is optional. If it is empty, EchoNotes does not call any LLM provider.

- `openwebui`: `base_url` should usually look like `http://host:3000/api`
- `ollama`: `base_url` should usually look like `http://host:11434/api`
- `openai`: `base_url` should usually look like `https://api.openai.com/v1`
- `claude` or `anthropic`: `base_url` should usually look like `https://api.anthropic.com`
- `openrouter`: `base_url` should usually look like `https://openrouter.ai/api/v1`
- `legacy_generate`: keeps compatibility with the older single-endpoint `api_url` style config

Chunking settings under `chunking:` apply to LLM-based transcript formatting and summarization.

## Logging

The application logs all activities and errors to help with debugging and tracking its operations. The log includes details about:
- Files processed
- Errors encountered
- Summaries generated

## Folder Structure

- **incoming**: Monitored folder where new files are placed for processing.
- **working**: Temporary folder where files are processed.
- **completed**: Once processed, files (and summaries) are moved to the `completed` folder.

## Contributing

We welcome contributions to EchoNotes! Please fork the repository and submit a pull request with your changes.

## License

EchoNotes is licensed under the MIT License.
