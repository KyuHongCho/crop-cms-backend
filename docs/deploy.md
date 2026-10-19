# Deploying to the server

## What this document covers and its status

Steps 1-12, from a fresh server to a running stack behind a Let's Encrypt staging certificate, were run once on 2026-10-19 on the real server, and every step gave its expected result. Steps 13-18, in "Database migration and seed", have not been run, so their commands and expected results are unproven until a run confirms them. The rest (the production certificate, the first admin and the demo member, redeploying) is still an outline.

The commands are shown in fenced blocks, and `bash prod/check.sh` lints them: a `docker compose` command must carry `-f docker-compose.prod.yaml` and `--env-file /opt/cms/.env` (only `docker compose version` is exempt); there is no `compose run`, no `down -v`, no `--force-recreate` without `--no-deps` and no `config` without `-q`; `read` never gets a `-p` flag; and every fence closes. A block marked `text` is output and is not linted. Commands for the laptop work in zsh and bash; commands for the server are written for bash.

### Stop rules

Stop and ask rather than guess when any of these holds:

- The resolvers disagree about the domain (step 5): do not start Caddy.
- The answer to question (a) in step 5 is yes, or the answer to (b) is a number near 50: the production certificate cannot be requested on time; re-plan the timing first.
- `/opt/cms/.env` fails a check in step 4, above all if `ACME_CA` is not the single active staging line: the template's default is production, and a forgotten edit starts production.
- `sudo ufw status` says the firewall is active (step 2): ports 80 and 443 may be blocked on the host itself.
- The build is killed or `dmesg` shows out-of-memory lines (step 6): wait if CPU credits are the cause and retry once; a second failure is a decision for the owner, not a third try.
- `df -h /` shows more than 85 percent used: `docker builder prune -f`, then `docker image prune -f` (dangling images only).
- The readiness request in step 10 is not 200: the database is not reachable, so do not go on; check `DB_HOST`, that `DB_PASSWORD` is hex or alphanumeric only, the security group and `PGSSLMODE`.
- Either provider console (OpenAI, Anthropic) shows no monthly spend limit, or the owner cannot read one (the gate before step 13): do not start the database steps.
- The dry run in step 16 reports more than 100 chunks to embed: do not embed; the corpus is not the one expected (about 62).
- `no-draft` fails in step 18: the unpublished draft is public, a data-exposure defect; do not go on.

