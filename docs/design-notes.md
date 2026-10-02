# Design notes

The reasoning behind the decisions summarised in the [README](../README.md).

## Trade-offs and known limits

The costs and known limits of these design decisions, in brief. The first two are explained in
full further down this file; the last two are documented next to the code they concern.

- **Token budgets are estimated, not measured.** Context size is counted in characters and
  converted at 4 characters per token (a common rule of thumb) rather than with a real tokeniser,
  because the model provider is not chosen yet. Only titles and bodies are counted: if the chat
  feature later puts provenance text into the prompt, the real context is roughly 1.4–2x larger
  and the budget must be revisited. [More below](#topic-set-retrieval-the-no-truncation-guarantee)
- **The refile rule is hard to spot in the model.** Refiling lives in a database trigger, so the
  foreign key in `app/model/model.py` says only `ON DELETE RESTRICT` — which reads as "the delete is
  refused". A comment beside it names the trigger. [More below](#data-model-and-integrity)
- **The refile count can under-report.** A document added between counting and deleting is
  refiled but not counted. Locking the sub-category first (`SELECT ... FOR UPDATE`) would close
  the gap; it is not worth it for a single-user CMS with no concurrent writer.
  [`app/crud/category.py`](../app/crud/category.py), `delete_sub_category`
- **Topic normalisation is not retroactive.** Topics are trimmed and lower-cased when written
  through the ORM; rows inserted without it keep their original form and would need a one-time
  backfill. None is included, because the dev database has no un-normalised topics.
  [`tests/test_retrieval.py`](../tests/test_retrieval.py),
  `test_normalization_does_not_heal_a_row_written_without_the_orm`

## Topic-set retrieval: the no-truncation guarantee

`GET /retrieval/{crop_slug}/{topic}` returns **every** published document sharing that topic for
that crop — never a top-k slice. `app/model/model.py` states why: *"retrieval returns every
document sharing a `topic` rather than a top-k slice -- otherwise a LIMIT silently picks a winner
among disagreeing sources."* Basil's `optimal-temperature` topic carries three attributed,
disagreeing claims (FAO ECOCROP, Chang/Alderson/Wright, Walters & Currey); a `LIMIT 1` or
`LIMIT 2` over that set would not return "the best answer" — it would silently drop at least one
of them, with nothing in the response to say so. There is no `limit` parameter anywhere in this
endpoint's surface, and none is coming.

**The deliberate departure from common RAG practice.** A common RAG pattern is
`similarity_search(query, k=N)` — a top-k slice of *documents*. This system does not do that. Once
topic *selection* lands (over the chunk embeddings the `published_item_chunks` view exposes), `k` will
select **topics**, by the topic's single best-matching passage (MAX, not mean — a mean would
perversely penalise topics that hold more disagreeing sources, exactly the ones this design exists
to surface). Every topic that selection picks still returns **complete**; `k` never truncates a
topic's own document set.

**Budget policy — degrade by whole topics, refuse only as a last resort.** Retrieved context is
measured in **characters**, against a documented characters-per-token ratio (4 chars/token, the
commonly cited rule of thumb for English prose) rather than a real tokeniser — the model provider
is a later decision, and a tokeniser would be provider-specific regardless. It is an estimate, not
a guarantee: only titles and bodies are counted, so if the chat feature later puts provenance text
into the prompt, the real context is roughly 1.4–2x larger (measured on the seed corpus; see
`document_context_chars` in `app/crud/retrieval.py`) and the budget must be revisited.

1. If an assembled combination of topics would exceed the budget, whole topics are dropped,
   lowest-scoring first, until it fits — and the dropped topics are named in the response.
2. Only when the topic left standing after that dropping **still exceeds the budget on its own**
   is the request refused (`413`), naming that topic and its document count. Dropping every other
   topic did not make it fit, so there is nothing left to degrade. When other topics remain, an
   oversized lowest-scoring topic is dropped under step 1 above instead of refused.

A topic is never partially truncated — every topic in a response is either complete or absent,
named either way. This is compatible with the model's constraint above: that guarantee is about
never picking a winner *within* one topic's disagreeing sources; dropping a whole topic discards
a whole question, visibly, rather than silently elevating one source over a rival on the same
question.

Today there is only ever one topic in play — `crop_slug` + `topic` name it directly, and there is
no topic *selection* yet — so in practice a successful response's `dropped` field is always `[]`:
with a single candidate, step 1 never has anything else to drop it against, and only the
single-topic refusal (step 2) is reachable through this HTTP endpoint. The response shape
already carries `dropped` on every successful call, though — not added later — so real
multi-topic selection, once it lands, populates it without a response-shape change. The
drop-whole-topics machinery (step 1) itself is built and tested against constructed topic
candidates in `tests/test_retrieval.py`, ready to call unchanged once that lands.

