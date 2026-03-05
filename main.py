import os
import queue
import threading
import time
import pytesseract
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from docx import Document
import requests
import logging
import yaml
import json
import shutil
import ffmpeg
import whisperx


DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".txt")
AUDIO_EXTENSIONS = (
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".mpga",
    ".mp2",
    ".m4b",
    ".mka",
    ".amr",
    ".ac3",
)
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS + AUDIO_EXTENSIONS + VIDEO_EXTENSIONS
TEMP_FILE_SUFFIXES = (".part", ".tmp", ".crdownload")
DEFAULT_SUMMARY_PROMPT_PATH = "/app/summarize-notes.md"
DEFAULT_VAULT_PATH = "/app/vault"
DEFAULT_OBSIDIAN_TEMPLATE_NAME = "obsidian-template.md"
DEFAULT_LLM_TIMEOUT_SECONDS = 120
DEFAULT_CHUNK_MAX_INPUT_CHARS = 24000
DEFAULT_CHUNK_TARGET_CHARS = 16000
DEFAULT_CHUNK_OVERLAP_CHARS = 400
DEFAULT_TRANSCRIPT_FORMAT_PROMPT = """Rewrite the following raw transcript as clean, readable Markdown.

## Goals
- Preserve the speaker meaning and factual content.
- Fix obvious transcription punctuation and paragraph breaks.
- Group the transcript into readable paragraphs.
- Keep names, technical terms, and numbers intact when possible.

## Output Instructions
- Output only the formatted transcript in Markdown.
- Start with a single `# Transcript` heading.
- Do not summarize or omit content.
- Do not add commentary, warnings, or analysis.
- Do not invent speaker names.
"""
DEFAULT_OBSIDIAN_TEMPLATE = """---
type: audio-note
created: {{created}}
source: EchoNotes
---

# {{title}}

## Audio

![[{{audio_filename}}]]

## Files

- Transcript: [[{{transcript_filename}}]]
{{summary_file_line}}

## Summary

{{summary_content}}

## Transcript

{{transcript_body}}
"""


# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Load config
def load_config(config_path="/app/config.yml"):
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file not found at {config_path}. Please ensure the file exists.")
        raise
    except yaml.YAMLError as e:
        logging.error(f"Error reading configuration file {config_path}: {e}")
        raise


def get_worker_count(config):
    configured_count = config.get("worker_count")
    if configured_count:
        return max(1, int(configured_count))

    try:
        import torch

        if torch.cuda.is_available():
            return 1
    except Exception:
        pass

    return max(1, min(4, os.cpu_count() or 1))


def get_llm_settings(config):
    llm_settings = config.get("llm")
    if isinstance(llm_settings, dict):
        return llm_settings

    if config.get("api_url") or config.get("model"):
        return {
            "provider": "legacy_generate",
            "api_url": config.get("api_url"),
            "api_key": config.get("bearer_token"),
            "model": config.get("model"),
        }

    return {}


def get_chunking_settings(config):
    chunking = config.get("chunking", {})
    return {
        "enabled": chunking.get("enabled", True),
        "max_input_chars": int(chunking.get("max_input_chars", DEFAULT_CHUNK_MAX_INPUT_CHARS)),
        "target_chunk_chars": int(chunking.get("target_chunk_chars", DEFAULT_CHUNK_TARGET_CHARS)),
        "overlap_chars": int(chunking.get("overlap_chars", DEFAULT_CHUNK_OVERLAP_CHARS)),
    }


def get_file_extension(file_path):
    return os.path.splitext(file_path)[1].lower()


def is_audio_file(file_path):
    return get_file_extension(file_path) in AUDIO_EXTENSIONS


def is_video_file(file_path):
    return get_file_extension(file_path) in VIDEO_EXTENSIONS


def is_supported_file(file_path):
    return get_file_extension(file_path) in SUPPORTED_EXTENSIONS


def join_text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    text_parts.append(item["content"])
        return "".join(text_parts)
    return ""