Never run `down -v` or delete the `cms_caddy_data` volume (the certificates are in it), never run a compose command without `--env-file /opt/cms/.env`, never use `docker compose run` (the image's entrypoint is the web server, so the command that follows becomes its arguments), never run `alembic downgrade` except the rollback in "Database migration and seed", and never print the rendered configuration (`config` without `-q` shows every secret).

Do not paste into a chat or a ticket: the contents of `/opt/cms/.env`, `docker compose config` output, `docker inspect`, `printenv` or `ps e` output, API keys, passwords, invite codes, tokens, `Authorization` headers, or unfiltered `docker compose logs` (they hold client addresses). Safe to share: exit codes, `free -m`, `df -h /`, `docker compose ps`, `grep`-filtered certificate and error lines, HTTP status lines.

## Before you start

The server and the database must exist ([infra README](../infra/README.md#steps-in-order)), the app role must be created ([the database](../infra/README.md#the-database)), and a DNS record must point the domain at the server's fixed address; this repo does not create the record (step 5).

**Step 1, on the laptop.** Sign in to AWS and read the server's fixed address. Do not paste the address anywhere; compare your current public address with `ssh_cidr` in `infra/terraform.tfvars` by eye (inside it: yes or no). If it is outside, change `ssh_cidr` first ([if your IP changes](../infra/README.md#if-your-ip-changes)).

```sh
aws login --profile cropcms
export AWS_PROFILE=cropcms
cd infra
terraform output -raw elastic_ip; echo
curl https://checkip.amazonaws.com
```

**Step 2, log in and look around (read-only).** Write down the Compose version (the infra README records the one from an earlier server; read this server's own), the memory and swap figures, how full `/` is, and whether `cloud-init` says `done`. The two `/opt/cms` directories should belong to `ubuntu`. The host firewall was inactive on the first run (`Status: inactive`); if `ufw` says `active`, see the stop rules. The last command prints the server's own public address: it should equal the fixed address from step 1 (answer yes or no, do not paste it).

Run the `ssh` line in the terminal of step 1: still in `infra/`, with `AWS_PROFILE` exported, because it reads the address from Terraform. The key file is the private key made in step 5 of the infra README; its name is your choice, so use yours in place of `cropcms_ed25519_v2`.

```sh
ssh -i ~/.ssh/cropcms_ed25519_v2 ubuntu@"$(terraform output -raw elastic_ip)"
```

```sh
docker --version
docker compose version
free -m
swapon --show
df -h /
cloud-init status
ls -ld /opt/cms /opt/cms/site
sudo ufw status
curl -s https://checkip.amazonaws.com
```

**Step 3, on the server.** Clone the repo and note the commit.

```sh
git clone https://github.com/KyuHongCho/crop-cms-backend /opt/cms/app
git -C /opt/cms/app log --oneline -1
```

## The secret env file at /opt/cms/.env

It is created on the server from [`.env.prod.example`](../.env.prod.example), and every compose command is pointed at it with `--env-file /opt/cms/.env`, because compose does not read it by itself; each variable is described in [the variable table](operations.md#environment-variables).

**Step 4, on the server.** Copy the template with mode 600, then fill it in with an editor of your choice (for example `nano /opt/cms/.env`):

```sh
install -m 600 /opt/cms/app/.env.prod.example /opt/cms/.env
openssl rand -hex 32
```

Fill in `DB_HOST` (the output of `terraform output -raw db_endpoint` in `infra/`, run on the laptop), `DB_PASSWORD` (the app role's password, from your password manager; hex or alphanumeric only), `SECRET_KEY` (the `openssl rand -hex 32` output above, shown once), both API keys, and `SITE_ADDRESS=demo.cropcms.com`. Then make the staging line the only active `ACME_CA`: put `#` in front of the production line and remove the `# ` (hash and space) in front of the staging line.

Checks, all on the server. The first prints `600 ubuntu`; the second prints `1`; the third prints one line that contains `staging`; the fourth prints nothing; the fifth prints nothing, and the `0` printed by the last is its exit code.

```sh
stat -c '%a %U' /opt/cms/.env
grep -c '^ACME_CA=' /opt/cms/.env
grep '^ACME_CA=' /opt/cms/.env
grep -E '^(DB_HOST|DB_PASSWORD|SECRET_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|SITE_ADDRESS)=$' /opt/cms/.env
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env config -q
echo $?
```

## Choosing the certificate authority

The `ACME_CA` line of the env file picks the authority: the Let's Encrypt staging address for testing, then the production address, which is also the default. The first start uses staging, whose limits are far larger than production's, so a retry costs nothing. A staging certificate is not trusted by browsers or by `curl` without `-k`; that is expected.

**Step 5, on the laptop: the DNS record, then a gate.** At the registrar (Namecheap, Advanced DNS) create one `A` record, host `demo`, value the fixed address, the lowest TTL offered. Create no `AAAA` record and remove any other `demo` record.

Production limits, from [Let's Encrypt](https://letsencrypt.org/docs/rate-limits/) as read on 2026-10-18: 5 certificates per 7 days for one exact set of names, and 50 per 7 days for one registered domain; neither can be reset early. They do not bite on staging, but they decide the later production start, so answer now: (a) was a production certificate for exactly `demo.cropcms.com` requested in the last 7 days? (b) roughly how many production certificates for other names under `cropcms.com` in the last 7 days? See the stop rules for what the answers mean.

The gate: all of the following agree before step 7. The first, second and fifth print the fixed address; the third prints the name servers, and the fourth stores the first of them in `ns` and prints nothing; the sixth prints nothing; the last two print nothing or name Let's Encrypt (a CAA record at the subdomain takes precedence over the one at the parent).

```sh
dig +short demo.cropcms.com @one.one.one.one
dig +short demo.cropcms.com @dns.google
dig +short NS cropcms.com
ns=$(dig +short NS cropcms.com | head -1)
dig +short demo.cropcms.com @"$ns"
dig +short AAAA demo.cropcms.com
dig +short CAA demo.cropcms.com
dig +short CAA cropcms.com
```

## First start

The first start builds the image and brings up `cms` and `caddy` from `docker-compose.prod.yaml`. Every command here runs on the server from `/opt/cms/app`.

**Step 6, build alone in the background** so a dropped SSH session cannot kill it. The `time` line at the end of the log is the wall time. Watch memory and CPU in two more SSH sessions, one command each (both run until Ctrl-C, which does not touch the build); in the AWS console (read-only) note the instance's `CPUCreditBalance` before and after.

```sh
cd /opt/cms/app
nohup bash -c 'time docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env build' > /opt/cms/build.log 2>&1 &
tail -f /opt/cms/build.log
```

Ctrl-C ends the `tail`, not the build.

```sh
vmstat 5
```

```sh
free -m -s 5
```

Afterwards record the wall time, the lowest `available` figure, the swap used, the `st` column of `vmstat`, and the credit balance before and after. Then run these; the first count is expected to be 0.

```sh
sudo dmesg | grep -ci 'out of memory'
df -h /
docker system df
```

**Step 7, start and wait.** `--wait` proves the processes are healthy, not that a certificate was issued; both services should show `healthy`.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env up -d --wait
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps
```

**Step 8, which authority Caddy uses.** The container holds only `SITE_ADDRESS` and `ACME_CA`, so nothing secret prints. Expect the staging address.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T caddy sh -c 'echo $ACME_CA'
```

**Step 9, the certificate lines.** Read only the filtered lines and share only those. Once the site is live the log holds client addresses, so the `sed` masks IPv4 addresses and the address fields; IPv6 addresses are not masked, so look before you share.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs caddy | grep -iE 'certificate|acme|error' | sed -E 's/"(client_ip|remote_ip|remote_addr)":"[^"]*"/"\1":"<ip>"/g; s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/<ip>/g'
```

Expect the line `certificate obtained successfully` with `"issuer":"acme-staging-v02.api.letsencrypt.org-directory"`. Before it, Caddy registers a new ACME account and runs an `http-01` challenge, which the certificate authority's validation servers check with about 5 requests, each answered with `status 200`. The match on `error` also hits one harmless info line on the first start, "creating new account ... open /data/caddy/acme/.../default.json: no such file or directory".

**Step 10, readiness from inside.** The proxy blocks `/health/ready` from outside, so ask the app directly. The first command prints `200` and is the first proof that the app role reaches the database over TLS; the second exits 0 and, on an empty database, prints no revision.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').status)"
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms alembic current
echo $?
```

**Step 11, what the stack costs in memory.** Record the numbers. The volume list should include `cms_caddy_data`.

```sh
free -m
docker stats --no-stream
docker volume ls
```

To undo this stage, stop the stack without removing volumes (there is no valuable data yet), and delete the DNS record if you are abandoning the deploy:

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env down
```

### Memory and disk

The server has 1 GiB of RAM plus 1 GiB of swap and a 10 GB root disk, and its CPU credits are in `standard` mode (`infra/main.tf`), where a drained balance slows everything instead of billing extra.

Measured on the real server (first run): the image build took 1m13s of wall time. During it the lowest `available` figure was 433 MiB, swap use was at most 47 MiB, the out-of-memory count in `dmesg` was 0 and CPU steal (`st`) was at most 4. The root disk `/` is 8.7 GB: 3.8 GB used before the build, 6.8 GB (78 percent) after it, and 6.9 GB (79 percent) with the stack up. After the build `docker system df` showed one image of 1.836 GB and a build cache of 1.837 GB (15 entries), of which only 1.278 MB is reclaimable. The idle stack uses about 430 MiB, with 475 MiB available; `cms` uses 102 MiB and `caddy` 13 MiB. Redeploys have not been measured yet, so how many the disk holds is not known; the 85 percent stop rule leaves about 0.6 GB above the figure after the build.

## Database migration and seed

The tables, the seed data and the embeddings are created on the new database once, from inside the `cms` container. Step 12 (in [Checks from outside the network](#checks-from-outside-the-network)) must have passed first. Steps 13-17 run on the server from `/opt/cms/app`; step 18 runs on the laptop.

**The gate, before step 13 (the owner reads, nothing is typed on the server).** The only paid call here is the embedding in step 17: about 62 short documents, a fraction of a cent at OpenAI's listed price (not re-read here). Open the billing or limits page of both provider consoles (OpenAI and Anthropic; the second is for the chat call later). Each must show a monthly spend limit. The owner records both amounts. If either limit is missing or unreadable, stop (see the stop rules); an amount noted earlier is not a reading.

**Step 13, the app has the OpenAI key.** This prints `True` or `False` and never the key. `False` means the key line in `/opt/cms/.env` is empty: stop, fill it in, repeat step 7 (it recreates `cms` with the new environment), then repeat this step.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -c "import os;print(bool(os.environ.get('OPENAI_API_KEY')))"
```

**Step 14, create the tables.** The first command exits 0. Its output has INFO lines about the migration context and then one `Running upgrade` line for each of the nine migrations (the exact wording is unconfirmed until the run). The second prints a revision id followed by `(head)`. The migrations do not create the `vector` extension; it comes from [the database](../infra/README.md#the-database) set-up.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms alembic upgrade head
echo $?
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms alembic current
```

**Step 15, load the seed data.** First read the memory: `scripts.seed` and `scripts.reindex` each start a second Python process beside the app. Record the `available` figure. If the seed prints `Killed`, the process was an out-of-memory kill: stop, read `sudo dmesg | grep -i 'out of memory'` and ask. The seed prints nothing and exits 0, and running it again is safe (it updates what exists and adds what is missing). It includes one unpublished draft on purpose; step 18 checks that it is not public.

```sh
free -m
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.seed
echo $?
```

**Step 16, count before the paid call.** The dry run reads the database and embeds nothing; it needs no key and costs nothing. It prints two lines: `N documents, M chunks; K to embed with text-embedding-3-small, 0 stale to delete`, then `dry run: nothing embedded, nothing written`. On a fresh database K equals M, and about 62 is expected. Up to 100: go on. Above 100: stop and ask. `0 documents` means step 15 did not fill the table: stop.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex --dry-run
```

**Step 17, embed.** This is the paid OpenAI call, and the owner runs it. It prints the same count line, then `embedded K chunks, deleted 0 stale chunks`. The second command must then report `0 to embed`, which shows every chunk is stored with a matching hash and model; if it does not, stop and ask. If the embedding prints `Killed`, it was an out-of-memory kill: stop, read `sudo dmesg | grep -i 'out of memory'` and ask. Read the memory again and record `available`; its value is unconfirmed until the run. An authentication error can quote part of the key, so read it before pasting it anywhere.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex --dry-run
free -m
```

**Step 18, the site shows the data.** On the laptop, from the repository root, on a network that is not the server's, run all groups against the staging certificate (see step 12 for the script and its options):

```sh
bash prod/outside-check.sh --staging demo.cropcms.com
```

Every row passes: the ten rows are the seven of step 12 plus the three data rows below, and the summary is `10 passed, 0 failed, 0 skipped` then `ALL PASS`. The wording of the data rows comes from the script and is unconfirmed until the run. Any `FAIL` other than `no-draft` (which is a stop rule): stop and share the rows.

```text
PASS items         200 and a non-empty JSON list
PASS no-draft      draft marker absent
PASS retrieval     200 and 3 documents
```

The `retrieval` row reads the published documents of one topic from the database; it does not touch the embeddings. The proof of the embeddings is the `0 to embed` of step 17.

### If a database step fails

- The dry run in step 16 is the brake before the paid call; nothing is billed before step 17.
- If `alembic upgrade head` fails, share only the last error line and the exit code (the full output may name the database host). An error that names `vector` points at the database set-up; one about a connection points at step 10.
- If step 17 stops midway, run its first command again: a chunk whose stored hash and model match is skipped, and each document is written in one transaction.
- `no-draft` failing is a stop rule: do not go on.
- To roll the migrations back, use the line below. It drops every table, the seed data and the embeddings with them, and it is allowed only before any member exists (no member exists until "The first admin and the demo member"). `ALEMBIC_ALLOW_DESTRUCTIVE=1` is needed because the database is not named `*_test`. Afterwards repeat from step 14.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T -e ALEMBIC_ALLOW_DESTRUCTIVE=1 cms alembic downgrade base
```

Once a member exists, never run it: undoing the migrations would delete the members. The recovery then is a restore of the database from its automated backup, which no step here covers yet.

## Checks from outside the network

The public address is checked from a machine outside the server's network, and only ports 80 and 443 should answer.

**Step 12, on the laptop, from the repository root, on a network that is not the server's** (a phone hotspot works). `prod/outside-check.sh` sends only GET requests, logs in nowhere, makes no paid call and needs only `curl`, `openssl` and `nc`. It runs every selected check, prints one row each (`PASS`, `FAIL`, or `SKIP` with its reason), ends with `ALL PASS` or `FAILED`, and exits 1 if any row failed and 2 on a usage error. The domain and the flags may come in any order. The groups are `tls` (certificate issuer, trust, the HTTP redirect, `/api/health`), `edge` (blocked paths, the trailing-slash redirect), `data` (the item list, the unpublished draft, one retrieval) and `ports` (5432, 8000 and 8081 must not answer). Until the database is filled, leave `data` out with `--only`. Always include `tls` in a strict run: without it, an untrusted certificate shows as plain `FAIL` rows (a status of `000` or a `curl exit 60`) instead of one `trust` failure with the rest skipped. A `closed` PASS means only that nothing answered from this network, not that the ports are shut: a mistyped domain or a network that blocks outbound ports passes too, so the check on the server itself (`ss -ltnup`) stays the proof.

The first run is strict, as the production certificate will be checked. The server still has the staging certificate, so it must fail on `issuer` and `trust` and nowhere else, and the rows that would need `-k` are skipped:

```sh
bash prod/outside-check.sh demo.cropcms.com --only tls
```

```text
outside-check demo.cropcms.com (strict)
FAIL issuer        STAGING certificate on a strict run; issuer: C=US, O=Let's Encrypt, CN=(STAGING) Artificial Amaranth YE1; notAfter: ...
FAIL trust         curl exit 60 without -k
PASS http-redirect 308 to https://demo.cropcms.com/api/health
SKIP health        skipped: trust failed, so this request would need -k
1 passed, 2 failed, 1 skipped
FAILED
```

The second run expects the staging certificate, so every row passes and the exit code is 0:

```sh
bash prod/outside-check.sh --staging --only tls,edge,ports demo.cropcms.com
```

| Row | Result | What it shows |
|---|---|---|
| `issuer` | PASS | the certificate's issuer name contains `STAGING` |
| `trust` | PASS | `curl` without `-k` fails with exit 60, because the staging certificate is not trusted |
| `http-redirect` | PASS | port 80 redirects to `https://demo.cropcms.com` |
| `health` | PASS | `/api/health` is 200 with `status` `ok` |
| `blocked` | PASS | the readiness, docs, redoc and openapi paths answer 404 |
| `slash` | PASS | `/api/items/` redirects with 307 to `https://demo.cropcms.com/api/items` |
| `closed` | PASS | 5432, 8000 and 8081 refuse or time out within 5 seconds |

The output of that run:

```text
outside-check demo.cropcms.com (staging)
PASS issuer        issuer: C=US, O=Let's Encrypt, CN=(STAGING) Artificial Amaranth YE1; notAfter: ...
PASS trust         curl exit 60: the staging certificate is untrusted, as expected
PASS http-redirect 308 to https://demo.cropcms.com/api/health
PASS health        200 and status ok
PASS blocked       6 paths answer 404
PASS slash         307 to https://demo.cropcms.com/api/items
PASS closed        5432 8000 8081 refuse or time out
7 passed, 0 failed, 0 skipped
ALL PASS
```

Stop if the first run fails anywhere but `issuer` and `trust`, or the second run shows any `FAIL`. If the laptop's resolver still holds an old answer for the domain, add `--ip "$(cd infra && terraform output -raw elastic_ip)"` to point both `curl` and `openssl` at the server directly. The table holds only public facts (issuer, expiry, status codes), so it is safe to share.

## The first admin and the demo member

The first admin and the shared demo member are created after the app is up; invites, roles and the starting budget are in [members and the token budget](operations.md#members-and-the-token-budget).

## Redeploying

A later release has to be deployed without losing the certificates kept in the `cms_caddy_data` volume.

## If something fails

Server and database problems are listed in the [infra README](../infra/README.md#if-something-fails); app problems have no list yet beyond the stop rules above.
