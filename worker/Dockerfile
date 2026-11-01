FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ backend/
COPY worker/ worker/
RUN pip install --no-cache-dir ./backend
ENV PYTHONPATH=/app/backend/src:/app TLC_JAR_PATH=/app/worker/tla2tools.jar
CMD ["python", "-m", "worker.src.runner"]
