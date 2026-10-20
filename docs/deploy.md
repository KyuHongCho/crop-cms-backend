# Deploying to the server

## What this document covers and its status

Steps 1-18, from a fresh server to a running stack behind a Let's Encrypt staging certificate with the database filled, were run once on 2026-10-19 on the real server, and every step gave its expected result. The steps from 19 on, in "Switching to the production certificate", are unproven until they are run: where a result is a prediction the text says so. The rest (the first admin and the demo member, redeploying) is still an outline.

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
- `no-draft` fails in step 18: either the unpublished draft is public (a data-exposure defect) or `/api/items` gave no 200 body to inspect; do not go on.
- Before step 19, a gate of "Switching to the production certificate" does not hold: do not change `ACME_CA`.
- Production certificate starts: at most 2 in all, one planned (step 20) and one repair. Any failed production start means `docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env stop caddy` at once, then stop and ask; after a second failure, re-plan with the owner instead of trying a third time.
- The Caddy log after step 20 says `too many certificates`: stop Caddy as above; the limit cannot be reset early (see the table in "Switching to the production certificate").
- The strict run of step 21 still reports a STAGING issuer after the wait the step names: do not restart Caddy again and again; see the table.
- From a network outside `ssh_cidr`, port 22, 5432, 8000 or 8081 answers (step 23), or `ss` shows a listener on 5432, 8000 or 8081 (step 24): stop and read the security group.