class BaseLLMClient:
    def __init__(self, settings):
        self.settings = settings
        self.provider = settings.get("provider")
        self.model = settings.get("model")
        self.timeout_seconds = float(settings.get("timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS))
        self.temperature = settings.get("temperature")
        self.max_tokens = settings.get("max_tokens")

    def generate(self, prompt):
        raise NotImplementedError

    def _post_json(self, url, headers, payload):
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


class LegacyGenerateClient(BaseLLMClient):
    def generate(self, prompt):
        headers = {"Content-Type": "application/json"}
        api_key = self.settings.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        response = self._post_json(self.settings["api_url"], headers, payload)
        return response.get("response", "")


class OllamaClient(BaseLLMClient):
    def generate(self, prompt):
        base_url = self.settings.get("base_url", "http://localhost:11434/api").rstrip("/")
        headers = {"Content-Type": "application/json"}
        api_key = self.settings.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        response = self._post_json(f"{base_url}/generate", headers, payload)
        return response.get("response", "")


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(self, settings, base_url, path, extra_headers=None):
        super().__init__(settings)
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.extra_headers = extra_headers or {}

    def generate(self, prompt):
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        api_key = self.settings.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        response = self._post_json(f"{self.base_url}{self.path}", headers, payload)
        message = response.get("choices", [{}])[0].get("message", {})
        return join_text_content(message.get("content", ""))


class AnthropicClient(BaseLLMClient):
    def generate(self, prompt):
        base_url = self.settings.get("base_url", "https://api.anthropic.com").rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.settings.get("api_key", ""),
            "anthropic-version": self.settings.get("anthropic_version", "2023-06-01"),
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(self.max_tokens or 2048),
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        response = self._post_json(f"{base_url}/v1/messages", headers, payload)
        return join_text_content(response.get("content", []))


def build_llm_client(config):
    settings = get_llm_settings(config)
    provider = (settings.get("provider") or "").strip().lower()

    if not provider:
        logging.info("No LLM provider configured; LLM-based formatting and summarization will be skipped.")
        return None

    if not settings.get("model") and provider != "legacy_generate":
        raise ValueError(f"LLM provider '{provider}' requires a model to be configured")

    if provider == "legacy_generate":
        if not settings.get("api_url"):
            raise ValueError("legacy_generate provider requires api_url")
        return LegacyGenerateClient(settings)

    if provider == "ollama":
        return OllamaClient(settings)

    if provider == "openai":
        base_url = settings.get("base_url", "https://api.openai.com/v1")
        return OpenAICompatibleClient(settings, base_url, "/chat/completions")

    if provider == "openrouter":
        base_url = settings.get("base_url", "https://openrouter.ai/api/v1")
        extra_headers = {}
        if settings.get("site_url"):
            extra_headers["HTTP-Referer"] = settings["site_url"]
        if settings.get("site_name"):
            extra_headers["X-Title"] = settings["site_name"]
        return OpenAICompatibleClient(settings, base_url, "/chat/completions", extra_headers)

    if provider == "openwebui":
        base_url = settings.get("base_url", "http://localhost:3000/api")
        return OpenAICompatibleClient(settings, base_url, "/chat/completions")

    if provider in ("claude", "anthropic"):
        return AnthropicClient(settings)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def load_prompt(prompt_path, default_content):
    if prompt_path and os.path.exists(prompt_path):
        with open(prompt_path, 'r') as prompt_file:
            return prompt_file.read()
    return default_content


def get_summary_prompt_path(config):
    return config.get("summary_prompt_path", DEFAULT_SUMMARY_PROMPT_PATH)


def get_obsidian_template_path(config):
    configured_path = config.get("obsidian_template_path")
    if configured_path:
        return configured_path
    return os.path.join(
        os.path.dirname(get_summary_prompt_path(config)),
        DEFAULT_OBSIDIAN_TEMPLATE_NAME,
    )


def get_vault_path(config):
    return config.get("vault_path", DEFAULT_VAULT_PATH)


def render_template(template, context):
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def format_timestamp_link(seconds):
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        display = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        display = f"{minutes:02d}:{secs:02d}"
    return total_seconds, display


def build_linked_transcript(audio_filename, segments):
    lines = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue

        start_seconds, display_time = format_timestamp_link(segment.get("start", 0))
        speaker = segment.get("speaker")
        speaker_prefix = f"{speaker}: " if speaker else ""
        lines.append(
            f"[[{audio_filename}#t={start_seconds}|{display_time}]] {speaker_prefix}{text}"
        )

    return "\n".join(lines).strip()


def split_text_into_chunks(text, target_chars, overlap_chars):
    if len(text) <= target_chars:
        return [text]

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + target_chars, text_length)
        if end < text_length:
            split_at = text.rfind("\n\n", start, end)
            if split_at <= start:
                split_at = text.rfind("\n", start, end)
            if split_at <= start:
                split_at = text.rfind(" ", start, end)
            if split_at <= start:
                split_at = end
        else:
            split_at = end

        chunk = text[start:split_at].strip()
        if chunk:
            chunks.append(chunk)

        if split_at >= text_length:
            break

        start = max(split_at - overlap_chars, start + 1)

    return chunks


def build_prompt(prompt_instructions, input_text, heading="INPUT"):
    return f"{prompt_instructions}\n\n# {heading}:\n\n{input_text}"

# Helper function to extract text from PDF using OCR and write it back to the same folder
def extract_text_from_pdf(pdf_path):
    try:
        logging.info(f"Extracting text from {pdf_path}")
        
        # Initialize empty string to collect extracted text
        text = ""
        
        # Extract text using PyPDF2
        with open(pdf_path, "rb") as f:
            pdf = PdfReader(f)
            num_pages = len(pdf.pages)
            for page_num in range(num_pages):
                page = pdf.pages[page_num]
                text += page.extract_text()

        # Fallback to OCR if no text is extracted
        if not text.strip():
            logging.warning(f"No extractable text found in {pdf_path}. Falling back to OCR.")

            # Convert PDF to images and perform OCR
            images = convert_from_path(pdf_path)
            for img in images:
                text += pytesseract.image_to_string(img)

        # Define the output filename based on the original PDF file
        base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
        output_filename = os.path.join(os.path.dirname(pdf_path), f"{base_filename}_extracted.txt")

        # Write the extracted text to the file
        with open(output_filename, 'w') as output_file:
            output_file.write(text)
        
        logging.info(f"Extracted text written to {output_filename}")
        
        return text, output_filename
    except FileNotFoundError:
        logging.error(f"The file {pdf_path} does not exist. Please ensure the file is available.")
        raise
    except Exception as e:
        logging.error(f"Error extracting text from {pdf_path}: {e}")
        raise


# Ensure "completed" and "working" directories exist
def ensure_folders(path_to_watch):
    completed_folder = os.path.join(path_to_watch, "completed")
    working_folder = os.path.join(path_to_watch, "working")
    for folder in [completed_folder, working_folder]:
        if not os.path.exists(folder):
            os.makedirs(folder)
            logging.info(f"Created folder at: {folder}")
    return working_folder, completed_folder


def ensure_folder(folder_path):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        logging.info(f"Created folder at: {folder_path}")
    return folder_path


# Move files to the "working" folder
def move_to_working(file_path, working_folder):
    try:
        file_dest = os.path.join(working_folder, os.path.basename(file_path))
        shutil.move(file_path, file_dest)
        logging.info(f"Moved {file_path} to {file_dest}")
        return file_dest
    except Exception as e:
        logging.error(f"Error moving file to working folder: {e}")
        raise


def wait_for_file_stable(file_path, checks=3, delay=2, timeout=300):
    """
    Wait until file size stops changing to ensure it finished copying.
    """
    deadline = time.time() + timeout
    stable_checks = 0
    last_size = None

    while time.time() < deadline:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} no longer exists")

        current_size = os.path.getsize(file_path)
        if current_size == last_size:
            stable_checks += 1
            if stable_checks >= checks:
                return
        else:
            stable_checks = 0
            last_size = current_size

        time.sleep(delay)

    raise TimeoutError(f"Timed out waiting for {file_path} to finish writing")


# Move processed files to the "completed" folder
def move_to_completed(file_path, output_files, completed_folder):
    try:
        # Move output files first
        for output_file in output_files:
            if os.path.exists(output_file):  # Ensure the file exists before moving
                output_dest = os.path.join(completed_folder, os.path.basename(output_file))
                shutil.move(output_file, output_dest)
                logging.info(f"Moved {output_file} to {output_dest}")
        
        # Now move the original file
        if os.path.exists(file_path):  # Ensure the file exists before moving
            file_dest = os.path.join(completed_folder, os.path.basename(file_path))
            shutil.move(file_path, file_dest)
            logging.info(f"Moved {file_path} to {file_dest}")
    except Exception as e:
        logging.error(f"Error moving files to completed folder: {e}")
        raise


def copy_files_to_folder(file_paths, destination_folder):
    copied_paths = []
    for file_path in file_paths:
        if not file_path or not os.path.exists(file_path):
            continue
        destination_path = os.path.join(destination_folder, os.path.basename(file_path))
        shutil.copy2(file_path, destination_path)
        copied_paths.append(destination_path)
        logging.info(f"Copied {file_path} to {destination_path}")
    return copied_paths


def build_output_path(source_path, suffix):
    base_filename = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(os.path.dirname(source_path), f"{base_filename}{suffix}")

