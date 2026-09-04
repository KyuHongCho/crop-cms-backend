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
> There are **no automated tests**, **no authentication**, **no `DELETE` endpoints**, and
> **no embedding column** — so despite pgvector being installed, there is no vector search yet.
> **Nothing consumes this API**: the advisor has no client for it, verified. This README
> describes only what actually runs; planned work is labelled as such.

## What works / what's planned

| | Component | Status |
|---|-----------|--------|
| ✅ | Containerised dev stack — Python 3.13, PostgreSQL 17.11, pgvector 0.8.6 | **working** |
| ✅ | Four-table schema: `crops`, `main_categories`, `sub_categories`, `items` | **working** |
| ✅ | Provenance invariant enforced in the database, not in Python — a claim read first-hand cannot also name the paper it was read through | **working** — CI asserts the `INSERT` is *rejected* |
| ✅ | Database-level cascades (`ON DELETE CASCADE` + `passive_deletes`) | **working** — CI asserts a crop delete removes its documents |
| ✅ | Pydantic schemas + CRUD layer + DB-backed endpoints | **working** — `GET`/`POST` for categories and items, `GET` for crops |
| ✅ | CI — builds the stack and asserts the schema invariants on every push and PR | **working** — 8 checks |
| ⏳ | `DELETE` endpoints | not built |
| ⏳ | Automated tests (`pytest`/`httpx` are not even installed yet) | not built |
| ⏳ | Agentic **`review` → `review-audit`** stage in CI — an adversarially-audited review on every pull request, ported from [agentic-workflow](https://github.com/KyuHongCho/agentic-workflow) as [crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) already does | not built — worth more once the row above exists |
| ⏳ | Authentication | not built |
| ⏳ | Embedding column + vector search over document bodies | not built — the model is undecided, and it is a real constraint (see below) |
| ⏳ | Retrieval endpoint the advisor would actually call (crop + topic) | not built |
| ⏳ | Frontend (`crop-cms-frontend/`) | not started |

Known rough edges, recorded rather than hidden: a duplicate `slug` currently surfaces as
`500` instead of `409`, and whether a sub-category should *own* its documents or merely
*classify* them is still undecided — today deleting a category destroys everything filed
under it.

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

# 3. Create the tables (drops and recreates them — see the warning below)
docker compose exec cms python -m app.db.migrate_db

# 4. The API
curl localhost:8000/            # {"Hello":"World"}
open  localhost:8000/docs       # interactive OpenAPI
```

> ⚠️ `migrate_db.py` runs `drop_all()` then `create_all()` — it **wipes every row on every
> run**. Harmless while the tables are empty; do not run it once there is real content.
> Choosing Alembic instead is an open decision.

The API is published on **8000** and PostgreSQL on **55432** (not 5432, to avoid colliding
with a local install).

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/crops` | Read-only. Crops are **seeded** to match the advisor's `data/ecocrop/<slug>.json`, not authored here |
| `GET` `POST` | `/main-categories` | Kind of knowledge: crop profile, research literature, cultivation practice, pests and disorders |
| `GET` `POST` | `/sub-categories` | Unique per parent, not globally |
| `GET` `POST` | `/items` | One narrative document with its provenance |

`GET /items` returns **everything, unfiltered**. `published` defaults to false server-side, so
a published-only filter would make every freshly created document invisible to the CMS that
just created it. When the advisor needs published-only retrieval it gets a separate endpoint.

## Architecture & design decisions

**A crop is an entity, not a category.** Filing crops as taxonomy means every new crop
duplicates the whole tree. `crops` is its own table, and an item points at both a crop and a
sub-category.

**Nothing ranks contradicting sources.** There is deliberately no `priority`, `rank` or
`is_primary` column on `items`. Retrieval is intended to return the whole set sharing a
`topic`, never a top-k slice — a `LIMIT` silently picks a winner among sources that disagree.
This mirrors the advisor's own rule, where basil's optimal temperature is carried as three
attributed, disagreeing published claims rather than one.

**The provenance rule is a database constraint, not a validator.** `items` carries a `CHECK`
forbidding a row that is marked as read first-hand *and* names the paper it was read through
— that combination would credit the wrong URL and drop the citation chain silently. It
mirrors `crop_advisor/claims.py` in the sibling repo. Application-level validation exists too,
so the client gets a `422` naming the rule rather than a `500`, but the database is what
actually guarantees it.

**Cascades live in the schema.** `ON DELETE CASCADE` with `passive_deletes=True`, not ORM-only
cascade, because ORM cascade is bypassed by bulk deletes and raises instead.

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

**This repository has only `ci.yml`.** Porting the review workflow is planned, and it is worth
sequencing after a test suite rather than before one. The shared verification rules
(`core/shared/verify.md`) tell a reviewer to *run the relevant existing tests and read the
output*, and — when no test covers a claim — to write a throwaway one, run it, and delete it
afterwards. So the loop is not blocked by having no suite; it just does more work for weaker
evidence, and the runner has to install Python and the test dependencies before the agent
starts, which this repository's CI does not yet do. Hence "automated tests" sitting directly
above it in the table.

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
  db/migrate_db.py   sync engine, drop_all + create_all
  model/model.py     Crop, MainCategory, SubCategory, Item — the contract everything matches
  schema/            Pydantic request/response shapes
  crud/              data access — queries and commits (routers do 404 pre-checks)
  router/            HTTP surface
initdb/01-init.sh    creates the pgvector extension and the least-privilege app role
.github/workflows/   CI
```

## Contributing

This is a personal portfolio repository, so **I'm not accepting external pull requests.**
You're welcome to fork it and reuse it under the MIT licence, and issues are welcome if
something is wrong or unclear.
