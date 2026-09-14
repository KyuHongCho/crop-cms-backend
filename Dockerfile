FROM python:3.13-bookworm

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/src
WORKDIR /src

# Installs both requirement files unconditionally -- see docs/design-notes.md's
# "Dev tooling" paragraph for what ships. No production-target / ARG INSTALL_DEV
# split: `build: .` in docker-compose.yaml passes no build args, so a conditional
# branch would be reachable only by a `docker build --build-arg` nothing here does.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--reload"]