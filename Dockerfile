FROM python:3.13-bookworm

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/src
WORKDIR /src

# Installs both requirement files unconditionally, so dev tooling (pytest,
# httpx2, ...) ships in the same image as the application -- see
# docs/design-notes.md's "Dev tooling" paragraph for why.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--reload"]