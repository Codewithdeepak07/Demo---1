"""
Invoice Monitor Agent (no LLM).

Looks at the incoming folder, checksums every invoice file and decides
whether it is new. The actual polling loop lives in app/pipeline.py.
"""

import hashlib
import json
from pathlib import Path

from app.settings import INCOMING_DIR, SUPPORTED_EXTENSIONS


def compute_checksum(file_path: Path) -> str:
    """SHA-256 of the file content. Vendors rename files, so content is what counts."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


def detect_file_type(file_path: Path) -> str | None:
    """Route by extension: 'pdf', 'docx' or 'image'. None means unsupported."""
    return SUPPORTED_EXTENSIONS.get(file_path.suffix.lower())


def load_email_meta(file_path: Path) -> dict:
    """INV_EN_001.pdf -> INV_EN_001.meta.json (the simulated email header), if present."""
    meta_path = file_path.with_name(file_path.stem + ".meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def list_invoice_files(folder: Path = INCOMING_DIR) -> list[Path]:
    """All files in the incoming folder except the .meta.json email headers."""
    if not folder.exists():
        return []
    files = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and not path.name.endswith(".meta.json") and not path.name.startswith("."):
            files.append(path)
    return files