# Extract text from a Word document (.docx)
def extract_text_from_word(docx_path):
    try:
        logging.info(f"Extracting text from Word document: {docx_path}")
        document = Document(docx_path)
        text = '\n'.join([paragraph.text for paragraph in document.paragraphs])

        base_filename = os.path.splitext(os.path.basename(docx_path))[0]
        output_filename = os.path.join(os.path.dirname(docx_path), f"{base_filename}_extracted.txt")
        with open(output_filename, 'w') as output_file:
            output_file.write(text)
        
        logging.info(f"Extracted text written to {output_filename}")
        return text, output_filename
    except Exception as e:
        logging.error(f"Error extracting text from {docx_path}: {e}")
        raise


# Convert audio input to MP3 so the downstream transcription and vault output stay consistent.
def convert_audio_to_mp3(audio_path):
    try:
        if get_file_extension(audio_path) == ".mp3":
            return audio_path

        logging.info(f"Converting audio to MP3: {audio_path}")
        base_filename = os.path.splitext(os.path.basename(audio_path))[0]
        mp3_output = os.path.join(os.path.dirname(audio_path), f"{base_filename}.mp3")
        ffmpeg.input(audio_path).output(mp3_output, acodec="libmp3lame").run(overwrite_output=True)
        logging.info(f"Audio converted and saved to {mp3_output}")
        return mp3_output
    except Exception as e:
        logging.error(f"Error converting audio file {audio_path} to MP3: {e}")
        raise


# Extract audio from video and save as MP3
def extract_audio_from_video(video_path):
    try:
        logging.info(f"Extracting audio from video: {video_path}")
        base_filename = os.path.splitext(os.path.basename(video_path))[0]
        mp3_output = os.path.join(os.path.dirname(video_path), f"{base_filename}.mp3")

        ffmpeg.input(video_path).output(mp3_output, acodec="libmp3lame", vn=None).run(overwrite_output=True)
        logging.info(f"Audio extracted and saved to {mp3_output}")
        return mp3_output
    except Exception as e:
        logging.error(f"Error extracting audio from video {video_path}: {e}")
        raise

# Function to read the entire content of a text file
def extract_text_from_txt(file_name):
    try:
        with open(file_name, 'r') as file:
            # Read the entire content of the file
            contents = file.read()
        return contents, file_name
    except FileNotFoundError:
        print(f"Error: The file {file_name} was not found.")
    except Exception as e:
        print(f"Error: An error occurred while reading the file: {e}")
        

