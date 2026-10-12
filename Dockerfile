FROM python:3.13-bookworm AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/src
WORKDIR /src

# Dev dependencies (pytest, httpx2) ship in every target, production included:
# they are two small packages and docs/design-notes.md "Dev tooling" explains why.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

# One worker: the login throttle lives in the database, so more workers add nothing.
# "*" for forwarded-allow-ips is safe only while cms has no published port.
FROM base AS prod
ENV PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --no-create-home --uid 10001 app
USER app
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--root-path", "/api", "--proxy-headers", "--forwarded-allow-ips", "*"]

# Last on purpose: `build: .` and a plain `docker build` use the final stage.
FROM base AS dev
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--reload"]
