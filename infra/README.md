# infra: the server on AWS

This folder builds one small server (EC2) in the AWS region eu-north-1 (Stockholm) with Terraform.
It has Docker installed, a fixed public IP address, and a firewall that lets the whole internet reach
ports 80 and 443 only. SSH (port 22) is open to your own IP address and nobody else. It also
creates a PostgreSQL database (RDS) that only that server can reach; see "The database".

This README was written before anything was applied to a real AWS account. A first real run on
2026-10-14 confirmed part of it; see "What is verified" and "What is not verified" at the end, and
trust what a real run shows over this text.

## What you need first

1. An AWS account, and a way for the command line to sign in to it. There are two routes; step 4
   says how to use each.
   - **Route A, browser sign-in.** This is what the first real run used: an AWS account made with
     "Sign up for AWS (new)" (a project account), entered with the role `AccountFullAccessRole`. You
     need no IAM user and no access key. AWS applies a service control policy to project accounts;
     its statement `DenyIAMRestrictedActions` denies `iam:*LoginProfile*`, `billing:*` is denied,
     and `budgets:*` is allowed (see
     https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html). If you
     use this route, you can skip the "IAM rights" lists.
   - **Route B, IAM user and access key.** For a classic account where route A does not apply. Turn
     on MFA for the root login, then create one IAM user for Terraform, turn on MFA for it, give it
     rights (see "IAM rights" below) and create an access key for that user.
2. A default VPC in eu-north-1. A new account has one, but it can be deleted. Check in the console:
   VPC, then Your VPCs, and look for one with Default VPC = yes. Without it, `plan` fails with "no
   matching EC2 VPC found".
3. Install the tools: [Terraform](https://developer.hashicorp.com/terraform/install) 1.11 or newer
   and the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
4. Sign in from the command line, using the route from item 1.
   - **Route A.** Run `aws login --profile <name>` (for example `cropcms`) and finish the sign-in
     in the browser. Then run `export AWS_PROFILE=<name>` in every terminal window that runs `aws`
     or `terraform`; a new window does not remember it. From AWS's docs (not from the real run;
     https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html): `aws login` needs
     AWS CLI 2.32.0 or newer (`aws --version`), and it gives short-lived credentials that refresh
     for up to 12 hours, after which you run `aws login --profile <name>` again. `aws login` may
     offer to set up "AWS skills and the AWS MCP server" for AI agents. That prompt appeared once,
     right after `aws login` finished in the first real run; it is not needed here, so answer `n`
     (or `never`; only `n` was considered here, and it declines this time). The same docs say the
     `SignInLocalDevelopmentAccess` managed policy is required and point IAM Identity Center users
     to `aws configure sso`. The real run on this account succeeded with `aws login` anyway,
     without checking either; `aws configure sso` itself was not tried.
   - **Route B.** Run `aws configure` and paste the access key. It is stored in
     `~/.aws/credentials`, outside this repository. Never put it in a file here or paste it into a
     chat.

   Then check that it works: `aws sts get-caller-identity` should print your account id and who you
   are signed in as. If it prints an error, fix the sign-in before going on.
5. Make an SSH key (use a passphrase): `ssh-keygen -t ed25519`. The file name is your choice; the
   examples here use the default, `id_ed25519`, so if you pick another name, use it in every later
   command. You get a private file `~/.ssh/id_ed25519`, which never leaves your laptop, and a public
   file `~/.ssh/id_ed25519.pub`, which is safe to share.

### IAM rights

This section is for route B. The lists below were derived from the AWS provider's source code and
AWS documentation. They were not run against IAM, so expect to iterate: if a step fails with
`AccessDenied` or `UnauthorizedOperation`, the message names the missing action; add it and run the
step again.

**Easiest:** attach the AWS managed policy `PowerUserAccess` to the Terraform IAM user. It allows
everything except IAM, Organizations and Account management, which is enough in principle. One
catch: for IAM users to use Budgets, the root user may first have to turn on "IAM access to
Billing" (Account page in the console).

**Least privilege, if you prefer.** Each group is a separate statement:

