FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
        libelf-dev libsimavr-dev libcjson-dev \
    && rm -rf /var/lib/apt/lists/*

# Install arduino-cli + AVR core
ARG ARDUINO_CLI_VERSION=1.4.1
RUN curl -fsSL "https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
    | tar -xz -C /usr/local/bin arduino-cli \
    && arduino-cli core install arduino:avr \
    && rm -rf /root/.arduino15/staging
