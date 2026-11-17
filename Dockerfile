FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 fonts-dejavu-core && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app \
    && mkdir -p /data/beats /data/secrets /data/renders /data/visuals && chown -R app:app /data && chmod 700 /data/secrets
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER app
ENV PYTHONUNBUFFERED=1
