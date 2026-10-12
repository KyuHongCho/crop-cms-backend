# API

Interactive docs are at `/docs` on `localhost:8000` (see the [README](../README.md#quickstart)).

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
| `POST` | `/members/login` | `{"access_token": ...}`; the same `401` for an unknown email, a wrong password and a deactivated member. `429` with `Retry-After` once an account has used its counted attempts (see [Login throttling](operations.md#login-throttling)); `422` for an email over 255 characters or containing NUL, or a password over 128 |
| `GET` | `/members/me` | Needs `Authorization: Bearer <token>`; `401` otherwise |
| `POST` | `/members/invites` | Admin only (`401` without a token, `403` otherwise). Body `role` (`member` default, or `editor`; `admin` is `422`), `expires_in_days` (1-30, default 7) and an optional `email` the invite is bound to (stored stripped and lower-cased). `201` returns the 43-character `code` **once**; only its SHA-256 is stored. Writes an `invite_create` audit row (role and expiry, never the code or the email). The operator, with no admin yet, runs `docker compose exec -T cms python -m scripts.make_invite [--role editor] [--days N] [--email ADDRESS]` (prints the code; no audit row). Signup consumes it |
| `GET` | `/members/invites` | Admin only (`401` without a token, `403` otherwise). The invites that can still be claimed (unused and unexpired), ordered by id, `limit` 1-100 (default 50) and `offset` >= 0 (else `422`). Each is `id`, `role`, `email`, `created_at`, `expires_at`; never the code or its hash |
| `DELETE` | `/members/invites/{id}` | Admin only (`401` without a token, `403` otherwise). Revokes by deleting the row (`204`); an unknown id is `404`, a used invite `409` (it is already spent and is kept as the record that the code was claimed), an expired unused one is revoked. Writes an `invite_revoke` audit row (the role only) in the same transaction. A revoked code then fails signup with the usual `400 Invalid or expired invite` |
| `GET` | `/members` | Admin only (`401` without a token, `403` otherwise). `limit` 1-100 (default 50), `offset`, filters `role` and `is_active`; ordered by id; never the password hash |
| `PATCH` | `/members/{id}` | Admin only. Body `role`, `tokens_budget_daily` (0-10 000 000) and/or `is_active` (a JSON boolean only); any other field or an empty body is `422`; `404` for an unknown id. `is_active: false` deactivates a member at once (their token gets `401` on the next request and works again on reactivation). `403` if the acting admin was deactivated or demoted while the request was in flight (nothing changes, no audit row); `409` if an admin changes their own role or deactivates themselves (the only way to reach the last active admin, so that is the message a client sees; a separate last-admin refusal sits behind it as defence in depth). Each real change writes one `member_audit_events` row in the same transaction (no email); a PATCH that changes nothing is `200` with no row |
| `DELETE` | `/members/{id}` | Admin only. `204`, a hard delete of the member row; their token gets `401` on the next request and login with their email is the usual `401`. `404` for an unknown id (so a second delete is `404`); `403` if the acting admin was deactivated or demoted while the request was in flight; `409` if an admin deletes themselves (the only way to reach the last active admin, so that is the message a client sees; a separate last-admin refusal sits behind it as defence in depth). Each delete writes one `member_audit_events` row (`action` `delete`, `detail` `{"role": ...}` only, no email) in the same transaction; the row outlives the member (no foreign key). Refusals delete nothing and write no row |
| `POST` | `/members/{id}/unlock` | Admin only (`401` without a token, `403` otherwise). Clears that account's login throttle (`204`; idempotent: nothing to clear is still `204`); an unknown id is `404`; `403` if the acting admin was deactivated or demoted while the request was in flight. Every successful call writes an `unlock` audit row (`{"cleared": 0 or 1}`, the member id as target, never the email). The delete and the audit row commit together |
| `GET` | `/retrieval/{crop_slug}/{topic}` | Every published document on a topic; `413` if the topic exceeds the budget |
| `POST` | `/chat` | Members only (`401` without a token). Body `{"question": "..."}`, 1-2000 characters, not blank (else `422`). `200` with the answer and its cited documents; an off-topic question or one with no relevant topic is also `200`, with `abstained` set. `429` with `Retry-After` when the daily token budget is spent, `413` when the topic set exceeds the context budget, `503` when a model or embedding provider is unavailable. See [POST /chat](#post-chat) below |
| `GET` | `/health` | Liveness probe: `200 {"status": "ok"}` with `Cache-Control: no-store`. Open, no token, never touches the database |
| `GET` | `/health/ready` | Readiness probe: runs `SELECT 1`. `200 {"status": "ok"}`, or `503 {"status": "unavailable"}` when the database is unreachable, errors, or takes over 3 s; the body never carries the cause (it is logged as an exception class name). Open, `Cache-Control: no-store` on both |
| `GET` | `/` | `{"Hello": "World"}`; a leftover placeholder, open |

**`GET /items`.** Published documents only, ordered by id, paged by `limit` (1-500, default 500) and
`offset` (>= 0); anything out of range is `422`. `?status=all` (default `published`; any other value is
`422`) adds drafts and needs an editor or admin token: `401` without a token or with a bad one,
`403` for a plain `member`. A new document is a draft, so it appears only under `status=all`. The default path
ignores any token, but Swagger shows a lock on this route because the `status=all` path takes one.

## POST /chat

A question in, a cited answer out. Members only: send `Authorization: Bearer <token>` from
`POST /members/login`.

```bash
curl -X POST localhost:8000/chat -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"question":"how hot should basil be?"}'
```

**Request.** `question`, a string of 1-2000 characters, checked as sent. It is then stripped, and a blank one is `422`.

**Response** (`200`).

| Field | Meaning |
|---|---|
| `answer` | The answer text, citing documents by key (`S1`, ...); a fixed message when declined or abstained |
| `documents` | The cited documents: the stored fields of each retrieved document, verbatim, plus its `key` and, when the question did not fix the crop, `crop_slug` |
| `topics_used` | The topics whose documents were retrieved |
| `topics_used_crops` | Parallel to `topics_used`: each topic's crop; empty when the question fixed the crop |
| `dropped` | Whole topics dropped to fit the context budget (`topic`, `score`, `document_count`, `context_chars`, and `crop_slug` when the crop was not fixed) |
| `abstained` | `null` for an answer, else `{"reason": "out_of_scope"}` (no retrieval) or `{"reason": "no_relevant_topics"}` |
| `truncated` | `true` when the answer was cut at the generator's `max_tokens` and may be incomplete; see below |

**Behaviour.**

- `POST /chat`: classify, select topics, generate a cited answer. Off-topic questions are declined and questions with no relevant topic abstain, both `200` with `abstained` set and no generation call.
- Needs both `ANTHROPIC_API_KEY` (classifier, generator) and `OPENAI_API_KEY` (query embedding); a missing key or a provider error (usage limit, rate limit, outage) from either is a plain `503`, after the budget `429` check (the cause is logged, not returned).
- Models are `CHAT_MODEL_CLASSIFY` and `CHAT_MODEL_GENERATE` (both default to `claude-haiku-5-5`, set in `app/chat/llm.py`).
- The response carries `truncated` (`true` when the answer was cut at the generator's `max_tokens` and may be incomplete; the answer then also ends with a blank line and a fixed notice, but a frontend should read `truncated` rather than string-match the notice; a declined or abstained response is always `false` and carries no notice).
- The SDK's default retries stay on, so a `429` or `5xx` is called up to 3 times, with backoff, before the `503`.
- Answers are not word-for-word repeatable: no sampling parameters are set, and the default model accepts only `temperature` 1 and `top_p` 0.99 ([migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide#remove-sampling-parameters)).
- `anthropic` is in `requirements.txt`: run `docker compose build` so the image has it (the tests in `tests/test_chat.py` that use the real client fail with ImportError otherwise).

**Status codes.** `200` (answered, declined or abstained); `401` without a valid token; `413` when
the topic set exceeds the context budget (the same refusal body as `GET /retrieval`); `422` for a
bad `question`; `429` with `Retry-After` when the member's daily token budget is spent (see
[Members and the token budget](operations.md#members-and-the-token-budget)); `503` when a provider
is unavailable.
