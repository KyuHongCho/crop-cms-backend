# Crop CMS Backend

[![CI](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml)

A FastAPI + PostgreSQL service that stores crop-growing documents together with their sources —
the document store for a planned chat assistant that answers crop questions with citations.

## Why it exists

The goal is a chat endpoint where you ask *"what temperature does basil want?"* and get an answer
that cites its sources. Two kinds of data answer that question:

- **Numbers** — basil's temperature range — come from structured data in the sibling repo
  [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor).
- **Prose about those numbers** — who measured what, under which conditions — is stored here, and
  will be retrieved with RAG over pgvector.

Published sources often disagree: this corpus holds three attributed claims about basil's optimal
temperature. A standard top-k RAG query returns the *k* most similar passages, which can silently
leave a dissenting source out. This service returns a topic's documents **complete**, so every
source that disagrees reaches the answer.

## Status

**Early and in progress.** Nothing consumes this API yet.

| Works today | Not built yet |
|---|---|
| Document store — 4 tables, sources recorded per document | Embeddings and vector search |
| Topic-set retrieval — `GET /retrieval/{crop_slug}/{topic}` | `POST /chat` |
| Category delete that refiles documents instead of deleting them | Authentication |
| Database migrations (Alembic), exercised for real in CI | Editing (`PATCH`) and deleting documents |
| Test suite on an isolated database, run in CI | Frontend and deployment |
| AI code review on pull requests (advisory) | |

Remaining work in the build plan: embeddings, vector search that selects topics, advisor tools
over MCP, `POST /chat`, then conversation context and caching.

## Engineering highlights

- **No silent truncation.** Retrieval has no `limit` parameter. When topics exceed the context
  budget, whole topics are dropped, lowest-scoring first, and named — never cut part-way. Only if
  the topic left is still too large on its own is the request refused with `413`.
  [`app/crud/retrieval.py`](app/crud/retrieval.py) · [`tests/test_retrieval.py`](tests/test_retrieval.py) · [why](docs/design-notes.md#topic-set-retrieval-the-no-truncation-guarantee)
- **Integrity rules live in PostgreSQL, not only in Python.** A `CHECK` constraint rejects a
  document marked as read first-hand that also names a secondary source; CI asserts the insert is
  rejected.
  [`app/model/model.py`](app/model/model.py) · [why](docs/design-notes.md#data-model-and-integrity)
- **Deleting a category never deletes documents.** `ON DELETE RESTRICT` plus a `BEFORE DELETE`
  trigger refiles them to "Uncategorised", so `psql` and bulk SQL follow the same rule as the API.
  [`app/db/migrate_db.py`](app/db/migrate_db.py) · [why](docs/design-notes.md#data-model-and-integrity)
- **CI runs the real migration, not just `create_all()`.** `alembic upgrade head` builds the schema
  from an empty database and the generated `upgrade()` is checked non-empty before being committed
  — autogenerating against an already-built database instead emits a migration that silently does
  nothing.
  [`alembic/versions/`](alembic/versions/) · [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
- **Tests cannot touch the dev database.** The suite runs on a separate throwaway Postgres, and a
  guard fails immediately if it is pointed anywhere else.
  [`tests/conftest.py`](tests/conftest.py) · [why](docs/design-notes.md#testing-details)
- **Citations cannot drift silently.** The seed tests import crop-climate-advisor and fail if the
  pinned sources no longer match it.
  [`tests/test_seed.py`](tests/test_seed.py) · [why](docs/design-notes.md#testing-details)

Trade-offs, known limits and the full rationale: [`docs/design-notes.md`](docs/design-notes.md#trade-offs-and-known-limits).

## Quickstart

Requires Docker.

```bash
# 1. Secrets (.env is git- and docker-ignored)
cat > .env <<'EOF'
POSTGRES_PASSWORD=<choose one>
DB_USER=cms_app
DB_PASSWORD=<choose another>
EOF

# 2. Start the API and the database
docker compose up -d --build

# 3. Create the tables, the index and the refile trigger
docker compose exec cms alembic upgrade head

# 4. Load the basil demo corpus (13 documents across 5 topics)
docker compose exec cms python -m scripts.seed

# 5. Ask for one topic — returns 3 documents, one per disagreeing source
curl localhost:8000/retrieval/basil/optimal-temperature
```

API on `localhost:8000` (interactive docs at `/docs`); PostgreSQL on `127.0.0.1:5432`, loopback only.

## Testing

```bash
docker compose --profile test up -d --wait
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms alembic upgrade head
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms python -m pytest -q
```

The seed tests need [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor)
checked out next to this repo; without it they skip. CI checks the sibling out and fails the build
if those tests would skip.

## Migrations

Schema changes go through Alembic (`alembic/`), run **inside the `cms` container, never from the
host venv** — the async template needs `greenlet`, which is installed there but not on the host.

```bash
docker compose exec cms alembic upgrade head    # apply every migration not yet run
docker compose exec cms alembic downgrade base  # undo them all -- DROPS every table, all data with it
```

The one-time exception is the database this repository shipped with before Alembic existed: it
already has the tables (built by the retired `python -m app.db.migrate_db`), so applying the
baseline migration to it fails with `DuplicateTable`. That database's `alembic_version` was set to
head with `alembic stamp head` — which only records the migration as applied and runs none of its
SQL, so it does **not** by itself bring the schema in line with what the migration would produce.
Any DDL the baseline migration adds beyond what `migrate_db.py` already created (for example an
index) still has to be applied by hand once, after which `alembic check` reporting no drift is what
actually confirms reconciliation — not the `stamp head` step alone. A database created after this
point always uses `alembic upgrade head`, which needs no such manual follow-up.

[`app/db/migrate_db.py`](app/db/migrate_db.py) stays in the tree as a guarded pre-Alembic learning
artifact: it now refuses to run at all once `alembic_version` exists, so it can never be pointed at
a database Alembic manages. CI no longer calls it.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/crops` | Read-only; seeded to match crop-climate-advisor |
| `GET` `POST` | `/main-categories` | `409` on a duplicate `slug` |
| `GET` `POST` | `/sub-categories` | Unique per parent, not globally; `409` on a duplicate `slug` under the same parent |
| `DELETE` | `/main-categories/{id}` | `409` while it still has sub-categories |
| `DELETE` | `/sub-categories/{id}` | Refiles its documents to "Uncategorised" and returns the count |
| `GET` `POST` | `/items` | A document and its sources |
| `GET` | `/retrieval/{crop_slug}/{topic}` | Every published document on a topic; `413` if the topic exceeds the budget |

## Related repositories

- [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) — crop suitability from
  NASA POWER climate data and FAO ECOCROP requirements; the structured half of the planned chat.
- [agentic-workflow](https://github.com/KyuHongCho/agentic-workflow) — the plan → build → review
  workflow with adversarial auditors, used to build this repo; its review stage runs when a pull
  request is opened here.

## Acknowledgements

Built while working through two Inflearn courses:

- [Dipping into FastAPI (FastAPI + React.js + AWS LightSail)](https://www.inflearn.com/en/course/fastapi-%EC%B0%8D%EC%96%B4%EB%A8%B9%EA%B8%B0)
  by ddur — the starting point for the `app/` layout (`db`, `model`, `schema`, `crud`, `router`).
  Departures: PostgreSQL + pgvector instead of MySQL; schemas that map field-for-field onto the
  models; response shaping through `response_model` rather than by hand.
- [Everything about AI Agent Development Learned by Building a Chatbot (FastAPI, RAG, Vector, LangChain, sLLM/Fine-tuning)](https://www.inflearn.com/en/course/everything-about-ai?cid=342279)
  (*"Everything about AI agent development, by building a chatbot"*) — the RAG and chatbot material
  the planned chat work draws on. Departure: retrieval returns complete topic sets instead of top-k
  passages, and numeric questions will go to structured data rather than to RAG.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Crop data is not covered: FAO ECOCROP content is © FAO,
under the [FAO Terms and Conditions](https://www.fao.org/contact-us/terms/en/); `items.licence_note`
carries those terms per document.

Personal portfolio repository — issues are welcome; external pull requests are not accepted.
