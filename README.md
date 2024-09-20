# echonotes

Welcome to **echonotes**! This is an exciting and powerful Python application designed to automate the process of extracting handwritten notes from PDFs and summarizing them using a local AI model. Whether you're organizing notes or processing lecture scans, **echonotes** makes it simple and efficient. Running inside a Docker container, it monitors a folder for new PDF files, extracts text from them using OCR (Tesseract), and sends the text to a local API for summarization. All of this happens seamlessly and offline!

## Features

- 📂 **Monitors folders** for new PDFs and automatically processes them.
- 📝 **Extracts handwritten notes** from PDFs using Tesseract OCR.
- ⚡ **Prepares content** with an additional Markdown prompt to enrich the extracted data.
- 🤖 **Summarizes** the content using your local AI model through API requests.
- 🚀 **Deploys quickly** inside a Docker container, fully offline.
- 🛠️ **Customizable** via configuration file for easy API integration and model selection.
  
## How It Works

1. The app continuously monitors a folder (`/app/incoming`) for new PDF files.
2. When a new PDF is added, it extracts the contents using Tesseract OCR.
3. The extracted text is combined with a Markdown prompt and sent to a local AI model API.
4. The summarized response is written back to the folder as a new text file.

## Getting Started

### Prerequisites

- [Docker](https://www.docker.com/get-started) installed on your system.

### Running echonotes with Docker

First, clone the repository:

```bash
git clone https://github.com/your-repo/echonotes.git
cd echonotes
```

Next, ensure that you have an appropriate folder structure with the necessary files:

- A folder where PDFs will be uploaded (`/path/to/your/pdfs`)
- A markdown file for the summarization prompt (`/path/to/summarize-notes.md`)
- A configuration file (`/path/to/config.yml`)

Your configuration file (`config.yml`) should look something like this:

```yaml
api_url: "http://localhost:8000/api/v1/summarize"
bearer_token: "your-token-here"
model: "gpt-3.5-turbo"
```

Now, let's build and run the Docker container.

### Build and Run Using Docker

1. **Build the Docker Image**:
    Run the following command to build the Docker image from the Dockerfile:

    ```bash
    docker build -t echonotes:latest .
    ```

2. **Run the Container**:
    Use the `run.sh` script to mount your directories and start the app.

    ```bash
    ./run.sh echonotes:latest /path/to/your/pdfs /path/to/config.yml /path/to/summarize-notes.md
    ```

This will start the container, and **echonotes** will begin monitoring the `/path/to/your/pdfs` directory for new PDF files. Once a PDF is detected, it will extract the text, prepend the Markdown prompt, send it to your local API for summarization, and save the result as a `.summary.txt` file in the same directory.

### Running echonotes with Docker Compose

We can also leverage Docker Compose for a simplified and more automated approach. Here's how you can do it:

1. **Create a `docker-compose.yml` file** in your project directory:

    ```yaml
    version: '3.8'
    services:
    echonotes:
        build: .
        volumes:
        - ./incoming:/app/incoming        # Relative path to the folder where your PDFs will be dropped
        - ./config.yml:/app/config.yml     # Relative path to the configuration file
        - ./summarize-notes.md:/app/summarize-notes.md  # Relative path to the markdown prompt file
    ```

2. **Run Docker Compose**:

    With Docker Compose, starting your app is as easy as running:

    ```bash
    docker-compose up --build
    ```

This will build the Docker image and launch the container, just like before. The application will monitor the `/path/to/your/pdfs` directory and process PDFs automatically.

### Project Structure

```
echonotes/
├── app/
│   ├── main.py                # Main Python script for monitoring and processing PDFs
│   ├── utils.py               # (Optional) Helper functions for logging or OCR
│   ├── Dockerfile             # Dockerfile for building the container
├── summarize-notes.md         # The markdown file used as a prompt for summarization
├── config.yml                 # Configuration file for API settings
├── run.sh                     # Bash script to build and run the app
└── docker-compose.yml          # Docker Compose configuration file
```

### Configuration

**echonotes** uses a `config.yml` file for essential configuration options:

- **api_url**: The URL of your local API for summarization.
- **bearer_token**: A token used for authenticating with the API.
- **model**: The model to be used in the API (e.g., `gpt-3.5-turbo`).

You can also override these configurations by passing them as command-line arguments or mounting a new `config.yml` file.

### Logging

All operations, including errors, are extensively logged and can be viewed within the Docker container logs. To view real-time logs, you can use the following command:

```bash
docker logs -f <container_id>
```

### Contributing

We welcome contributions to make **echonotes** even better! If you'd like to contribute, feel free to open an issue or submit a pull request. Together, we can make note processing even easier!

### License

This project is licensed under the MIT License.

---

Thank you for choosing **echonotes**! We're excited to see how you'll use it to streamline your note-taking workflow.

Happy summarizing! ✨