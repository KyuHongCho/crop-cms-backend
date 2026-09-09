# Crop CMS Backend

[![CI](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/KyuHongCho/crop-cms-backend/actions/workflows/ci.yml)

A FastAPI + PostgreSQL content service for **narrative crop documents carried with their
provenance** — who said it, under what stated condition, and whether the claim was read
first-hand or through another paper.

It is the **content layer for [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor)**
(sibling repo). That project already reasons over *structured* agronomic figures; this one
stores the *prose about* those figures so the advisor can retrieve and cite it. The boundary
is deliberate and load-bearing: agronomic bands live in the advisor's ECOCROP data, never
here. If a figure's only home is a document body, it is in the wrong system.

> **Status: early. Building in progress — not finished.**
> The stack runs end to end: PostgreSQL 17 + pgvector in Docker, a four-table schema whose
> provenance rule is enforced by a database `CHECK`, and HTTP endpoints that read and write it.
> There are **no automated tests**, **no authentication**, **no `DELETE` endpoint for
> documents**, and **no embedding column** — so despite pgvector being installed, there is
> no vector search yet.
> **Nothing consumes this API**: the advisor has no client for it, verified. This README
> describes only what actually runs; planned work is labelled as such.

## What works / what's planned

| | Component | Status |
|---|-----------|--------|
| ✅ | Containerised dev stack — Python 3.13, PostgreSQL 17.11, pgvector 0.8.6 | **working** |
| ✅ | Four-table schema: `crops`, `main_categories`, `sub_categories`, `items` | **working** |
| ✅ | Provenance invariant enforced in the database, not in Python — a claim read first-hand cannot also name the paper it was read through | **working** — CI asserts the `INSERT` is *rejected* |
| ✅ | Deleting a category **refiles** its documents instead of destroying them — `ON DELETE RESTRICT` + a `BEFORE DELETE` trigger + `passive_deletes="all"` | **working** — CI asserts the documents survive, that the bucket cannot be deleted even when empty, and that a crop still holding documents cannot be deleted |
| ✅ | Pydantic schemas + CRUD layer + DB-backed endpoints | **working** — `GET`/`POST` for categories and items, `DELETE` for categories, `GET` for crops |
| ✅ | CI — builds the stack and asserts the schema invariants on every push and PR | **working** — 17 named steps |
| ✅ | Automated tests, `pytest` + `httpx2`, run against an isolated `db-test`/`cms_test` server | **working** — see Testing below |
| ⏳ | `PATCH` everywhere, and `DELETE /items/{id}` | not built — `PATCH` today would blank every field the caller omitted |
| ✅ | Agentic **`review` → `review-audit`** stage in CI — an adversarially-audited review on a pull request, ported from [agentic-workflow](https://github.com/KyuHongCho/agentic-workflow) as [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) already does | **working** — `.github/workflows/agentic-review.yml`; runs on `opened`/`reopened`/`ready_for_review`, or on a `/agentic-review` comment. Advisory: it gates nothing |
| ⏳ | Authentication | not built |
| ⏳ | Embedding column + vector search over document bodies | not built — the model is undecided, and it is a real constraint (see below) |
| ✅ | Topic-set retrieval endpoint (crop + topic) — the **no-truncation guarantee** | **working** — `GET /retrieval/{crop_slug}/{topic}` returns every published document sharing a topic, never a top-k slice. No `limit` parameter exists in the API surface |
| ⏳ | Frontend (`crop-cms-frontend/`) | not started |

Known rough edges, recorded rather than hidden: `PATCH` does not exist, so there is no way to
rename a category or edit a document without replacing it. (A duplicate `slug` used to surface
as `500` instead of `409` — fixed; see Testing.)

The question that used to sit here — whether a sub-category *owns* its documents or merely
*classifies* them — is now settled in favour of **classifies**: deleting a sub-category
refiles its documents rather than destroying them (see below).

## Quickstart

Requires Docker. Credentials are read from a `.env` file, which is git- and docker-ignored
and is **not** in the repository — `docker compose` refuses to parse without it.

```bash
# 1. Create the secrets the compose file requires
cat > .env <<'EOF'
POSTGRES_PASSWORD=<choose one>
DB_USER=cms_app
DB_PASSWORD=<choose another>
EOF

# 2. Bring up the API and the database
docker compose up -d --build

# 3. Create the tables, seed the "Uncategorised" bucket, install the refile
#    trigger (drops and recreates everything — see the warning below)
docker compose exec cms python -m app.db.migrate_db

# 4. Optional: load the basil demo corpus — 13 documents across 5 topics.
#    Idempotent: re-running it changes nothing (tests/test_seed.py asserts that).
docker compose exec cms python -m scripts.seed

# 5. The API
curl localhost:8000/            # {"Hello":"World"}
open  localhost:8000/docs       # interactive OpenAPI
```

> ⚠️ `migrate_db.py` runs `drop_all()` then `create_all()` — it **wipes every row on every
> run**. Harmless while the tables are empty; do not run it once there is real content.
> Choosing Alembic instead is an open decision.

The API is published on **8000**, and PostgreSQL on **5432** — the default port, so a GUI
client connects without being told a custom one — but bound to **loopback only**
(`127.0.0.1:5432:5432`). Without that host-IP prefix Docker publishes on every interface, which
would put the dev database on the port a scanner tries first. If you already run PostgreSQL on
the host, change the published port in `docker-compose.yaml`; only host tools are affected,
since the app reaches the database over the Docker network (`DB_HOST: db`), never the published
port.

## Testing

The suite never runs against the dev database. `docker-compose.yaml` has a second,
ephemeral Postgres server, `db-test` — `pgvector/pgvector:pg17` with `POSTGRES_DB: cms_test`,
tmpfs-backed so it starts empty every time, and hidden from a plain `docker compose up` behind
`profiles: [test]`.

```bash
# 1. Start the test database alongside the normal stack (needs --profile test;
#    a bare `docker compose up` never sees db-test)
docker compose --profile test up -d --wait

# 2. Build the schema in cms_test (never cms) -- DB_HOST/DB_NAME override for
#    this one exec only; app/db/db.py already reads both from the environment
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms \
  python -m app.db.migrate_db

# 3. Run the suite, same override
docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test cms \
  python -m pytest -q
```

### The seed corpus tests need the sibling repo checked out

`tests/test_seed.py` checks that `scripts/seed.py`'s pinned source strings still match what
[crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) actually publishes,
so it imports that project live. `docker-compose.yaml` mounts it read-only at `/advisor`,
defaulting to `../crop-climate-advisor` — check the sibling out next to this repo and it works
with no extra setup. Set `ADVISOR_HOST_PATH` if it lives somewhere else.

Without it, every test in that file **skips rather than fails**, and `pytest -q` reports the
whole file as a single `1 skipped` with no reason — add `-rs` to see it. CI has no such gap: a
dedicated step runs `pytest tests/test_seed.py --collect-only -q -rs` and fails the build if
nothing collects, so a skip there can never pass for a green run. It names no module on
purpose — any guarded import that fires collapses the file to "no tests collected" (exit 5),
which covers guards added later too.

A guard test (`tests/test_categories.py::test_suite_talks_to_the_test_database_never_dev`)
asserts `DB_HOST=db-test` / `DB_NAME=cms_test` before anything else runs, and
`tests/conftest.py`'s per-test fixture checks it again immediately before it TRUNCATEs every
content table between tests — omitting the `-e` overrides fails loudly rather than quietly
touching the dev database. That fixture also reseeds the "Uncategorised" bucket after every
TRUNCATE (`RESTART IDENTITY CASCADE` would otherwise remove it, breaking every test after the
first one that touches it).

Dev tooling: `pytest==9.1.1`, `httpx2==2.12.0` (**not** `httpx` — `starlette==1.6.0`'s
`TestClient` raises `RuntimeError` naming `httpx2` rather than merely warning), both in
`requirements-dev.txt` and installed into the same image as `requirements.txt`.

CI (`.github/workflows/ci.yml`) runs this exact sequence — `docker compose --profile test
up -d --wait`, then the two `exec` calls above — on every push and PR.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/crops` | Read-only. Crops are **seeded** to match the advisor's `data/ecocrop/<slug>.json`, not authored here |
| `GET` `POST` | `/main-categories` | Kind of knowledge: crop profile, research literature, cultivation practice, pests and disorders. `409` on a duplicate `slug`, naming the violated constraint |
| `DELETE` | `/main-categories/{id}` | `204` if empty. `409` naming the count if it still holds sub-categories — a main category never takes its documents with it |
| `GET` `POST` | `/sub-categories` | Unique per parent, not globally. `409` on a duplicate `slug`, naming the violated constraint |
| `DELETE` | `/sub-categories/{id}` | `200 {"documents_refiled": n, "refiled_to": 1}` — the documents move to "Uncategorised", they are not deleted. `409` for "Uncategorised" itself |
| `GET` `POST` | `/items` | One narrative document with its provenance |
| `GET` | `/retrieval/{crop_slug}/{topic}` | Every **published** document sharing that crop and topic. No `limit` parameter, ever — see below. `404` for an unknown crop; `200` with an empty `documents` list for a topic with no published documents (there is no topic registry to 404 against); `413` naming the topic and its document count if that topic alone would exceed the context budget |

`GET /items` returns **everything, unfiltered**. `published` defaults to false server-side, so
a published-only filter would make every freshly created document invisible to the CMS that
just created it. When the advisor needs published-only retrieval it gets a separate endpoint —
`/retrieval`, described next.

### Topic-set retrieval: the no-truncation guarantee

`GET /retrieval/{crop_slug}/{topic}` returns **every** published document sharing that topic for
that crop — never a top-k slice. `app/model/model.py` states why: *"retrieval returns every
document sharing a `topic` rather than a top-k slice -- otherwise a LIMIT silently picks a winner
among disagreeing sources."* Basil's `optimal-temperature` topic carries three attributed,
disagreeing claims (FAO ECOCROP, Chang/Alderson/Wright, Walters & Currey); a `LIMIT 1` or
`LIMIT 2` over that set would not return "the best answer" — it would silently pick one side of an
open disagreement. There is no `limit` parameter anywhere in this endpoint's surface, and none is
coming.

**The deliberate departure from the RAG course this project is built alongside.** The course
teaches `similarity_search(query, k=N)` — a top-k slice of *documents*. This system does not do
that. Once topic *selection* lands (needing chunk embeddings this repository does not have yet),
`k` will select **topics**, by the topic's single best-matching passage (MAX, not
mean — a mean would perversely penalise topics that hold more disagreeing sources, exactly the
ones this design exists to surface). Every topic that selection picks still returns **complete**;
`k` never truncates a topic's own document set.

**Budget policy — degrade by whole topics, refuse only as a last resort.** Retrieved context is
measured in **characters**, against a documented characters-per-token ratio (4 chars/token, the
commonly cited rule of thumb for English prose) rather than a real tokeniser — the model provider
is a later decision, and a tokeniser would be provider-specific regardless. Erring approximate is
fine here because it only ever errs toward dropping early, never toward silently overrunning a
real budget.

1. If an assembled combination of topics would exceed the budget, whole topics are dropped,
   lowest-scoring first, until it fits — and the dropped topics are named in the response.
2. Only when a **single topic alone** exceeds the budget is the request refused (`413`), naming
   that topic and its document count. Dropping every other topic could not have made it fit, so
   there is nothing left to degrade.

A topic is never partially truncated — every topic in a response is either complete or absent,
named either way. This is compatible with the model's constraint above: that guarantee is about
never picking a winner *within* one topic's disagreeing sources; dropping a whole topic discards
a whole question, visibly, rather than silently elevating one source over a rival on the same
question.

Today there is only ever one topic in play — `crop_slug` + `topic` name it directly, and there is
no topic *selection* yet — so in practice a successful response's `dropped` field is always `[]`:
with a single candidate, clause 1 never has anything else to drop it against, and only the
single-topic refusal (clause 2) is reachable through this HTTP endpoint. The response shape
already carries `dropped` on every successful call, though — not added later — so real
multi-topic selection, once it lands, populates it without a response-shape change. The
drop-whole-topics machinery (clause 1) itself is built and tested against constructed topic
candidates in `tests/test_retrieval.py`, ready to call unchanged once that lands.

## Architecture & design decisions

**A crop is an entity, not a category.** Filing crops as taxonomy means every new crop
duplicates the whole tree. `crops` is its own table, and an item points at both a crop and a
sub-category.

**Nothing ranks contradicting sources.** There is deliberately no `priority`, `rank` or
`is_primary` column on `items`. `GET /retrieval/{crop_slug}/{topic}` returns the whole set
sharing a `topic`, never a top-k slice — a `LIMIT` silently picks a winner among sources that
disagree. This mirrors the advisor's own rule, where basil's optimal temperature is carried as
three attributed, disagreeing published claims rather than one. See "Topic-set retrieval: the
no-truncation guarantee" above for the full policy, including the deliberate departure from the
course's `similarity_search(query, k=N)` pattern.

**The provenance rule is a database constraint, not a validator.** `items` carries a `CHECK`
forbidding a row that is marked as read first-hand *and* names the paper it was read through
— that combination would credit the wrong URL and drop the citation chain silently. It
mirrors `crop_advisor/claims.py` in the sibling repo. Application-level validation exists too,
so the client gets a `422` naming the rule rather than a `500`, but the database is what
actually guarantees it.

**Deleting a category never destroys documents, and that rule lives in the schema.** Every
foreign key is `ON DELETE RESTRICT`, and a `BEFORE DELETE` trigger on `sub_categories`
refiles the documents to an **"Uncategorised"** bucket (seeded at id 1) before the delete
lands. Deleting the bucket itself is refused — including when it is empty, which Postgres
alone would allow, after which deleting any sub-category that still held documents would
fail with an error naming `items`, a table the caller never touched.

It is in the database rather than in the router because `psql` and bulk SQL route around
Python entirely. The cost is discoverability, and it is real: `model.py` reads `RESTRICT`,
from which a reader would conclude the delete is *refused*. The comment at the foreign key
names the trigger.

The relationships carry `passive_deletes="all"` and deliberately **no**
`cascade="all, delete-orphan"`. `delete-orphan` issues the child `DELETE`s from Python —
exactly the data loss this design exists to prevent — and plain `passive_deletes=True` only
suppresses the pre-emptive `SELECT`, so a collection that happens to have been loaded
earlier in the same request is still cascaded. `"all"` is load-independent. Putting
`delete-orphan` back beside it is a configuration error SQLAlchemy refuses outright, so
CI's endpoint checks catch the regression rather than a silent data loss reaching
production.

**PostgreSQL, diverging from the course this was built alongside.** The course is
[Dipping into FastAPI (FastAPI + React.js + AWS LightSail)](https://www.inflearn.com/en/course/fastapi-%EC%B0%8D%EC%96%B4%EB%A8%B9%EA%B8%B0)
by ddur on Inflearn; its companion code is
[ym7596/FASTAPI-CMS-SERVER](https://github.com/ym7596/FASTAPI-CMS-SERVER), which uses MySQL and
whose `schema/` and `crud/` layers are what the comments in this repo's `app/schema/` and
`app/crud/` compare against. The RAG content-layer requirement makes pgvector the deciding
factor here.

The constraint that makes the embedding choice a real design question: an index — HNSW or
IVFFlat — takes a `vector` column of at most **2,000 dimensions**, so a 3072-dimension model
cannot be indexed as `vector`. That does not rule such a model out. `halfvec` indexes up to
4,000 dimensions, and an HNSW index on `halfvec(3072)` builds over real rows here with the
planner using it — verified on this stack. Keeping the column as `vector(3072)` and indexing a
`halfvec` cast also works, but only for queries written to match that expression; the naive
query falls back to a sequential scan with no error. So what is open is the model and which of
those two column shapes to use — not whether 3072 fits. That is why there is no embedding
column yet.

## Related repositories

- **[crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor)** — the consumer
  this exists to serve. Crop suitability from live NASA POWER climate data and FAO ECOCROP
  requirements, with an MCP server and attributed, disagreeing claims. Also a work in progress.
- **[agentic-workflow](https://github.com/KyuHongCho/agentic-workflow)** — the tool-agnostic
  `plan → build → review` loop, with adversarial auditors and hard-enforced gates, used to
  build both.

### The agentic review pipeline, and why it is not here yet

`agentic-workflow` pairs every role with an adversarial auditor — `plan ↔ plan-audit`,
`build ↔ build-audit`, `review ↔ review-audit` — so no stage grades its own homework. Locally
that loop is enforced by hooks. In CI it runs as an `Agentic Review` workflow whose jobs are
`gate → review → review-audit → finalize`: the reviewer inspects the pull request, then a
separate auditor attacks the reviewer's findings before anything is reported.

[crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) already runs it
(`.github/workflows/agentic-review.yml`), checking the portable `core/` instructions out of
`agentic-workflow` at run time rather than vendoring a copy that would drift.

**This repository now runs it too**, as `.github/workflows/agentic-review.yml`: `review`
posts its findings — inline where they sit on a changed line — and `review-audit` then posts a
second comment correcting or confirming them, having formed its own findings blind first. The
review is advisory and gates nothing; only `stack` is a required check.

It still has no test suite, and that costs the reviewer real evidence. The shared verification
rules (`core/shared/verify.md`) tell it to *run the relevant existing tests and read the
output*, and — when no test covers a claim — to write a throwaway one, run it, and delete it
afterwards. In CI it holds read-only tools and cannot do the second half, so a claim it cannot
ground is marked unverified rather than asserted. Hence "automated tests" sitting directly
above it in the table: the loop works without a suite, it just does more work for weaker
evidence.

## Licence

The **source code** in this repository is MIT licensed — see [LICENSE](LICENSE).

That grant does not extend to any crop data seeded into the database. FAO ECOCROP content is
© FAO and remains subject to the
[FAO Terms and Conditions](https://www.fao.org/contact-us/terms/en/) (non-commercial research,
with attribution); `items.licence_note` exists to carry those terms per document.

## Project layout

```
app/
  main.py            FastAPI app, router registration
  db/db.py           async engine, session factory, the single declarative Base
  db/migrate_db.py   sync engine, drop_all + create_all, the "Uncategorised" seed
                     and the BEFORE DELETE refile trigger
  model/model.py     Crop, MainCategory, SubCategory, Item — the contract everything matches
  schema/            Pydantic request/response shapes
  crud/              data access — queries and commits (routers do 404 pre-checks)
  router/            HTTP surface
scripts/seed.py      the basil demo corpus — idempotent, keyed on (crop, title, source)
initdb/01-init.sh    creates the pgvector extension and the least-privilege app role
.github/workflows/   CI
```

## Contributing

This is a personal portfolio repository, so **I'm not accepting external pull requests.**
You're welcome to fork it and reuse it under the MIT licence, and issues are welcome if
something is wrong or unclear.
