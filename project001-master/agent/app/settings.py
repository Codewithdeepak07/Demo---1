"""
Central settings for the agent.

* Connection details and model names come from environment variables
  (supplied at runtime by docker compose via env_file - never baked in).
* Business rules come from config/rules.yaml.
* Prompt templates and personas come from config/prompts.yaml.
"""

import logging
import os
import sys
from pathlib import Path

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent          # /app inside the container
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", APP_ROOT / "config"))


def _load_yaml(file_name: str) -> dict:
    with open(CONFIG_DIR / file_name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


RULES = _load_yaml("rules.yaml")
PROMPTS = _load_yaml("prompts.yaml")

# ---------------------------------------------------------------
# Services inside the compose network (service names, container ports)
# ---------------------------------------------------------------
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "invoice_auditor")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
ERP_BASE_URL = os.environ.get("ERP_BASE_URL", "http://mock-erp:8000")

# ---------------------------------------------------------------
# Folders (mounted volumes)
# ---------------------------------------------------------------
INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", APP_ROOT / "data" / "incoming"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", APP_ROOT / RULES["reporting"]["output_dir"]))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
SUPPORTED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".png": "image",
                        ".jpg": "image", ".jpeg": "image"}

# Tesseract language packs needed by the dataset (English, Spanish, German)
OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES", "eng+spa+deu")

# ---------------------------------------------------------------
# Models (AWS Bedrock through LiteLLM). AWS credentials are read by
# LiteLLM/boto3 straight from the environment.
# ---------------------------------------------------------------
def chat_model_setting(env_name: str, default: str) -> str:
    """Chat models always go through Bedrock's Converse API: "bedrock/converse/<model id>"."""
    value = (os.environ.get(env_name) or "").strip() or default
    model_id = value.removeprefix("bedrock/converse/").removeprefix("bedrock/")
    return f"bedrock/converse/{model_id}"


def embedding_model_setting(env_name: str, default: str) -> str:
    """Embedding models must use the plain "bedrock/<model id>" route.

    LiteLLM picks the embedding provider from the first part of the model id
    ("amazon." -> Titan). A "converse/" or "invoke/" route, or a region prefix
    such as "us." / "global.", makes it fail with "Unable to map request to
    provider", so all of those are stripped here.
    """
    value = (os.environ.get(env_name) or "").strip() or default
    model_id = value
    for prefix in ("bedrock/", "converse/", "invoke/"):
        model_id = model_id.removeprefix(prefix)
    for region in ("us.", "eu.", "apac.", "global."):
        model_id = model_id.removeprefix(region)
    return f"bedrock/{model_id}"


REASONING_MODEL = chat_model_setting("REASONING_MODEL", "global.amazon.nova-2-lite-v1:0")
RAG_MODEL = chat_model_setting("RAG_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
TRANSLATION_MODEL = chat_model_setting("TRANSLATION_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
EMBEDDING_MODEL = embedding_model_setting("EMBEDDING_MODEL", "amazon.titan-embed-text-v1")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS") or "1536")

# ---------------------------------------------------------------
# Cache / RAG settings
# ---------------------------------------------------------------
LLM_CACHE_TTL_SECONDS = int(os.environ.get("LLM_CACHE_TTL_SECONDS", str(7 * 24 * 3600)))
ANSWER_CACHE_TTL_SECONDS = int(os.environ.get("ANSWER_CACHE_TTL_SECONDS", "3600"))
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "invoice_chunks")
RAG_PASS_SCORE = 0.7        # problem statement: show answer if avg triad score > 0.7
RAG_MAX_RETRIES = 3         # problem statement: max 3 retries

# ---------------------------------------------------------------
# Logging - always stdout (containers must not log to files)
# ---------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=RULES.get("logging", {}).get("log_level", "INFO"),
    format="%(asctime)s [agent] %(levelname)s %(name)s: %(message)s",
)


def postgres_conninfo() -> str:
    return (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )