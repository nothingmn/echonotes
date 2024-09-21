
# EchoNotes

EchoNotes is a Python-based application that monitors a folder for new files, extracts the content (text, audio, video), summarizes it using a local instance of an LLM model (like Whisper and others), and saves the summarized output back to disk. It supports offline operation and can handle multiple file formats, including PDFs, Word documents, text files, video/audio files.

## Features

- **Monitors a directory** for new files (PDF, DOCX, TXT, MP4, MP3 formats).
- **Text Extraction**:
  - PDF files (via PyPDF2 and Tesseract for OCR)
  - Word documents (via python-docx)
  - Plain text files
  - Audio files (via Whisper for speech-to-text)
  - Video files (audio extracted via FFmpeg and transcribed using Whisper)
- **Summarization**: 
  - Sends extracted text to a local LLM API for summarization.
  - Supports customizable markdown prompts.
- **Offline Operation**: 
  - All processing (text extraction, transcription, summarization) can be done offline.
  - Pre-downloads Whisper models and handles everything locally.
- **Logging**: Extensive logging to help track operations and errors.

## Requirements

### System Dependencies

- **ffmpeg**: Required for extracting audio from video files.
- **tesseract**: Required for OCR when processing PDF files.
  
You can install these on Ubuntu with:
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg tesseract-ocr
```

### Python Libraries

All Python dependencies are managed via `requirements.txt`. Install them using:
```bash
pip install -r requirements.txt
```

Key Python libraries used:
- `PyPDF2`
- `pdf2image`
- `tesseract`
- `whisper` (OpenAI Whisper for speech-to-text)
- `python-docx` (for DOCX processing)
- `ffmpeg-python`
- `watchdog` (for directory monitoring)
- `requests` (for sending summarization requests)

## Installation

### Docker Setup

1. **Build the Docker Image**:
   Clone the repository and build the Docker image:
   ```bash
   docker build -t echonotes .
   ```

2. **Run the Docker Container**:
   Run the Docker container, mounting the appropriate volumes:
   ```bash
   docker run -v /path/to/incoming:/app/incoming -v /path/to/config.yml:/app/config.yml -v /path/to/summarize-notes.md:/app/summarize-notes.md echonotes
   ```

3. **Pre-Download Whisper Models (Optional)**:
   The Whisper models are automatically downloaded, but you can pre-download them by running:
   ```bash
   docker exec -it <container_id> python -c "import whisper; whisper.load_model('base')"
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
- **Audio Files (MP3)**: Transcribes speech to text using Whisper.
- **Video Files (MP4)**: Extracts audio using FFmpeg, then transcribes it with Whisper.

Once the text is extracted, it is summarized by sending the text and a customizable markdown prompt to a local LLM API.

## Configuration

The application is configured via a `config.yml` file mounted into the Docker container. An example configuration file is shown below:

```yaml
api_url: "http://localhost:5000/api/summarize"
bearer_token: "your_api_token_here"
model: "base"
whisper_model: "base" # Specify the Whisper model to use ('tiny', 'base', 'small', 'medium', 'large')
```

### Markdown Prompt Customization

The prompt file (`summarize-notes.md`) is used to prepend any instructions for summarization. An example structure is below:

```markdown
# Summarization Prompt

Please summarize the following notes in a structured format using the Cornell Method.
```

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
