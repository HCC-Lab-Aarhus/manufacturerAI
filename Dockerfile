FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install arduino-cli and the AVR core for firmware compilation
ARG ARDUINO_CLI_VERSION=1.4.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
       | tar -xz -C /usr/local/bin arduino-cli \
    && arduino-cli core install arduino:avr \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.arduino15/staging

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN useradd -m manufacturerai-user
RUN mkdir -p /app/outputs/sessions && chown -R manufacturerai-user:manufacturerai-user /app
USER manufacturerai-user

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.web.server:app", "--host", "0.0.0.0", "--port", "8000"]