class WhisperXTranscriber:
    def __init__(self, whisper_model):
        import torch

        self.whisper_model = whisper_model
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.batch_size = 16 if self.device == "cuda" else 4
        self.align_models = {}

        logging.info(
            f"Loading WhisperX model at startup "
            f"(device={self.device}, compute_type={self.compute_type}, model={self.whisper_model})"
        )
        self.model = whisperx.load_model(
            self.whisper_model,
            self.device,
            compute_type=self.compute_type,
        )

    def _get_align_model(self, language_code):
        if language_code not in self.align_models:
            logging.info(f"Loading WhisperX alignment model for language: {language_code}")
            self.align_models[language_code] = whisperx.load_align_model(
                language_code=language_code,
                device=self.device,
            )
        return self.align_models[language_code]

    def transcribe(self, audio_path):
        try:
            logging.info(
                f"Converting audio to text using WhisperX: {audio_path} "
                f"(device={self.device}, model={self.whisper_model})"
            )

            audio = whisperx.load_audio(audio_path)
            result = self.model.transcribe(audio, batch_size=self.batch_size)

            segments = result.get("segments", [])
            if segments and result.get("language"):
                try:
                    align_model, metadata = self._get_align_model(result["language"])
                    aligned_result = whisperx.align(
                        segments,
                        align_model,
                        metadata,
                        audio,
                        self.device,
                        return_char_alignments=False,
                    )
                    segments = aligned_result.get("segments", segments)
                except Exception as align_error:
                    logging.warning(
                        f"WhisperX alignment failed for {audio_path}; using unaligned transcript: {align_error}"
                    )

            normalized_segments = [
                {
                    "start": segment.get("start", 0),
                    "end": segment.get("end"),
                    "text": segment.get("text", "").strip(),
                    "speaker": segment.get("speaker"),
                }
                for segment in segments
                if segment.get("text") and segment["text"].strip()
            ]

            transcript = "\n".join(segment["text"] for segment in normalized_segments).strip()

            if not transcript:
                transcript = result.get("text", "").strip()

            return {
                "text": transcript,
                "segments": normalized_segments,
                "language": result.get("language"),
            }
        except Exception as e:
            logging.error(f"Error transcribing audio from {audio_path}: {e}")
            raise


