# Deploying to the server

## What this document covers and its status

Steps 1-18, from a fresh server to a running stack behind a Let's Encrypt staging certificate with the database filled, ran on 2026-10-19 on the real server. Steps 19-36 ran on 2026-10-20: the switch to the production certificate (19-27), then the first admin, the shared demo member, one real chat question, the published-only count and the log check (28-36). Every step ended as expected, except that the first run of step 21, from a phone hotspot, timed out and then passed from the usual network. Step 37 is a written-down recovery command and was not run. The site is live at https://demo.cropcms.com behind a Let's Encrypt production certificate. [What was verified on the server](#what-was-verified-on-the-server) lists the measurements and what is still unconfirmed. Redeploying is still an outline.

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
- Before step 28, either provider console shows no monthly spend limit, or the owner cannot say, for each limit, whether it is enforced or only shown: do not start step 28. A limit that is only shown and has no decision recorded by the owner: do not send the question of step 34.
- The chat question of step 34 has been asked three times: stop and ask, whatever the answers were.
- A count in step 36 is above 0 (a token in the Caddy log, or member-insert SQL in the `cms` log): stop and ask; do not paste the lines.

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

Expect the line `certificate obtained successfully` with `"issuer":"acme-staging-v02.api.letsencrypt.org-directory"`. Before it, Caddy registers a new ACME account and runs a challenge, which the certificate authority's validation servers check five times. The staging run validated with `http-01` (port 80), where each request was answered with `status 200`; the production run (step 20) validated with `tls-alpn-01` (port 443). Either may be chosen. The match on `error` also hits one harmless info line on the first start, "creating new account ... open /data/caddy/acme/.../default.json: no such file or directory".

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

The server has 1 GiB of RAM plus 1 GiB of swap and a 10 GB root disk, and its CPU credits are in `standard` mode (`infra/main.tf`), where a drained balance slows everything instead of billing extra. The image build and the running stack fit in that memory on the real server; the figures are in [What was verified on the server](#what-was-verified-on-the-server).

**Disk budget.** The root file system is 8.7 GB. After the first deploy it read 6.9 GB used and 1.9 GB free (80 percent), and the 85 percent stop rule stops at about 7.4 GB used, so there is about 0.5 GB of room. How much one rebuild after a code change adds was not measured, so how many redeploys the disk holds is not known and no number is given. At the next redeploy run `df -h /` and `docker system df` before and after the rebuild and write both down. After the first build only 1.278 MB of the 1.837 GB build cache was reclaimable, so `docker builder prune -f` may free little; run the pruning of the stop rules only when `df -h /` is above 85 percent, and record what it freed.

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

**Step 12, on the laptop, from the repository root, on a network that is not the server's** (a phone hotspot is enough, but a slow one can make a request time out, see "If something fails" at the end). `prod/outside-check.sh` sends only GET requests, logs in nowhere, makes no paid call and needs only `curl`, `openssl` and `nc`. It runs every selected check, prints one row each (`PASS`, `FAIL`, or `SKIP` with its reason), ends with `ALL PASS` or `FAILED`, and exits 1 if any row failed and 2 on a usage error. The domain and the flags may come in any order. The groups are `tls` (certificate issuer, trust, the HTTP redirect, `/api/health`), `edge` (blocked paths, the trailing-slash redirect), `data` (the item list, the unpublished draft, one retrieval) and `ports` (5432, 8000 and 8081 must not answer). Until the database is filled, leave `data` out with `--only`. Always include `tls` in a strict run: without it, an untrusted certificate shows as plain `FAIL` rows (a status of `000` or a `curl exit 60`) instead of one `trust` failure with the rest skipped. A `closed` PASS means only that nothing answered from this network, not that the ports are shut: a mistyped domain or a network that blocks outbound ports passes too, so the check on the server itself (`ss -ltnup`) stays the proof.

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

Steps 19-27 run after steps 1-18. They change `ACME_CA` from the staging address to the production one, so that Caddy asks Let's Encrypt for a certificate that browsers trust, then check the result from outside, check the open ports, and survive a reboot. Steps 19, 20 and 24-27 are typed on the server (from `/opt/cms/app`, except the edit of step 19), steps 21 and 23 on the laptop from the repository root, step 22 on the phone; steps 25 and 27 end with a run of the script on the laptop. The strict runs of steps 21, 25 and 27 need only a network that is not the server's; the phone hotspot is needed only for step 23, because port 22 is open from the owner's usual network and the laptop must be outside `ssh_cidr` for that step.

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

Expect, as on the staging run, the line `certificate obtained successfully`. On the real run it carried `"issuer":"acme-v02.api.letsencrypt.org-directory"`, with no `staging` in it. Caddy created a new account for the new authority: the harmless "creating new account" line did appear again. This run validated the name with `tls-alpn-01` (port 443) and the authority checked it five times from its servers; the staging run used `http-01` (port 80), and either may be chosen. The renewal information in the log gave a renewal window that starts about 59 days after the issuance (about 2026-12-18), and the certificate lasts about 90 days (`notAfter` 2027-01-18). Caddy renews by itself; a real renewal has not been seen yet, so that is unconfirmed. If the log shows an error about a challenge or about too many certificates, go to the stop rules and stop Caddy at once. If after two minutes the log shows neither a success nor an error, run the log command once more and go on to step 21, whose issuer row settles it.

**Step 21, on the laptop, on a network that is not the server's: the certificate seen from outside.** The strict run, with all four groups, must end `ALL PASS`; check the exit code with `echo $?` (it prints `0`). The hotspot is not needed here. On a phone hotspot the run failed once with `curl exit 28`: the script gives each request 10 seconds, and the hotspot took 2.6 s to connect and 8.3 s to finish the handshake, so a slow hotspot can time out; run it again from the usual network, where it passed with 10 rows. The issuer and the expiry date are public; the real run printed the issuer row below.

```sh
bash prod/outside-check.sh demo.cropcms.com
echo $?
```

```text
outside-check demo.cropcms.com (strict)
PASS issuer        issuer: C=US, O=Let's Encrypt, CN=YE2; notAfter: Jan 18 01:52:54 2027 GMT
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

Then run it once more in staging mode. The certificate is no longer a staging one, so exactly two rows must fail, `issuer` and `trust`, and the exit code is 1. This shows the check can tell the two apart. The real run gave exactly that: `issuer` and `trust` FAIL, the other eight rows PASS, `8 passed, 2 failed, 0 skipped`, and exit code 1.

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

**Step 22, on the phone, on mobile data (Wi-Fi off).** Open `https://demo.cropcms.com/api/health` in the browser. Expect a padlock, no warning page, and a short JSON answer with `status` `ok`. On the run Safari showed a secure connection and the JSON with `status` `ok`.

**Step 23, on the laptop (hotspot): which ports answer.** The check is of the owner's own server only, on a short fixed list, with no timing options. The address stays in a shell variable and is never printed; each `nc` prints nothing, and the loop prints a port and an exit code (`0` means the port answered, `1` means it refused or timed out). Sign in again as in step 1 if the session has expired. On macOS `-G 5` bounds each connect to 5 seconds; on Linux write `-w 5`. Report only which ports answered.

```sh
export AWS_PROFILE=cropcms
ip="$(cd infra && terraform output -raw elastic_ip)"
for p in 22 5432 8000 8081 80 443; do nc -z -G 5 "$ip" "$p" >/dev/null 2>&1; echo "$p $?"; done
```

Expect `22 1`, `5432 1`, `8000 1` and `8081 1`, then `80 0` and `443 0`; the run printed exactly these six lines. Port 22 is limited to `ssh_cidr` in the security group, and the laptop's usual network is inside it, so there port 22 answers: that is why this step uses the hotspot, whose address is outside `ssh_cidr` (compare it with the file by eye, as in step 1). Ports 80 and 443 answering show that this network lets connections out, so a port that does not answer means something. UDP 443 (HTTP/3) is not scanned from outside, and `nmap` is neither installed nor needed.

**Step 24, on the server: ground truth on the host.** The first command lists the listening sockets and masks every address except `0.0.0.0`, `*`, `[::]` and the `127.x` ones (replace by hand any other address that remains before you paste); the second prints `0`.

```sh
sudo ss -ltnup | sed -E '/ (0\.0\.0\.0|127\.[0-9.]+)(%[A-Za-z0-9]+)?:[0-9]+ /!s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/<ip>/g; s/\[[0-9a-fA-F:]*[0-9a-fA-F]\]/[<ip>]/g'
sudo ss -ltn | grep -cE ':(5432|8000|8081) '
```

Expect listeners on TCP 80 and 443 and UDP 443 (held by Docker's proxy process or by Caddy), on TCP 22 (held by `sshd`, or by `systemd` when SSH is socket-activated), and the system resolver on a loopback address. A DHCP client or time-sync socket may also show. On the run the listeners were `docker-proxy` on 80/tcp, 443/tcp and 443/udp (IPv4 and IPv6), `sshd` and `systemd` on 22, `systemd-resolve` on two loopback addresses, `chronyd`, and `systemd-network` (DHCP, UDP 68); the count of listeners on 5432, 8000 and 8081 was `0`. What must hold is that nothing listens on 8000, 8081 or 5432.

**Step 25, on the server: the recreate check.** Recreating `cms` must not disturb Caddy or its certificate. Note the `STATUS` of `caddy` (for example `Up 20 minutes (healthy)`), recreate only `cms`, wait until it is healthy, and read the status again: the uptime of `caddy` must have kept counting, not restarted. Requests get a 502 for a few seconds while `cms` is replaced.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps caddy
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env up -d --force-recreate --no-deps --wait cms
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps
```

Then on the laptop run the certificate and edge groups, strict. Expect six `PASS` rows (`issuer`, `trust`, `http-redirect`, `health`, `blocked`, `slash`), `6 passed, 0 failed, 0 skipped`, `ALL PASS`. On the run the uptime of `caddy` kept counting (44 minutes, then 54) while `cms` was recreated, and the six rows passed.

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

The `curl` printed `200` on the run, as the Caddyfile predicts (`root * /srv`, then `try_files {path} /index.html` and `file_server`; the directory is a bind mount, so no restart is needed). The same fallback answers 200 with this page for any unknown path outside `/api`, so look at `/` only. The real front end replaces this file later.

**Step 27, the reboot check, last.** Run it only when steps 20-26 passed. Reboot the server and wait about two minutes; `ssh` drops at once, and the same `ssh` line as in step 2 (from `infra/` on the laptop, `AWS_PROFILE` exported) logs in again.

```sh
sudo reboot
```

Then, on the server, without starting anything by hand: `uptime -p` shows a few minutes (the reboot happened); `systemctl is-enabled docker` prints `enabled`; `swapon --show` lists `/swapfile` of 1G (the `/etc/fstab` entry comes from the server's first-boot script); both services show `healthy` in `ps` (allow up to a minute, as `cms` has a start period of 30 seconds); the `grep -ciE` line prints `0`, since the certificate is in the volume and the reboot must not ask for a new one (it can be above 0 only if the command runs within ten minutes of step 20, because `--since 10m` counts back from the moment the command runs). Record `available` from `free -m`.

```sh
uptime -p
systemctl is-enabled docker
swapon --show
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env ps
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs --since 10m caddy | grep -ciE 'obtaining|obtained'
free -m
```

That both services come back `healthy` on their own relies on `restart: unless-stopped` together with Docker being enabled at boot. On the run `uptime -p` said "up 1 minute", Docker was `enabled`, `/swapfile` was 1024M, both services were `healthy` without any command, the obtain/obtained count was `0`, and `free -m` showed 484 MiB available with swap at 0. Finish on the laptop with the strict run of step 21 (all groups): it must still end `ALL PASS`, and on the run it did.

### If a production-certificate step fails

None of these was seen on the real run; the certificate rows come from Let's Encrypt's published limits and Caddy's documented behaviour, and the port and reboot rows are the expected repairs, not seen either. The only failure met in steps 19-27, the hotspot timeout of step 21, is under "If something fails" at the end.

| Situation | Seen as | Repair |
|---|---|---|
| Typo in the `ACME_CA` URL | issuance errors in the log; the strict run fails | Stop Caddy as in the stop rules, fix the line, then `up -d --no-deps --wait caddy` as in step 20. This is the repair start. The attempt may count as a failure. |
| The staging certificate is still served after the switch | the strict `issuer` row says STAGING, and the log has no `certificate obtained successfully` | Not seen: the strict run that passed after step 20 showed the production issuer. Wait two more minutes and read the log again. An offline test with Caddy 2.11.7, made while the production-certificate steps were being checked and not on the real server, showed that Caddy ignores a staging certificate once the authority is production, so the volume steps below are probably never needed (unconfirmed); stop and ask before using them. |
| Production issuance fails (DNS, port 80 or 443, wrong name) | a challenge failure in the log | Stop Caddy at once so it stops retrying. Each failed authorization uses the hourly failure allowance (5 per name per hour, one refilled every 12 minutes); wait 12 minutes for every failure the log shows, fix the cause, then the repair start. It is not a weekly slot. |
| `too many certificates` for the exact set of names | log text with a retry time | Stop Caddy. The exact-set limit (5 per 7 days for exactly these names) refills one certificate per 34 hours and has no override; the registered-domain limit (50 per 7 days) has an override request, per the Let's Encrypt page. Put the staging line back and run `up -d --no-deps --wait caddy`, so the API answers (untrusted) meanwhile. `acme_ca` makes Let's Encrypt the only authority (see the `ACME_CA` row of [the variable table](operations.md#environment-variables)), and changing the set of names to get around the limit is deliberately not done. Re-plan the timing. |
| 22 (from a network outside `ssh_cidr`), 5432, 8000 or 8081 answers | step 23 or 24 | Stop. Read the security group in the console (read-only) and the `ss` listing again. |
| The server does not come back cleanly | a service is down, no swap, or SSH refused | Look at the instance status checks in the console and run `sudo journalctl -u docker`, then `up -d` from `/opt/cms/app` with the two flags. The data is on the database and in the volumes, so nothing is lost. |

**If the staging certificate persists (not seen, probably never needed).** Only while no production certificate exists, and as the repair start. Stop Caddy and copy the volume out first (the copy holds the certificate's private key: keep it on the server, never paste it; the first `docker run` downloads the small `busybox` image):

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

Whether Caddy keeps a staging certificate in storage after the switch is unconfirmed: it did not happen on the real run, and the per-authority directory layout above is expected from Caddy's behaviour but was not seen. Never delete the volume, never run `down -v`, and once a production certificate has been obtained never remove anything from the volume or recreate the certificate "to try again".

## The first admin and the demo member

The first admin and the shared demo member are created after the app is up; invites, roles and the starting budget are in [members and the token budget](operations.md#members-and-the-token-budget). Steps 28-37 create the admin and the demo member and put one question to the chat. Each step says where it is typed: on the server (bash, from `/opt/cms/app`) or on the laptop (zsh or bash, on any network that reaches the site; the hotspot is not needed). The laptop steps run in one terminal, in order, because the shell variables carry from one step to the next.

**How the secrets are handled.** A password or an invite code is read at a silent prompt (`read -rs`); an e-mail address is read at a visible one (`read -r`) and is never written in this document. The JSON body is built by `python3` from exported variables and sent from standard input (`curl --data @-`), and the `Authorization` header is handed to `curl` as a config stream (`-K <(...)`), so no secret is ever an argument of a command: none is in the shell history or on the command line of a process (a running `python3` does hold the exported variables in its environment, which is why `ps e` output is never pasted). A token is held in a shell variable only. A token lives 30 minutes: when a request answers `401`, log in again with the matching login block under "If a step of this section fails". Each laptop command prints only a status code (`000` means no answer at all) or a few non-secret fields, never a response body (a `422` body echoes back the values you sent).

**Do not paste into a chat or a ticket (this section):** invite codes, passwords, tokens, the admin's and the demo member's e-mail addresses, any response body, and the output of `unlock_login` (its error stream names the database host). Safe to share: HTTP status codes, the row count printed in steps 30 and 35, `tokens_budget_daily` and `tokens_used_today`, the time of the chat call, the `abstained` and `truncated` values, the number of documents and their citation keys (`S1`, ...), `free -m` and `docker compose ps`. The text of the chat answer may be shared only if the owner chooses.

**The gate, before step 28 (the owner reads and states; nothing is typed).** The chat call of step 34 is the only paid call of this section: one Anthropic call to classify the question, one to write the answer, and one OpenAI call to embed the question. The provider limits were last stated before the database steps, so state them again now. Both provider consoles (OpenAI and Anthropic) must show a monthly spend limit, and for each the owner says whether the limit is enforced (the provider refuses calls at the limit) or only shown (it informs, and the spending goes on). A limit that is shown but not enforced is a decision the owner records, with the reason, before step 34; it is never assumed to hold. A figure or a statement from an earlier day is not a reading. If a limit is missing or unreadable, or the owner cannot say whether it is enforced, stop (see the stop rules).

**Step 28, on the server: the invite for the admin.** `make_invite` prints the code once and stores only its hash, so copy it from the terminal (select it with the mouse) and paste it only at the silent prompt of step 29. It is one line of 43 characters (letters, digits, `-` and `_`), valid for one day and good for one signup. An invite makes a `member`, never an admin: step 30 promotes.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.make_invite --days 1
```

**Step 29, on the laptop: sign up the admin.** Use your own address and a password from your password manager (8 to 128 characters). The command prints the status code only: expect `201`. The address is stored in lower case.

```sh
printf 'Invite code: '; read -rs INVITE_CODE; echo
printf 'Admin e-mail: '; read -r MEMBER_EMAIL
printf 'Admin password: '; read -rs MEMBER_PASSWORD; echo
export INVITE_CODE MEMBER_EMAIL MEMBER_PASSWORD
python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"],"invite_code":os.environ["INVITE_CODE"]}))' | curl -s -o /dev/null -w '%{http_code}\n' -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/signup
unset INVITE_CODE
```

**Step 30, on the server: make that member the admin.** The API never grants the admin role, so one `UPDATE` does it, from inside the running `cms` container. The address is typed at the prompt and reaches the container through standard input; the same letter case as in step 29 is not needed, because the command lower-cases it as signup did. It prints the number of rows changed: expect `1`. `0` means no member has that address (a typo, or step 29 did not print `201`): run this step again.

```sh
cd /opt/cms/app
printf 'Admin e-mail: '; read -r ADMIN_EMAIL
printf '%s\n' "$ADMIN_EMAIL" | docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -c 'import sys; from sqlalchemy import text; from app.auth.invite import normalise_email; from app.db.migrate_db import engine; c = engine.connect(); r = c.execute(text("UPDATE members SET role = :r WHERE email = :e"), {"r": "admin", "e": normalise_email(sys.stdin.readline())}); c.commit(); print(r.rowcount)'
```

**Step 31, on the laptop: log in as the admin and make the invite for the demo member.** The first command keeps the token in `ADMIN_TOKEN` and prints only its length: a number above 100. The second asks for a one-day invite for a plain `member` and keeps the code in `DEMO_INVITE`; it prints the length, `43`. A length of `0` means the request failed: see the table below.

```sh
ADMIN_TOKEN=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"]}))' | curl -s -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/login | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))')
printf '%s\n' "${#ADMIN_TOKEN}"
DEMO_INVITE=$(curl -s -K <(printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN") -H 'content-type: application/json' --data '{"role":"member","expires_in_days":1}' https://demo.cropcms.com/api/members/invites | python3 -c 'import json,sys;print(json.load(sys.stdin).get("code",""))')
printf '%s\n' "${#DEMO_INVITE}"
```

**Step 32, on the laptop: sign up the shared demo member.** The address is a role-style one on your own domain; it needs no mailbox. The password comes from your password manager, is never written in the repo, the chat or a command line, and is shared by you alone with the people you let try the demo. Expect `201`.

```sh
printf 'Demo e-mail: '; read -r MEMBER_EMAIL
printf 'Demo password: '; read -rs MEMBER_PASSWORD; echo
INVITE_CODE=$DEMO_INVITE
export INVITE_CODE MEMBER_EMAIL MEMBER_PASSWORD
python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"],"invite_code":os.environ["INVITE_CODE"]}))' | curl -s -o /dev/null -w '%{http_code}\n' -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/signup
unset INVITE_CODE DEMO_INVITE
```

**Step 33, on the laptop: log in as the demo member and read the budget.** The second command prints `tokens_budget_daily` and `tokens_used_today` and nothing else: expect `100000 0`. The last line clears the e-mail and password; to log in again later, use the demo member's login block under "If a step of this section fails". The server stamps the budget from `TOKENS_BUDGET_DAILY` when the member signs up; `20000 0` means `cms` was started before that line was in `/opt/cms/.env` (see the table).

```sh
DEMO_TOKEN=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"]}))' | curl -s -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/login | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))')
printf '%s\n' "${#DEMO_TOKEN}"
curl -s -K <(printf 'header = "Authorization: Bearer %s"\n' "$DEMO_TOKEN") https://demo.cropcms.com/api/members/me | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("tokens_budget_daily"),d.get("tokens_used_today"))'
unset MEMBER_EMAIL MEMBER_PASSWORD
```

**Step 34, one chat question (the paid call; the owner sends it).** The decision about a limit that is only shown must already be recorded (the gate). On the server, read the memory before the call:

```sh
free -m
```

Then, on the laptop, put exactly one question as the demo member. `curl` prints the status and the time of the call (`time_total`, in seconds) and writes the answer to a private temporary file; the `python3` line prints the non-secret fields, then the answer text for you to read. Expect `status 200`, `abstained` `null`, `truncated` `false`, a non-empty `documents` list and keys such as `S1`. Caddy waits up to 120 seconds for the answer, and the command gives up after 150. On the real run the status was 200 after 18.149 s, with `abstained` `null`, `truncated` `false` and 7 documents (`S1` to `S7`).

```sh
CHAT_OUT=$(mktemp)
curl -s --max-time 150 -o "$CHAT_OUT" -w 'status %{http_code}, time_total %{time_total} s\n' -K <(printf 'header = "Authorization: Bearer %s"\n' "$DEMO_TOKEN") -H 'content-type: application/json' --data '{"question":"how hot should basil be?"}' https://demo.cropcms.com/api/chat
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps({"abstained":d.get("abstained"),"truncated":d.get("truncated"),"documents":len(d.get("documents",[])),"keys":[x["key"] for x in d.get("documents",[])]}));print(d.get("answer") or d.get("detail"))' "$CHAT_OUT"
rm -f "$CHAT_OUT"
```

Then read the budget again: `tokens_used_today` must be above 0, and `tokens_budget_daily` is still `100000`. A second question is allowed only if the first looks odd or slow, and three in all is the most. Then, on the server, read `free -m` again and record both `available` figures.

```sh
curl -s -K <(printf 'header = "Authorization: Bearer %s"\n' "$DEMO_TOKEN") https://demo.cropcms.com/api/members/me | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("tokens_budget_daily"),d.get("tokens_used_today"))'
```

```sh
free -m
```

**Step 35, the published-only count.** On the server this prints the number of published documents, and on the laptop the number of documents the public list returns; the two numbers must be equal (61 on a freshly seeded database: the seed has 62 documents, and the unpublished draft is in neither number). Equal numbers show that the list holds published documents only; on the real server both printed 61.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -c 'from sqlalchemy import text; from app.db.migrate_db import engine; c = engine.connect(); print(c.execute(text("SELECT count(*) FROM items WHERE published")).scalar())'
```

```sh
curl -s https://demo.cropcms.com/api/items | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))'
```

**Step 36, log hygiene, then clear the secrets.** On the server, both commands print a count and no log line: the first counts requests in the Caddy log that carry a token after the word `Bearer` (a `401` answer shows the bare word `Bearer` in its `WWW-Authenticate` header, which does not match), the second counts SQL text of a member insert in the `cms` log. Both must print `0`. Caddy writes the `Authorization` header of a request as `REDACTED` in its request log (seen in a local test of this Caddyfile); on the real server both counts were 0.

```sh
cd /opt/cms/app
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs caddy | grep -c 'Bearer [A-Za-z0-9_-]'
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env logs cms | grep -ci 'insert into members'
unset ADMIN_EMAIL
```

Then, on the laptop, remove every secret from the shell, and close the terminal:

```sh
unset ADMIN_TOKEN DEMO_TOKEN DEMO_INVITE INVITE_CODE MEMBER_EMAIL MEMBER_PASSWORD CHAT_OUT
```

**Step 37, on the server: the unlock command, written down.** Anyone who knows the demo member's address can lock the account with ten wrong logins within 900 seconds (the answer is then `429`, even for the right password); that is accepted, and this is the way out ([the lock and its recovery](operations.md#login-throttling)). Do not run it now. It prints `unlocked` or `nothing to unlock`, and an error line that names the database host (do not paste it); an admin's `POST /api/members/{id}/unlock` does the same. The address is an argument of this command (the script reads no standard input), so it shows in a process listing while the command runs; the shared demo address is not a secret.

```sh
cd /opt/cms/app
printf 'Locked e-mail: '; read -r LOCKED_EMAIL
docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env exec -T cms python -m scripts.unlock_login "$LOCKED_EMAIL"
unset LOCKED_EMAIL
```

### What the chat costs and what it needs in memory

One question is one paid call to each provider. The classifier answers at most 256 tokens and the generator at most 1024 (`app/chat/llm.py`), and the measured median is 2771 tokens per question on the previous model (`docs/live-eval.md`; the current default has not been measured here, but the first real question counted 4021 tokens, so about 24 such questions fit in the 100000 a day). At the listed Haiku 5.5 price of $0.50 per million output tokens, which was read on 2026-10-12 and is unverified since, one question costs about $0.002 even if every token were billed at the output rate; the embedding of the question adds a few hundred tokens at $0.02 per million. The demo member's cap of 100000 tokens a day is therefore at most about $0.05 a day (concurrent requests can overshoot the cap), and the provider limits of the gate are the second bound. The cost per question is not confirmed: the month's total on the provider's usage page was accounted for by the earlier live-evaluation runs, and the first real question had not appeared on the page yet, so its cost stays an unverified estimate.

Each login hashes the password with Argon2, about 64 MiB, and at most four hashes run at once, so a burst of logins stays near 325 MiB; that is a figure from a laptop and unconfirmed on the server. The steps hash four passwords, one after the other (two signups, two logins). On the real server `available` was 519 MiB before step 34 and 392 MiB after it (484 MiB after the reboot of step 27).

### If a step of this section fails

| Situation | Seen as | Repair |
|---|---|---|
| Signup `400` | step 29 or 32 prints `400` | The invite is unknown, used or expired, or the address is already registered. Make a new invite (step 28, or step 31 for the demo member); if the address is registered, the member exists, so go on. |
| Signup `422` | `422` | A field is refused: the password is under 8 characters, or the code is under 20. Never paste the body. |
| Step 30 prints `0` | no row changed | Wrong address, or step 29 failed: repeat step 30 with the address of step 29. |
| Admin login prints a length of `0` | step 31 | Wrong address or password (one `401` answer covers both), a lock (`429`), or an app error (an empty `SECRET_KEY` in `/opt/cms/.env` gives `500`). Each wrong login counts toward the lock, so retry once at most: repeat the first two lines of step 31, because `MEMBER_EMAIL` and `MEMBER_PASSWORD` are still set after step 29. If they were cleared or the terminal was replaced, use the admin's login block below the table. When the whole of step 31 was pasted as one block, the failed login also left `DEMO_INVITE` empty: once the admin token works, run the last two lines of step 31 again. If the retry also fails, stop and ask: the password cannot be changed through the API, and the last admin cannot be deleted. |
| Admin token length above 100, invite length `0` | step 31 | The login worked but the invite request failed: the member is not an admin (the route answers `403`; check that step 30 printed `1`, and repeat it if not), or the body was refused (`422`) or the app failed (`500`). Repeat the last two lines of step 31 after the cause is fixed. |
| Demo login prints a length of `0` | step 33 | Wrong address or password (one `401` answer covers both), a deleted member, a lock (`429`), or an app error (an empty `SECRET_KEY` in `/opt/cms/.env` gives `500`). Each wrong login counts toward the lock, so retry once at most, with the demo member's login block below the table, because step 33 cleared the variables. If the retry also fails, the demo password may have been mistyped at step 32: use the row "Wrong role, or a wrong demo account" below (delete the member as admin, then sign up again with a new invite). |
| Login or any call answers `429` | status `429` | The account is locked (ten wrong logins in 900 seconds), or the chat budget is spent. For the lock use step 37; the chat budget resets when the day changes in the database. |
| Token calls answer `401` | status `401` | The token is older than 30 minutes: log in again with the matching block below the table (the demo member's for `DEMO_TOKEN`, the admin's for `ADMIN_TOKEN`). |
| Step 32 failed after it cleared `DEMO_INVITE` | the signup printed anything but `201` | Make a new demo invite first: run the last two lines of step 31 (they need `ADMIN_TOKEN`; if it is unset or older than 30 minutes, run the admin's login block below the table first), then repeat step 32. |
| `tokens_budget_daily` is `20000` | step 33 | Log in as the admin and raise this member's budget to 100000 (the blocks below). For members who sign up later, first run the `up -d --no-deps --wait` command of step 20 with `cms` in place of `caddy`, so the new value is read. |
| Chat `503` | `status 503` | A provider key is missing or invalid, or a provider limit is reached; the cause is in the `cms` log, not in the answer. Do not retry in a loop. Step 13 shows whether the OpenAI key is there. |
| Chat `200` with `abstained` set | `abstained` is not `null` | The question matched no topic: not an outage. Check that step 17 still reports `0 to embed` before a second question. |
| Chat `413` | `status 413` | Not expected for this corpus; stop and ask. |
| Wrong role, or a wrong demo account | the wrong member shows in the list | Delete it as admin (the block below), which is audited, then make a new invite. There is no route that changes a password: to rotate the demo password, delete the member and sign up again with a new invite. |

To log in as the demo member again (after step 33 cleared the variables, or when the token is older than 30 minutes), type the address and password again. The block keeps only the token in `DEMO_TOKEN`, clears the e-mail and password, and prints the token's length (a number above 100; `0` means the login failed).

```sh
printf 'Demo e-mail: '; read -r MEMBER_EMAIL
printf 'Demo password: '; read -rs MEMBER_PASSWORD; echo
export MEMBER_EMAIL MEMBER_PASSWORD
DEMO_TOKEN=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"]}))' | curl -s -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/login | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))')
printf '%s\n' "${#DEMO_TOKEN}"
unset MEMBER_EMAIL MEMBER_PASSWORD
```

To find a member's number, raise a budget or delete a member, log in as the admin again; the first block does that and lists the members. The list shows each member's number, role, whether the member is active, and the address (on your screen only). An admin cannot delete themselves, and the last admin cannot be deleted.

```sh
printf 'Admin e-mail: '; read -r MEMBER_EMAIL
printf 'Admin password: '; read -rs MEMBER_PASSWORD; echo
export MEMBER_EMAIL MEMBER_PASSWORD
ADMIN_TOKEN=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["MEMBER_EMAIL"],"password":os.environ["MEMBER_PASSWORD"]}))' | curl -s -H 'content-type: application/json' --data @- https://demo.cropcms.com/api/members/login | python3 -c 'import json,sys;print(json.load(sys.stdin).get("access_token",""))')
curl -s -K <(printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN") https://demo.cropcms.com/api/members | python3 -c 'import json,sys;[print(m["id"],m["role"],m["is_active"],m["email"]) for m in json.load(sys.stdin)]'
```

To raise a budget to 100000, type the number at the prompt (the answer is `200`):

```sh
printf 'Member number: '; read -r MEMBER_ID
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH -K <(printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN") -H 'content-type: application/json' --data '{"tokens_budget_daily":100000}' https://demo.cropcms.com/api/members/"$MEMBER_ID"
```

To delete a wrong member (the answer is `204`), type its number at the prompt:

```sh
printf 'Member number: '; read -r MEMBER_ID
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE -K <(printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN") https://demo.cropcms.com/api/members/"$MEMBER_ID"
```

When done, clear the shell again:

```sh
unset ADMIN_TOKEN MEMBER_EMAIL MEMBER_PASSWORD MEMBER_ID
```

## What was verified on the server

Measured on the real server (1 GiB of RAM, 904 MiB usable, and 1023 MiB of swap) while running the steps of this document, on 2026-10-19 (steps 1-18) and 2026-10-20 (steps 19-36).

- **Software.** Docker 29.9.1 and Docker Compose v5.6.0.
- **Image build.** 1m13s on the 1 GiB host. The lowest `available` figure was 433 MiB, swap use at most 47 MiB, CPU steal (`st`) at most 4, and the out-of-memory count in `dmesg` 0. The CPU credit balance was 288 before the build and still 288 about a day later (the cap for this size is believed to be 288, unconfirmed).
- **Disk.** The root file system is 8.7 GB. Used: 3.8 GB before the build, 6.8 GB (78 percent) after it, 6.9 GB (79 percent) with the stack up after the first start, and 6.9 GB used with 1.9 GB free (80 percent) 18 hours after the reboot. `docker system df` after the build: images 1.836 GB, build cache 1.837 GB (15 entries) with 1.278 MB reclaimable. Later: 2 images 1.925 GB, 2 containers 1.7 MB, 2 volumes 14.49 kB, build cache 1.837 GB (1.278 MB reclaimable). The budget drawn from this is under [Memory and disk](#memory-and-disk).
- **Memory.** Idle stack right after the start: about 428 MiB used and 475 MiB available, `cms` 102.5 MiB and `caddy` 13 MiB. At the database steps `available` was 514 MiB (swap 91 MiB) before the seed and 517 MiB (swap 98 MiB) after the reindex. After the reboot: 484 MiB available and swap 0. Around the first chat question `available` was 519 MiB before and 392 MiB after (swap 79 to 82 MiB). 18 hours after the reboot (uptime 18 h 8 min): 502 MiB used, 401 MiB available, swap 87 MiB, and in `docker stats` `cms` 191.5 MiB and `caddy` 32.7 MiB with CPU at 0.15 and 0.00 percent.
- **Database.** The migrations ran up to `c5e8a2f41d70` (nine). The 62 documents and 62 chunks were embedded in one call, and the second dry run reported 0 to embed. The published count was 61 and `/api/items` returned 61 documents (one unpublished draft).
- **Certificates.** The staging issuer was `C=US, O=Let's Encrypt, CN=(STAGING) Artificial Amaranth YE1` (challenge `http-01`). The production issuer is `C=US, O=Let's Encrypt, CN=YE2`, issued on 2026-10-20 (challenge `tls-alpn-01`), `notAfter` Jan 18 01:52:54 2027 GMT. Port 80 redirects with status 308. The renewal window starts about 2026-12-18 (from the renewal information in the log); a real renewal has not been seen.
- **From outside.** The strict run of `prod/outside-check.sh` passed all 10 rows (`ALL PASS`) from the usual network. On a phone hotspot the first strict run failed with `curl exit 28`: the script gives each request 10 s, and the hotspot took 2.6 s to connect and 8.3 s to finish the TLS handshake. With `--staging` against the production certificate exactly `issuer` and `trust` fail. Ports 5432, 8000, 8081 and 22 (from a network outside `ssh_cidr`) are closed, and 80 and 443 are open. On the host, `ss` lists `docker-proxy` on 80/tcp, 443/tcp and 443/udp (IPv4 and IPv6), `sshd` and `systemd` on 22, `systemd-resolve`, `chronyd` and `systemd-network`.
- **Recreate and reboot.** Recreating `cms` left the uptime of `caddy` counting (44 to 54 minutes). After the reboot Docker was enabled, `/swapfile` was 1024M, both services were healthy without any command, and no new certificate was requested.
- **The first chat question.** Status 200 in `time_total` 18.149 s, `abstained` null, `truncated` false, 7 documents (`S1` to `S7`). `tokens_used_today` was 4021 afterwards against a budget of 100000, so about 24 such questions a day fit on the shared account. Both provider spend limits were confirmed to be enforced before the paid call.
- **Log hygiene.** Both counts of step 36 were 0.

Not confirmed:

- A real certificate renewal (expected from Caddy's behaviour and the renewal window above).
- The memory burst of several Argon2 hashes on the server (the 325 MiB figure is from a laptop).
- The cost of a chat question. The estimate in [the section on chat costs](#what-the-chat-costs-and-what-it-needs-in-memory) comes from listed prices that are unverified since 2026-10-12. The month's total on the provider's usage page was accounted for by the earlier live-evaluation runs, and the first real question had not appeared on the page yet, so its real cost is not read from the provider and the cost per question is not confirmed.
- The CPU credit cap of 288 for this size.
- How much one rebuild adds to the disk (see the budget under Memory and disk).

## Redeploying

A later release has to be deployed without losing the certificates kept in the `cms_caddy_data` volume. Measure `df -h /` and `docker system df` before and after the rebuild, as in the disk budget under [Memory and disk](#memory-and-disk).

## If something fails

Server and database problems are listed in the [infra README](../infra/README.md#if-something-fails). For the app, the stop rules above, the tables under "If a database step fails", "If a production-certificate step fails" and "If a step of this section fails" cover each stage.

The one failure the real runs met: a strict run of `prod/outside-check.sh` from a phone hotspot failed with `curl exit 28` (a timeout). The script gives each request 10 seconds, and a slow hotspot can take longer to finish the TLS handshake. Run it again from the usual network; no change on the server is needed.
