import os
import pickle
import queue
import re
import threading
import time
import gc
from contextlib import contextmanager
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
APP_ROOT = "/app"
DEFAULT_INCOMING_PATH = f"{APP_ROOT}/incoming"
DEFAULT_VAULT_PATH = f"{APP_ROOT}/vault"
DEFAULT_CONFIG_DIR = f"{APP_ROOT}/config"
DEFAULT_CONFIG_DEFAULTS_DIR = f"{APP_ROOT}/config-defaults"
DEFAULT_CONFIG_PATH = f"{DEFAULT_CONFIG_DIR}/config.yml"
DEFAULT_CONFIG_FALLBACK_PATH = f"{DEFAULT_CONFIG_DEFAULTS_DIR}/config.yml"
LEGACY_CONFIG_PATH = f"{APP_ROOT}/config.yml"
DEFAULT_SUMMARY_PROMPT_NAME = "summarize-notes.md"
DEFAULT_SUMMARY_PROMPT_PATH = f"{DEFAULT_CONFIG_DIR}/{DEFAULT_SUMMARY_PROMPT_NAME}"
DEFAULT_SUMMARY_PROMPT_FALLBACK_PATH = f"{DEFAULT_CONFIG_DEFAULTS_DIR}/{DEFAULT_SUMMARY_PROMPT_NAME}"
DEFAULT_TRANSCRIPT_FORMAT_PROMPT_NAME = "format-transcript.md"
DEFAULT_TRANSCRIPT_FORMAT_PROMPT_PATH = f"{DEFAULT_CONFIG_DIR}/{DEFAULT_TRANSCRIPT_FORMAT_PROMPT_NAME}"
DEFAULT_OBSIDIAN_EXTRACT_PROMPT_NAME = "obsidian-extract.md"
DEFAULT_OBSIDIAN_EXTRACT_PROMPT_PATH = f"{DEFAULT_CONFIG_DIR}/{DEFAULT_OBSIDIAN_EXTRACT_PROMPT_NAME}"
DEFAULT_OBSIDIAN_EXTRACT_PROMPT_FALLBACK_PATH = f"{DEFAULT_CONFIG_DEFAULTS_DIR}/{DEFAULT_OBSIDIAN_EXTRACT_PROMPT_NAME}"
DEFAULT_OBSIDIAN_TEMPLATE_NAME = "obsidian-template.md"
DEFAULT_OBSIDIAN_TEMPLATE_PATH = f"{DEFAULT_CONFIG_DIR}/{DEFAULT_OBSIDIAN_TEMPLATE_NAME}"
DEFAULT_LLM_TIMEOUT_SECONDS = 120
DEFAULT_CHUNK_MAX_INPUT_CHARS = 24000
DEFAULT_CHUNK_TARGET_CHARS = 16000
DEFAULT_CHUNK_OVERLAP_CHARS = 400
DEFAULT_INCOMING_RESCAN_INTERVAL_SECONDS = 5
DEFAULT_GPU_WHISPER_BATCH_SIZE = 16
DEFAULT_CPU_WHISPER_BATCH_SIZE = 4
DEFAULT_GPU_MIN_BATCH_SIZE = 1
DEFAULT_GPU_OOM_FALLBACK = "cpu"
TORCH_LOAD_PATCH_LOCK = threading.Lock()
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
DEFAULT_OBSIDIAN_TEMPLATE = """{{frontmatter}}

# {{title}}

## Audio

![[{{audio_filename}}]]

## Files

- Transcript: [[{{transcript_filename}}]]
{{summary_file_line}}

## Linked Entities

{{entity_links_section}}

## Summary

{{summary_content}}

## Context

{{context_section}}

## Main Ideas

{{main_ideas_section}}

## Decisions

{{decisions_section}}

## Action Items

{{action_items_section}}

## Challenges and Risks

{{challenges_and_risks_section}}

## Next Steps

{{next_steps_section}}

## Transcript

{{transcript_body}}
"""
DEFAULT_OBSIDIAN_EXTRACT_PROMPT = """Extract structured Obsidian note data from the following transcript or summary.

Return JSON only. Do not return Markdown. Do not wrap the JSON in code fences.

Schema:
{
  "context": "string or null",
  "main_ideas": ["string"],
  "decisions": ["string"],
  "action_items": ["string"],
  "recommendations": ["string"],
  "insights": ["string"],
  "challenges_and_risks": ["string"],
  "next_steps": ["string"],
  "inferred_people": ["string"],
  "inferred_projects": ["string"],
  "inferred_topics": ["string"],
  "inferred_context": "string or null",
  "inferred_meeting_type": "string or null"
}

Rules:
- Use only information supported by the input.
- Never invent names, roles, deadlines, projects, or actions.
- `inferred_*` fields may contain careful interpretation, but only when strongly supported.
- Keep list items short and atomic.
- Use empty arrays when nothing is present.
- Use null for unknown single-value fields.
"""


# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Load config
def load_config(config_path=None):
    if not config_path:
        config_path = (
            os.environ.get("ECHONOTES_CONFIG_PATH")
            or first_existing_path(DEFAULT_CONFIG_PATH, DEFAULT_CONFIG_FALLBACK_PATH, LEGACY_CONFIG_PATH)
            or DEFAULT_CONFIG_PATH
        )

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


def get_default_whisper_model():
    try:
        import torch

        if torch.cuda.is_available():
            return "small"
    except Exception:
        pass

    return "base"


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


def get_diarization_settings(config):
    return {
        "enabled": bool(config.get("diarization_enabled", True)),
        "hf_token": (config.get("diarization_hf_token") or "").strip(),
        "model_name": (config.get("diarization_model_name") or "").strip() or None,
        "num_speakers": config.get("diarization_num_speakers"),
        "min_speakers": config.get("diarization_min_speakers"),
        "max_speakers": config.get("diarization_max_speakers"),
    }


def get_transcription_runtime_settings(config):
    configured_batch_size = config.get("whisper_batch_size")
    configured_min_batch_size = config.get("whisper_min_batch_size")
    gpu_oom_fallback = (config.get("gpu_oom_fallback") or DEFAULT_GPU_OOM_FALLBACK).strip().lower()

    batch_size = int(configured_batch_size) if configured_batch_size not in (None, "") else None
    min_batch_size = (
        int(configured_min_batch_size)
        if configured_min_batch_size not in (None, "")
        else DEFAULT_GPU_MIN_BATCH_SIZE
    )

    return {
        "batch_size": batch_size,
        "min_batch_size": max(1, min_batch_size),
        "gpu_oom_fallback": gpu_oom_fallback if gpu_oom_fallback in ("cpu", "fail") else DEFAULT_GPU_OOM_FALLBACK,
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
        self.timeout_seconds = self._parse_timeout_seconds(settings.get("timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS))
        self.temperature = settings.get("temperature")
        self.max_tokens = settings.get("max_tokens")

    def generate(self, prompt):
        raise NotImplementedError

    @staticmethod
    def _parse_timeout_seconds(timeout_value):
        if timeout_value is None:
            return None

        if isinstance(timeout_value, str):
            normalized = timeout_value.strip().lower()
            if normalized in ("", "none", "null", "false", "off"):
                return None
            timeout_value = normalized

        timeout_seconds = float(timeout_value)
        if timeout_seconds <= 0:
            return None
        return timeout_seconds

    def _post_json(self, url, headers, payload):
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


class RecoverableProcessingError(RuntimeError):
    pass


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


def first_existing_path(*paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def resolve_config_asset_path(configured_path, default_path, fallback_path=None):
    if configured_path and os.path.exists(configured_path):
        return configured_path

    if configured_path:
        logging.warning(f"Configured path does not exist, falling back to defaults: {configured_path}")

    return first_existing_path(default_path, fallback_path) or configured_path or default_path or fallback_path


def path_has_hidden_component(path, root_path=None):
    if not path:
        return False

    normalized_path = os.path.normpath(path)
    if root_path:
        normalized_root = os.path.normpath(root_path)
        try:
            normalized_path = os.path.relpath(normalized_path, normalized_root)
        except ValueError:
            pass

    parts = [part for part in normalized_path.split(os.sep) if part not in ("", ".", "..")]
    return any(part.startswith(".") for part in parts)


def strip_outer_fenced_block(text):
    if not isinstance(text, str):
        return ""

    stripped = text.strip()
    if not stripped:
        return ""

    for fence in ("```", "~~~"):
        if not stripped.startswith(fence):
            continue

        lines = stripped.splitlines()
        if len(lines) < 2:
            return stripped

        if lines[0].strip().startswith(fence) and lines[-1].strip() == fence:
            return "\n".join(lines[1:-1]).strip()

    return stripped


def sanitize_markdown_output(text):
    return strip_outer_fenced_block((text or "").replace("\r\n", "\n")).strip()


@contextmanager
def whisperx_torch_load_compat():
    try:
        import torch
        from omegaconf import DictConfig, ListConfig
        from omegaconf.base import ContainerMetadata
    except Exception:
        yield
        return

    serialization = getattr(torch, "serialization", None)
    if serialization and hasattr(serialization, "add_safe_globals"):
        try:
            serialization.add_safe_globals([ListConfig, DictConfig, ContainerMetadata])
        except Exception as safe_globals_error:
            logging.debug(f"Unable to register torch safe globals for WhisperX: {safe_globals_error}")

    original_torch_load = torch.load

    def compat_torch_load(*args, **kwargs):
        try:
            return original_torch_load(*args, **kwargs)
        except pickle.UnpicklingError as exc:
            if kwargs.get("weights_only", True) is False:
                raise

            if "Weights only load failed" not in str(exc):
                raise

            retry_kwargs = dict(kwargs)
            retry_kwargs["weights_only"] = False
            if args and hasattr(args[0], "seek"):
                try:
                    args[0].seek(0)
                except Exception as seek_error:
                    logging.debug(f"Unable to rewind checkpoint stream before torch.load retry: {seek_error}")
            logging.warning(
                "Retrying torch.load with weights_only=False for WhisperX/pyannote checkpoint compatibility"
            )
            return original_torch_load(*args, **retry_kwargs)

    with TORCH_LOAD_PATCH_LOCK:
        torch.load = compat_torch_load
        try:
            yield
        finally:
            torch.load = original_torch_load


def get_summary_prompt_path(config):
    return resolve_config_asset_path(
        config.get("summary_prompt_path"),
        DEFAULT_SUMMARY_PROMPT_PATH,
        DEFAULT_SUMMARY_PROMPT_FALLBACK_PATH,
    )


def get_transcript_format_prompt_path(config):
    return resolve_config_asset_path(
        config.get("transcript_format_prompt_path"),
        DEFAULT_TRANSCRIPT_FORMAT_PROMPT_PATH,
    )


def get_obsidian_extract_prompt_path(config):
    return resolve_config_asset_path(
        config.get("obsidian_extract_prompt_path"),
        DEFAULT_OBSIDIAN_EXTRACT_PROMPT_PATH,
        DEFAULT_OBSIDIAN_EXTRACT_PROMPT_FALLBACK_PATH,
    )


def get_obsidian_template_path(config):
    return resolve_config_asset_path(
        config.get("obsidian_template_path"),
        DEFAULT_OBSIDIAN_TEMPLATE_PATH,
        os.path.join(os.path.dirname(get_summary_prompt_path(config)), DEFAULT_OBSIDIAN_TEMPLATE_NAME),
    )


def get_vault_path(config):
    return config.get("vault_path", DEFAULT_VAULT_PATH)


def get_watch_path(config):
    return config.get("path_to_watch", DEFAULT_INCOMING_PATH)


def get_incoming_rescan_interval_seconds(config):
    configured_interval = config.get("incoming_rescan_interval_seconds")
    if configured_interval in (None, ""):
        return DEFAULT_INCOMING_RESCAN_INTERVAL_SECONDS
    return max(0, int(configured_interval))


def render_template(template, context):
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def sanitize_json_output(text):
    sanitized = strip_outer_fenced_block((text or "").replace("\r\n", "\n")).strip()
    if not sanitized:
        raise ValueError("Structured JSON output is empty")

    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        start = sanitized.find("{")
        end = sanitized.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(sanitized[start : end + 1])


def normalize_optional_string(value):
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def normalize_string_list(values):
    if not isinstance(values, list):
        return []

    normalized = []
    seen = set()
    for value in values:
        text = normalize_optional_string(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def normalize_obsidian_structured_data(data):
    if not isinstance(data, dict):
        data = {}

    return {
        "context": normalize_optional_string(data.get("context")),
        "main_ideas": normalize_string_list(data.get("main_ideas")),
        "decisions": normalize_string_list(data.get("decisions")),
        "action_items": normalize_string_list(data.get("action_items")),
        "recommendations": normalize_string_list(data.get("recommendations")),
        "insights": normalize_string_list(data.get("insights")),
        "challenges_and_risks": normalize_string_list(data.get("challenges_and_risks")),
        "next_steps": normalize_string_list(data.get("next_steps")),
        "inferred_people": normalize_string_list(data.get("inferred_people")),
        "inferred_projects": normalize_string_list(data.get("inferred_projects")),
        "inferred_topics": normalize_string_list(data.get("inferred_topics")),
        "inferred_context": normalize_optional_string(data.get("inferred_context")),
        "inferred_meeting_type": normalize_optional_string(data.get("inferred_meeting_type")),
    }


def render_markdown_bullets(items):
    if not items:
        return "None stated."
    return "\n".join(f"- {item}" for item in items)


def strip_leading_markdown_heading(text):
    if not text:
        return ""

    lines = text.splitlines()
    if not lines:
        return ""

    if not lines[0].lstrip().startswith("#"):
        return text

    index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).strip()


def sanitize_obsidian_link_target(value):
    if not value:
        return None
    return re.sub(r'[\[\]\|#^]', "", value).strip() or None


def render_entity_links_section(label, folder_name, items):
    normalized_items = []
    seen = set()
    for item in items:
        link_target = sanitize_obsidian_link_target(item)
        if not link_target:
            continue
        key = link_target.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized_items.append(f"[[{folder_name}/{link_target}]]")

    if not normalized_items:
        return None

    return f"- {label}: " + ", ".join(normalized_items)


def render_frontmatter(metadata):
    return "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip() + "\n---"


def format_filesystem_timestamp(timestamp_value):
    if timestamp_value is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp_value))


def get_source_file_metadata(source_path):
    try:
        stat = os.stat(source_path)
    except OSError:
        return {"source_created": None, "source_modified": None}

    source_created = None
    if hasattr(stat, "st_birthtime"):
        source_created = format_filesystem_timestamp(stat.st_birthtime)

    return {
        "source_created": source_created,
        "source_modified": format_filesystem_timestamp(stat.st_mtime),
    }


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


def normalize_speaker_name(raw_speaker, speaker_names):
    if not raw_speaker:
        return None

    if raw_speaker not in speaker_names:
        speaker_names[raw_speaker] = f"Speaker {len(speaker_names) + 1}"

    return speaker_names[raw_speaker]


def normalize_diarization_turns(diarization_segments):
    if diarization_segments is None:
        return []

    if hasattr(diarization_segments, "itertuples"):
        turns = []
        for row in diarization_segments.itertuples(index=False):
            speaker = getattr(row, "speaker", None)
            start = getattr(row, "start", None)
            end = getattr(row, "end", None)
            if speaker is None or start is None or end is None:
                continue
            turns.append(
                {
                    "start": float(start),
                    "end": float(end),
                    "speaker": speaker,
                }
            )
        return turns

    turns = []
    for segment in diarization_segments:
        speaker = segment.get("speaker")
        start = segment.get("start")
        end = segment.get("end")
        if speaker is None or start is None or end is None:
            continue
        turns.append(
            {
                "start": float(start),
                "end": float(end),
                "speaker": speaker,
            }
        )
    return turns


def get_speaker_for_interval(start, end, diarization_turns):
    if not diarization_turns:
        return None

    interval_start = float(start or 0)
    interval_end = float(end if end is not None else interval_start)
    if interval_end < interval_start:
        interval_end = interval_start

    best_speaker = None
    best_overlap = 0.0

    for turn in diarization_turns:
        overlap = min(interval_end, turn["end"]) - max(interval_start, turn["start"])
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn["speaker"]

    if best_speaker:
        return best_speaker

    midpoint = (interval_start + interval_end) / 2
    nearest_turn = min(
        diarization_turns,
        key=lambda turn: min(abs(midpoint - turn["start"]), abs(midpoint - turn["end"])),
    )
    return nearest_turn["speaker"]


def combine_word_tokens(words):
    tokens = [word.get("text", "").strip() for word in words if word.get("text", "").strip()]
    return " ".join(tokens).strip()


def append_speakerized_segment(target, words, fallback_segment=None):
    if not words:
        return

    text = combine_word_tokens(words)
    if not text:
        return

    segment_start = words[0].get("start")
    segment_end = words[-1].get("end")

    if segment_start is None and fallback_segment:
        segment_start = fallback_segment.get("start", 0)
    if segment_end is None and fallback_segment:
        segment_end = fallback_segment.get("end")

    target.append(
        {
            "start": segment_start if segment_start is not None else 0,
            "end": segment_end,
            "text": text,
            "speaker": words[0].get("speaker"),
        }
    )


def assign_diarization_to_transcript_segments(segments, diarization_segments):
    diarization_turns = normalize_diarization_turns(diarization_segments)
    if not diarization_turns:
        return segments

    speakerized_segments = []

    for segment in segments:
        words = segment.get("words") or []
        aligned_words = []

        for word in words:
            text = (word.get("word") or "").strip()
            if not text:
                continue

            word_start = word.get("start", segment.get("start", 0))
            word_end = word.get("end")
            if word_end is None:
                word_end = word_start if word_start is not None else segment.get("end")

            aligned_words.append(
                {
                    "text": text,
                    "start": word_start,
                    "end": word_end,
                    "speaker": get_speaker_for_interval(word_start, word_end, diarization_turns),
                }
            )

        if aligned_words:
            current_words = []
            current_speaker = None

            for word in aligned_words:
                word_speaker = word.get("speaker")
                if current_words and word_speaker != current_speaker:
                    append_speakerized_segment(speakerized_segments, current_words, segment)
                    current_words = []

                current_words.append(word)
                current_speaker = word_speaker

            append_speakerized_segment(speakerized_segments, current_words, segment)
            continue

        speakerized_segments.append(
            {
                "start": segment.get("start", 0),
                "end": segment.get("end"),
                "text": segment.get("text", "").strip(),
                "speaker": get_speaker_for_interval(
                    segment.get("start", 0),
                    segment.get("end"),
                    diarization_turns,
                ),
            }
        )

    return [segment for segment in speakerized_segments if segment.get("text")]


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


def get_default_batch_size_for_device(device):
    return DEFAULT_GPU_WHISPER_BATCH_SIZE if device == "cuda" else DEFAULT_CPU_WHISPER_BATCH_SIZE


def build_batch_size_plan(device, preferred_batch_size=None, min_batch_size=1):
    batch_size = preferred_batch_size or get_default_batch_size_for_device(device)
    batch_size = max(1, int(batch_size))

    if device != "cuda":
        return [batch_size]

    floor = max(1, int(min_batch_size))
    plan = []
    current = batch_size

    while current >= floor:
        plan.append(current)
        if current == floor:
            break
        next_size = max(floor, current // 2)
        if next_size == current:
            break
        current = next_size

    if plan[-1] != floor:
        plan.append(floor)

    return plan


def is_cuda_oom_error(error):
    message = str(error).lower()
    return "cuda out of memory" in message or "cuda failed with error out of memory" in message


def build_prompt(prompt_instructions, input_text, heading="INPUT"):
    return f"{prompt_instructions}\n\n# {heading}:\n\n{input_text}"


def get_missing_ocr_dependencies():
    required_binaries = ("pdfinfo", "pdftoppm", "tesseract")
    return [binary for binary in required_binaries if not shutil.which(binary)]

# Helper function to extract text from PDF using OCR and write it back to the same folder
def extract_text_from_pdf(pdf_path):
    try:
        logging.info(f"Extracting text from {pdf_path}")
        
        # Initialize empty string to collect extracted text
        text = ""
        
        # Extract text using PyPDF2
        with open(pdf_path, "rb") as f:
            pdf = PdfReader(f)
            if pdf.is_encrypted:
                decrypt_result = pdf.decrypt("")
                if not decrypt_result:
                    raise RecoverableProcessingError(
                        f"Encrypted PDF requires a password and was skipped: {pdf_path}"
                    )
            num_pages = len(pdf.pages)
            for page_num in range(num_pages):
                page = pdf.pages[page_num]
                text += page.extract_text() or ""

        # Fallback to OCR if no text is extracted
        if not text.strip():
            logging.warning(f"No extractable text found in {pdf_path}. Falling back to OCR.")

            missing_dependencies = get_missing_ocr_dependencies()
            if missing_dependencies:
                missing_list = ", ".join(missing_dependencies)
                raise RecoverableProcessingError(
                    f"OCR fallback requires installed executable dependencies ({missing_list}); "
                    f"skipped OCR for {pdf_path}"
                )

            try:
                images = convert_from_path(pdf_path)
                for img in images:
                    text += pytesseract.image_to_string(img)
            except Exception as ocr_error:
                raise RecoverableProcessingError(
                    f"OCR fallback failed for {pdf_path}. Ensure Poppler and Tesseract are installed "
                    f"and executable. Original error: {ocr_error}"
                ) from ocr_error

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
    except RecoverableProcessingError:
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
    def __init__(self, whisper_model, diarization_settings=None, runtime_settings=None):
        import torch

        self.whisper_model = whisper_model
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.runtime_settings = runtime_settings or {}
        self.batch_size = self.runtime_settings.get("batch_size") or get_default_batch_size_for_device(self.device)
        self.min_batch_size = self.runtime_settings.get("min_batch_size", DEFAULT_GPU_MIN_BATCH_SIZE)
        self.gpu_oom_fallback = self.runtime_settings.get("gpu_oom_fallback", DEFAULT_GPU_OOM_FALLBACK)
        self.align_models = {}
        self.models = {}
        self.diarization_settings = diarization_settings or {}
        self.diarization_models = {}

        logging.info(
            f"Loading WhisperX model at startup "
            f"(device={self.device}, compute_type={self._get_compute_type(self.device)}, "
            f"batch_size={self.batch_size}, model={self.whisper_model})"
        )
        self._get_model(self.device)

        if self.diarization_settings.get("enabled", True):
            hf_token = self.diarization_settings.get("hf_token")
            if hf_token:
                self._get_diarization_model(self.device)
            else:
                logging.warning(
                    "Speaker diarization is enabled but no diarization_hf_token is configured; "
                    "transcripts will not include speaker labels"
                )

    def _get_compute_type(self, device):
        return "float16" if device == "cuda" else "int8"

    def _clear_runtime_memory(self, device):
        gc.collect()
        if device == "cuda":
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()

    def _get_model(self, device):
        if device not in self.models:
            logging.info(
                f"Loading WhisperX model "
                f"(device={device}, compute_type={self._get_compute_type(device)}, model={self.whisper_model})"
            )
            with whisperx_torch_load_compat():
                self.models[device] = whisperx.load_model(
                    self.whisper_model,
                    device=device,
                    compute_type=self._get_compute_type(device),
                )
        return self.models[device]

    def _get_align_model(self, language_code, device):
        cache_key = (device, language_code)
        if cache_key not in self.align_models:
            logging.info(f"Loading WhisperX alignment model for language: {language_code} on {device}")
            with whisperx_torch_load_compat():
                self.align_models[cache_key] = whisperx.load_align_model(
                    language_code=language_code,
                    device=device,
                )
        return self.align_models[cache_key]

    def _get_diarization_model(self, device):
        if device not in self.diarization_models:
            from whisperx.diarize import DiarizationPipeline

            logging.info(f"Loading WhisperX diarization pipeline at startup (device={device})")
            with whisperx_torch_load_compat():
                self.diarization_models[device] = DiarizationPipeline(
                    model_name=self.diarization_settings.get("model_name"),
                    use_auth_token=self.diarization_settings.get("hf_token"),
                    device=device,
                )
        return self.diarization_models[device]

    def _transcribe_with_device(self, audio_path, audio, device):
        batch_plan = build_batch_size_plan(
            device,
            preferred_batch_size=self.batch_size if device == self.device else None,
            min_batch_size=self.min_batch_size,
        )
        model = self._get_model(device)
        last_error = None

        for batch_size in batch_plan:
            try:
                if batch_size != batch_plan[0]:
                    logging.info(
                        f"Retrying WhisperX transcription for {audio_path} "
                        f"with smaller batch_size={batch_size} on {device}"
                    )
                return model.transcribe(audio, batch_size=batch_size)
            except RuntimeError as error:
                last_error = error
                if device != "cuda" or not is_cuda_oom_error(error):
                    raise

                logging.warning(
                    f"WhisperX GPU transcription ran out of memory for {audio_path} "
                    f"with batch_size={batch_size}: {error}"
                )
                self._clear_runtime_memory(device)

        if last_error:
            raise last_error

    def transcribe(self, audio_path):
        try:
            logging.info(
                f"Converting audio to text using WhisperX: {audio_path} "
                f"(device={self.device}, model={self.whisper_model})"
            )

            audio = whisperx.load_audio(audio_path)
            transcribe_device = self.device

            try:
                result = self._transcribe_with_device(audio_path, audio, self.device)
            except RuntimeError as error:
                if self.device != "cuda" or not is_cuda_oom_error(error) or self.gpu_oom_fallback != "cpu":
                    raise

                logging.warning(
                    f"Falling back to CPU transcription for {audio_path} after GPU OOM"
                )
                self._clear_runtime_memory("cuda")
                transcribe_device = "cpu"
                result = self._transcribe_with_device(audio_path, audio, transcribe_device)

            transcript_result = result

            segments = transcript_result.get("segments", [])
            if segments and result.get("language"):
                try:
                    align_model, metadata = self._get_align_model(result["language"], transcribe_device)
                    aligned_result = whisperx.align(
                        segments,
                        align_model,
                        metadata,
                        audio,
                        transcribe_device,
                        return_char_alignments=False,
                    )
                    transcript_result = aligned_result
                    segments = transcript_result.get("segments", segments)
                except Exception as align_error:
                    logging.warning(
                        f"WhisperX alignment failed for {audio_path}; using unaligned transcript: {align_error}"
                    )

            diarization_model = None
            if self.diarization_settings.get("enabled", True) and self.diarization_settings.get("hf_token"):
                diarization_model = self._get_diarization_model(transcribe_device)

            if diarization_model and segments:
                try:
                    diarization_kwargs = {}
                    for key in ("num_speakers", "min_speakers", "max_speakers"):
                        value = self.diarization_settings.get(key)
                        if value is not None and value != "":
                            diarization_kwargs[key] = int(value)

                    diarize_segments = diarization_model(audio, **diarization_kwargs)
                    segments = assign_diarization_to_transcript_segments(segments, diarize_segments)
                    transcript_result["segments"] = segments
                except Exception as diarization_error:
                    logging.warning(
                        f"WhisperX diarization failed for {audio_path}; using transcript without speaker labels: "
                        f"{diarization_error}"
                    )

            speaker_names = {}
            normalized_segments = [
                {
                    "start": segment.get("start", 0),
                    "end": segment.get("end"),
                    "text": segment.get("text", "").strip(),
                    "speaker": normalize_speaker_name(segment.get("speaker"), speaker_names),
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
        if os.path.basename(source_path).startswith("."):
            logging.info(f"Skipping hidden path: {source_path}")
            return

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
                    source_path=working_file_path,
                    audio_path=vault_files[0],
                    transcript_path=extracted_text_file,
                    transcript_content=text,
                    transcript_body=transcript_body,
                    summary_path=output_filename,
                    transcription=transcription,
                )
                output_files.append(obsidian_file)
                vault_files.extend([extracted_text_file, output_filename, obsidian_file])
                copy_files_to_folder(vault_files, self.vault_folder)

            move_to_completed(working_file_path, output_files, self.completed_folder)
            logging.info(f"Processing is complete for {working_file_path}")
        except RecoverableProcessingError as recoverable_error:
            logging.warning(str(recoverable_error))
            move_to_completed(working_file_path, output_files, self.completed_folder)
            logging.info(f"Skipped processing for {working_file_path}")
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
                prompt_path = get_transcript_format_prompt_path(self.config)
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

    def generate_obsidian_structured_data(self, transcript_text, summary_content=""):
        prompt_path = get_obsidian_extract_prompt_path(self.config)
        prompt_content = load_prompt(prompt_path, DEFAULT_OBSIDIAN_EXTRACT_PROMPT)

        extraction_input = transcript_text
        if len(extraction_input) > self.chunking["max_input_chars"] and summary_content.strip():
            logging.info("Using summary content as Obsidian extraction input due to transcript length")
            extraction_input = summary_content

        prompt = build_prompt(prompt_content, extraction_input)
        return normalize_obsidian_structured_data(
            sanitize_json_output(self.llm_client.generate(prompt))
        )

    def build_obsidian_frontmatter(self, note_type, source_path, audio_path, transcript_path, structured_data, summary_content, transcript_body, detected_language):
        source_metadata = get_source_file_metadata(source_path)
        has_diarization = "Speaker 1:" in transcript_body or "Speaker 2:" in transcript_body

        tags = ["echonotes"]
        if note_type == "video-note":
            tags.append("video")
        else:
            tags.append("audio")
        if transcript_path:
            tags.append("transcript")
        if summary_content.strip():
            tags.append("summary")
        if has_diarization:
            tags.append("diarized")

        return {
            "type": note_type,
            "source": "EchoNotes",
            "filename": os.path.basename(source_path),
            "audio_filename": os.path.basename(audio_path),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source_created": source_metadata["source_created"],
            "source_modified": source_metadata["source_modified"],
            "detected_language": detected_language,
            "has_summary": bool(summary_content.strip()),
            "has_transcript": bool(transcript_path),
            "has_diarization": has_diarization,
            "inferred_people": structured_data["inferred_people"],
            "inferred_projects": structured_data["inferred_projects"],
            "inferred_topics": structured_data["inferred_topics"],
            "inferred_context": structured_data["inferred_context"],
            "inferred_meeting_type": structured_data["inferred_meeting_type"],
            "tags": tags,
        }

    def build_obsidian_context(
        self,
        source_path,
        audio_path,
        transcript_path,
        transcript_body,
        summary_path,
        structured_data,
        transcription,
    ):
        summary_content = ""
        summary_filename = ""
        if summary_path and os.path.exists(summary_path):
            with open(summary_path, 'r') as summary_file:
                summary_content = strip_leading_markdown_heading(summary_file.read().strip())
            summary_filename = os.path.basename(summary_path)

        note_type = "video-note" if is_video_file(source_path) else "audio-note"
        frontmatter = self.build_obsidian_frontmatter(
            note_type,
            source_path,
            audio_path,
            transcript_path,
            structured_data,
            summary_content,
            transcript_body,
            transcription.get("language"),
        )

        entity_lines = []
        for label, folder_name, values in (
            ("People", "People", structured_data["inferred_people"]),
            ("Projects", "Projects", structured_data["inferred_projects"]),
            ("Topics", "Topics", structured_data["inferred_topics"]),
        ):
            line = render_entity_links_section(label, folder_name, values)
            if line:
                entity_lines.append(line)

        return {
            "frontmatter": render_frontmatter(frontmatter),
            "title": os.path.splitext(os.path.basename(audio_path))[0],
            "audio_filename": os.path.basename(audio_path),
            "transcript_filename": os.path.basename(transcript_path),
            "summary_filename": summary_filename,
            "summary_file_line": f"- Summary: [[{summary_filename}]]" if summary_filename else "- Summary: Not generated",
            "summary_content": summary_content or "None stated.",
            "context_section": structured_data["context"] or "None stated.",
            "transcript_body": transcript_body,
            "entity_links_section": "\n".join(entity_lines) if entity_lines else "None stated.",
            "main_ideas_section": render_markdown_bullets(structured_data["main_ideas"]),
            "decisions_section": render_markdown_bullets(structured_data["decisions"]),
            "action_items_section": render_markdown_bullets(structured_data["action_items"]),
            "challenges_and_risks_section": render_markdown_bullets(structured_data["challenges_and_risks"]),
            "next_steps_section": render_markdown_bullets(structured_data["next_steps"]),
        }

    def generate_formatted_transcript(self, prompt_content, transcript_text):
        if not self.chunking["enabled"] or len(transcript_text) <= self.chunking["max_input_chars"]:
            return sanitize_markdown_output(
                self.llm_client.generate(build_prompt(prompt_content, transcript_text))
            )

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
            formatted_chunk = sanitize_markdown_output(self.llm_client.generate(chunk_prompt))
            if formatted_chunk:
                formatted_chunks.append(formatted_chunk)

        return "\n\n".join(formatted_chunks)

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

    def write_obsidian_note(self, source_path, audio_path, transcript_path, transcript_content, transcript_body, summary_path, transcription):
        output_filename = build_output_path(audio_path, ".md")
        template = load_prompt(
            get_obsidian_template_path(self.config),
            DEFAULT_OBSIDIAN_TEMPLATE,
        )
        structured_data = normalize_obsidian_structured_data({})

        if self.llm_client:
            try:
                logging.info(f"Generating structured Obsidian data for {audio_path}")
                summary_content = ""
                if summary_path and os.path.exists(summary_path):
                    with open(summary_path, 'r') as summary_file:
                        summary_content = summary_file.read().strip()
                structured_data = self.generate_obsidian_structured_data(transcript_content, summary_content)
            except Exception as extraction_error:
                logging.warning(
                    f"Structured Obsidian extraction failed for {audio_path}; using template defaults: "
                    f"{extraction_error}"
                )

        context = self.build_obsidian_context(
            source_path,
            audio_path,
            transcript_path,
            transcript_body,
            summary_path,
            structured_data,
            transcription,
        )

        with open(output_filename, 'w') as output_file:
            output_file.write(render_template(template, context))

        logging.info(f"Obsidian note saved to {output_filename}")
        return output_filename


class ProcessingWorker(threading.Thread):
    def __init__(
        self,
        worker_id,
        job_queue,
        pending_jobs,
        config,
        working_folder,
        completed_folder,
        vault_folder,
        whisper_model,
    ):
        super().__init__(daemon=True, name=f"worker-{worker_id}")
        self.worker_id = worker_id
        self.job_queue = job_queue
        self.pending_jobs = pending_jobs
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
        transcriber = WhisperXTranscriber(
            self.whisper_model,
            get_diarization_settings(self.config),
            get_transcription_runtime_settings(self.config),
        )
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
                if job is not None:
                    self.pending_jobs.release(job)
                self.job_queue.task_done()


class PendingJobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending_jobs = set()

    def claim(self, file_path):
        with self._lock:
            if file_path in self._pending_jobs:
                return False
            self._pending_jobs.add(file_path)
            return True

    def release(self, file_path):
        with self._lock:
            self._pending_jobs.discard(file_path)


class WorkerPool:
    def __init__(
        self,
        worker_count,
        job_queue,
        pending_jobs,
        config,
        working_folder,
        completed_folder,
        vault_folder,
        whisper_model,
    ):
        self.workers = [
            ProcessingWorker(
                worker_id=index + 1,
                job_queue=job_queue,
                pending_jobs=pending_jobs,
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
        return sanitize_markdown_output(api_response)
    except Exception as e:
        logging.error(f"Error formatting API response to Markdown: {e}")
        return ""
    
# Event handler for newly created files
class FileHandler(FileSystemEventHandler):
    def __init__(self, job_queue, pending_jobs, path_to_watch):
        self.job_queue = job_queue
        self.pending_jobs = pending_jobs
        self.path_to_watch = path_to_watch

    def _should_ignore_path(self, file_path):
        if path_has_hidden_component(file_path, self.path_to_watch):
            logging.info(f"Ignoring hidden path: {file_path}")
            return True

        if file_path.lower().endswith(TEMP_FILE_SUFFIXES):
            logging.info(f"Ignoring temporary file: {file_path}")
            return True

        if not is_supported_file(file_path):
            logging.info(f"Ignoring unsupported file: {file_path}")
            return True

        return False

    def _queue_file(self, file_path):
        parent_dir = os.path.dirname(file_path)
        if parent_dir != self.path_to_watch:
            return

        if self._should_ignore_path(file_path):
            return

        if not self.pending_jobs.claim(file_path):
            return

        self.job_queue.put(file_path)
        logging.info(f"Queued file for background processing: {file_path}")

    def queue_existing_files(self):
        try:
            for entry in sorted(os.scandir(self.path_to_watch), key=lambda item: item.name):
                if not entry.is_file():
                    continue
                self._queue_file(entry.path)
        except Exception as e:
            logging.error(f"Error queueing existing files in {self.path_to_watch}: {e}")

    def on_created(self, event):
        try:
            if event.is_directory:
                if path_has_hidden_component(event.src_path, self.path_to_watch):
                    logging.info(f"Ignoring hidden directory: {event.src_path}")
                return

            self._queue_file(event.src_path)
        except Exception as e:
            logging.error(f"Error queueing {event.src_path}: {e}")

    def on_moved(self, event):
        try:
            if event.is_directory:
                if path_has_hidden_component(event.dest_path, self.path_to_watch):
                    logging.info(f"Ignoring hidden directory: {event.dest_path}")
                return

            self._queue_file(event.dest_path)
        except Exception as e:
            logging.error(f"Error queueing moved file {event.dest_path}: {e}")


class IncomingRescanLoop(threading.Thread):
    def __init__(self, file_handler, interval_seconds):
        super().__init__(daemon=True, name="incoming-rescan")
        self.file_handler = file_handler
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        if self.interval_seconds <= 0:
            logging.info("Periodic incoming rescan disabled")
            return

        logging.info(
            f"Starting periodic incoming rescan loop (interval={self.interval_seconds}s)"
        )

        while not self.stop_event.wait(self.interval_seconds):
            self.file_handler.queue_existing_files()



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
        whisper_model = config['whisper_model'] if 'whisper_model' in config and config['whisper_model'] else get_default_whisper_model()
        worker_count = get_worker_count(config)
        logging.info(f"Starting worker pool with {worker_count} worker(s)")

        # Set up directory monitoring
        path_to_watch = get_watch_path(config)
        if not os.path.exists(path_to_watch):
            logging.error(f"Directory {path_to_watch} does not exist. Please ensure the folder is mounted.")
            raise FileNotFoundError(f"Directory {path_to_watch} not found")

        # Ensure the output folders exist
        working_folder, completed_folder = ensure_folders(path_to_watch)
        vault_folder = ensure_folder(get_vault_path(config))

        job_queue = queue.Queue()
        pending_jobs = PendingJobRegistry()
        worker_pool = WorkerPool(
            worker_count,
            job_queue,
            pending_jobs,
            config,
            working_folder,
            completed_folder,
            vault_folder,
            whisper_model,
        )

        event_handler = FileHandler(job_queue, pending_jobs, path_to_watch)
        rescan_loop = IncomingRescanLoop(
            event_handler,
            get_incoming_rescan_interval_seconds(config),
        )
        observer = Observer()
        observer.schedule(event_handler, path=path_to_watch, recursive=False)
        observer.start()
        event_handler.queue_existing_files()
        rescan_loop.start()
        worker_pool.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            rescan_loop.stop()
            worker_pool.stop()
        observer.join()
        rescan_loop.join()
        worker_pool.join()

    except Exception as e:
        logging.critical(f"Application failed to start: {e}")