- EC2 read, `Resource: "*"`: DescribeVpcs, DescribeVpcAttribute, DescribeRouteTables,
  DescribeImages, DescribeKeyPairs, DescribeSecurityGroups, DescribeSecurityGroupRules,
  DescribeInstances, DescribeInstanceAttribute, DescribeInstanceCreditSpecifications,
  DescribeInstanceTypes, DescribeVolumes, DescribeAddresses, DescribeAddressesAttribute,
  DescribeTags, and DescribeSubnets (the database's subnet group reads the default subnets).
  (DescribeRouteTables is only logged as a warning if denied; the others fail.)
- EC2 write: ImportKeyPair, DeleteKeyPair, CreateSecurityGroup, DeleteSecurityGroup,
  AuthorizeSecurityGroupIngress, AuthorizeSecurityGroupEgress, RevokeSecurityGroupIngress,
  RevokeSecurityGroupEgress, ModifySecurityGroupRules, RunInstances, TerminateInstances,
  ModifyInstanceAttribute, ModifyInstanceMetadataOptions, ModifyInstanceCreditSpecification,
  StopInstances, StartInstances, AllocateAddress, AssociateAddress, DisassociateAddress,
  ReleaseAddress, CreateTags, DeleteTags. `RunInstances` is checked against each resource it uses
  (image, subnet, security group, volume, key pair, network interface, instance), so give it
  `Resource: "*"` or list all of those.
- Not needed to create, destroy or replace the server, but needed if you later change the root
  volume size or type in place: ModifyVolume and DescribeVolumesModifications. Also optional:
  UpdateSecurityGroupRuleDescriptionsIngress and Egress, and DescribeNetworkInterfaces.
- RDS (the database): CreateDBInstance, ModifyDBInstance, DeleteDBInstance, DescribeDBInstances,
  CreateDBSubnetGroup, ModifyDBSubnetGroup, DeleteDBSubnetGroup, DescribeDBSubnetGroups,
  CreateDBParameterGroup, ModifyDBParameterGroup, DeleteDBParameterGroup, DescribeDBParameterGroups,
  DescribeDBParameters, AddTagsToResource, RemoveTagsFromResource, ListTagsForResource,
  CreateDBSnapshot and DescribeDBSnapshots (the final snapshot), and DescribeDBEngineVersions. RDS
  may also need `iam:CreateServiceLinkedRole` for `rds.amazonaws.com` the first time.
- Secrets Manager and KMS, for the master password RDS manages: `kms:DescribeKey`,
  `secretsmanager:CreateSecret` and `secretsmanager:TagResource` (AWS lists these three for creating
  such a database). Probably also `secretsmanager:DescribeSecret` and `secretsmanager:DeleteSecret`
  for later changes and destroy, and `secretsmanager:GetSecretValue` to read the password; those
  three are guesses.
- Budgets: `budgets:ViewBudget`, `budgets:ModifyBudget`, `budgets:TagResource`,
  `budgets:UntagResource`, `budgets:ListTagsForResource`. The tag actions are needed because the
  provider tags every resource; without them `apply` fails on the budget.
- Terraform state: `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on
  `arn:aws:s3:::cropcms-tfstate-<your-account-id>/cropcms/terraform.tfstate*` (this also covers the
  lock object `terraform.tfstate.tflock`), and `s3:ListBucket` on the bucket itself.
- Bootstrap script, on `cropcms-tfstate-*`: `s3:CreateBucket`, `s3:ListBucket`,
  `s3:PutBucketVersioning`, `s3:PutBucketPublicAccessBlock` and `s3:PutEncryptionConfiguration`.
  `aws sts get-caller-identity` needs no permission.

## Steps, in order

Steps 1 and 2 run from the repository root. Step 3 moves you into `infra/`; from there on, run
everything from the `infra/` folder.

1. **Bootstrap the state bucket (once).** Terraform keeps a record of what it built, called state.
   It lives in an S3 bucket that Terraform cannot create for itself, so a script does it:

   ```
   bash infra/bootstrap-state.sh --dry-run   # only prints the commands
   bash infra/bootstrap-state.sh             # does it; safe to run again
   ```

   The last lines print the bucket name, `cropcms-tfstate-<your-account-id>`.

2. **Fill in your settings.**

   ```
   cp infra/terraform.tfvars.example infra/terraform.tfvars
   curl https://checkip.amazonaws.com        # your public IP; write it as <ip>/32
   cat ~/.ssh/id_ed25519.pub                 # paste the whole line; use your own key file name
   ```

   Edit `infra/terraform.tfvars` (also set your e-mail address for the budget alert). Git ignores
   that file, so your IP stays off GitHub. Terraform refuses anything that is not a single IP
   ending in `/32`, and refuses a private key.

3. **Initialise.** Use the bucket name from step 1:

   ```
   cd infra
   terraform init -backend-config="bucket=cropcms-tfstate-<your-account-id>"
   ```

   Repeat this command on any new clone of the repository or any other computer; the bucket name
   is not stored in the files.

4. **Plan.** `terraform plan -out=tfplan` shows what would be created and changes nothing. Read it;
   see "What the plan should show" below. The saved plan file `tfplan` stays in `infra/`; Git
   ignores it.
   If `apply` later reports that the saved plan is stale or no longer matches (Terraform refuses a
   saved plan once the state or your settings have changed), run `terraform plan -out=tfplan` again
   and then `terraform apply tfplan`.

5. **Apply.** `terraform apply tfplan` applies exactly the plan you read, and does not ask for
   `yes`. It prints the IP, the instance id, an ssh command and the database's address and name. The
   database takes several minutes to become available. Then follow "The database" for the one-time
   setup of the app's database role.

6. **Log in.**

   ```
   terraform output -raw ssh_command         # prints a line with PATH_TO_YOUR_PRIVATE_KEY
                                             # in it; replace that with your private key file
                                             # (for example ~/.ssh/id_ed25519), then run it
   cloud-init status --wait                  # on the server: waits for the first-boot setup
   docker --version
   docker compose version
   docker run --rm hello-world
   swapon --show                             # lists /swapfile
   ls -ld /opt/cms /opt/cms/site             # owned by ubuntu
   ```

   The first time, ssh asks whether to trust the server's host key. Type `yes`: ssh then remembers
   it and warns you if it ever changes, but this first answer is taken on trust. The commands after
   the first one run on the server. If you log in before the setup ends, log out and in again so
   the `docker` group applies.

7. **Check from outside.** From a different network than the one in `ssh_cidr` (a phone hotspot
   works), run `nc -zv -G 5 <elastic-ip> 22` on macOS (`nc -zv -w 5 <elastic-ip> 22` on Linux). It
   should time out. Without the time limit, macOS waits about 75 seconds before giving up. In the
   AWS console, look at the security group `cropcms-web` (or run
   `aws ec2 describe-security-groups --region eu-north-1`): only 22 from your `/32`, plus 80/tcp,
   443/tcp and 443/udp. The state file should be in the bucket as `cropcms/terraform.tfstate`;
   while a `plan` or `apply` runs, a `cropcms/terraform.tfstate.tflock` object appears next to it.
   Under Budgets, `cropcms-monthly` should be listed.

8. **Destroy** when you are done: `terraform destroy`. This deletes the server and its disk, so
   `/opt/cms/.env`, the Caddy certificates and the site files are gone, and the Elastic IP is
   released: a new one gets a new address, so the DNS record must be pointed at it again. It also
   deletes the database, but only after you turn deletion protection off (see "The database");
   RDS then takes a final snapshot first. With protection still on, a plain destroy removes the
   server and the IP and then fails on the database. The state bucket stays (it costs almost nothing); delete
   it by hand in the console if you want it gone.

Being in the `docker` group on the server is the same as being root there, so keep the SSH key
private and protected by its passphrase.

## The database

`terraform apply` creates a PostgreSQL 17 database on RDS: `db.t4g.micro`, 20 GB gp3, encrypted,
one zone, not publicly accessible. Its own firewall group, `cropcms-db`, has one inbound rule,
5432/tcp from the group `cropcms-web`, and no outbound rule. A parameter group requires TLS
(`rds.force_ssl`); the app also sets `PGSSLMODE=require`, which encrypts but does not check the
server certificate. RDS creates the administrator role `cms_master` and keeps its password in AWS
Secrets Manager. The password is in no file here, and Terraform's state and saved plan hold only
the address of that entry (output `db_master_secret_arn`); that is what AWS's and HashiCorp's
documentation say, and no real state was inspected. RDS rotates the password about every seven
days by default (AWS documentation), so read it fresh each time, never copy it down. The entry
costs about 0.40 USD a month. The app never uses this role: it uses `cms_app`, below.

**Before the first apply (read-only).** Check that the region offers what the configuration asks
for. If either command prints nothing, stop.

```
aws rds describe-db-engine-versions --engine postgres --region eu-north-1 \
  --query "DBEngineVersions[?starts_with(EngineVersion,'17')].EngineVersion" --output text
aws rds describe-orderable-db-instance-options --engine postgres --engine-version <17.x> \
  --db-instance-class db.t4g.micro --region eu-north-1 \
  --query "OrderableDBInstanceOptions[].[DBInstanceClass,StorageType,MultiAZCapable]" --output text
```

The first should list 17.x minors. For `<17.x>` use the highest one listed (compare the numbers
after the dot: 17.11 is higher than 17.9). AWS creates its default minor for `engine_version =
"17"` (17.9 on the real apply, while 17.11 was the highest listed), so this checks the region, not
that exact minor. The second must list `gp3`.

**Settings.** `db_backup_retention_days` (default 1, allowed 1 to 35): days of automated backups.
`db_deletion_protection` (default true). `db_final_snapshot_name` (default `cropcms-final`).
On one real apply, AWS's Free plan refused 7 days and then 3 (`FreeTierRestrictionError`); it did
not state the maximum, accepted 1, and 2 was not tried. A Paid plan may allow more (untested): set
`db_backup_retention_days` in `terraform.tfvars` and apply. On that run a refused database create
left the other four new resources (the two firewall objects, the subnet group and the parameter
group) in place and the saved `tfplan` stale; fix the cause, run `terraform plan -out=tfplan`
again, then apply.

**One-time setup of the app role.** The database admits only the server, so run
`db-bootstrap.sql` from your laptop through an SSH tunnel via the server; the master password then
never touches the server. It needs Docker on the laptop (the `postgres:17` image supplies `psql`).
The command below uses `host.docker.internal`, which Docker Desktop provides; it has not been
tried on Linux.

Terminal A, left running (take the address from `terraform output -raw db_endpoint`):

```
ssh -N -L 15432:<db_endpoint>:5432 -i <your-private-key-file> ubuntu@<elastic-ip>
```

`<your-private-key-file>` is the same private key file you use to ssh to the server.

Terminal B, from the repository root:

```
PGPASSWORD=$(aws secretsmanager get-secret-value --secret-id <db_master_secret_arn> \
  --query SecretString --output text \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
export PGPASSWORD
APP_PW=$(openssl rand -hex 24)
export APP_PW
docker run --rm -i -e PGPASSWORD -e APP_PW postgres:17 \
  psql "host=host.docker.internal port=15432 dbname=cms user=cms_master sslmode=require" \
  -f - < infra/db-bootstrap.sql
```

Before you close this shell, copy `APP_PW` somewhere private (a password manager): it is the only
copy of the app role's password. On the server it becomes `DB_PASSWORD` in `/opt/cms/.env`
(mode 600), next to `DB_HOST` set to the `db_endpoint` output. It must stay hex or alphanumeric
(`.env.prod.example` says why). Expect `CREATE EXTENSION`, `CREATE ROLE` and two `GRANT` lines. An
`APP_PW` that is empty or shorter than 16 characters stops the script before it creates anything.
The SQL is not repeatable: a second run fails with `role "cms_app" already exists`, on purpose. To
reset a lost password, connect the same way as the master user, load the new value into a psql
variable (`\getenv new_pw NEW_PW` after exporting `NEW_PW`) and run `ALTER ROLE cms_app` with
`PASSWORD` followed by `:'new_pw'`. If the tunnel does not work, run the same `docker run` on the
server and pipe the master password in over ssh stdin, never as an argument.

**Checks after the setup (run by you).**

- Through the tunnel as `cms_app` (set `PGPASSWORD` to `APP_PW`, `user=cms_app`):
  `SELECT extname FROM pg_extension;` lists `vector`;
  `SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector;` returns a number;
  `SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();` shows `t`; a connection with
  `sslmode=disable` is refused with `no pg_hba.conf entry ... no encryption`. (`SHOW rds.force_ssl;`
  does not work: RDS answers `unrecognized configuration parameter`.) The refusal proves TLS is
  required, but cannot tell our setting from RDS's own default.
- On the server, `getent hosts <db_endpoint>` shows a private address.
- From the laptop without the tunnel, `nc -zv -G 5 <db_endpoint> 5432` times out (`-G` is the macOS
  flag; use `-w 5` on Linux).
- `SHOW max_connections;` as `cms_app`. The app's pool (5 plus 10 overflow, one worker) should be
  far below it; write the figure under "What is verified".

**Undo before real data exists.** Deletion protection blocks `terraform destroy`. Set
`db_deletion_protection = false` in `terraform.tfvars`, run `terraform apply`, then destroy only the
database objects, because a plain `terraform destroy` also removes the server and releases the IP:

```
terraform destroy -target=aws_db_instance.cms -target=aws_db_parameter_group.cms \
  -target=aws_db_subnet_group.cms -target=aws_vpc_security_group_ingress_rule.db_from_web \
  -target=aws_security_group.db
```

The destroy takes a final snapshot named by `db_final_snapshot_name`. AWS says snapshots cost money
until deleted (RDS User Guide, "Deleting a DB instance"). A second destroy fails while a snapshot
with that name exists: delete the old snapshot, or set a new name in `terraform.tfvars` before
destroying again. Automated backups go with the instance (the provider default, not run), so only
that snapshot survives.
Restoring from it is a console or CLI task plus a Terraform import, not covered here. Never
`-replace` the database without taking a snapshot first.

## Running the app on the server

This folder stops at the server and the database. What remains to run the app on them is outlined
in [docs/deploy.md](../docs/deploy.md); it has no steps yet.

## If something fails

- **`cloud-init status --wait` prints `error`, or `docker` is missing.** The first-boot script
  failed part-way (for example a download from Docker's servers). Read
  `/var/log/cloud-init-output.log` on the server (`sudo less /var/log/cloud-init-output.log`); the
  last lines name the failing command. The script runs only once, and the only way to run it again
  is a new server: `terraform apply -replace=aws_instance.cms` (see "Changing the server setup
  later" for what that destroys).
- **`apply` stopped part-way** (for example the budget was refused after the server was created).
  Fix the cause, then run `terraform apply` again (plain `apply` plans afresh; `apply tfplan` with
  the old plan file will likely be refused as stale, so make a new one with
  `terraform plan -out=tfplan` first). Terraform remembers what it already built and only does the
  rest.
- **`UnauthorizedOperation` or `AccessDenied`.** Route B: the IAM user lacks a right. The message
  names the action; see "IAM rights". Route A: the message may instead name a policy that denies the
  action; that is not something you can add a right for.
- **A sign-in or credentials error after hours of work (route A).** The short-lived login may have
  run out (the docs give up to 12 hours; no expiry was observed). Run `aws login --profile <name>`
  again, and check `AWS_PROFILE` is set in this terminal.
- **"Your query returned no results" for the image.** The Ubuntu image filter in `main.tf` found
  nothing in this region. Check the image name in the EC2 console (AMIs, then Public images) and
  adjust the `name` filter.
- **"no matching EC2 VPC found".** The account has no default VPC; see "What you need first".
- **`InsufficientInstanceCapacity` or "Unsupported instance type".** AWS has no `t4g.micro` free
  right now, or your account may not use it. Set `instance_type = "t4g.small"` (or another ARM size)
  in `terraform.tfvars` and run `terraform apply` again. A larger size costs more.

## If you lose the SSH key

The key's passphrase cannot be recovered; keep it in a password manager. With a lost key, make a
new key pair, put the new PUBLIC key in `terraform.tfvars` (`ssh_public_key`), and run
`terraform plan -out=tfplan -replace=aws_instance.cms`. Check that the plan replaces only the key
pair, the server and the Elastic IP association, and not the database or the Elastic IP; then
apply, and run `ssh-keygen -R <ip>` because the new server has a new host fingerprint. Observed in
a plan, and the replacement apply completed on the owner's account (server up, `cloud-init`
`done`).

Replacing the server loses its disk (`/opt/cms/.env`, Caddy certificates); see
[Changing the server setup later](#changing-the-server-setup-later) and copy out what you can
first. With the old key lost, that copy may be impossible.

## If your IP changes

You will be locked out of SSH. Read your new IP with `curl https://checkip.amazonaws.com`, change
`ssh_cidr` in `terraform.tfvars` (in this folder), and run `terraform apply` again from `infra/`.
It edits one firewall rule and nothing else.

## Changing the server setup later

Terraform deliberately ignores edits to `user_data.sh` and a newer Ubuntu image (`ignore_changes`
in `main.tf`), so after such an edit `terraform apply` says "No changes". That is expected: the
script runs only once, on the server's first boot, so an edit would never take effect on a running
server anyway. To apply a changed script or image, replace the server:
`terraform apply -replace=aws_instance.cms`. Replacing destroys the old disk, so `/opt/cms/.env`
and the Caddy certificates are lost; copy out what you need first. The Elastic IP stays.

Changing `ssh_public_key` replaces only the key pair in AWS. It does not change the keys already
installed on a running server, so the old key keeps working and the new one does not. A real
plan showed only the key pair `# forces replacement`; the server is replaced only with
`-replace=aws_instance.cms`. To use the new key without replacing the server, log in with the old
key and append the new public line to `~/.ssh/authorized_keys` on the server (and remove the old
line if you want the old key gone).

## What the plan should show

On an empty state, `Plan: 16 to add, 0 to change, 0 to destroy.`; on a state where the server
already exists, the database adds `Plan: 5 to add, 0 to change, 0 to destroy.` (the database, its
subnet group, parameter group and security group, and the one inbound rule), with no change to the
server, the IP or `cropcms-web`. If `monthly_budget_usd` is set in `terraform.tfvars` (as this
README tells you) and differs from the default 25, the plan also shows the budget as 1 change:
`Plan: 5 to add, 1 to change, 0 to destroy.` (seen with 32). A harmless parameter-group diff for
`rds.force_ssl` showed in every plan made after the group existed (three plans seen). Setting
`apply_method = "pending-reboot"` removed it: the next plan on the owner's account showed no
changes. If anything else changes, stop. The
outputs are `elastic_ip`, `instance_id`, `ssh_command`, `db_endpoint`, `db_name`,
`db_master_username` and `db_master_secret_arn`. Check:

- **Firewall (`cropcms-web`):** inbound 22/tcp from your `/32`; 80/tcp, 443/tcp and 443/udp from
  `0.0.0.0/0`; nothing else inbound; all outbound. If port 22 shows any other source, stop.
- **Image (`ami-...`):** note the id, then run `aws ec2 describe-images --region eu-north-1
  --image-ids <ami-id> --query 'Images[0].[OwnerId,Name,Architecture]'`. Expect owner
  `099720109477` (Canonical), a name starting `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-`
  and `arm64`.
- **Server:** `t4g.micro`, `http_tokens = "required"`, `cpu_credits = "standard"`, root volume
  `gp3`, `10` GB, `encrypted = true`, `key_name = "cropcms-admin"`.
- **Key pair:** `public_key` is your `ssh-ed25519 ...` line.
- **Elastic IP:** one `aws_eip` and one `aws_eip_association`.
- **Database (`cropcms`):** `publicly_accessible = false`, `storage_encrypted = true`,
  `deletion_protection = true`, `backup_retention_period = 1`, `manage_master_user_password = true`,
  no `password` attribute, and the final snapshot name from `db_final_snapshot_name`.
  `cropcms-db` has one inbound rule, 5432/tcp, whose source is `cropcms-web` and not an address.
- **Budget:** `limit_amount` equal to your `monthly_budget_usd` (25 by default; 32 if you set it as
  the README tells you below), `USD`, `MONTHLY`, two notifications to your e-mail address
  (`ACTUAL` above 80 percent, `FORECASTED` above 100 percent), and `include_credit = false`.

## Cost and the spend alert

What is created here costs about 10.8 USD a month before tax: server 6.28, disk 0.84, public IP
3.65 (AWS list prices for Stockholm, not re-checked). The database adds about 14.48 USD (instance
11.68, 20 GB gp3 2.40, the password entry 0.40), which brings the whole stack to about 25.25 USD a
month before tax. Backup storage beyond what AWS includes, data transfer and tax are not counted.

The budget `cropcms-monthly` is 25 USD a month. It e-mails you when the actual spend passes 80
percent (20 USD) and when AWS forecasts that the month will pass 100 percent (25 USD). The alert
counts tax, and credits are not subtracted, so it shows what you really use rather than what is
left after credits. With only the server (about 10.8 USD) neither alert would fire. The forecast
alert stays silent at first: AWS needs approximately 5 weeks of usage data before it can forecast.

## With the database

The default of 25 was set for the server alone. With the database the stack is about 25.25 USD a
month before tax, above it, so the 20 USD actual alert will fire once a month (AWS sends an actual
alert once per budget period; see
https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-best-practices.html) and the
forecast alert at 25 USD will fire every month, until you raise the budget. The default stays 25 in
the code (`check.sh` pins it); put `monthly_budget_usd = 32` in your untracked `terraform.tfvars`
before the first apply of the database. 32 is the rule below applied to 25.25 and rounded up
(25.25 / 0.8 is about 31.6), before any tax, whose rate is unknown. After the first full month,
read the real monthly total (with tax, if any) in the Billing console, and set `monthly_budget_usd`
so that 80 percent of it is above that total.

The master password is not in Terraform state, because RDS manages it; state still holds the
database's other settings, so keep the bucket private anyway.

**Free plan.** If the AWS account is on the Free plan, AWS's plan page says it ends after six
months or when the credits run out; the account is then suspended and its data kept for 90 days
unless it is upgraded (read once, not re-checked here). A stack of about 25 USD a month would use
credits of 100 to 200 USD up in four to eight months. Moving to the Paid plan is your decision.

## Checks without AWS

`bash infra/check.sh` needs only Docker and python3. It fails if the firewall opens anything beyond
22 (your IP), 80, 443, if a key, token, account id, real IP, AWS endpoint name or personal e-mail
is in these files, in `docs/*.md`, `README.md`, `docker-compose.yaml`, `docker-compose.prod.yaml`,
`proxy/Caddyfile`, `prod/check.sh`, `prod/smoke.sh` or a `.env*` file that git tracks or would add
(ignored ones are never read). It also fails if one of those env files gives a value to a name
ending in PASSWORD, SECRET, KEY, TOKEN or PRIVATEKEY, or a password to a URL; blank, quoted-empty
and `${...}` values pass. It fails if the server lacks IMDSv2 or an encrypted disk, if the budget
alerts change, or if the bootstrap script allows public access. It also rejects `dynamic` ingress or
egress blocks, `module` blocks and `.tf.json` files, which could hide rules from these checks. It
also runs `terraform fmt`, `init -backend=false` and `validate`,
and checks that bad `ssh_cidr` values and bad database settings (backup days, final snapshot name,
deletion protection) are refused. For the database it fails if the database group admits anything
but one 5432/tcp rule from `cropcms-web`, if the instance is public, unencrypted, without a final
snapshot, without deletion protection from the variable, or sets a password, snapshot, replica or
restore source, if a database resource other than the instance, subnet group or parameter group
appears, if a generated password, secret read, `provisioner` or `ephemeral` block appears, if an
output could expose a password, or if any file here contains a quoted SQL password literal
(`PASSWORD '...'` in any case, `E'...'`, `$$...$$`). `infra/db-bootstrap.test.sh` runs the
bootstrap SQL in a throwaway local pgvector container and connects through the app's own URL
lines, which downloads images and Python packages. It also checks that git ignores `.env.*` except
the two example files, and that `.dockerignore` keeps env files at any depth out of the image
(the Docker part builds a throwaway context). CI runs it all. `bash infra/check.sh --static`
skips the Docker parts.

## What is verified

A first real run on 2026-10-14, with route A, created all 11 resources, including the budget.
Confirmed in that run: the Ubuntu image lookup in eu-north-1; budget creation; a `t4g.micro`
launched; first boot finished (`cloud-init` done), with Docker 29.9.0 and Docker Compose
v5.6.0 installed, 1 GiB of swap, and `/opt/cms` and `/opt/cms/site` owned by `ubuntu`; and SSH login
with the key. Also confirmed in it: `aws login --profile cropcms`, `export AWS_PROFILE=cropcms`,
`terraform init`, `plan -out=tfplan` and `apply "tfplan"` all worked in that account type.
`bash infra/bootstrap-state.sh` also ran against that account: it created the state bucket, and the
versioning, public-access-block and encryption calls all succeeded. Step 7's port 22 check also
ran: from a different network (a phone hotspot), `nc -zv -G 5 <elastic-ip> 22` timed out, which is
the result step 7 predicts, while the same command from the allowed address succeeded. That was one
attempt from one other address.

The database setup SQL (`db-bootstrap.sql`) ran in a throwaway local `pgvector/pgvector:pg17`
container: it created the extension and the `cms_app` role, `cms_app` created a `vector(3)` table
and ran `<=>`, a second run failed with `role "cms_app" already exists`, and `app/db/db.py` and
`app/db/migrate_db.py` connected with a hex password while an `@` in the password failed. That is
a local PostgreSQL, not RDS.

The database, observed on one real run, not generalised: `describe-db-engine-versions` listed
postgres 17.5 to 17.11 in eu-north-1, and `db.t4g.micro` with 17.11 listed gp2, gp3, io1 and io2
storage. A create with retention 7 and with 3 was refused (`FreeTierRestrictionError`), and 1
succeeded: the instance was `available`, engine 17.9, not public, encrypted, deletion protection
on, and the master secret was `active`. Inferred from that, not separately tested: the Free plan
allows RDS and the managed secret. Through the tunnel as `cms_app`, with `host.docker.internal` on
Docker Desktop for Mac, `vector` worked (a `<=>` query returned a number), `pg_stat_ssl` showed ssl
`t` with TLSv1.3, and `sslmode=disable` was refused with "no encryption" (this cannot separate our
parameter group from RDS's default). The parameter group was `in-sync`; `max_connections` was 79.
`getent hosts <db_endpoint>` on the server returned a private address, and `nc -zv -G 5
<db_endpoint> 5432` from the laptop timed out. Replacing the server with `-replace=aws_instance.cms`
completed, the new server came up and `cloud-init` said `done`.

## What is not verified

- Running the bootstrap script a second time ("safe to run again") against the real account was not
  tried; `bootstrap-state.test.sh` still uses a stub `aws` that records its calls.
- That route A works for every account type is not verified: only the account type above was used.
  `aws configure sso` was not tried. Route B was not run.
- The route B IAM rights lists above are derived, not run, and that Budgets works for an IAM user is
  unchecked.
- The forecast alert and the prices above are unchecked.
- Still unverified for the database: backup retention of 2; higher retention on a Paid plan;
  `host.docker.internal` on Linux; verify-full TLS (not done); free backup storage behaviour; and
  the real bill. Not yet checked: that `cms_app` can run `alembic current` against this database;
  that moves to the first deploy.
- The Secrets Manager and RDS lines in the IAM lists are derived, not run; the last three
  Secrets Manager actions are guesses.
- The Free plan limits above (six months, credits, suspension) are from AWS's plan page as read
  once.
