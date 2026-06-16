FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MAIL_CACHE_PATH=/tmp/email_cache.sqlite

RUN useradd --create-home app
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && pip install .

USER app

ENTRYPOINT ["imap-readonly-mcp"]
CMD ["--config", "/app/config/accounts.yaml"]
