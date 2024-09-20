#!/bin/bash

# Default image name
IMAGE_NAME="echonotes:latest"

# Usage information
usage() {
    echo "Usage: ./run.sh [--image-name <docker_image_name>] --incoming <incoming_folder> --config <config_file> --prompt <markdown_file>"
    echo "  --image-name: Optional, Docker image name (default: echonotes:latest)"
    echo "  --incoming: Required, path to the folder where PDFs will be monitored"
    echo "  --config: Required, path to the config.yml file"
    echo "  --prompt: Required, path to the markdown prompt file"
    exit 1
}

# Function to validate that required arguments are provided
validate_args() {
    if [[ -z "$INCOMING_FOLDER" || -z "$CONFIG_FILE" || -z "$PROMPT_FILE" ]]; then
        echo "Error: Missing required arguments."
        usage
    fi

    if [[ ! -d "$INCOMING_FOLDER" ]]; then
        echo "Error: Incoming folder '$INCOMING_FOLDER' does not exist."
        exit 1
    fi

    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "Error: Config file '$CONFIG_FILE' does not exist."
        exit 1
    fi

    if [[ ! -f "$PROMPT_FILE" ]]; then
        echo "Error: Prompt file '$PROMPT_FILE' does not exist."
        exit 1
    fi
}

# Parse named arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --image-name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --incoming)
            INCOMING_FOLDER="$2"
            shift 2
            ;;
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --prompt)
            PROMPT_FILE="$2"
            shift 2
            ;;
        *)
            echo "Error: Invalid argument '$1'"
            usage
            ;;
    esac
done

# Validate required arguments
validate_args

# Build the Docker image
echo "Building Docker image $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" .

# Run the Docker container
echo "Running Docker container..."
docker run -v "$INCOMING_FOLDER:/app/incoming" \
           -v "$CONFIG_FILE:/app/config.yml" \
           -v "$PROMPT_FILE:/app/summarize-notes.md" \
           "$IMAGE_NAME"
