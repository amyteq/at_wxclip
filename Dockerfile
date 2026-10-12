# wx-dlh in a container: the proxy, `capture` and the dashboard, with Python and mitmproxy
# baked in. What it does not change is the host side - WeChat still has to point at the
# published proxy port and trust the CA. See docs/docker.md.
#
#   docker compose up -d --build        # proxy on 127.0.0.1:8899, dashboard on :8080
#
# Every dependency has a manylinux wheel for amd64 and arm64, so the same file builds on
# Intel and Apple Silicon / ARM hosts without a compiler. Nothing here needs BuildKit, so
# the legacy builder and Podman work too.

ARG PYTHON_IMAGE=python:3.12.12-slim-trixie

FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
# .dockerignore keeps the context to what the wheel is built from.
COPY . .
# A venv, so the runtime stage takes the installed tool and leaves the build backend and
# the source tree behind. Both stages share the base image, so its interpreter path holds.
RUN python -m venv /opt/wx-dlh && /opt/wx-dlh/bin/pip install ".[proxy]"

FROM ${PYTHON_IMAGE}
# ffmpeg and tesseract are only for `wx-dlh process` / `shots` (post-processing): the
# proxy, `capture` and the dashboard never call them. Off by default - they add a few
# hundred MB. Build with `--build-arg WITH_MEDIA_TOOLS=1` (WX_DLH_MEDIA_TOOLS=1 for compose).
ARG WITH_MEDIA_TOOLS=0
RUN if [ "$WITH_MEDIA_TOOLS" = 1 ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends \
         ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY --from=build /opt/wx-dlh /opt/wx-dlh
COPY scripts/docker-entrypoint.sh /usr/local/bin/wx-dlh-entrypoint
# .gitattributes keeps the script LF in a checkout, but a Windows editor can still convert
# it, and an exec bit does not survive every route into the build context.
RUN sed -i 's/\r$//' /usr/local/bin/wx-dlh-entrypoint \
    && chmod 0755 /usr/local/bin/wx-dlh-entrypoint \
    && useradd --uid 1000 --user-group --no-create-home --home-dir /data wxdlh

# Everything stateful lives under /data and is a mount (compose.yaml). The CA in
# particular: a container that minted a fresh one on every start would need trusting again.
ENV PATH=/opt/wx-dlh/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    WX_DLH_CA_DIR=/data/ca \
    WX_DLH_CAPTURE_DIR=/data/captures \
    WX_DLH_DOWNLOAD_DIR=/data/downloads \
    WX_DLH_PROCESS_DIR=/data/processed
WORKDIR /data

EXPOSE 8899 8080
ENTRYPOINT ["wx-dlh-entrypoint"]
CMD ["--help"]
