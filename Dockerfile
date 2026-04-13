ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.21
FROM ${BUILD_FROM}

# Install system dependencies for PDF processing
RUN apk add --no-cache \
    mupdf-tools \
    mupdf-dev \
    gcc \
    musl-dev \
    python3-dev \
    freetype-dev \
    harfbuzz-dev \
    jpeg-dev \
    openjpeg-dev \
    zlib-dev \
    jbig2dec-dev

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy application
COPY app/ /app/
COPY schemas/ /schemas/
COPY config/ /config/
COPY rootfs/ /

# Copy run script
COPY run.sh /
RUN chmod a+x /run.sh

WORKDIR /

CMD ["/run.sh"]
