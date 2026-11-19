# ---------------------------------------------------------------
# agent : LangGraph workflow + FastAPI (container port 8000)
# Build:  docker build -t invoice-auditor/agent:1.0 ./agent
#
# Layer order (slowest changing first):
#   1. OS packages (Tesseract + language packs, Poppler)
#   2. Python dependencies
#   3. Application config + code
# ---------------------------------------------------------------
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 1) System packages for OCR and PDF rendering.
#    eng + spa + deu are the languages in the dataset (English, Spanish, German).
#    The apt cache is removed in the SAME layer so it never lands in the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
        tesseract-ocr-deu \
        poppler-utils \
 && rm -rf /var/lib/apt/lists/*

# unprivileged user
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# 2) Python dependencies - only re-installed when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) Config and code - editing these does NOT trigger a dependency reinstall
COPY config ./config
COPY app ./app

# mount points for the incoming invoices and generated reports
RUN mkdir -p /app/data/incoming /app/outputs/reports \
 && chown -R appuser:appuser /app/data /app/outputs

USER appuser
EXPOSE 8000

# long-running foreground server, logs to stdout
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
