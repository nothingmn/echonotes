
import os
import time
import pytesseract
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import requests
import logging
import yaml
import json

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

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
        
        return text
    except FileNotFoundError:
        logging.error(f"The file {pdf_path} does not exist. Please ensure the file is available.")
        raise
    except Exception as e:
        logging.error(f"Error extracting text from {pdf_path}: {e}")
        raise



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
        logging.info(f"Request headers: {json.dumps(headers, indent=2)}")
        logging.info(f"Request payload: {json.dumps(payload, indent=2)}")


        response = requests.post(api_url, headers=headers, data=json.dumps(payload))


        # Log the details of the response
        logging.info(f"Response status code: {response.status_code}")
        logging.info(f"Response headers: {json.dumps(dict(response.headers), indent=2)}")
        logging.info(f"Response content: {response.text}")

        response.raise_for_status()  # Will raise an error for HTTP codes 4xx or 5xx
        parsed_response = response.json()
        logging.info(f"Parsed Response content: {parsed_response}")
        return parsed_response.get('response', 'No text found in response')
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        logging.error("Please check the API URL, bearer token, and model in the configuration.")
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

# Event handler for new PDFs
class PDFHandler(FileSystemEventHandler):
    def __init__(self, config):
        self.config = config

    def on_created(self, event):
        if event.src_path.endswith(".pdf"):
            logging.info(f"New PDF detected: {event.src_path}")
            try:
                extracted_text = extract_text_from_pdf(event.src_path)
                full_text = prepend_markdown_prompt(extracted_text, "/app/summarize-notes.md")
                
                logging.info(f"Full Text to send to our API:{full_text}")
                api_response = send_to_api(
                    self.config['api_url'],
                    self.config['bearer_token'],
                    self.config['model'],
                    full_text
                )
                output_filename = f"{event.src_path}.summary.txt"
                with open(output_filename, 'w') as f:
                    f.write(api_response.get("summary", "No summary provided"))
                logging.info(f"Summary written to {output_filename}")
            except Exception as e:
                logging.error(f"Error processing {event.src_path}: {e}")

if __name__ == "__main__":
    try:
        # Load configuration
        config = load_config()

        # Set up directory monitoring
        path_to_watch = "/app/incoming"
        if not os.path.exists(path_to_watch):
            logging.error(f"Directory {path_to_watch} does not exist. Please ensure the folder is mounted.")
            raise FileNotFoundError(f"Directory {path_to_watch} not found")

        event_handler = PDFHandler(config)
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
