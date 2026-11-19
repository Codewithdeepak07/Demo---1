# ---------------------------------------------------------------
# streamlit-ui : human-in-the-loop dashboard (container port 8501)
# Build:  docker build -t invoice-auditor/streamlit-ui:1.0 ./streamlit-ui
# ---------------------------------------------------------------
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# dependencies first (cached), code last
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .streamlit ./.streamlit
COPY app ./app

USER appuser
EXPOSE 8501

# bind to 0.0.0.0 so the dashboard is reachable from the host
CMD ["streamlit", "run", "app/Home.py", "--server.address=0.0.0.0", "--server.port=8501"]
