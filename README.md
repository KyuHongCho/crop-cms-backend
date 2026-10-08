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
| Document store — 5 content tables (8 with `members`, `member_audit_events` and `member_invites`), sources recorded per document | |
| Members: invite-only signup, login (JWT), `/members/me`; daily token budget, enforced by `/chat` | Editing (`PATCH`) documents |
| Embeddings for every document, offline-testable (`scripts/reindex.py`) | Frontend and deployment |
| Vector topic selection — `python -m scripts.ask "<question>"` | |
| `POST /chat`: classify, select topics, generate a cited answer. Off-topic questions are declined and questions with no relevant topic abstain, both `200` with `abstained` set and no generation call. Needs both `ANTHROPIC_API_KEY` (classifier, generator) and `OPENAI_API_KEY` (query embedding); a missing key or a provider error (usage limit, rate limit, outage) from either is a plain `503`, after the budget `429` check (the cause is logged, not returned). Models are `CHAT_MODEL_CLASSIFY` and `CHAT_MODEL_GENERATE` (both default to `claude-haiku-4-5`, set in `app/chat/llm.py`). The response carries `truncated` (`true` when the answer was cut at the generator's `max_tokens` and may be incomplete; the answer then also ends with a blank line and a fixed notice, but a frontend should read `truncated` rather than string-match the notice; a declined or abstained response is always `false` and carries no notice). The SDK's default retries stay on, so a `429` or `5xx` is called up to 3 times, with backoff, before the `503`. Answers are not word-for-word repeatable: no sampling parameters are set. `anthropic` is in `requirements.txt`: run `docker compose build` so the image has it (the tests in `tests/test_chat.py` that use the real client fail with ImportError otherwise) | |
| Topic-set retrieval — `GET /retrieval/{crop_slug}/{topic}` | |
| Category delete that refiles documents instead of deleting them | |
| Deleting documents (`DELETE /items/{id}`) | |
| Database migrations (Alembic), exercised for real in CI | |
| `POST /chat` crop labels when no crop is fixed; the routing question set and manual live-eval script (run live twice; results under "Live eval") | |
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
```

API on `localhost:8000` (interactive docs at `/docs`) and PostgreSQL on `5432`; both ports are bound to
`127.0.0.1` only, so other machines on your network can't reach them (Docker 28 or newer).

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
docker compose exec cms alembic downgrade base  # undo them all -- DROPS every table, all data with it; refused unless you add -e ALEMBIC_ALLOW_DESTRUCTIVE=1
```

Alembic refuses a downgrade on any database whose name does not end in `_test` unless
`ALEMBIC_ALLOW_DESTRUCTIVE=1` is set (CI and the Testing commands already target `cms_test`).

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
| `GET` `POST` | `/main-categories` | `POST` needs editor or admin. `409` on a duplicate `slug` |
| `GET` `POST` | `/sub-categories` | `POST` needs editor or admin. Unique per parent, not globally; `409` on a duplicate `slug` under the same parent |
| `DELETE` | `/main-categories/{id}` | Editor or admin. `409` while it still has sub-categories |
| `DELETE` | `/sub-categories/{id}` | Editor or admin. Refiles its documents to "Uncategorised" and returns the count |
| `GET` `POST` | `/items` | A document and its sources. `POST` needs editor or admin; `GET` is published-only unless `status=all` (see the note below the table) |
| `DELETE` | `/items/{id}` | Editor or admin. `200` with the deleted row (`ItemResponse`), because a hard delete leaves no backup or audit trail and the response is the only recovery; its chunks go with it. `404` for an unknown id, so a repeat is `404`; `422` for an id outside the 32-bit integer range |
| `POST` | `/members/signup` | Needs an `invite_code` in the body (`422` without one). `201`; the new member takes the invite's role and a `role` in the body is ignored. `400 "Invalid or expired invite"` for an unknown, used, expired or wrong-email code (one message for all four); `400` on a duplicate email, which leaves the invite unused. Argon2id hash, run in the threadpool |
| `POST` | `/members/login` | `{"access_token": ...}`; the same `401` for an unknown email, a wrong password and a deactivated member. `429` with `Retry-After` once an account has used its counted attempts (see Login throttling); `422` for an email over 255 characters or containing NUL, or a password over 128 |
| `GET` | `/members/me` | Needs `Authorization: Bearer <token>`; `401` otherwise |
| `POST` | `/members/invites` | Admin only (`401` without a token, `403` otherwise). Body `role` (`member` default, or `editor`; `admin` is `422`), `expires_in_days` (1-30, default 7) and an optional `email` the invite is bound to (stored stripped and lower-cased). `201` returns the 43-character `code` **once**; only its SHA-256 is stored. Writes an `invite_create` audit row (role and expiry, never the code or the email). The operator, with no admin yet, runs `docker compose exec -T cms python -m scripts.make_invite [--role editor] [--days N] [--email ADDRESS]` (prints the code; no audit row). Signup consumes it |
| `GET` | `/members/invites` | Admin only (`401` without a token, `403` otherwise). The invites that can still be claimed (unused and unexpired), ordered by id, `limit` 1-100 (default 50) and `offset` >= 0 (else `422`). Each is `id`, `role`, `email`, `created_at`, `expires_at`; never the code or its hash |
| `DELETE` | `/members/invites/{id}` | Admin only (`401` without a token, `403` otherwise). Revokes by deleting the row (`204`); an unknown id is `404`, a used invite `409` (it is already spent and is kept as the record that the code was claimed), an expired unused one is revoked. Writes an `invite_revoke` audit row (the role only) in the same transaction. A revoked code then fails signup with the usual `400 Invalid or expired invite` |
| `GET` | `/members` | Admin only (`401` without a token, `403` otherwise). `limit` 1-100 (default 50), `offset`, filters `role` and `is_active`; ordered by id; never the password hash |
| `PATCH` | `/members/{id}` | Admin only. Body `role`, `tokens_budget_daily` (0-10 000 000) and/or `is_active` (a JSON boolean only); any other field or an empty body is `422`; `404` for an unknown id. `is_active: false` deactivates a member at once (their token gets `401` on the next request and works again on reactivation). `403` if the acting admin was deactivated or demoted while the request was in flight (nothing changes, no audit row); `409` if an admin changes their own role or deactivates themselves (the only way to reach the last active admin, so that is the message a client sees; a separate last-admin refusal sits behind it as defence in depth). Each real change writes one `member_audit_events` row in the same transaction (no email); a PATCH that changes nothing is `200` with no row |
| `DELETE` | `/members/{id}` | Admin only. `204`, a hard delete of the member row; their token gets `401` on the next request and login with their email is the usual `401`. `404` for an unknown id (so a second delete is `404`); `403` if the acting admin was deactivated or demoted while the request was in flight; `409` if an admin deletes themselves (the only way to reach the last active admin, so that is the message a client sees; a separate last-admin refusal sits behind it as defence in depth). Each delete writes one `member_audit_events` row (`action` `delete`, `detail` `{"role": ...}` only, no email) in the same transaction; the row outlives the member (no foreign key). Refusals delete nothing and write no row |
| `POST` | `/members/{id}/unlock` | Admin only (`401` without a token, `403` otherwise). Clears that account's login throttle (`204`; idempotent: nothing to clear is still `204`); an unknown id is `404`; `403` if the acting admin was deactivated or demoted while the request was in flight. Every successful call writes an `unlock` audit row (`{"cleared": 0 or 1}`, the member id as target, never the email). The delete and the audit row commit together |
| `GET` | `/retrieval/{crop_slug}/{topic}` | Every published document on a topic; `413` if the topic exceeds the budget |

**`GET /items`.** Published documents only, ordered by id, paged by `limit` (1-500, default 500) and
`offset` (>= 0); anything out of range is `422`. `?status=all` (default `published`; any other value is
`422`) adds drafts and needs an editor or admin token: `401` without a token or with a bad one,
`403` for a plain `member`. A new document is a draft, so it appears only under `status=all`. The default path
ignores any token, but Swagger shows a lock on this route because the `status=all` path takes one.

## Members and the token budget

Set `SECRET_KEY` in `.env` (`openssl rand -hex 32`); signing a token without it fails loudly. Tokens
last 30 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`) and **no refresh-token flow is implemented** -- log in
again. Every CMS read is open except `GET /items?status=all` (drafts: editor or admin). **Who may write:** the CMS write routes (`POST /items`, `DELETE /items/{id}`, `POST /main-categories`,
`POST /sub-categories`, `DELETE /main-categories/{id}`, `DELETE /sub-categories/{id}`) need a token from a member whose
`members.role` is `editor` or `admin` (`401` without a token, `403` for a plain `member`). Signup needs an invite and the member takes
the invite's role (`member` or `editor`, never `admin`); the body cannot set one. The role is read from the member's row on every request, not from the token, so a
demotion takes effect at once. An operator grants the first role with SQL, for example
`UPDATE members SET role = 'admin' WHERE email = '...'` (the column accepts `member`, `editor`, `admin`; a CHECK refuses anything else).
An admin lists members with `GET /members` and changes a member's role or daily budget with `PATCH /members/{id}`, audited in
`member_audit_events` (`is_active` included: `{"is_active": {"from": true, "to": false}}`). `is_active` is read on every request, like the role, so
deactivating a member (`PATCH {"is_active": false}`) takes effect at once: their existing token gets the usual `401 Not authenticated` on every
route (`/members/me`, `/chat`, the editor and admin routes) and works again, unchanged, after `{"is_active": true}`; nothing is revoked. Login with the
right password for a deactivated member returns the very same `401 "Incorrect email or password"` as a wrong password (the password is verified first,
so the cost and the answer do not tell a caller the account is deactivated). An admin cannot deactivate themselves and the last active admin cannot be
deactivated (`409`, no audit row).
An admin deletes a member with `DELETE /members/{id}`: a hard delete, audited (the row keeps the deleted member's role and nothing else), and an admin cannot delete
themselves. No foreign key references `members` yet, so a delete removes only the member row and nothing cascades; whoever adds the first one decides `ON DELETE CASCADE`
versus `RESTRICT` then.
The budget exists for the model-calling route, `POST /chat`.

Each member has a daily token budget (`members.tokens_budget_daily`, default 20000), implemented in
[`app/auth/budget.py`](app/auth/budget.py). `POST /chat` calls `check_budget` (resets a stale day, answers `429` with
`Retry-After` before any model call) and `record_usage` with the tokens the provider reported.
Check and record are separate steps with no lock, so concurrent requests from one member can overshoot
the cap. The overall spend bound is the monthly spend limit set in each provider's console (Billing
page); this budget is the per-member control on top of it.

`TOKENS_BUDGET_DAILY` (default 20000) sets the starting budget for members who sign up after the
container is recreated (`docker compose up -d`; a plain `restart` does not re-read the environment); existing members keep theirs, so raise one with an `UPDATE members SET tokens_budget_daily = ...`.

## Login throttling

`POST /members/login` counts attempts per account (the SHA-256 of the stripped, lower-cased email, so
case and padding share one counter) in the `login_throttle` table: `LOGIN_MAX_FAILURES` (default 10) per
window of `LOGIN_WINDOW_SECONDS` (default 900). The next attempt in that window is `429 "Too many failed
attempts"` with `Retry-After`, even with the correct password, and does no hashing. A wrong password and
an unknown email each count one (a row is made for an unknown email, so a `429` does not reveal which
emails exist); a correct password for an active member deletes the row; a deactivated member's correct
password keeps the attempt and gets the usual `401`. A `422` counts nothing. Both settings are read at
import (`docker compose up -d` after changing them) and a value below 1 or not an integer stops the app
at startup, naming the variable; `LOGIN_WINDOW_SECONDS` must also be <= 315360000 (10 years).

The counter is one atomic statement in the database, so it holds across workers and restarts. The
window is fixed: it starts at the first attempt after the previous one expired, so a burst can straddle
a boundary, but at most 50 attempts per account fit in any 3600 seconds. There is no per-IP limit, and
guesses spread across many accounts are not stopped.

**Lockout risk:** someone who sends ten attempts at the start of every 900-second window keeps one
account locked out indefinitely, and `Retry-After` tells them when the next window opens. This is
accepted.

**Recovery:** an admin with a valid token calls `POST /members/{id}/unlock`, which deletes the account's
throttle row so the owner can log in at once. A persistent attacker can lock the account again, so this
buys a gap, not a fix. A sole admin who is locked out and whose token has expired (30 minutes) uses the
operator script below; without shell access to the deployment it cannot be cleared early, and the lock
lifts when the current window ends unless the attacker keeps going.

**Operator unlock:** `docker compose exec -T cms python -m scripts.unlock_login ADDRESS` deletes that
account's throttle row and prints `unlocked`, or `nothing to unlock` if it had none. Case and padding of
`ADDRESS` do not matter. An address starting with `-` needs `--` before it
(`... scripts.unlock_login -- -a@b.c`). An argument that is not valid UTF-8 is refused with exit 2
before any database access. An address with no throttle row prints `nothing to unlock` and is a harmless
no-op, member or not. An address that is not a member but was tried at login does have a throttle row;
that row is deleted and reported as `unlocked`. Every run that reaches
the database writes one `unlock` audit row (`{"cleared": 0 or 1}`) in the same transaction as the
delete, with `actor_id` 0, which means "operator or script, not a member" (the target is the member's id,
or NULL if the address is not a member); anything that joins `actor_id` to `members` must treat 0 that
way. It works even when `LOGIN_MAX_FAILURES` or `LOGIN_WINDOW_SECONDS` is invalid.

**Check the database first.** The script acts on the database named by `DB_HOST`, `DB_PORT`, `DB_NAME`,
`DB_USER` and `DB_PASSWORD`. Under `docker compose exec cms` that is the DEV database `cms` on host `db`.
Check `DB_HOST` and `DB_NAME` in the environment before you run it, for example by printing them; for
production, export that environment's values first. The stderr line
`unlock_login: DB_HOST=... DB_NAME=...` (host and database only, not port or user) names the database the
script is about to act on. It is printed before the connection is opened, in the same call that deletes
and commits, so it cannot stop a wrong-database run, and it still appears if the connection then fails.
Worst case against the wrong database: one throttle row deleted and one audit row added.

Expired rows are pruned by later logins (up to 20 per attempt). To sweep them by hand, with the
`LOGIN_WINDOW_SECONDS` value in place of `<seconds>`:
`DELETE FROM login_throttle WHERE window_started_at < now() - make_interval(secs => <seconds>);`

The throttle table must exist before the new code runs: if it is missing every login fails with a `500`
(the throttle fails closed). Run `alembic upgrade head` first. To roll back, revert the code, then
`docker compose exec cms alembic downgrade -1`, which loses only the throttle's counts. Alembic refuses it
on a database not named `*_test` unless you deliberately add `-e ALEMBIC_ALLOW_DESTRUCTIVE=1`.

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

### Measured results

Two live runs against the dev database (claude-haiku-4-5, text-embedding-3-small, 17 questions x 3
repeats, 51 classifier calls, no refusals for the context budget). These describe this question set only:
it is also the set the wording and slug fixes below were tuned against (no held-out set), and 3 repeats per
question is a small sample, so they say nothing about general accuracy.

| | Routing accuracy | Flip rate (questions) |
|---|---|---|
| Run 1, before slug separator mapping and the multi-crop wording | 44/51 = 86.3% | 1/17 = 5.9% |
| Run 2, with both | 46/51 = 90.2% | 2/17 = 11.8% |

With 3 repeats per question, the rise in the flip rate (1/17 to 2/17) is not distinguishable from sampling noise.

- "how do I grow sweet peppers from seed?" went 2/3 to 3/3: the model had emitted `sweet pepper`, and
  separators in the slug are now mapped to hyphens.
- "is basil or tomato more sensitive to cold?" went 0/3 to 2/3: the classifier set `crop_slug="basil"`,
  scoping a comparison to one crop; the tool description and system prompt now say a comparison must not
  set it. It still flips.
- "what causes leaf spots?" went 3/3 to 2/3: one repeat routed `out_of_scope`; cause unknown, possibly
  sampling noise.
- "how should I clean between cycles?" is routed `out_of_scope` 3/3 in both runs, against its
  `document_lookup` label (the corpus has no cleaning document; the label is not changed).
- All 7 out-of-scope questions, including the prompt-injection one, were routed correctly 3/3 in both
  runs. The basil cuttings answer stated the gap first and plainly in all 6 repeats. `claude-haiku-4-5`
  accepted the forced `tool_choice` live (no `400`).
- Tokens (run 2): classifier 922 / 952 / 987 per call (min / median / max; run 1's median was 905, before
  the longer classifier wording), generator 1458 / 1841 / 2523. The lookup-question median is 2771 per question, so **7.2 questions per member per
  day** at the 20000 default (worst single question 3445, 5.8 per day). The 51 questions used 97,853
  tokens (mean 1919). Run 1: lookup median 2745.5, 7.3 per day, 98,218 tokens (both within 1% of run 2).
- Cost: Haiku 4.5 is $1 / $5 per million input / output tokens
  ([pricing](https://platform.claude.com/docs/en/models/overview)), so a 51-question run costs between
  about $0.10 and $0.49 depending on the input/output split (the script reports the sum only). These token
  and cost figures cover the two Anthropic calls only. Each lookup question also makes one OpenAI embedding
  call (`text-embedding-3-small`, $0.02 per million tokens per
  [OpenAI's pricing](https://developers.openai.com/api/docs/pricing)): a few hundred tokens per run,
  effectively free. The script does not measure it, and the member token budget does not count it. Other
  `CHAT_MODEL_*` choices change the Anthropic prices (see the models page above).

Answers are not word-for-word repeatable: the pinned SDK has no `temperature` argument and Claude 4.7 and later models reject non-default values, so the
same question can be routed or worded differently between runs, which is why the eval reports a rate. The
question set's two collision labels (for the not-yet-built `crop_cycle_days`) are checked only by this live
run; offline the stub just returns the label. The live runs showed "how many days is a lettuce crop cycle?"
routed to lookup with `lettuce` 3/3, and "how should I clean between cycles?" routed `out_of_scope` 3/3.
Relabel the first when that intent ships.

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
