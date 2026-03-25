FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
        libelf-dev libsimavr-dev libcjson-dev \
    && rm -rf /var/lib/apt/lists/*

# Install OpenSCAD nightly (AppImage, extracted — FUSE unavailable in containers)
ARG OPENSCAD_SNAPSHOT=OpenSCAD-2026.03.22-x86_64.AppImage
RUN curl -fsSL -o /tmp/openscad.AppImage \
        "https://files.openscad.org/snapshots/${OPENSCAD_SNAPSHOT}" \
    && chmod +x /tmp/openscad.AppImage \
    && cd /opt && /tmp/openscad.AppImage --appimage-extract \
    && ln -s /opt/squashfs-root/AppRun /usr/local/bin/openscad \
    && rm /tmp/openscad.AppImage

# Install arduino-cli + AVR core
ARG ARDUINO_CLI_VERSION=1.4.1
RUN curl -fsSL "https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
    | tar -xz -C /usr/local/bin arduino-cli \
    && arduino-cli core install arduino:avr \
    && rm -rf /root/.arduino15/staging
