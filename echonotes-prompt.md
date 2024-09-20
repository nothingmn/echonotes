Lets create an app together.  Here are the requirements:


## Requirements

### Application Overview
Our application, called "echonotes", will be used to monitor a folder for PDF files, extract the contents, and then send those contents along with a summarize prompt to a local ollama instance.  Be sure to use our cool name "echonotes" in the source code, dockerfile, etc.

1. **Python Application**: 
   - A Python application that runs in a Docker container.
   - The application monitors a specific folder (mounted as a Docker volume) for new PDF files.
   - When a new PDF is detected, the app extracts handwritten notes from the PDF using OCR (Tesseract).
   - The extracted text is written back to the same folder with a filename derived from the original PDF.
   - The contents of the markdown prompt file will be **prepended** (not appended) to the extracted text from the PDF before sending it in the API request.
   - HTTP requests to the API will be made directly without using third-party libraries.
   - The API response will be saved to disk with a filename derived from the PDF.
   - The application will be fully functional offline (including using Tesseract).
   - The path to monitor for new PDFs, this will need to be a hard coded path to a folder within the container at /app/incoming, but mounted as a volume from the caller.
   - The path to the markdown prompt file, this will need to be a hard coded path to a file within the container at /app/summarize-notes.md, but mounted as a volume from the caller.


2. **Configuration**:
   - A `config.yml` file will be used for configuration, passed as a volume to the Docker container.
   - This configuration file will include:
     - The API URL.
     - A bearer token for authentication.
     - The model to be used in the API call.
   - The configuration variables can be overwritten by command-line arguments.

3. **Exception Handling**:
   - Extensive exception handling and management will be expected. 
   - Be sure to intelligently catch all common exceptions and deal with them accordingly, incluiding instructing the user on how to deal with the issue.
   - Never let the application crash, ever.  It should just log exceptions, errors, fatals, etc.. and keep running. Never crash.

4. **Logging**:
   - Extensive logging will be implemented in the Python script to track operations and errors.

5. **Docker Setup**:
   - The application, including its dependencies (Tesseract OCR), will be built and packaged into a single Docker image.
   - The app will be fully deployable offline.

6. **GitHub Workflow**:
   - Create a GitHub Actions workflow to automate the building and pushing of the Docker image to DockerHub.
   - The workflow should:
     - Trigger on new commits to the main branch.
     - Build the Docker image.
     - Push the Docker image to DockerHub using the appropriate credentials (supplied via GitHub secrets).

7. **`run.sh` Bash Script**:
   - Develop a separate `run.sh` script to automate the building and execution of the Docker container.
   - Accept the docker image name as an optional argument, but by default to the latest for the project.
   - Use named arguments to avoid ambiguity
   - The script should:
     - Validate that the required arguments are passed (e.g., config path, prompt file, incoming folder).
     - Provide usage information and fail with a helpful message if invalid or missing arguments are provided.
     - Build the Docker image locally.
     - Run the Docker container, mounting the appropriate volumes (e.g., PDF monitoring folder, config file).

8. **Project README.md**
   - Write a README file, in markdown suitable for github
   - It will provide an overview of the project, in a moderate level of detail
   - It must have a professional and excited tone
   - It will include instructions as to how to use the project via docker (include sample code)
   - It will also include instructions as to how to use docker compose (include sample code)
   - For the docker compose sample, assume relative paths to the files and folders


9. **Ollama System Prompt**
   - Write a file, "prompt.md", which is the default value for the project's "markdown prompt file"
   - In this file, create a LLM prompt appropriate for summarizing hand written notes.  
   - The structure should follow the "Cornell Method".  Research this method to find an optimal structure to follow.