class FileProcessor:
    def __init__(self, config, working_folder, completed_folder, vault_folder, transcriber, llm_client):
        self.config = config
        self.working_folder = working_folder
        self.completed_folder = completed_folder
        self.vault_folder = vault_folder
        self.transcriber = transcriber
        self.llm_client = llm_client
        self.chunking = get_chunking_settings(config)

    def process(self, source_path):
        wait_for_file_stable(source_path)
        working_file_path = move_to_working(source_path, self.working_folder)
        file_extension = get_file_extension(working_file_path)
        output_files = []
        vault_files = []

        try:
            if file_extension == ".pdf":
                logging.info(f"Processing PDF: {working_file_path}")
                text, extracted_text_file = extract_text_from_pdf(working_file_path)
                output_files.append(extracted_text_file)

            elif file_extension == ".docx":
                logging.info(f"Processing Word document: {working_file_path}")
                text, extracted_text_file = extract_text_from_word(working_file_path)
                output_files.append(extracted_text_file)

            elif file_extension == ".txt":
                logging.info(f"Processing text file: {working_file_path}")
                text, extracted_text_file = extract_text_from_txt(working_file_path)
                output_files.append(extracted_text_file)

            elif file_extension in VIDEO_EXTENSIONS:
                logging.info(f"Processing video file: {working_file_path}")
                mp3_file = extract_audio_from_video(working_file_path)
                output_files.append(mp3_file)
                transcription = self.transcriber.transcribe(mp3_file)
                text, transcript_body, extracted_text_file = self.format_and_write_transcript(mp3_file, transcription)
                output_files.append(extracted_text_file)
                vault_files.append(mp3_file)

            elif file_extension in AUDIO_EXTENSIONS:
                audio_path = convert_audio_to_mp3(working_file_path)
                if audio_path != working_file_path:
                    logging.info(f"Processing audio file via FFmpeg normalization: {working_file_path}")
                    output_files.append(audio_path)
                else:
                    logging.info(f"Processing MP3 file: {working_file_path}")

                transcription = self.transcriber.transcribe(audio_path)
                text, transcript_body, extracted_text_file = self.format_and_write_transcript(audio_path, transcription)
                output_files.append(extracted_text_file)
                vault_files.append(audio_path)

            else:
                logging.warning(f"Skipping unsupported file type: {working_file_path}")
                return

            output_filename = None
            if self.llm_client:
                summary_content = self.generate_summary(text)
                output_filename = build_output_path(working_file_path, "_summary.md")
                with open(output_filename, 'w') as f:
                    f.write(summary_content)
                output_files.append(output_filename)
            else:
                logging.info(f"No LLM provider configured; skipping summary for {working_file_path}")

            if vault_files:
                obsidian_file = self.write_obsidian_note(
                    audio_path=vault_files[0],
                    transcript_path=extracted_text_file,
                    transcript_content=text,
                    transcript_body=transcript_body,
                    summary_path=output_filename,
                )
                output_files.append(obsidian_file)
                vault_files.extend([extracted_text_file, output_filename, obsidian_file])
                copy_files_to_folder(vault_files, self.vault_folder)

            move_to_completed(working_file_path, output_files, self.completed_folder)
            logging.info(f"Processing is complete for {working_file_path}")
        except Exception:
            logging.exception(f"Error processing {working_file_path}")
            raise

    def format_and_write_transcript(self, audio_path, transcription):
        plain_transcript = transcription["text"]
        formatted_transcript = plain_transcript
        linked_transcript = ""

        if self.llm_client and self.config.get("format_transcripts", True):
            try:
                logging.info(f"Formatting transcript for {audio_path}")
                prompt_path = self.config.get("transcript_format_prompt_path", "/app/format-transcript.md")
                prompt_content = load_prompt(prompt_path, DEFAULT_TRANSCRIPT_FORMAT_PROMPT)
                candidate = self.generate_formatted_transcript(prompt_content, plain_transcript).strip()
                if candidate:
                    formatted_transcript = candidate
                else:
                    logging.warning(f"Transcript formatter returned empty output for {audio_path}; using raw transcript")
            except Exception as format_error:
                logging.warning(f"Transcript formatting failed for {audio_path}; using raw transcript: {format_error}")

        if transcription.get("segments"):
            linked_transcript = build_linked_transcript(
                os.path.basename(audio_path),
                transcription["segments"],
            )

        transcript_file_content = linked_transcript or formatted_transcript
        if not transcript_file_content.startswith("#"):
            transcript_file_content = f"# Transcript\n\n{transcript_file_content}"

        output_filename = build_output_path(audio_path, "_transcribed.md")
        with open(output_filename, 'w') as output_file:
            output_file.write(transcript_file_content)

        logging.info(f"Transcribed text saved to {output_filename}")
        return formatted_transcript, linked_transcript or formatted_transcript, output_filename

    def generate_formatted_transcript(self, prompt_content, transcript_text):
        if not self.chunking["enabled"] or len(transcript_text) <= self.chunking["max_input_chars"]:
            return self.llm_client.generate(build_prompt(prompt_content, transcript_text))

        chunks = split_text_into_chunks(
            transcript_text,
            self.chunking["target_chunk_chars"],
            self.chunking["overlap_chars"],
        )
        formatted_chunks = []
        total_chunks = len(chunks)

        for index, chunk in enumerate(chunks, start=1):
            chunk_prompt = (
                f"{prompt_content}\n\n"
                f"# CHUNK INFO:\nThis is transcript chunk {index} of {total_chunks}. "
                "Format this chunk only and do not add summaries.\n\n"
                f"# INPUT:\n\n{chunk}"
            )
            formatted_chunks.append(self.llm_client.generate(chunk_prompt).strip())

        return "\n\n".join(chunk for chunk in formatted_chunks if chunk)

    def generate_summary(self, text):
        prompt_content = load_prompt(get_summary_prompt_path(self.config), "")
        if not prompt_content.strip():
            raise ValueError("Summary prompt is empty")

        if not self.chunking["enabled"] or len(text) <= self.chunking["max_input_chars"]:
            return format_markdown(self.llm_client.generate(build_prompt(prompt_content, text))).strip()

        partial_summaries = self.generate_chunk_summaries(prompt_content, text)
        return self.reduce_partial_summaries(prompt_content, partial_summaries)

    def generate_chunk_summaries(self, prompt_content, text):
        chunks = split_text_into_chunks(
            text,
            self.chunking["target_chunk_chars"],
            self.chunking["overlap_chars"],
        )
        partial_summaries = []
        total_chunks = len(chunks)

        for index, chunk in enumerate(chunks, start=1):
            chunk_prompt = (
                "You are summarizing one chunk of a longer meeting or transcript. "
                "Only summarize what appears in this chunk. Preserve concrete names, decisions, action items, deadlines, and risks.\n\n"
                f"{prompt_content}\n\n"
                f"# CHUNK INFO:\nThis is chunk {index} of {total_chunks}. "
                "Do not assume facts from other chunks.\n\n"
                f"# INPUT:\n\n{chunk}"
            )
            partial_summary = format_markdown(self.llm_client.generate(chunk_prompt)).strip()
            if partial_summary:
                partial_summaries.append(partial_summary)

        return partial_summaries

    def reduce_partial_summaries(self, prompt_content, partial_summaries):
        if not partial_summaries:
            return ""

        combined = "\n\n".join(
            f"## Partial Summary {index}\n\n{summary}"
            for index, summary in enumerate(partial_summaries, start=1)
        )

        reducer_prompt = (
            "Combine the following partial summaries into one final summary. "
            "Deduplicate repeated points, keep concrete decisions and action items, and preserve Markdown structure.\n\n"
            f"{prompt_content}\n\n"
            f"# PARTIAL SUMMARIES:\n\n{combined}"
        )

        if len(combined) <= self.chunking["max_input_chars"]:
            return format_markdown(self.llm_client.generate(reducer_prompt)).strip()

        reduced_partials = self.generate_chunk_summaries(
            "Compress these partial summaries into a smaller set of faithful partial summaries.",
            combined,
        )
        return self.reduce_partial_summaries(prompt_content, reduced_partials)

    def write_obsidian_note(self, audio_path, transcript_path, transcript_content, transcript_body, summary_path):
        base_filename = os.path.splitext(os.path.basename(audio_path))[0]
        output_filename = build_output_path(audio_path, ".md")
        template = load_prompt(
            get_obsidian_template_path(self.config),
            DEFAULT_OBSIDIAN_TEMPLATE,
        )

        summary_content = ""
        summary_filename = ""
        if summary_path and os.path.exists(summary_path):
            with open(summary_path, 'r') as summary_file:
                summary_content = summary_file.read().strip()
            summary_filename = os.path.basename(summary_path)

        context = {
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": base_filename,
            "audio_filename": os.path.basename(audio_path),
            "transcript_filename": os.path.basename(transcript_path),
            "summary_filename": summary_filename,
            "summary_file_line": f"- Summary: [[{summary_filename}]]" if summary_filename else "- Summary: Not generated",
            "transcript_content": transcript_content,
            "transcript_body": transcript_body,
            "summary_content": summary_content,
        }

        with open(output_filename, 'w') as output_file:
            output_file.write(render_template(template, context))

        logging.info(f"Obsidian note saved to {output_filename}")
        return output_filename