`GET /items`, by contrast, returns **everything, unfiltered**. `published` defaults to false
server-side, so a published-only filter would make every freshly created document invisible to
the CMS that just created it. Published-only reads go through `/retrieval` instead.

## Data model and integrity

**A crop is an entity, not a category.** Filing crops as taxonomy means every new crop
duplicates the whole tree. `crops` is its own table, and an item points at both a crop and a
sub-category.

**Nothing ranks contradicting sources.** There is deliberately no `priority`, `rank` or
`is_primary` column on `items`. `GET /retrieval/{crop_slug}/{topic}` returns the whole set
sharing a `topic`, never a top-k slice — a `LIMIT` silently picks a winner among sources that
disagree. This mirrors the advisor's own rule, where basil's optimal temperature is carried as
three attributed, disagreeing published claims rather than one. See "Topic-set retrieval" above
for the full policy, including why it departs from the common `similarity_search(query, k=N)`
pattern.

**The provenance rule is a database constraint, not a validator.** `items` carries a `CHECK`
forbidding a row that is marked as read first-hand *and* names the paper it was read through
— that combination would credit the wrong URL and drop the citation chain silently. It
mirrors `crop_advisor/claims.py` in the sibling repo. Application-level validation exists too,
so the client gets a `422` naming the rule rather than a `500`, but the database is what
actually guarantees it.

**Deleting a category never destroys documents, and that rule lives in the schema.** A
sub-category *classifies* its documents rather than owning them. Every foreign key is
`ON DELETE RESTRICT`, and a `BEFORE DELETE` trigger on `sub_categories` refiles the documents to
an **"Uncategorised"** bucket (seeded at id 1) before the delete lands. Deleting the bucket itself
is refused — including when it is empty, which Postgres alone would allow, after which deleting
any sub-category that still held documents would fail with an error naming `items`, a table the
caller never touched.

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

## PostgreSQL, pgvector and the embedding dimension

Retrieval-augmented chat needs vector search next to the documents, and pgvector provides it
inside the same PostgreSQL database.

The constraint that makes the embedding choice a real design question: an index — HNSW or
IVFFlat — takes a `vector` column of at most **2,000 dimensions**, so a 3072-dimension model
cannot be indexed as `vector`. That does not rule such a model out. `halfvec` indexes up to
4,000 dimensions, and an HNSW index on `halfvec(3072)` builds over real rows here with the
planner using it — verified on this stack. Keeping the column as `vector(3072)` and indexing a
`halfvec` cast also works, but only for queries written to match that expression; the naive
query falls back to a sequential scan with no error.

**Decided: OpenAI `text-embedding-3-small`, 1536 dimensions, a plain `vector(1536)` column.**
1536 is under the 2,000-dimension cap, so neither `halfvec` nor a newer pgvector is needed.
`text-embedding-3-large` (3072) would have forced the `halfvec` route above for an index this
corpus does not need yet (next section).

### No vector index yet

`item_chunks` has no HNSW or IVFFlat index. The seed corpus is 13 chunks, 12 of them published.
With an HNSW index built over those 13 rows, the planner still chose a sequential scan for a
nearest-neighbour `ORDER BY embedding <=> ... LIMIT 3` (checked with `EXPLAIN`), and a sequential
scan is exact where HNSW is approximate. Because the column is `vector(1536)` — under the cap — an
index can be added later as a new migration with no column change.

`model` is stored on every row, so a table holding vectors from two embedders is detectable, and
`scripts/reindex.py` re-embeds a chunk whose stored model differs from the current one. Each row
also has two timestamps: `created_at` is when the row was first inserted and never changes;
`embedded_at` is when its current vector was produced, and moves on every re-embed.

## Vector topic selection

[`app/chat/retrieval.py`](../app/chat/retrieval.py) selects topics for a question. All four rules
act on whole topics; none ranks or filters documents inside a topic, which is the winner-picking
the model forbids for sources that disagree.

- **Rule 0 — the floor.** A topic whose score is below `TOPIC_SCORE_FLOOR` is dropped; if none
  clears it, `NoRelevantTopics` is raised and the system abstains. The floor works on whole
  topics only: a design-time measurement (embedder and date not recorded here) scored the three
  sources of `optimal-temperature` for one query at 0.0976 / 0.0729 / 0.0538, so any per-document
  floor in that range would keep some and drop others.
  It ships at **`-1.0`**, not `0.0`: cosine similarity is in `[-1, 1]`, so `-1.0` is the only
  true no-op, whereas `0.0` already drops a topic whose best chunk is slightly anti-correlated
  with the question. The course's `0.4` does not port: it sits on LangChain's normalised `[0, 1]`
  relevance scale, and this code uses raw cosine. Choose the value with
  `scripts/calibrate_floor.py` once the corpus is embedded with the real model.
