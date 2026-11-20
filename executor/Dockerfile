FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pyspark==3.5.8
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 SPARK_LOCAL_IP=127.0.0.1
COPY entry.py /runner/entry.py
ENTRYPOINT ["python", "/runner/entry.py"]