Never run `down -v` or delete the `cms_caddy_data` volume (the certificates are in it), never recreate the certificate "to try again" after a production certificate was obtained, never run a compose command without `--env-file /opt/cms/.env`, never use `docker compose run` (the image's entrypoint is the web server, so the command that follows becomes its arguments), never run `alembic downgrade` except the rollback in "Database migration and seed", and never print the rendered configuration (`config` without `-q` shows every secret).

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

Measured on the real server (first run): the image build took 1m13s of wall time. During it the lowest `available` figure was 433 MiB, swap use was at most 47 MiB, the out-of-memory count in `dmesg` was 0 and CPU steal (`st`) was at most 4. The root disk `/` is 8.7 GB: 3.8 GB used before the build, 6.8 GB (78 percent) after it, and 6.9 GB (79 percent) with the stack up. After the build `docker system df` showed one image of 1.836 GB and a build cache of 1.837 GB (15 entries), of which only 1.278 MB is reclaimable. The idle stack uses about 430 MiB, with 475 MiB available; `cms` uses 102 MiB and `caddy` 13 MiB. Around the database steps `available` was 514 MiB (swap used 91 MiB) before the seed and 517 MiB (swap used 98 MiB) after the reindex. Redeploys have not been measured yet, so how many the disk holds is not known; the 85 percent stop rule leaves about 0.6 GB above the figure after the build.

## Database migration and seed

The tables, the seed data and the embeddings are created on the new database once, from inside the `cms` container. Step 12 (in [Checks from outside the network](#checks-from-outside-the-network)) must have passed first. Steps 13-17 run on the server from `/opt/cms/app`; step 18 runs on the laptop.

**The gate, before step 13 (the owner reads, nothing is typed on the server).** The only paid call here is the embedding in step 17: about 62 short documents, a fraction of a cent at OpenAI's listed price (not re-read here). Open the billing or limits page of both provider consoles (OpenAI and Anthropic; the second is for the chat call later). Each must show a monthly spend limit. The owner records both amounts. If either limit is missing or unreadable, stop (see the stop rules); an amount noted earlier is not a reading.

**Step 13, the app has the OpenAI key.** This prints `True` or `False` and never the key. `False` means the key line in `/opt/cms/.env` is empty: stop, fill it in, repeat step 7 (it recreates `cms` with the new environment), then repeat this step.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -c "import os;print(bool(os.environ.get('OPENAI_API_KEY')))"
```

**Step 14, create the tables.** The first command exits 0. Its output has INFO lines about the migration context and then one `Running upgrade` line for each of the nine migrations; the first run printed exactly nine and exit code 0. The second prints a revision id followed by `(head)`; on the first run it was `c5e8a2f41d70 (head)`, and a later release prints its own id. The migrations do not create the `vector` extension; it comes from [the database](../infra/README.md#the-database) set-up.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms alembic upgrade head
echo $?
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms alembic current
```

**Step 15, load the seed data.** First read the memory: `scripts.seed` and `scripts.reindex` each start a second Python process beside the app. Record the `available` figure (514 MiB, with 91 MiB of swap used, on the first run). If the seed prints `Killed`, the process was an out-of-memory kill: stop, read `sudo dmesg | grep -i 'out of memory'` and ask. The seed prints nothing and exits 0, and running it again is safe (it updates what exists and adds what is missing). It includes one unpublished draft on purpose; step 18 checks that it is not public.

```sh
free -m
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.seed
echo $?
```

**Step 16, count before the paid call.** The dry run reads the database and embeds nothing; it needs no key and costs nothing. It prints two lines: `N documents, M chunks; K to embed with text-embedding-3-small, 0 stale to delete`, then `dry run: nothing embedded, nothing written`. On a fresh database K equals M, and about 62 is expected; the first run printed `62 documents, 62 chunks; 62 to embed with text-embedding-3-small, 0 stale to delete`. Up to 100: go on. Above 100: stop and ask. `0 documents` means step 15 did not fill the table: stop.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex --dry-run
```

**Step 17, embed.** This is the paid OpenAI call, and the owner runs it. It prints the same count line, then `embedded K chunks, deleted 0 stale chunks` (on the first run `embedded 62 chunks, deleted 0 stale chunks`). The second command must then report `0 to embed` (on the first run `62 documents, 62 chunks; 0 to embed ...`), which shows every chunk is stored with a matching hash and model; if it does not, stop and ask. If the embedding prints `Killed`, it was an out-of-memory kill: stop, read `sudo dmesg | grep -i 'out of memory'` and ask. Read the memory again and record `available` (517 MiB, with 98 MiB of swap used, on the first run). An authentication error can quote part of the key, so read it before pasting it anywhere.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.reindex --dry-run
free -m
```

**Step 18, the site shows the data.** On the laptop, from the repository root, on a network that is not the server's, run all groups against the staging certificate (see step 12 for the script and its options):

```sh
bash prod/outside-check.sh --staging demo.cropcms.com
```

Every row passes: the ten rows are the seven of step 12 plus the three data rows below, and the summary is `10 passed, 0 failed, 0 skipped` then `ALL PASS`. The first run printed the three data rows below, and the summary line above. Any `FAIL` other than `no-draft` (which is a stop rule): stop and share the rows.

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

## Switching to the production certificate

Steps 19-27 run after steps 1-18. They change `ACME_CA` from the staging address to the production one, so that Caddy asks Let's Encrypt for a certificate that browsers trust, then check the result from outside, check the open ports, and survive a reboot. Steps 19, 20 and 24-27 are typed on the server (from `/opt/cms/app`, except the edit of step 19), steps 21 and 23 on the laptop from the repository root, step 22 on the phone; steps 25 and 27 end with a run of the script on the laptop. Keep the phone hotspot on for steps 21-23: the laptop must be on a network that is not the server's and is outside `ssh_cidr`.

The limits that shape these steps, from [Let's Encrypt](https://letsencrypt.org/docs/rate-limits/) as read again on 2026-10-19: 5 certificates per 7 days for one exact set of names (so for `demo.cropcms.com`) and 50 per 7 days for one registered domain, neither resettable; separately, 5 authorization failures per name per account per hour, one of them refilled every 12 minutes. A failed attempt uses the hourly failure allowance; only an issued certificate uses one of the weekly 5. A production start is each time Caddy runs with the production line active while no production certificate is stored; there are at most two, the planned one in step 20 and one repair.

**The gates, before step 19 (the owner confirms each; if one fails, stop).**

- Steps 1-18 passed, including the `10 passed, 0 failed, 0 skipped` of step 18.
- Run the gate block of step 5 again: its first, second and fifth commands must still print the fixed address.
- The answers to the two questions of step 5 still hold. Count every production certificate for exactly `demo.cropcms.com` requested in the last 7 days against the 5; if unsure, wait.
- The owner accepts at most 2 production starts, as the stop rules say.

**Step 19, on the server: make production the only active authority.** In the editor, put `#` in front of the staging line and remove the `#` (and the space after it, if there is one) in front of the production line. The first command below prints `1` (compose takes the last `ACME_CA` line, so two active lines are a mistake); the second prints the one line, `ACME_CA=https://acme-v02.api.letsencrypt.org/directory`; the third prints `0` (no active line mentions `staging`); the fifth prints nothing, and the `0` printed by the last is its exit code. If any of this is different, fix the file before step 20.

```sh
grep -c '^ACME_CA=' /opt/cms/.env
grep '^ACME_CA=' /opt/cms/.env
grep -c '^ACME_CA=.*staging' /opt/cms/.env
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env config -q
echo $?
```

**Step 20, on the server: start Caddy with the production authority.** This is the planned production start. The environment of `caddy` changed, so `up` recreates only `caddy`, and `--no-deps` keeps `cms` as it is; the certificate volume is kept. The second command prints the production address, with no `staging` in it.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env up -d --no-deps --wait caddy
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T caddy sh -c 'echo $ACME_CA'
```

Wait about a minute, then read the certificate lines with the same masking as step 9. The recreated container has a fresh log, so the staging lines are gone. Do not restart Caddy while you wait.

```sh
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs caddy | grep -iE 'certificate|acme|error' | sed -E 's/"(client_ip|remote_ip|remote_addr)":"[^"]*"/"\1":"<ip>"/g; s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/<ip>/g'
```

Expect, as on the staging run, the line `certificate obtained successfully`; its issuer field should name `acme-v02.api.letsencrypt.org-directory` and not contain `staging`. The production wording and the issuer field are predictions from the staging run, unconfirmed until the first run. Caddy creates a new account for the new authority, so the harmless "creating new account" line may appear again. If the log shows an error about a challenge or about too many certificates, go to the stop rules and stop Caddy at once. If after two minutes the log shows neither a success nor an error, run the command once more and go on to step 21, whose issuer row settles it.

**Step 21, on the laptop (hotspot): the certificate seen from outside.** The strict run, with all four groups, must end `ALL PASS`; check the exit code with `echo $?` (it prints `0`). The issuer name after `CN=` and the expiry date are public and unconfirmed until the run.

```sh
bash prod/outside-check.sh demo.cropcms.com
echo $?
```

```text
outside-check demo.cropcms.com (strict)
PASS issuer        issuer: C=US, O=Let's Encrypt, CN=...; notAfter: ...
PASS trust         curl accepted the certificate without -k
PASS http-redirect 308 to https://demo.cropcms.com/api/health
PASS health        200 and status ok
PASS blocked       6 paths answer 404
PASS slash         307 to https://demo.cropcms.com/api/items
PASS items         200 and a non-empty JSON list
PASS no-draft      draft marker absent
PASS retrieval     200 and 3 documents
PASS closed        5432 8000 8081 refuse or time out
10 passed, 0 failed, 0 skipped
ALL PASS
```

Then run it once more in staging mode. The certificate is no longer a staging one, so exactly two rows must fail, `issuer` and `trust`, and the exit code is 1. This shows the check can tell the two apart (the stub tests of the script show this failure pattern; the real run is unconfirmed).

```sh
bash prod/outside-check.sh --staging demo.cropcms.com
echo $?
```

```text
FAIL issuer        expected a STAGING issuer; issuer: ...
FAIL trust         expected curl exit 60 (untrusted), got 0
8 passed, 2 failed, 0 skipped
FAILED
```

If the strict run still reports a STAGING issuer, see the table below before doing anything on the server. If the laptop's resolver holds an old answer, use `--ip` as in step 12. Both tables hold only public facts, so they are safe to share.

**Step 22, on the phone, on mobile data (Wi-Fi off).** Open `https://demo.cropcms.com/api/health` in the browser. Expect a padlock, no warning page, and a short JSON answer with `status` `ok`.

**Step 23, on the laptop (hotspot): which ports answer.** The check is of the owner's own server only, on a short fixed list, with no timing options. The address stays in a shell variable and is never printed; each `nc` prints nothing, and the loop prints a port and an exit code (`0` means the port answered, `1` means it refused or timed out). Sign in again as in step 1 if the session has expired. On macOS `-G 5` bounds each connect to 5 seconds; on Linux write `-w 5`. Report only which ports answered.

```sh
export AWS_PROFILE=cropcms
ip="$(cd infra && terraform output -raw elastic_ip)"
for p in 22 5432 8000 8081 80 443; do nc -z -G 5 "$ip" "$p" >/dev/null 2>&1; echo "$p $?"; done
```

Expect `22 1`, `5432 1`, `8000 1` and `8081 1`, then `80 0` and `443 0`. Port 22 is limited to `ssh_cidr` in the security group, and the laptop's usual network is inside it, so there port 22 answers: that is why this step uses the hotspot, whose address is outside `ssh_cidr` (compare it with the file by eye, as in step 1). Ports 80 and 443 answering show that this network lets connections out, so a port that does not answer means something. UDP 443 (HTTP/3) is not scanned from outside, and `nmap` is neither installed nor needed.

**Step 24, on the server: ground truth on the host.** The first command lists the listening sockets and masks every address except `0.0.0.0`, `*`, `[::]` and the `127.x` ones (replace by hand any other address that remains before you paste); the second prints `0`.

```sh
sudo ss -ltnup | sed -E '/ (0\.0\.0\.0|127\.[0-9.]+)(%[A-Za-z0-9]+)?:[0-9]+ /!s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/<ip>/g; s/\[[0-9a-fA-F:]*[0-9a-fA-F]\]/[<ip>]/g'
sudo ss -ltn | grep -cE ':(5432|8000|8081) '
```

Expect listeners on TCP 80 and 443 and UDP 443 (held by Docker's proxy process or by Caddy), on TCP 22 (held by `sshd`, or by `systemd` when SSH is socket-activated), and the system resolver on a loopback address. A DHCP client or time-sync socket may also show. These process names and extras are predictions for this Ubuntu version, unconfirmed until the run; what must hold is that nothing listens on 8000, 8081 or 5432.

**Step 25, on the server: the recreate check.** Recreating `cms` must not disturb Caddy or its certificate. Note the `STATUS` of `caddy` (for example `Up 20 minutes (healthy)`), recreate only `cms`, wait until it is healthy, and read the status again: the uptime of `caddy` must have kept counting, not restarted. Requests get a 502 for a few seconds while `cms` is replaced.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps caddy
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env up -d --force-recreate --no-deps --wait cms
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps
```

Then on the laptop run the certificate and edge groups, strict. Expect six `PASS` rows (`issuer`, `trust`, `http-redirect`, `health`, `blocked`, `slash`), `6 passed, 0 failed, 0 skipped`, `ALL PASS`.

```sh
bash prod/outside-check.sh demo.cropcms.com --only tls,edge
```

**Step 26, on the server: a placeholder page.** Caddy serves `/opt/cms/site` at `/` (mounted read-only at `/srv`, with `/index.html` as the fallback), and the directory is empty. Put one line in it, then open `https://demo.cropcms.com/` in a browser (or run the `curl` below on the laptop; it prints `200`).

```sh
echo 'Crop CMS API. Health check at /api/health.' > /opt/cms/site/index.html
```

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://demo.cropcms.com/
```

The `200` and the absence of any restart are predictions from the Caddyfile (`root * /srv`, then `try_files {path} /index.html` and `file_server`) and from the directory being a bind mount, unconfirmed until the run. The same fallback answers 200 with this page for any unknown path outside `/api`, so look at `/` only. The real front end replaces this file later.

**Step 27, the reboot check, last.** Run it only when steps 20-26 passed. Reboot the server and wait about two minutes; `ssh` drops at once, and the same `ssh` line as in step 2 (from `infra/` on the laptop, `AWS_PROFILE` exported) logs in again.

```sh
sudo reboot
```

Then, on the server, without starting anything by hand: `uptime -p` shows a few minutes (the reboot happened); `systemctl is-enabled docker` prints `enabled`; `swapon --show` lists `/swapfile` of 1G (the `/etc/fstab` entry comes from the server's first-boot script); both services show `healthy` in `ps` (allow up to a minute, as `cms` has a start period of 30 seconds); the `grep -ciE` line prints `0`, since the certificate is in the volume and the reboot must not ask for a new one. Record `available` from `free -m`.

```sh
uptime -p
systemctl is-enabled docker
swapon --show
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs --since 10m caddy | grep -ciE 'obtaining|obtained'
free -m
```

That both services come back `healthy` on their own relies on `restart: unless-stopped` together with Docker being enabled at boot; the figures and the log wording are unconfirmed until the run. Finish on the laptop with the strict run of step 21 (all groups): it must still end `ALL PASS`.

### If a production-certificate step fails

| Situation | Seen as | Repair |
|---|---|---|
| Typo in the `ACME_CA` URL | issuance errors in the log; the strict run fails | Stop Caddy as in the stop rules, fix the line, then `up -d --no-deps --wait caddy` as in step 20. This is the repair start. The attempt may count as a failure. |
| The staging certificate is still served after the switch | the strict `issuer` row says STAGING, and the log has no `certificate obtained successfully` | Wait two more minutes and read the log again. If Caddy still keeps the staging certificate, use the volume steps below (the repair start). |
| Production issuance fails (DNS, port 80, wrong name) | a challenge failure in the log | Stop Caddy at once so it stops retrying. Each failed authorization uses the hourly failure allowance (5 per name per hour, one refilled every 12 minutes); wait 12 minutes for every failure the log shows, fix the cause, then the repair start. It is not a weekly slot. |
| `too many certificates` for the exact set of names | log text with a retry time | Stop Caddy. The weekly limit refills one certificate per 34 hours and has no override. Put the staging line back and run `up -d --no-deps --wait caddy`, so the API answers (untrusted) meanwhile. `acme_ca` makes Let's Encrypt the only authority (see the `ACME_CA` row of [the variable table](operations.md#environment-variables)), and changing the set of names to get around the limit is deliberately not done. Re-plan the timing. |
| 22 (from a network outside `ssh_cidr`), 5432, 8000 or 8081 answers | step 23 or 24 | Stop. Read the security group in the console (read-only) and the `ss` listing again. |
| The server does not come back cleanly | a service is down, no swap, or SSH refused | Look at the instance status checks in the console and run `sudo journalctl -u docker`, then `up -d` from `/opt/cms/app` with the two flags. The data is on the database and in the volumes, so nothing is lost. |

**The staging certificate persists.** Only while no production certificate exists, and as the repair start. Stop Caddy and copy the volume out first (the copy holds the certificate's private key: keep it on the server, never paste it; the first `docker run` downloads the small `busybox` image):

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env stop caddy
docker run --rm -v cms_caddy_data:/data -v /opt/cms:/out busybox tar czf /out/caddy_data.tgz /data
sudo chmod 600 /opt/cms/caddy_data.tgz
docker run --rm -v cms_caddy_data:/data busybox ls /data/caddy/certificates
```

The listing shows one directory per authority. Remove only the one whose name contains `staging` (the staging log line named it `acme-staging-v02.api.letsencrypt.org-directory`; use the exact name the listing shows), then start Caddy and go back to the log check of step 20:

```sh
docker run --rm -v cms_caddy_data:/data busybox rm -r /data/caddy/certificates/acme-staging-v02.api.letsencrypt.org-directory
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env up -d --no-deps --wait caddy
```

Whether Caddy really keeps a staging certificate in storage after the switch is unconfirmed: the per-authority directory layout is expected from Caddy's behaviour but was not seen. Never delete the volume, never run `down -v`, and once a production certificate has been obtained never remove anything from the volume or recreate the certificate "to try again".

## The first admin and the demo member

The first admin and the shared demo member are created after the app is up; invites, roles and the starting budget are in [members and the token budget](operations.md#members-and-the-token-budget).

## Redeploying

A later release has to be deployed without losing the certificates kept in the `cms_caddy_data` volume.

## If something fails

Server and database problems are listed in the [infra README](../infra/README.md#if-something-fails); app problems have no list yet beyond the stop rules above.
