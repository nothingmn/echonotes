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


SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".mp3", ".mp4", ".avi", ".mov", ".mkv")
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


# Send extracted text to local API for summarization
def send_to_api(api_url, bearer_token, model, content):
    try:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "prompt": content,
            "stream": False
        }

        # Log the details of the request
        logging.info(f"Sending request to API: {api_url}")
        logging.info(f"Request headers: {headers}")
        logging.info(f"Request payload: {payload}")

        # Make the POST request
        response = requests.post(api_url, json=payload, headers=headers)

        # Ensure the status code is successful; raises error for 4xx or 5xx
        response.raise_for_status()

        # Attempt to parse the response as JSON
        try:
            parsed_response = response.json()  # Should return a dict
            logging.info(f"Parsed Response content: {parsed_response}")
            return parsed_response.get('response', 'No text found in response')
        except ValueError:
            logging.error(f"Failed to parse response as JSON: {response.text}")
            return 'No valid JSON response'

    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        raise
    except requests.exceptions.ConnectionError:
        logging.error("Failed to connect to the API. Please ensure the API server is running and accessible.")
        raise
    except requests.exceptions.Timeout:
        logging.error("Request to the API timed out. Consider increasing the timeout duration.")
        raise
    except Exception as e:
        logging.error(f"An error occurred while sending a request to the API: {e}")
        raise


def load_prompt(prompt_path, default_content):
    if prompt_path and os.path.exists(prompt_path):
        with open(prompt_path, 'r') as prompt_file:
            return prompt_file.read()
    return default_content

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


def wait_for_file_ready(file_path, timeout=300, check_interval=1):
    deadline = time.time() + timeout
    last_size = None

    while time.time() < deadline:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} no longer exists")

        current_size = os.path.getsize(file_path)
        if last_size is not None and current_size == last_size:
            return

        last_size = current_size
        time.sleep(check_interval)

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


# Extract audio from video and save as MP3
def extract_audio_from_video(video_path):
    try:
        logging.info(f"Extracting audio from video: {video_path}")
        base_filename = os.path.splitext(os.path.basename(video_path))[0]
        mp3_output = os.path.join(os.path.dirname(video_path), f"{base_filename}.mp3")

        # Use ffmpeg to extract the audio and save it as an MP3 file
        ffmpeg.input(video_path).output(mp3_output).run(overwrite_output=True)
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

            transcript = "\n".join(
                segment["text"].strip()
                for segment in segments
                if segment.get("text") and segment["text"].strip()
            ).strip()

            if not transcript:
                transcript = result.get("text", "").strip()

            return transcript
        except Exception as e:
            logging.error(f"Error transcribing audio from {audio_path}: {e}")
            raise


class FileProcessor:
    def __init__(self, config, working_folder, completed_folder, transcriber):
        self.config = config
        self.working_folder = working_folder
        self.completed_folder = completed_folder
        self.transcriber = transcriber

    def process(self, source_path):
        wait_for_file_ready(source_path)
        working_file_path = move_to_working(source_path, self.working_folder)
        output_files = []

        try:
            if working_file_path.endswith(".pdf"):
                logging.info(f"Processing PDF: {working_file_path}")
                text, extracted_text_file = extract_text_from_pdf(working_file_path)
                output_files.append(extracted_text_file)

            elif working_file_path.endswith(".docx"):
                logging.info(f"Processing Word document: {working_file_path}")
                text, extracted_text_file = extract_text_from_word(working_file_path)
                output_files.append(extracted_text_file)

            elif working_file_path.endswith(".txt"):
                logging.info(f"Processing text file: {working_file_path}")
                text, extracted_text_file = extract_text_from_txt(working_file_path)
                output_files.append(extracted_text_file)

            elif working_file_path.endswith((".mp4", ".avi", ".mov", ".mkv")):
                logging.info(f"Processing video file: {working_file_path}")
                mp3_file = extract_audio_from_video(working_file_path)
                output_files.append(mp3_file)
                text = self.transcriber.transcribe(mp3_file)
                text, extracted_text_file = self.format_and_write_transcript(mp3_file, text)
                output_files.append(extracted_text_file)

            elif working_file_path.endswith(".mp3"):
                logging.info(f"Processing MP3 file: {working_file_path}")
                text = self.transcriber.transcribe(working_file_path)
                text, extracted_text_file = self.format_and_write_transcript(working_file_path, text)
                output_files.append(extracted_text_file)

            else:
                logging.warning(f"Skipping unsupported file type: {working_file_path}")
                return

            full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
            api_response = send_to_api(
                self.config['api_url'],
                self.config['bearer_token'],
                self.config['model'],
                full_text,
            )
            output_filename = f"{working_file_path}.summary.md"
            with open(output_filename, 'w') as f:
                f.write(format_markdown(api_response))
            output_files.append(output_filename)

            move_to_completed(working_file_path, output_files, self.completed_folder)
            logging.info(f"Processing is complete for {working_file_path}")
        except Exception:
            logging.exception(f"Error processing {working_file_path}")
            raise

    def format_and_write_transcript(self, audio_path, transcript):
        formatted_transcript = transcript

        if self.config.get("format_transcripts", True):
            try:
                logging.info(f"Formatting transcript for {audio_path}")
                prompt_path = self.config.get("transcript_format_prompt_path", "/app/format-transcript.md")
                prompt_content = load_prompt(prompt_path, DEFAULT_TRANSCRIPT_FORMAT_PROMPT)
                formatting_prompt = f"{prompt_content}\n\n# INPUT:\n\n{transcript}"
                api_response = send_to_api(
                    self.config['api_url'],
                    self.config['bearer_token'],
                    self.config['model'],
                    formatting_prompt,
                )
                candidate = format_markdown(api_response).strip()
                if candidate:
                    formatted_transcript = candidate
                else:
                    logging.warning(f"Transcript formatter returned empty output for {audio_path}; using raw transcript")
            except Exception as format_error:
                logging.warning(f"Transcript formatting failed for {audio_path}; using raw transcript: {format_error}")

        if not formatted_transcript.startswith("#"):
            formatted_transcript = f"# Transcript\n\n{formatted_transcript}"

        base_filename = os.path.splitext(os.path.basename(audio_path))[0]
        output_filename = os.path.join(os.path.dirname(audio_path), f"{base_filename}_transcribed.md")
        with open(output_filename, 'w') as output_file:
            output_file.write(formatted_transcript)

        logging.info(f"Transcribed text saved to {output_filename}")
        return formatted_transcript, output_filename


class ProcessingWorker(threading.Thread):
    def __init__(self, worker_id, job_queue, config, working_folder, completed_folder, whisper_model):
        super().__init__(daemon=True, name=f"worker-{worker_id}")
        self.worker_id = worker_id
        self.job_queue = job_queue
        self.config = config
        self.working_folder = working_folder
        self.completed_folder = completed_folder
        self.whisper_model = whisper_model
        self.stop_event = threading.Event()
        self.processor = None

    def stop(self):
        self.stop_event.set()
        self.job_queue.put(None)

    def run(self):
        logging.info(f"Starting processing worker {self.worker_id}")
        transcriber = WhisperXTranscriber(self.whisper_model)
        self.processor = FileProcessor(
            self.config,
            self.working_folder,
            self.completed_folder,
            transcriber,
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
    def __init__(self, worker_count, job_queue, config, working_folder, completed_folder, whisper_model):
        self.workers = [
            ProcessingWorker(
                worker_id=index + 1,
                job_queue=job_queue,
                config=config,
                working_folder=working_folder,
                completed_folder=completed_folder,
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
    
# Prepend the markdown prompt file content
def prepend_markdown_prompt(pdf_text, prompt_path):
    try:
        with open(prompt_path, 'r') as prompt_file:
            prompt_content = prompt_file.read()
        return prompt_content + "\n" + pdf_text
    except FileNotFoundError:
        logging.error(f"Markdown prompt file not found at {prompt_path}. Please provide a valid prompt file.")
        raise
    except Exception as e:
        logging.error(f"Error reading markdown prompt file {prompt_path}: {e}")
        raise

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

            if not event.src_path.endswith(SUPPORTED_EXTENSIONS):
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

        # Ensure the "working" and "completed" folders exist
        working_folder, completed_folder = ensure_folders(path_to_watch)

        job_queue = queue.Queue()
        worker_pool = WorkerPool(
            worker_count,
            job_queue,
            config,
            working_folder,
            completed_folder,
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
