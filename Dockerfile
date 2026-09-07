FROM python:3.13-bookworm

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/src
WORKDIR /src

# The production-target / ARG INSTALL_DEV split is deliberately dropped:
# build: . in docker-compose.yaml passes no args, so a conditional branch
# would be reachable only by a `docker build --build-arg` nothing here
# performs. The dev image simply carries the test tooling; the README says so.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--reload"]