- **Rule 1 — MAX, not mean.** A mean penalises topics holding many disagreeing sources, which is
  perverse in a system built to surface them. Scores are grouped by `(crop_id, topic)`, since a
  topic name is only unique within a crop. Pass `crop_id` (`scripts.ask --crop SLUG`) to score one crop
  only; without it every crop competes, so with more than one crop a question about one can select
  another's topic. `POST /chat` must pass it.
- **Rule 2 — `k = 3`**, the existing `TOPIC_SELECTION_K`. The SQL `LIMIT` applies to topics;
  each selected topic's documents are fetched in a second query with none.
- **Rule 3 — the budget**, `assemble_within_budget`, reused unchanged.

Topics are scored from the `published_item_chunks` view, but the view has no title or provenance
and the chat layer may not name the raw tables, so each topic's documents come from
`topic_set_statement` in `app/crud/retrieval.py` — the same query `GET /retrieval` uses, so
"a topic's complete published document set" has one definition. Wiring a floor into the existing
retrieval router was rejected: it hardcodes `score=0.0` and takes the topic explicitly, so it
can never abstain.

**Calibration record:** not yet run. Run `docker compose exec -T cms python -m scripts.calibrate_floor`
after the corpus is re-embedded with `text-embedding-3-small`, and paste the output here. Until
then `TOPIC_SCORE_FLOOR` stays at its no-op default.

## Local ports

The API is published on **8000**, and PostgreSQL on **5432** — the default port, so a GUI
client connects without being told a custom one — but bound to **loopback only**
(`127.0.0.1:5432:5432`). Without that host-IP prefix Docker publishes on every interface, which
would put the dev database on the port a scanner tries first. If you already run PostgreSQL on
the host, change the published port in `docker-compose.yaml`; only host tools are affected,
since the app reaches the database over the Docker network (`DB_HOST: db`), never the published
port.

## Testing details

The suite never runs against the dev database. `docker-compose.yaml` has a second,
ephemeral Postgres server, `db-test` — `pgvector/pgvector:pg17` with `POSTGRES_DB: cms_test`,
tmpfs-backed so it starts empty every time, and hidden from a plain `docker compose up` behind
`profiles: [test]`.

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

## The agentic review pipeline

`agentic-workflow` pairs every role with an adversarial auditor — `plan ↔ plan-audit`,
`build ↔ build-audit`, `review ↔ review-audit` — so no stage grades its own homework. Locally
that loop is enforced by hooks. In CI it runs as an `Agentic Review` workflow whose jobs are
`gate → review → review-audit → finalize`: the reviewer inspects the pull request, then a
separate auditor attacks the reviewer's findings before anything is reported.

[crop-climate-advisor](https://github.com/KyuHongCho/crop-climate-advisor) runs it too
(`.github/workflows/agentic-review.yml`), checking the portable `core/` instructions out of
`agentic-workflow` at run time rather than vendoring a copy that would drift.

In this repository, `.github/workflows/agentic-review.yml` runs it: `review` posts its findings —
inline where they sit on a changed line — and `review-audit` then posts a second comment
correcting or confirming them, having formed its own findings blind first. The review is advisory
and gates nothing; only `stack` is a required check.

The reviewer cannot run the suite, and that costs it real evidence. The shared verification
rules (`core/shared/verify.md`) tell it to *run the relevant existing tests and read the
output*, and — when no test covers a claim — to write a throwaway one, run it, and delete it
afterwards. In CI its tool grant (`agentic-review.yml`'s `--allowedTools`) contains no test
runner, so it can do neither: a claim it cannot ground is marked unverified rather than
asserted. The suite is real and runs in `stack`; the reviewer just cannot reach it.

## Project layout

```
app/
  main.py            FastAPI app, router registration
  db/db.py           async engine, session factory, the single declarative Base
  db/migrate_db.py   sync engine; the "Uncategorised" seed SQL and the BEFORE DELETE
                     refile trigger SQL, copied -- deliberately not imported --
                     into alembic/versions/*_baseline.py, which is frozen.
                     Its own drop_all + create_all path is now a guarded pre-Alembic
                     artifact -- it refuses to run once alembic_version exists.
  model/model.py     Crop, MainCategory, SubCategory, Item, ItemChunk — the contract everything matches
  schema/            Pydantic request/response shapes
  crud/              data access — queries and commits (routers do 404 pre-checks)
  router/            HTTP surface
  chat/              embedder seam, chunking, topic selection; scores from the published_item_chunks view
alembic/             schema migrations, run inside the cms container (`alembic upgrade head`)
scripts/seed.py      the basil demo corpus — idempotent, keyed on (crop, title, source)
scripts/reindex.py   embeds every document into item_chunks; skips unchanged chunks
scripts/ask.py       asks a question, prints the selected topics in full with provenance
scripts/calibrate_floor.py  on-topic vs off-topic score distributions, for TOPIC_SCORE_FLOOR
initdb/01-init.sh    creates the pgvector extension and the least-privilege app role
.github/workflows/   CI
docs/                design notes (this file)
```
