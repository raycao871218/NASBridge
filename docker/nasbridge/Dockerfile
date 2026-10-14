FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY

COPY requirements.txt ./
RUN export HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" ALL_PROXY="$ALL_PROXY" \
    && pip install --no-cache-dir -r requirements.txt imageio-ffmpeg==0.6.0
RUN python -c 'import os, imageio_ffmpeg; os.symlink(imageio_ffmpeg.get_ffmpeg_exe(), "/usr/local/bin/ffmpeg")'

COPY app ./app
COPY config/config.yaml.example ./config/config.yaml.example

EXPOSE 18080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18080"]
