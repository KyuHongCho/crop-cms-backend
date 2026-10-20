# Operations

Running and maintaining the service. Quickstart and the basic commands are in the
[README](../README.md#quickstart); the endpoints are in [api.md](api.md).

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
[`app/auth/budget.py`](../app/auth/budget.py). `POST /chat` calls `check_budget` (resets a stale day, answers `429` with
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

## Migrations

The basic commands and the downgrade guard are in the [README](../README.md#migrations).

A `pg-data` volume that predates Alembic is the exception to `alembic upgrade head` on a fresh
database: it already has the tables (built by the
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

[`app/db/migrate_db.py`](../app/db/migrate_db.py) stays in the tree because `tests/conftest.py`, three
test modules and `scripts/seed.py` import its sync `engine`, and `conftest.py` its `SEED_BUCKET_SQL`.
Its own `reset_database()` path (`drop_all` + `create_all`) is a guarded pre-Alembic artifact: it
refuses to run once `alembic_version` exists, so it can never be pointed at a database Alembic
manages. A test asserts that refusal.

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
[why](design-notes.md#vector-topic-selection).
`FakeEmbedder` vectors carry no meaning, so ranking is tested with hand-built vectors
([`tests/test_topic_expansion.py`](../tests/test_topic_expansion.py)), and `scripts.ask` offline only
checks the plumbing.

`item_chunks` carries no HNSW or IVFFlat index yet: [why](design-notes.md#no-vector-index-yet).

## Health checks

The `cms` container healthcheck probes `GET /health` every 10 s, so the uvicorn access log shows it.
It probes `/health`, never `/health/ready`, which would log a `SELECT 1` every 10 s.

## Production database

Production uses an RDS PostgreSQL database that only the server can reach, built by `infra/`
([README](../infra/README.md#the-database)). It is not a container and has no `db` service.

- **Reaching it.** There is no public path. For admin SQL, open an SSH tunnel through the server
  from the laptop and run `psql` (for example in a `postgres:17` container) against the tunnel's
  local port; the steps are in the infra README. The app role `cms_app` is created once by
  `infra/db-bootstrap.sql`; it is not repeatable on purpose.
- **Passwords.** `DB_PASSWORD` in `/opt/cms/.env` is the `cms_app` password and must be hex or
  alphanumeric (see the variable table below). The administrator password (role `cms_master`) is
  managed by RDS in AWS Secrets Manager, rotates about every seven days by default, and is read from
  there when needed; the app never uses it.
- **TLS.** The database requires TLS, and the production compose file sets `PGSSLMODE=require`. That
  encrypts the connection but does not verify the server certificate (the `PGSSLMODE` row below).
- **Backups.** Automated backups are kept 1 day by default (`db_backup_retention_days`, 1 to 35). That
  is the value the Free plan accepted on one real apply; 7 and 3 were refused and 2 was not tried.
  A Paid plan may allow more (untested). Point-in-time recovery is configured for that window.
  Deleting the database takes one final snapshot.
  Restoring from a backup or snapshot is not set up or tried yet, and no one owns that runbook yet.
- **Deploying the app.** The steps that deployed the app to the server, and what they showed, are in
  [deploy.md](deploy.md).
- **Verified so far.** The database was applied once on a real account. The observed facts and the
  open items are in the infra README ("What is verified", "What is not verified").

## Environment variables

`docker-compose.yaml` passes some of these through from `.env` or the host and fixes others; the
"Read in" column names the file that reads each one. Variables read at import (most of them) need `docker compose up -d` after a change;
a plain `restart` does not re-read the environment.

| Variable | Default | Read in | Effect |
|---|---|---|---|
| `SECRET_KEY` | none | `app/auth/auth.py` (at call time) | Signs the login JWT. Unset or empty: logging in fails loudly with a `500`; the rest of the app starts |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | `app/auth/auth.py` (at import) | Token lifetime in minutes; there is no refresh-token flow |
| `TOKENS_BUDGET_DAILY` | `20000` (production template: `100000`, for the shared demo account) | `app/crud/member.py` (at import) | Starting daily token budget for members who sign up afterwards; a negative value stops the app at startup |
| `LOGIN_MAX_FAILURES` | `10` | `app/auth/throttle.py` (at import) | Counted login attempts per account per window; an integer >= 1 or the app does not start |
| `LOGIN_WINDOW_SECONDS` | `900` | `app/auth/throttle.py` (at import) | Length of the login throttle window; an integer from 1 to 315360000 or the app does not start |
| `EMBEDDER` | `openai` | `app/chat/embeddings.py` | `openai` or `fake` (offline); another name raises. Not passed through by compose: use `docker compose exec -e EMBEDDER=fake cms ...` |
| `CHAT_MODEL_CLASSIFY` | `claude-haiku-5-5` | `app/chat/llm.py` (at import) | Model for the `POST /chat` classifier; empty or unset falls back to the default |
| `CHAT_MODEL_GENERATE` | `claude-haiku-5-5` | `app/chat/llm.py` (at import) | Model for the `POST /chat` generator; empty or unset falls back to the default |
| `TOPIC_SCORE_FLOOR` | `-1.0` | `app/chat/retrieval.py` (at import) | Minimum topic score below which the chat layer abstains; `-1.0` is a no-op for cosine similarity. Not passed through by compose |
| `TOPIC_SELECTION_K` | `3` | `app/crud/retrieval.py` (at import) | How many topics a question keeps. Not passed through by compose |
| `CONTEXT_TOKEN_BUDGET` | `8000` | `app/crud/retrieval.py` (at import) | Context budget in tokens, estimated at 4 characters per token. Not passed through by compose |
| `SQL_ECHO` | off | `app/db/db.py`, `app/db/migrate_db.py` (at import) | `1` logs every SQL statement with its parameters; any other value or unset is off. The dev compose file sets `1`; the production one does not |
| `PGSSLMODE` | unset (production compose: `require`) | libpq, via `psycopg` | Encrypts the database connection; `require` does not verify the server certificate |
| `ALEMBIC_ALLOW_DESTRUCTIVE` | unset | `alembic/env.py` | `1` lets `alembic downgrade` run on a database whose name does not end in `_test`; any other value does not |
| `SITE_ADDRESS` | `http://:80` (dev compose); required in `docker-compose.prod.yaml` | `proxy/Caddyfile`, via the compose files | The proxy's site address; unset or empty is plain HTTP in dev, a domain turns on HTTPS |
| `SITE_DIR` | `./proxy/site` (dev compose), `/opt/cms/site` (prod compose) | the compose files | Directory the proxy serves at `/` |
| `ACME_CA` | `https://acme-v02.api.letsencrypt.org/directory` (Let's Encrypt production) | `proxy/Caddyfile`, via the production compose file | The certificate authority Caddy asks for certificates. Set `https://acme-staging-v02.api.letsencrypt.org/directory` to test; empty or unset gives production. With it set, the adapted config lists that CA as the only issuer, so the ZeroSSL fallback that Caddy's documentation describes for its defaults does not apply. Any string passes `caddy validate`, so a typo shows only when issuance fails |
| `COMPOSE_PROJECT_NAME` | the directory name | Docker Compose itself | Names the compose project. Production pins `cms` in its env file, so the volumes are `cms_caddy_data` and `cms_caddy_config`. Compose reads it itself, from the environment, an `--env-file`, or a `.env` in the project directory; production passes `/opt/cms/.env` with `--env-file` |
| `DB_USER` | `cms_app` | `app/db/db.py` | Application database role; the dev compose requires it in `.env`, the production one in the env file passed with `--env-file` |
| `DB_PASSWORD` | none | `app/db/db.py` | Application role's password; the app does not start without it, and compose requires it (dev: in `.env`; production: in the env file passed with `--env-file`). Use hex or alphanumeric characters: it goes into the database URL unescaped, so an `@` misparses it and a `%41`-style escape is silently decoded, which changes the password |
| `DB_HOST` | `db` | `app/db/db.py` | Database host; the dev compose sets `db` for `cms`, the prod compose requires it from the environment |
| `DB_PORT` | `5432` | `app/db/db.py` | Database port; compose sets `5432` for `cms` |
| `DB_NAME` | `cms` | `app/db/db.py` | Database name; compose sets `cms` for `cms` (the tests override it with `cms_test`) |
| `OPENAI_API_KEY` | none | the `langchain-openai` client (`app/chat/embeddings.py`) | Needed by `scripts/reindex.py` and the `POST /chat` query embedding; without it `/chat` answers `503` |
| `ANTHROPIC_API_KEY` | none | the `anthropic` client (`app/chat/llm.py`) | Needed by the `POST /chat` classifier and generator; without it `/chat` answers `503` |
| `POSTGRES_PASSWORD` | none | `docker-compose.yaml` | Password of the bootstrap superuser; compose requires it in `.env` |
| `PYTHONPATH` | `/src` (compose) | `docker-compose.yaml` | Fixed by compose for `cms` |
| `ADVISOR_PATH` | `/advisor` (compose) | `docker-compose.yaml`; read in `tests/test_seed.py` | Where the seed tests import crop-climate-advisor from |
| `ADVISOR_HOST_PATH` | `../crop-climate-advisor` (compose) | `docker-compose.yaml` | Host path of the sibling repo mounted read-only at `/advisor` for the seed tests |
