# Crop CMS Backend

[![CI](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml)

A FastAPI + PostgreSQL service that stores crop-growing documents together with their sources and
answers crop questions from them with citations (`POST /chat`).

## Why it exists

A chat endpoint where you ask *"what temperature does basil want?"* and get an answer that cites
its sources. Two kinds of data answer that question:

- **Numbers** — basil's temperature range — come from structured data in the sibling repo
  [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor).
- **Prose about those numbers** — who measured what, under which conditions — is stored here, and
  retrieved by vector similarity over pgvector.

The numbers half is not wired in yet: advisor tools over MCP are planned (see Status).

Published sources often disagree: this corpus holds three attributed claims about basil's optimal
temperature. A standard top-k RAG query returns the *k* most similar passages, which can silently
leave a dissenting source out. This service returns a topic's documents **complete**, so every
source that disagrees reaches the answer. Retrieval over pgvector selects the topics a question is
about, then hands each one over as a complete set.

## Status

**Early and in progress.** A React frontend, [crop-cms-frontend](https://github.com/KyuHongCho/crop-cms-frontend), now consumes this API; nothing is deployed yet.

| Works today | Not built yet |
|---|---|
| Document store: 9 tables (5 content tables, plus `members`, `member_audit_events`, `member_invites` and `login_throttle`), sources recorded per document | Editing (`PATCH`) documents |
| Members: invite-only signup, login (JWT), `/members/me`, login throttling — [members and budget](docs/operations.md#members-and-the-token-budget), [throttling](docs/operations.md#login-throttling) | Deployment: TLS, public ports and a production host |
| `POST /chat`: classify, select topics, generate a cited answer; needs both provider keys, and a daily token budget applies — [details](docs/api.md#post-chat) | |
| Embeddings for every document, offline-testable (`scripts/reindex.py`) — [operations](docs/operations.md#embeddings) | |
| Vector topic selection — `python -m scripts.ask "<question>"` | |
| Topic-set retrieval — `GET /retrieval/{crop_slug}/{topic}` | |
| Category delete that refiles documents instead of deleting them; document delete (`DELETE /items/{id}`) — [API](docs/api.md) | |
| Database migrations (Alembic), exercised for real in CI — [operations](docs/operations.md#migrations) | |
| Health endpoints, `GET /health` and `GET /health/ready` — [API](docs/api.md); the `cms` healthcheck probes `/health` every 10 s — [operations](docs/operations.md#health-checks) | |
| `POST /chat` crop labels when no crop is fixed; routing question set and manual live-eval script — [live eval](docs/live-eval.md) | |
| Test suite on an isolated database, run in CI | |
| Same-origin Caddy proxy (optional compose profile `proxy`), asserted by `proxy/check.sh` in CI — [why](docs/design-notes.md#how-the-browser-reaches-the-api) | |
| AI code review on pull requests (advisory) | |

Next: deployment, then advisor tools over MCP, then conversation context and caching.

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
#    the API keys are needed only for steps 5, 7 and 9:
#    OPENAI_API_KEY for 5, 7 and 9, and ANTHROPIC_API_KEY for 9
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

# 8. Optional: the first admin, which the write routes need. Signup requires an invite and
#    there is no admin yet to create one, so the operator makes one, signs up, then
#    promotes that member with SQL (a role is never granted through the API).
CODE=$(docker compose exec -T cms python -m scripts.make_invite)
curl -X POST localhost:8000/members/signup -H 'content-type: application/json' \
  -d "{\"email\":\"you@example.com\",\"password\":\"choose-a-password\",\"invite_code\":\"$CODE\"}"
docker compose exec -T db sh -c 'psql -U "$DB_USER" -d cms' \
  <<< "UPDATE members SET role = 'admin' WHERE email = 'you@example.com';"
curl -X POST localhost:8000/members/login -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"choose-a-password"}'   # the access_token it returns

# 9. Ask the chat endpoint, with the access_token from step 8. Needs ANTHROPIC_API_KEY and
#    OPENAI_API_KEY in the container's environment, and spends tokens on both providers.
TOKEN='<the access_token from step 8>'
curl -X POST localhost:8000/chat -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"question":"how hot should basil be?"}'
```

API on `localhost:8000` and PostgreSQL on `5432`; both ports are bound to
`127.0.0.1` only, so other machines on your network can't reach them (Docker 28 or newer).
Interactive docs are at `/docs` on `localhost:8000` directly only: the proxy below blocks
`/api/docs`, `/api/redoc` and `/api/openapi.json` with a 404.

## Run behind the proxy

The browser reaches the API through one same-origin proxy rather than CORS, so the API needs no CORS
code. The `proxy` service is off by default; with the stack from the Quickstart up:

```bash
docker compose --profile proxy up -d --wait proxy
bash proxy/check.sh                          # asserts the routing; prints ALL PASS
curl localhost:8080/api/health               # the API, through the proxy: {"status":"ok"}
```

It serves a placeholder page at `http://127.0.0.1:8080/` and forwards `/api/*` to the API with the
prefix stripped. `proxy/Caddyfile` is the config; its site address comes from the host's
`SITE_ADDRESS` (unset or empty: `http://:80`, plain HTTP; a domain turns on HTTPS and needs ports
80/443 and DNS, which this repo has not exercised with a real certificate). To serve the real
frontend build instead, point `SITE_DIR` at it and recreate the proxy
(`../crop-cms-frontend/dist` is the sibling repo's build output):

```bash
SITE_DIR=../crop-cms-frontend/dist docker compose --profile proxy up -d --wait proxy
```

`proxy/check.sh` fails against the real `dist/` by design: it asserts the placeholder's marker string.

`docker-compose.prod.yaml` is the production counterpart: a standalone file with only `cms` (the
`prod` image target, no published port, no source mount) and `caddy` (ports 80 and 443), and no
database service. `bash prod/check.sh` asserts that shape and runs in CI; nothing here has run it
against a real server yet.

Why same-origin, the Caddy behaviours that shape the config and what is not decided yet:
[design notes](docs/design-notes.md#how-the-browser-reaches-the-api).

## Testing

```bash
docker compose --profile test up -d --wait
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms alembic upgrade head
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms python -m pytest -q
```

Always pass both `-e` overrides, because the `cms` container's default environment is the DEV database.
The test suite refuses to run without them, but `alembic upgrade` and the `scripts/` commands do not
check, so look at `DB_HOST` and `DB_NAME` before running them.

The seed tests need [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor)
checked out next to this repo; without it they skip. CI checks the sibling out and fails the build
if those tests would skip.

The suite needs no API key: it embeds with `FakeEmbedder`, a deterministic offline stand-in.

## Migrations

Schema changes go through Alembic (`alembic/`), run **inside the `cms` container, never from the
host venv** — the container supplies `DB_PASSWORD`, `DB_HOST=db` and `greenlet`, which the async
template needs and SQLAlchemy does not install on every platform (Apple Silicon, for one).

```bash
docker compose exec cms alembic upgrade head    # apply every migration not yet run
docker compose exec cms alembic downgrade base  # undo them all -- DROPS every table, all data with it; refused unless you add -e ALEMBIC_ALLOW_DESTRUCTIVE=1
```

Alembic refuses a downgrade on any database whose name does not end in `_test` unless
`ALEMBIC_ALLOW_DESTRUCTIVE=1` is set (CI and the Testing commands already target `cms_test`).

The stamp procedure for a volume that predates Alembic, and why `migrate_db.py` stays in the tree,
are in [operations](docs/operations.md#migrations).

## More documentation

- [`docs/api.md`](docs/api.md) — every endpoint, and `POST /chat` in detail.
- [`docs/operations.md`](docs/operations.md) — members and the token budget, login throttling,
  migrations, embeddings, topic selection, health checks and the environment variables.
- [`docs/live-eval.md`](docs/live-eval.md) — the manual live eval of `POST /chat` and its measured
  results.
- [`docs/design-notes.md`](docs/design-notes.md) — the reasoning behind the decisions, trade-offs and
  known limits.

## Related repositories

- [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) — crop suitability from
  NASA POWER climate data and FAO ECOCROP requirements; the structured half of the data behind the
  chat, which numeric questions will reach through MCP tools (not built yet).
- [crop-cms-frontend](https://github.com/KyuHongCho/crop-cms-frontend) — the React app that consumes
  this API: log in, sign up with an invite code, and ask crop questions that come back with cited
  sources.
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
  the chat work draws on. Departure: retrieval returns complete topic sets instead of top-k
  passages, and numeric questions are planned to go to structured data (via MCP) rather than to RAG.

## Licence

Source code: MIT — see [LICENSE](LICENSE). Crop data is not covered: FAO ECOCROP content is © FAO,
under the [FAO Terms and Conditions](https://www.fao.org/contact-us/terms/en/); `items.licence_note`
carries those terms, or the CC BY 4.0 credit and change notice, per document. The demo corpus is for non-commercial use only.

Personal portfolio repository — issues are welcome; external pull requests are not accepted.
