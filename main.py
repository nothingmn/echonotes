import os
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
import whisper


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
        

# Convert MP3 to text using Whisper
def convert_audio_to_text(audio_path):
    try:
        logging.info(f"Converting audio to text using Whisper: {audio_path}")
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)

        # Save the transcribed text to a markdown file
        base_filename = os.path.splitext(os.path.basename(audio_path))[0]
        output_filename = os.path.join(os.path.dirname(audio_path), f"{base_filename}_transcribed.md")
        with open(output_filename, 'w') as output_file:
            output_file.write(f"# Transcribed Audio\n\n{result['text']}")
        
        logging.info(f"Transcribed text saved to {output_filename}")
        return result['text'], output_filename
    except Exception as e:
        logging.error(f"Error transcribing audio from {audio_path}: {e}")
        raise

# Properly format the API response to Markdown
def format_markdown(api_response):
    try:
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
    def __init__(self, config, working_folder, completed_folder):
        self.config = config
        self.working_folder = working_folder
        self.completed_folder = completed_folder

    def on_created(self, event):
        try:
            # Move the file to the working folder before processing
            working_file_path = move_to_working(event.src_path, self.working_folder)
            
            if working_file_path.endswith(".pdf"):
                logging.info(f"Processing PDF: {working_file_path}")
                text, extracted_text_file = extract_text_from_pdf(working_file_path)
                full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
                api_response = send_to_api(self.config['api_url'], self.config['bearer_token'], self.config['model'], full_text)
                output_filename = f"{working_file_path}.summary.md"
                with open(output_filename, 'w') as f:
                    f.write(format_markdown(api_response))
                move_to_completed(working_file_path, [extracted_text_file, output_filename], self.completed_folder)

            elif working_file_path.endswith(".docx"):
                logging.info(f"Processing Word document: {working_file_path}")
                text, extracted_text_file = extract_text_from_word(working_file_path)
                full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
                api_response = send_to_api(self.config['api_url'], self.config['bearer_token'], self.config['model'], full_text)
                output_filename = f"{working_file_path}.summary.md"
                with open(output_filename, 'w') as f:
                    f.write(format_markdown(api_response))
                move_to_completed(working_file_path, [extracted_text_file, output_filename], self.completed_folder)

            elif working_file_path.endswith(".txt"):
                logging.info(f"Processing text file: {working_file_path}")
                text, extracted_text_file = extract_text_from_txt(working_file_path)
                full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
                api_response = send_to_api(self.config['api_url'], self.config['bearer_token'], self.config['model'], full_text)
                output_filename = f"{working_file_path}.summary.md"
                with open(output_filename, 'w') as f:
                    f.write(format_markdown(api_response))
                move_to_completed(working_file_path, [extracted_text_file, output_filename], self.completed_folder)

            elif working_file_path.endswith((".mp4", ".avi", ".mov", ".mkv")):
                logging.info(f"Processing video file: {working_file_path}")
                mp3_file = extract_audio_from_video(working_file_path)
                text, extracted_text_file = convert_audio_to_text(mp3_file)
                full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
                api_response = send_to_api(self.config['api_url'], self.config['bearer_token'], self.config['model'], full_text)
                output_filename = f"{working_file_path}.summary.md"
                with open(output_filename, 'w') as f:
                    f.write(format_markdown(api_response))
                move_to_completed(working_file_path, [mp3_file, extracted_text_file, output_filename], self.completed_folder)

            elif working_file_path.endswith(".mp3"):
                logging.info(f"Processing MP3 file: {working_file_path}")
                text, extracted_text_file = convert_audio_to_text(working_file_path)
                full_text = prepend_markdown_prompt(text, "/app/summarize-notes.md")
                api_response = send_to_api(self.config['api_url'], self.config['bearer_token'], self.config['model'], full_text)
                output_filename = f"{working_file_path}.summary.md"
                with open(output_filename, 'w') as f:
                    f.write(format_markdown(api_response))
                move_to_completed(working_file_path, [extracted_text_file, output_filename], self.completed_folder)

        except Exception as e:
            logging.error(f"Error processing {event.src_path}: {e}")


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

        # Set up directory monitoring
        path_to_watch = "/app/incoming"
        if not os.path.exists(path_to_watch):
            logging.error(f"Directory {path_to_watch} does not exist. Please ensure the folder is mounted.")
            raise FileNotFoundError(f"Directory {path_to_watch} not found")

        # Ensure the "working" and "completed" folders exist
        working_folder, completed_folder = ensure_folders(path_to_watch)

        event_handler = FileHandler(config, working_folder, completed_folder)
        observer = Observer()
        observer.schedule(event_handler, path=path_to_watch, recursive=False)
        observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    except Exception as e:
        logging.critical(f"Application failed to start: {e}")