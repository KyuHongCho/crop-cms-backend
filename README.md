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
| Document store — 5 content tables (6 with `members`), sources recorded per document | The recorded live routing/token run (the eval is built; the run is pending, see "Live eval") |
| Members: signup, login (JWT), `/members/me`; daily token budget, enforced by `/chat` | Editing (`PATCH`) and deleting documents |
| Embeddings for every document, offline-testable (`scripts/reindex.py`) | Frontend and deployment |
| Vector topic selection — `python -m scripts.ask "<question>"` | |
| `POST /chat`: classify, select topics, generate a cited answer. Off-topic questions are declined and questions with no relevant topic abstain, both `200` with `abstained` set and no generation call. Needs both `ANTHROPIC_API_KEY` (classifier, generator) and `OPENAI_API_KEY` (query embedding); a missing key or a provider error (usage limit, rate limit, outage) from either is a plain `503`, after the budget `429` check (the cause is logged, not returned). Models are `CHAT_MODEL_CLASSIFY` and `CHAT_MODEL_GENERATE` (both default to `claude-haiku-4-5`, set in `app/chat/llm.py`). The response carries `truncated` (`true` when the answer was cut at the generator's `max_tokens` and may be incomplete; the answer then also ends with a blank line and a fixed notice, but a frontend should read `truncated` rather than string-match the notice; a declined or abstained response is always `false` and carries no notice). The SDK's default retries stay on, so a `429` or `5xx` is called up to 3 times, with backoff, before the `503`. Answers are not word-for-word repeatable: no sampling parameters are set. `anthropic` is in `requirements.txt`: run `docker compose build` so the image has it (`tests/test_chat.py`'s two real-client tests fail with ImportError otherwise) | |
| Topic-set retrieval — `GET /retrieval/{crop_slug}/{topic}` | |
| Category delete that refiles documents instead of deleting them | |
| Database migrations (Alembic), exercised for real in CI | |
| `POST /chat` crop labels when no crop is fixed; the routing question set and manual live-eval script (never run live yet) | |
| Test suite on an isolated database, run in CI | |
| AI code review on pull requests (advisory) | |

Remaining work in the build plan: advisor tools
over MCP, then conversation context and caching.

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
  [`alembic/versions/`](alembic/versions/) · [why](docs/design-notes.md#data-model-and-integrity)
- **CI runs the real migration, not just `create_all()`.** `alembic upgrade head` builds the schema
  from an empty database, CI asserts the tables it produced, and `alembic check` fails the build if
  a revision leaves the schema behind the models — autogenerating against an already-built database
  instead emits a migration that silently does nothing.
  [`alembic/versions/`](alembic/versions/) · [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
- **Tests cannot touch the dev database.** The suite runs on a separate throwaway Postgres, and a
  guard fails immediately if it is pointed anywhere else.
  [`tests/conftest.py`](tests/conftest.py) · [why](docs/design-notes.md#testing-details)
- **Citations cannot drift silently.** The seed tests import crop-climate-advisor and fail if the
  pinned sources no longer match it.
  [`tests/test_seed.py`](tests/test_seed.py) · [why](docs/design-notes.md#testing-details)

Trade-offs, known limits and the full rationale: [`docs/design-notes.md`](docs/design-notes.md#trade-offs-and-known-limits).

## Quickstart

Requires Docker (28 or newer recommended; see [Local ports](docs/design-notes.md#local-ports)).

```bash
# 1. Secrets (.env is git- and docker-ignored): generates the two passwords and SECRET_KEY;
#    OPENAI_API_KEY is only needed for step 5
[ -f .env ] || { cp .env.example .env && sed -i.bak \
  -e "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" \
  -e "s/^DB_PASSWORD=$/DB_PASSWORD=$(openssl rand -hex 16)/" \
  -e "s/^SECRET_KEY=$/SECRET_KEY=$(openssl rand -hex 32)/" .env && rm .env.bak; }

#    An .env that already exists is left untouched: if it predates SECRET_KEY, add
#    SECRET_KEY=$(openssl rand -hex 32) to it by hand, or logins return 500.

# 2. Start the API and the database
docker compose up -d --build

# 3. Create the schema: tables, index, refile trigger, item_chunks and its published view
docker compose exec cms alembic upgrade head

# 4. Load the demo corpus: basil, lettuce, strawberry, tomato, cucumber, sweet pepper and kale
#    (non-commercial demo data: FAO ECOCROP terms apply, see Licence)
docker compose exec cms python -m scripts.seed
#    The seed has no delete path: a database seeded by an older version keeps the rows it
#    replaced (basil's former RHS documents, the old folklore row). Reseed a fresh database,
#    or remove those rows by hand.

# 5. Embed every document; needs OPENAI_API_KEY -- without one, skip this
#    step: step 6 does not read embeddings. --dry-run prints the count without calling OpenAI.
#    Added the key to .env after step 2? Run `docker compose up -d` first: `exec` uses the
#    container's environment from when it was created, not the current .env.
docker compose exec cms python -m scripts.reindex --dry-run
docker compose exec cms python -m scripts.reindex

# 6. Ask for one topic — returns 3 documents, one per disagreeing source
curl localhost:8000/retrieval/basil/optimal-temperature

# 7. Ask a question — selects the best-matching topics by vector similarity and prints each in
#    full with its sources. Needs step 5 (embeddings) and OPENAI_API_KEY.
docker compose exec -T cms python -m scripts.ask --crop basil "how hot should basil be?"
```

API on `localhost:8000` (interactive docs at `/docs`) and PostgreSQL on `5432`; both ports are bound to
`127.0.0.1` only, so other machines on your network can't reach them (Docker 28 or newer).

## Testing

```bash
docker compose --profile test up -d --wait
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms alembic upgrade head
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms python -m pytest -q
```

The seed tests need [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor)
checked out next to this repo; without it they skip. CI checks the sibling out and fails the build
if those tests would skip.

The suite needs no API key: it embeds with `FakeEmbedder`, a deterministic offline stand-in.

## Embeddings

`scripts/reindex.py` embeds every document — drafts included — into `item_chunks`, one chunk per
document today. It skips a chunk whose stored hash and model already match, so a second run embeds
nothing; the hash covers title and body, so a title-only edit re-embeds too. When a document
shrinks to fewer chunks, the extra ones are deleted in the same transaction.

`EMBEDDER` selects the provider: `openai` (default, `text-embedding-3-small`, needs
`OPENAI_API_KEY`) or `fake` (offline). It is not read from `.env`; pass it per command, e.g.
`docker compose exec -e EMBEDDER=fake cms python -m scripts.reindex`. The `published_item_chunks`
view exposes only published documents, and is what the chat layer (`app/chat/`) reads.

That boundary is **a convention with a tripwire, not enforcement**:
`tests/test_chat_layer_isolation.py` fails if a module under `app/chat/` names `Item`, `ItemChunk`
or `item_chunks`, or reads `items` in SQL or as `table("items")`, but it only searches source
text, and the database still lets the application role read every table: `cms_app` owns them all
and can re-grant itself, so one role cannot enforce it. A second database role was considered and
rejected on budget.

## Topic selection

`app/chat/retrieval.py` finds the topics a question is about, then returns each one **complete**.
A topic scores as its single best-matching chunk (`MAX`, not the mean, which would penalise topics
with many disagreeing sources); the top `TOPIC_SELECTION_K` (3) topics are kept; their whole
document sets follow, with no `LIMIT`. Scoring can be limited to one crop (`crop_id`, or
`scripts.ask --crop SLUG`); without it every crop competes. `TOPIC_SCORE_FLOOR` lets the system abstain
(`NoRelevantTopics`) when no topic is relevant enough. It ships dark at `-1.0`, a no-op for cosine
similarity, until `scripts/calibrate_floor.py` has been run on real embeddings:
[why](docs/design-notes.md#vector-topic-selection).
`FakeEmbedder` vectors carry no meaning, so ranking is tested with hand-built vectors
([`tests/test_topic_expansion.py`](tests/test_topic_expansion.py)), and `scripts.ask` offline only
checks the plumbing.

`item_chunks` carries no HNSW or IVFFlat index yet: [why](docs/design-notes.md#no-vector-index-yet).

## Migrations

Schema changes go through Alembic (`alembic/`), run **inside the `cms` container, never from the
host venv** — the container supplies `DB_PASSWORD`, `DB_HOST=db` and `greenlet`, which the async
template needs and SQLAlchemy does not install on every platform (Apple Silicon, for one).

```bash
docker compose exec cms alembic upgrade head    # apply every migration not yet run
docker compose exec cms alembic downgrade base  # undo them all -- DROPS every table, all data with it
```

The exception is a `pg-data` volume that predates Alembic: it already has the tables (built by the
retired `python -m app.db.migrate_db`), so applying the baseline migration to it fails with
`DuplicateTable`. Run `docker compose exec cms alembic stamp 4b698ac48d60` against it once, which
records the baseline migration as applied and runs none of its SQL, then
`docker compose exec cms alembic upgrade head`, which runs every later migration. Stamp the
baseline revision, not `head`: `stamp head` would also mark the later migrations as applied without
running them, leaving `item_chunks` and the `published_item_chunks` view missing. Then run
`docker compose exec cms alembic check`:
a volume built before `ix_items_crop_id_topic` existed reports that index as missing, and
`docker compose exec db sh -c 'psql -U "$DB_USER" -d cms -c "CREATE INDEX ix_items_crop_id_topic ON items (crop_id, topic)"'`
adds it. `alembic check` cannot see the bucket row, the refile trigger or the
`published_item_chunks` view. A database created after
this point always uses `alembic upgrade head`, which needs no stamp.

[`app/db/migrate_db.py`](app/db/migrate_db.py) stays in the tree because `tests/conftest.py`, three
test modules and `scripts/seed.py` import its sync `engine`, and `conftest.py` its `SEED_BUCKET_SQL`.
Its own `reset_database()` path (`drop_all` + `create_all`) is a guarded pre-Alembic artifact: it
refuses to run once `alembic_version` exists, so it can never be pointed at a database Alembic
manages. A test asserts that refusal.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/crops` | Read-only; seeded to match crop-climate-advisor |
| `GET` `POST` | `/main-categories` | `409` on a duplicate `slug` |
| `GET` `POST` | `/sub-categories` | Unique per parent, not globally; `409` on a duplicate `slug` under the same parent |
| `DELETE` | `/main-categories/{id}` | `409` while it still has sub-categories |
| `DELETE` | `/sub-categories/{id}` | Refiles its documents to "Uncategorised" and returns the count |
| `GET` `POST` | `/items` | A document and its sources |
| `POST` | `/members/signup` | `201`; `400` on a duplicate email. Argon2id hash, run in the threadpool |
| `POST` | `/members/login` | `{"access_token": ...}`; the same `401` for an unknown email and a wrong password |
| `GET` | `/members/me` | Needs `Authorization: Bearer <token>`; `401` otherwise |
| `GET` | `/retrieval/{crop_slug}/{topic}` | Every published document on a topic; `413` if the topic exceeds the budget |

## Members and the token budget

Set `SECRET_KEY` in `.env` (`openssl rand -hex 32`); signing a token without it fails loudly. Tokens
last 30 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`) and **no refresh-token flow is implemented** -- log in
again. Every CMS endpoint, read and write alike, is still unauthenticated; the member identity and the
budget exist for the model-calling route, `POST /chat`.

Each member has a daily token budget (`members.tokens_budget_daily`, default 20000), implemented in
[`app/auth/budget.py`](app/auth/budget.py). `POST /chat` calls `check_budget` (resets a stale day, answers `429` with
`Retry-After` before any model call) and `record_usage` with the tokens the provider reported.
Check and record are separate steps with no lock, so concurrent requests from one member can overshoot
the cap. The overall spend bound is the monthly spend limit set in each provider's console (Billing
page); this budget is the per-member control on top of it.

`TOKENS_BUDGET_DAILY` (default 20000) sets the starting budget for members who sign up after the
container is recreated (`docker compose up -d`; a plain `restart` does not re-read the environment); existing members keep theirs, so raise one with an `UPDATE members SET tokens_budget_daily = ...`.

## Live eval (manual)

`scripts/live_chat_eval.py` runs the labelled questions in `tests/routing_questions.py` through the real
classifier, embedder and generator, against a database you name. It is never run by pytest or CI: the suite
proves the plumbing with stubs and says nothing about model quality, so this is where routing accuracy,
flip rate and token use are measured.

```bash
# ANTHROPIC_API_KEY and OPENAI_API_KEY must already be in the container's environment (from .env;
# `docker compose up -d` after editing it) -- do not type keys on the command line.
docker compose exec -T cms python -m scripts.live_chat_eval --db-host db --db-name cms --repeats 3
```

`--db-host` and `--db-name` are required (there is no default, since the container's default is dev); the
database must be seeded and reindexed. Nothing is written to it. It prints routing accuracy per question and
overall, the flip rate across repeats (refusals for the context budget are counted apart from misroutes),
tokens per call (classifier, generator) with the per-question range and median, the total and mean, and the
questions a member can ask per day at the 20000-token budget. **Base the budget decision on the lookup-only
figure**: declined questions cost one cheap call, so the all-questions median
is optimistic. It also prints the answers to the basil cuttings question, which should state the gap first.
If a call fails the run stops, names the cause and still prints what completed. Output goes to stdout, or
also to `--out PATH` (never overwritten); do not commit it as a fixture.

Rough cost, **estimate, unmeasured**: a 20-question run is estimated at about $0.05 (Haiku 4.5) to $0.20
(Opus 5.5); this run asks 17 questions 3 times, so roughly 2.5 times that. The run reports the real token counts.

Answers are not word-for-word repeatable: current models do not let you set the sampling temperature, so the
same question can be routed or worded differently between runs, which is why the eval reports a rate. The
question set's two collision labels (for the not-yet-built `crop_cycle_days`) are checked only by this live
run; offline the stub just returns the label. Relabel "how many days is a lettuce crop cycle?" when that
intent ships.

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
carries those terms, or the CC BY 4.0 credit and change notice, per document. The demo corpus is for non-commercial use only.

Personal portfolio repository — issues are welcome; external pull requests are not accepted.
