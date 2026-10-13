# infra: the server on AWS

This folder builds one small server (EC2) in the AWS region eu-north-1 (Stockholm) with Terraform.
It has Docker installed, a fixed public IP address, and a firewall that lets the whole internet reach
ports 80 and 443 only. SSH (port 22) is open to your own IP address and nobody else. It does not
create a database; that is a separate piece of work.

This README was written before anything was applied to a real AWS account. The files were checked
offline only; see "What is not verified" at the end, and trust what a real run shows over this text.

## What you need first

1. An AWS account. Turn on MFA for the root login, then create one IAM user for Terraform, turn on
   MFA for it, give it rights (see "IAM rights" below) and create an access key for that user.
2. A default VPC in eu-north-1. A new account has one, but it can be deleted. Check in the console:
   VPC, then Your VPCs, and look for one with Default VPC = yes. Without it, `plan` fails with "no
   matching EC2 VPC found".
3. Install the tools: [Terraform](https://developer.hashicorp.com/terraform/install) 1.11 or newer
   and the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
4. Run `aws configure` and paste the access key. It is stored in `~/.aws/credentials`, outside this
   repository. Never put it in a file here or paste it into a chat. Then check that it works:
   `aws sts get-caller-identity` should print your account id and the IAM user. If it prints an
   error, fix `aws configure` before going on.
5. Make an SSH key (use a passphrase): `ssh-keygen -t ed25519`. You get a private file
   `~/.ssh/id_ed25519`, which never leaves your laptop, and a public file `~/.ssh/id_ed25519.pub`,
   which is safe to share.

### IAM rights

The lists below were derived from the AWS provider's source code and AWS documentation. They were
not run against IAM, so expect to iterate: if a step fails with `AccessDenied` or
`UnauthorizedOperation`, the message names the missing action; add it and run the step again.

**Easiest:** attach the AWS managed policy `PowerUserAccess` to the Terraform IAM user. It allows
everything except IAM, Organizations and Account management, which is enough in principle. One
catch: for IAM users to use Budgets, the root user may first have to turn on "IAM access to
Billing" (Account page in the console).

**Least privilege, if you prefer.** Each group is a separate statement:

- EC2 read, `Resource: "*"`: DescribeVpcs, DescribeVpcAttribute, DescribeRouteTables,
  DescribeImages, DescribeKeyPairs, DescribeSecurityGroups, DescribeSecurityGroupRules,
  DescribeInstances, DescribeInstanceAttribute, DescribeInstanceCreditSpecifications,
  DescribeInstanceTypes, DescribeVolumes, DescribeAddresses, DescribeAddressesAttribute,
  DescribeTags. (DescribeRouteTables is only logged as a warning if denied; the others fail.)
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
   cat ~/.ssh/id_ed25519.pub                 # paste the whole line
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

4. **Plan.** `terraform plan` shows what would be created and changes nothing. Read it; see
   "What the plan should show" below.

5. **Apply.** `terraform apply`, then type `yes`. It prints the IP, the instance id and an ssh
   command.

6. **Log in.**

   ```
   terraform output -raw ssh_command         # copy the line it prints, paste it, press Enter
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
   released: a new one gets a new address, so the DNS record must be pointed at it again. The state
   bucket stays (it costs almost nothing); delete it by hand in the console if you want it gone.

Being in the `docker` group on the server is the same as being root there, so keep the SSH key
private and protected by its passphrase.

## If something fails

- **`cloud-init status --wait` prints `error`, or `docker` is missing.** The first-boot script
  failed part-way (for example a download from Docker's servers). Read
  `/var/log/cloud-init-output.log` on the server (`sudo less /var/log/cloud-init-output.log`); the
  last lines name the failing command. The script runs only once, and the only way to run it again
  is a new server: `terraform apply -replace=aws_instance.cms` (see "Changing the server setup
  later" for what that destroys).
- **`apply` stopped part-way** (for example the budget was refused after the server was created).
  Fix the cause, then run `terraform apply` again. Terraform remembers what it already built and
  only does the rest.
- **`UnauthorizedOperation` or `AccessDenied`.** The IAM user lacks a right. The message names the
  action; see "IAM rights".
- **"Your query returned no results" for the image.** The Ubuntu image filter in `main.tf` found
  nothing in this region. Check the image name in the EC2 console (AMIs, then Public images) and
  adjust the `name` filter.
- **"no matching EC2 VPC found".** The account has no default VPC; see "What you need first".
- **`InsufficientInstanceCapacity` or "Unsupported instance type".** AWS has no `t4g.micro` free
  right now, or your account may not use it. Set `instance_type = "t4g.small"` (or another ARM size)
  in `terraform.tfvars` and run `terraform apply` again. A larger size costs more.

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
installed on a running server, so the old key keeps working and the new one does not. This is what
the provider's design suggests; it has not been checked with a real plan. To use the new key
without replacing the server, log in with the old key and append the new public line to
`~/.ssh/authorized_keys` on the server (and remove the old line if you want the old key gone).

## What the plan should show

`Plan: 11 to add, 0 to change, 0 to destroy.` and these outputs: `elastic_ip`, `instance_id`,
`ssh_command`. Check:

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
- **Budget:** `limit_amount = "25"`, `USD`, `MONTHLY`, two notifications to your e-mail address
  (`ACTUAL` above 80 percent, `FORECASTED` above 100 percent), and `include_credit = false`.

## Cost and the spend alert

What is created here costs about 10.8 USD a month before tax: server 6.28, disk 0.84, public IP
3.65 (AWS list prices for Stockholm, not re-checked). A database added later brings the whole
stack to about 24.85 USD a month before tax.

The budget `cropcms-monthly` is 25 USD a month. It e-mails you when the actual spend passes 80
percent (20 USD) and when AWS forecasts that the month will pass 100 percent (25 USD). The alert
counts tax, and credits are not subtracted, so it shows what you really use rather than what is
left after credits. With only this server (about 10.8 USD) neither alert should fire. The forecast
alert may stay quiet for the first weeks, because AWS needs some usage history to forecast (not
checked).

## After a database is added

The database password will then be stored in the Terraform state file. That makes the state bucket
sensitive, so harden it at that point (for example refuse non-TLS access and limit who can read it).

## Checks without AWS

`bash infra/check.sh` needs only Docker and python3. It fails if the firewall opens anything beyond
22 (your IP), 80, 443, if a key, token or account id is in these files, if the server lacks IMDSv2 or
an encrypted disk, if the budget alerts change, or if the bootstrap script allows public access. It
also runs `terraform fmt`, `init -backend=false` and `validate`, and checks that bad `ssh_cidr`
values are refused. CI runs it. `bash infra/check.sh --static` skips the Docker parts.

## What is not verified

- No `plan`, `apply` or bootstrap run against a real account had happened when this was written:
  there were no credentials. The bootstrap script was tested against a stub `aws` that records its
  calls.
- The Ubuntu image name filter and Canonical's account id come from Ubuntu's documentation, not from
  an AWS call. If the plan finds no image, the filter needs adjusting.
- `user_data.sh` ran in an Ubuntu 24.04 arm64 container, not on the real image. Swap, `systemctl`
  and cloud-init behaviour on the real server are untested.
- That the account may launch `t4g.micro`, that the IAM user holds enough rights (the lists above
  are derived, not run), that Budgets works for an IAM user, the forecast alert, and the prices
  above, are unchecked.
