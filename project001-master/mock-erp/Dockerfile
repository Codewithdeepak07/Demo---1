# ---------------------------------------------------------------
# mock-erp : FastAPI service over the `erp` schema
# Build:  docker build -t invoice-auditor/mock-erp:1.0 ./mock-erp
# ---------------------------------------------------------------
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 1) unprivileged user
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# 2) dependencies first - changes rarely, so this layer stays cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) application code last - changes often
COPY app ./app

USER appuser
EXPOSE 8000

# long-running foreground process, logs go to stdout
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
