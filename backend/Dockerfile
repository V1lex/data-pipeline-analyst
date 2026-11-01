FROM python:3.12-slim
WORKDIR /app
COPY backend/ backend/
RUN pip install --no-cache-dir ./backend
ENV PYTHONPATH=/app/backend/src
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
