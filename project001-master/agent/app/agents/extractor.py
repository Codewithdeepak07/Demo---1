"""
Extractor Agent (no LLM).

    PDF   -> pdfplumber text layer first, Tesseract OCR if there is no text layer
    DOCX  -> python-docx (paragraphs + tables)
    Image -> Tesseract OCR

Returns (raw_text, extraction_method). The raw text is stored in full in PostgreSQL.
"""

import logging

import docx
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from app.settings import OCR_LANGUAGES

log = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 20   # less than this from pdfplumber = treat as a scanned PDF


def extract_pdf(file_path: str) -> tuple[str, str]:
    with pdfplumber.open(file_path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    text = "\n".join(pages).strip()

    if len(text) >= MIN_TEXT_LENGTH:
        return text, "pdfplumber"

    log.info("No text layer in %s - falling back to OCR", file_path)
    images = convert_from_path(file_path, dpi=300)          # needs poppler-utils
    ocr_pages = [pytesseract.image_to_string(image, lang=OCR_LANGUAGES) for image in images]
    return "\n".join(ocr_pages).strip(), "tesseract"


def extract_docx(file_path: str) -> tuple[str, str]:
    document = docx.Document(file_path)
    lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]

    # tables are where invoice line items usually live
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append(" | ".join(cells))

    return "\n".join(lines).strip(), "docx"


def extract_image(file_path: str) -> tuple[str, str]:
    with Image.open(file_path) as image:
        text = pytesseract.image_to_string(image.convert("RGB"), lang=OCR_LANGUAGES)
    return text.strip(), "tesseract"


EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "image": extract_image,
}


def extract_text(file_path: str, file_type: str) -> tuple[str, str]:
    raw_text, method = EXTRACTORS[file_type](file_path)
    if not raw_text:
        raise ValueError(f"No text could be extracted from {file_path}")
    return raw_text, method