class ProcessingWorker(threading.Thread):
    def __init__(self, worker_id, job_queue, config, working_folder, completed_folder, vault_folder, whisper_model):
        super().__init__(daemon=True, name=f"worker-{worker_id}")
        self.worker_id = worker_id
        self.job_queue = job_queue
        self.config = config
        self.working_folder = working_folder
        self.completed_folder = completed_folder
        self.vault_folder = vault_folder
        self.whisper_model = whisper_model
        self.stop_event = threading.Event()
        self.processor = None

    def stop(self):
        self.stop_event.set()
        self.job_queue.put(None)

    def run(self):
        logging.info(f"Starting processing worker {self.worker_id}")
        transcriber = WhisperXTranscriber(self.whisper_model)
        llm_client = build_llm_client(self.config)
        self.processor = FileProcessor(
            self.config,
            self.working_folder,
            self.completed_folder,
            self.vault_folder,
            transcriber,
            llm_client,
        )

        while not self.stop_event.is_set():
            job = self.job_queue.get()
            try:
                if job is None:
                    continue
                self.processor.process(job)
            except Exception:
                logging.exception(f"Worker failed for job: {job}")
            finally:
                self.job_queue.task_done()


class WorkerPool:
    def __init__(self, worker_count, job_queue, config, working_folder, completed_folder, vault_folder, whisper_model):
        self.workers = [
            ProcessingWorker(
                worker_id=index + 1,
                job_queue=job_queue,
                config=config,
                working_folder=working_folder,
                completed_folder=completed_folder,
                vault_folder=vault_folder,
                whisper_model=whisper_model,
            )
            for index in range(worker_count)
        ]

    def start(self):
        for worker in self.workers:
            worker.start()

    def stop(self):
        for worker in self.workers:
            worker.stop()

    def join(self):
        for worker in self.workers:
            worker.join()

# Properly format the API response to Markdown
def format_markdown(api_response):
    try:
        formatted_markdown = ""
        response_text = api_response

        # Replace placeholder characters to better fit markdown format
        if response_text:
            formatted_markdown += response_text.replace('\n', '\n\n')  # Double line break for markdown paragraphs
        
        return formatted_markdown
    except Exception as e:
        logging.error(f"Error formatting API response to Markdown: {e}")
        return ""
    
# Event handler for newly created files
class FileHandler(FileSystemEventHandler):
    def __init__(self, job_queue, path_to_watch):
        self.job_queue = job_queue
        self.path_to_watch = path_to_watch

    def on_created(self, event):
        try:
            if event.is_directory:
                return

            parent_dir = os.path.dirname(event.src_path)
            if parent_dir != self.path_to_watch:
                return

            normalized_path = event.src_path.lower()

            if normalized_path.endswith(TEMP_FILE_SUFFIXES):
                logging.info(f"Ignoring temporary file: {event.src_path}")
                return

            if not is_supported_file(event.src_path):
                logging.info(f"Ignoring unsupported file: {event.src_path}")
                return

            self.job_queue.put(event.src_path)
            logging.info(f"Queued file for background processing: {event.src_path}")
        except Exception as e:
            logging.error(f"Error queueing {event.src_path}: {e}")

        


def show_ascii_art():
    ascii_art = """
 _  _ |_  _ __  _ _|_ _  _ 
(/_(_ | |(_)| |(_) |_(/__> 
    """
    logging.info(ascii_art)

if __name__ == "__main__":
    try:
        show_ascii_art()

        # Load configuration
        config = load_config()
        whisper_model = config['whisper_model'] if 'whisper_model' in config and config['whisper_model'] else 'base'
        worker_count = get_worker_count(config)
        logging.info(f"Starting worker pool with {worker_count} worker(s)")

        # Set up directory monitoring
        path_to_watch = "/app/incoming"
        if not os.path.exists(path_to_watch):
            logging.error(f"Directory {path_to_watch} does not exist. Please ensure the folder is mounted.")
            raise FileNotFoundError(f"Directory {path_to_watch} not found")

        # Ensure the output folders exist
        working_folder, completed_folder = ensure_folders(path_to_watch)
        vault_folder = ensure_folder(get_vault_path(config))

        job_queue = queue.Queue()
        worker_pool = WorkerPool(
            worker_count,
            job_queue,
            config,
            working_folder,
            completed_folder,
            vault_folder,
            whisper_model,
        )
        worker_pool.start()

        event_handler = FileHandler(job_queue, path_to_watch)
        observer = Observer()
        observer.schedule(event_handler, path=path_to_watch, recursive=False)
        observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            worker_pool.stop()
        observer.join()
        worker_pool.join()

    except Exception as e:
        logging.critical(f"Application failed to start: {e}")
