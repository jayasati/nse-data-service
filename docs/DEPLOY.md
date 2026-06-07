# Deployment — Phase 1, Week 1 (the VPS gate)

> ## ⚡ Daily ops cheat-sheet
>
> **Instance:** `i-0a2677d417ab9109c` · `ap-south-1` · public IP `13.200.215.86`
>
> **Connect (preferred — AWS SSM, immune to dynamic-IP changes):**
> ```bash
> nse-shell                       # 1. open the SSM session (lands as ssm-user, bare $ prompt)
> sudo su - ubuntu                # 2. switch to the app user 'ubuntu'
> cd /opt/nse-data-service        # 3. go to the project folder
> source .venv/bin/activate       # 4. activate the server venv (only for python/migrate cmds)
> ```
> Confirm you're in the right place: `whoami` → `ubuntu`, `pwd` → `/opt/nse-data-service`.
> Dashboard tunnel (separate terminal): `nse-tunnel` → http://localhost:8000
>
> (`nse-shell`/`nse-tunnel` are bash functions in `~/.bashrc`. SSH fallback:
> `./scripts/allow_ssh.sh && ssh -i ~/nse-data-service/stock-key.pem ubuntu@13.200.215.86`.)
>
> **Update the server after pushing from the laptop** (laptop → GitHub → server):
> ```bash
> # on the laptop:  git add -A && git commit -m "..." && git push origin main
> # then on the server (data/.env are gitignored — DB & secrets untouched):
> ./scripts/deploy.sh ubuntu     # pull + deps + migrations + restart services
> ```
>
> **Applying DB migrations on the server** (run from `/opt/nse-data-service`):
> `deploy.sh` already does this — these are for applying a migration *without* a
> full redeploy, or to verify one landed. The runner is idempotent (applied files
> are skipped) and never touches `data/`/`.env`:
> ```bash
> git pull --ff-only origin main                      # get the new migrations/0NN_*.sql
> .venv/bin/python scripts/migrate.py --status        # list applied vs pending (applies nothing)
> .venv/bin/python scripts/migrate.py                 # apply the pending files onto the live DB
> sudo systemctl restart nse-collector@ubuntu         # pick up the new code
> ```
> `main.py` also applies pending migrations on every boot, so a plain
> `systemctl restart` would suffice — running `migrate.py` first just lets you see
> what changed before the service starts.
>
> **Check it's alive:**
> ```bash
> systemctl is-active nse-collector@ubuntu       # expect: active
> tail -1 data/backfill_7d.log                    # backfill: "done · ... errors"
> ```
>
> Full details below; SSM/SSH access is in [§2](#2-connect).

**What this file is:** the step-by-step runbook for standing up the always-on
host. It is the deliverable for checklist task **1.12**, and every section maps
to a Week-1 task (1.1–1.11) so you can tick them off as you go.

**Why this is Phase 1, Week 1 ("THE GATE"):** the collector is a single
long-running process (`python -m nse_data.main`) that schedules every enabled
feed in `config/endpoints.yaml`. It only collects while that process is alive.
On a laptop that sleeps, any daily/weekly run scheduled for an off hour
(`fii_dii` 19:00, `surveillance_*` 20:00, …) silently never fires. Every
minute-cadence job in Weeks 2–6 is meaningless on a sleeping host. This week
retires the #1 blocker; nothing else in Phase 1 starts until it runs **5 clean
trading days with zero laptop dependency**.

> **Naming note:** the checklist (task 1.7) calls the unit `nse-data.service`.
> In this repo it's `deploy/nse-collector.service`, installed as a `%i`-templated
> instance — so the running unit is `nse-collector@<user>.service`. Same thing,
> more descriptive name and wired into `scripts/deploy.sh`.

---

## Week-1 task map

| Task | Where |
|---|---|
| 1.1 Provision VPS | [§1](#1-provision-the-instance-task-11) |
| 1.2 Install deps (Python 3.12, Redis, Git, tmux) | [§3](#3-system-packages-task-12) |
| 1.3 Transfer the 5.1 GB `nse.db` | [§6](#6-transfer-the-existing-database-task-13) |
| 1.4 Transfer codebase | [§4](#4-get-the-code-task-14) |
| 1.5 Configure `.env` | [§7](#7-secrets-and-config-task-15) |
| 1.6 Redis on boot | [§5](#5-redis--enable-on-boot-task-16) |
| 1.7 systemd unit | [§8](#8-install-the-systemd-units-tasks-17-18) |
| 1.8 Enable + start | [§8](#8-install-the-systemd-units-tasks-17-18) |
| 1.9–1.11 Verify 5 trading days | [§9](#9-verify--the-5-day-gate-tasks-1911) |
| 1.12 This document | — |

---

## 1. Provision the instance (task 1.1)

A single instance in **Mumbai (ap-south-1)** runs everything. Mumbai matters:
NSE is sensitive to non-Indian IPs, so collect from an Indian region.

EC2 → Launch instance:

- **Region:** ap-south-1 (Mumbai) — set in the top-right selector *before* launching.
- **AMI:** Ubuntu Server 24.04 LTS.
- **Type:** the Week-1 target is **4 vCPU / 8 GB**. On AWS:
  - **`t3.xlarge`** (4 vCPU / 16 GB, burstable) — **recommended**: meets the vCPU
    target with RAM headroom for Redis + the minute-cadence indicator jobs landing
    in Weeks 2–4.
  - `c6i.xlarge` (4 vCPU / 8 GB) — exact spec match, non-burstable.
  - **`m7i-flex.large`** (2 vCPU / 8 GB) — **best budget pick**. Newer Sapphire
    Rapids, cheaper than `t3.large` (~$65–70/mo on-demand vs `t3.xlarge` ~$150),
    meets the RAM target. "Flex" = sustained ~40% CPU baseline + bursting, which is
    plenty for Phase-1 polling. Watch CPU once the Week 2–4 minute jobs land; if
    they throttle, move to `m7i.large` (full 2 vCPU) or `m7i.xlarge` (4 vCPU).
  - `t3.large` (2 vCPU / 8 GB) — older budget option; `m7i-flex.large` is the
    better-value equivalent.
- **Storage:** **100 GB gp3** (task 1.1). The DB is ~5 GB today; intraday candles,
  backfills, and 30 days of DB backups grow it steadily.
- **Key pair:** create one, download the `.pem` — this is your SSH login.
- **Security group:** allow **SSH (22) from *My IP* only**. Do **not** open 8000 —
  the dashboard has no auth; reach it over an SSH tunnel ([§10](#10-optional-dashboard-over-ssh-tunnel)).

> **Cost note:** AWS on-demand for these types in ap-south-1 runs higher than the
> Hetzner CX32 the checklist names as cheapest (`t3.xlarge` ≈ $0.21/hr on-demand).
> A 1-year Savings Plan or Reserved Instance roughly halves it. Lightsail's fixed
> ~$40/mo 8 GB plan is a simpler-billing alternative with the same systemd flow.

---

## 2. Connect

### Preferred: AWS SSM Session Manager (no SSH, no IP allowlist)

The home connection has a **dynamic public IP**, so the "SSH from My IP only"
security-group rule breaks on every laptop/router restart (SSH then hangs until
it times out). SSM sidesteps this entirely — it connects through the AWS API, so
there's no open port and no IP rule to maintain.

One-time setup (already done for this box):
- Instance has IAM role **`nse-ec2-ssm-role`** (`AmazonSSMManagedInstanceCore`) attached.
- IAM user `jay` has an `allow-ssm-session` inline policy (`ssm:StartSession`, …).
- Laptop has the `session-manager-plugin` installed.

Daily use (shortcuts are in `~/.bashrc` — `nse-shell` / `nse-tunnel`):

```bash
# interactive shell (lands as ssm-user; `sudo su - ubuntu` for the app user)
aws ssm start-session --target i-0a2677d417ab9109c --region ap-south-1

# dashboard tunnel -> http://localhost:8000 (replaces the SSH -L tunnel in §10)
aws ssm start-session --target i-0a2677d417ab9109c --region ap-south-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

### Fallback: plain SSH (needs the SG rule to match your current IP)

```bash
chmod 400 /home/jay/nse-data-service/stock-key.pem
./scripts/allow_ssh.sh    # updates the SG to your current public IP (dynamic-IP fix)
ssh -i /home/jay/nse-data-service/stock-key.pem ubuntu@13.200.215.86
```

---

## 3. System packages (task 1.2)

Ubuntu 24.04 ships Python 3.12 as `python3`. Install it plus Redis, Git, tmux,
and build tooling:

```bash
sudo apt update && sudo apt install -y \
  python3 python3-venv python3-pip \
  redis-server git tmux build-essential rsync sqlite3
python3 --version            # expect 3.12.x
```

---

## 4. Get the code (task 1.4)

```bash
sudo mkdir -p /opt/nse-data-service && sudo chown "$USER" /opt/nse-data-service
git clone https://github.com/jayasati/nse-data-service.git /opt/nse-data-service
cd /opt/nse-data-service
```

Private repo? Use a **read-only deploy key**: `ssh-keygen -t ed25519 -f
~/.ssh/deploy -N ""`, add `~/.ssh/deploy.pub` to the repo (Settings → Deploy
keys, read-only), then clone the `git@github.com:…` URL with that key.

> Push first. The server pulls from `origin`, so commit and `git push origin main`
> on the laptop before cloning — nothing here works against unpushed code. The
> DB is **not** in git (it's gitignored); it's transferred separately in §6.

### venv + install

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dashboard,redis,indicators]"   # add ,broker for Groww/Kite backfill
```

The `redis` extra installs the client so the collector uses the Redis dedup
cache instead of the in-process fallback. `indicators` pulls pandas for the
nightly/minute indicator jobs.

---

## 5. Redis — enable on boot (task 1.6)

`apt install redis-server` already started it. Make it come back on every reboot
and confirm it answers:

```bash
sudo systemctl enable --now redis-server
redis-cli ping                       # expect: PONG
sudo systemctl is-enabled redis-server   # expect: enabled
```

The collector connects to the default `localhost:6379` (no URL to configure).
The collector unit is ordered `After=redis-server.service` with `Wants=`, so on
boot Redis starts first — but if Redis is ever down the collector still runs,
falling back to the memory cache (logged as `dedup_cache_redis_unavailable`).

---

## 6. Transfer the existing database (task 1.3)

**This is the step that carries your ~5 GB of history.** Do not run `migrate.py`
to create a fresh DB here — that would give you an empty schema and throw away
everything collected so far. Migrations are applied automatically against the
*transferred* DB on first boot.

Run this **from the laptop** (repo root), pointing at the VPS:

```bash
./scripts/transfer_db.sh ubuntu@13.200.215.86 \
    /opt/nse-data-service/data \
    /home/jay/nse-data-service/stock-key.pem
```

The script takes a consistent `sqlite3 .backup` snapshot (safe even while the
local collector is running), then `rsync`s it compressed and resumable — a
dropped connection mid-transfer resumes on re-run. ~5 GB over a typical home
uplink is tens of minutes; run it inside `tmux`/`screen` on the laptop, or let
it resume.

Verify on the server once it finishes:

```bash
ssh -i /home/jay/nse-data-service/stock-key.pem ubuntu@13.200.215.86 \
  "sqlite3 /opt/nse-data-service/data/nse.db 'PRAGMA integrity_check;'"   # expect: ok
```

Then confirm the schema is current (the boot path also does this):

```bash
# on the server, in the repo dir
.venv/bin/python scripts/migrate.py --status   # all applied, none pending
.venv/bin/python scripts/migrate.py            # applies any new migrations onto the transferred DB
```

---

## 7. Secrets and config (task 1.5)

```bash
cp .env.example .env && nano .env
```

`.env.example` documents every variable the code actually reads (Azure OpenAI,
Groww/Kite) and notes what is *not* set here (the DB path and Redis are not env
vars). Telegram keys are placeholders for now — they get filled in Week 5 when
the dispatcher lands. `config/endpoints.yaml` ships in the repo; tune `enabled:`
flags there if needed.

---

## 8. Install the systemd units (tasks 1.7, 1.8)

The units are `%i`-templated on the run user, so they must be installed under
the template filename (`name@.service`) — that's what lets `@ubuntu` resolve
`%i` to the `ubuntu` user.

```bash
sudo cp deploy/nse-collector@.service /etc/systemd/system/
sudo cp deploy/nse-dashboard@.service /etc/systemd/system/   # optional UI/API
sudo systemctl daemon-reload
sudo systemctl enable --now nse-collector@ubuntu             # %i = ubuntu
sudo systemctl enable --now nse-dashboard@ubuntu             # optional
```

The unit files point at `/opt/nse-data-service`, run as the `%i` user (`ubuntu`),
restart on crash, and load `.env`. Verify:

```bash
systemctl status nse-collector@ubuntu
journalctl -u nse-collector@ubuntu -f       # JSON logs: collector_run, catchup_*
```

On start, `main.py` also runs a **catch-up pass** (`scheduler.catchup.run_due`):
any daily/weekly collector whose stored data lags its expected run fires once
immediately, so a reboot self-heals same-day misses. (This recovers a *missed
schedule*, not lost history — NSE snapshot endpoints serve only the latest day.)

---

## 9. Verify — the 5-day gate (tasks 1.9–1.11)

The gate is **all 32 collectors firing on schedule for 5 consecutive trading
days, with the laptop off.**

**Each trading day:**

1. **Watch the feed (1.9).** The health dashboard (`/`, via the tunnel in §10) or
   the logs show each feed's freshness. Intraday feeds (5-min) should tick all
   session; daily feeds should land at their scheduled time. A daily feed sitting
   in the **stale/down** group after its run time = a missed run.
   ```bash
   journalctl -u nse-collector@ubuntu --since "06:00" | grep -E 'collector_run|error'
   .venv/bin/python scripts/run_collectors.py --due --dry-run   # what's overdue right now
   ```
2. **Spot-check 10 values by hand (1.10).** Pick a few prices, OI numbers, and the
   VIX level from the DB and compare against the live NSE website:
   ```bash
   sqlite3 data/nse.db \
     "SELECT symbol, last_price FROM raw_equity_quotes ORDER BY ts DESC LIMIT 5;"
   sqlite3 data/nse.db \
     "SELECT * FROM raw_india_vix ORDER BY ts DESC LIMIT 1;"
   ```
   Prices/OI/VIX should match NSE within the polling lag.
3. **Repeat for 5 consecutive trading days (1.11).** Log anything odd in
   `LEARNINGS.md`. The clock doesn't advance the gate — clean days do.

**Gate met when:** 32 collectors ran 5 straight trading days, zero laptop
dependency, data spot-checked. Only then start Week 2.

---

## 10. (Optional) Dashboard over SSH tunnel

The dashboard has no auth — never expose port 8000. Tunnel from the laptop:

```bash
ssh -i /home/jay/nse-data-service/stock-key.pem -L 8000:localhost:8000 ubuntu@13.200.215.86
# then open http://localhost:8000
```

---

## 11. Checking server status (quick reference)

Day-to-day "is it alive and collecting?" checks. The SSH key lives at
`/home/jay/nse-data-service/stock-key.pem` and the instance is `ubuntu@13.200.215.86`
(ap-south-1, Mumbai).

> First time only: `chmod 400 /home/jay/nse-data-service/stock-key.pem`
> (SSH refuses a key that's group/world-readable).

> **If SSH hangs / "freezes" after a laptop restart:** the security group allows
> SSH from "My IP" only, but a home connection gets a *new* public IP on each
> restart — so the old rule no longer matches and `ssh` silently times out.
> Fix in one command from the laptop (needs AWS CLI configured once via
> `aws configure`):
> ```bash
> ./scripts/allow_ssh.sh      # detects current public IP, updates the SG rule
> ```
> If it reports "no running instance", the box is stopped or its public IP
> changed — check the EC2 console.

**Fastest check — liveness from the laptop, no full login:**

```bash
ssh -i /home/jay/nse-data-service/stock-key.pem ubuntu@13.200.215.86 \
  "systemctl is-active nse-collector@ubuntu; redis-cli ping"
# expect: active   /   PONG
```

**Full session — SSH in, then inspect:**

```bash
ssh -i /home/jay/nse-data-service/stock-key.pem ubuntu@13.200.215.86
```

Once on the server:

```bash
# Service health
systemctl status nse-collector@ubuntu          # collector (the critical one)
systemctl status nse-dashboard@ubuntu          # optional UI/API
sudo systemctl is-enabled redis-server         # expect: enabled
redis-cli ping                                 # expect: PONG

# Logs / freshness
journalctl -u nse-collector@ubuntu -f                              # live JSON logs
journalctl -u nse-collector@ubuntu --since "06:00" | grep -E 'collector_run|error'
cd /opt/nse-data-service
.venv/bin/python scripts/run_collectors.py --due --dry-run         # what's overdue now

# Spot-check the data is current
sqlite3 /opt/nse-data-service/data/nse.db \
  "SELECT symbol, last_price FROM raw_equity_quotes ORDER BY ts DESC LIMIT 5;"
sqlite3 /opt/nse-data-service/data/nse.db \
  "SELECT * FROM raw_india_vix ORDER BY ts DESC LIMIT 1;"
```

**View the dashboard (no auth — tunnel from the laptop):**

```bash
ssh -i /home/jay/nse-data-service/stock-key.pem -L 8000:localhost:8000 ubuntu@13.200.215.86
# then open http://localhost:8000
```

---

## 12. Backups (preview of task 6.2)

Not a Week-1 gate item, but cheap to set up now. Nightly local snapshot, 30-day
rotation:

```bash
# crontab -e   (on the server)
0 2 * * *  cd /opt/nse-data-service && sqlite3 data/nse.db ".backup data/archive/db_backups/nse_$(date +\%Y\%m\%d).db" && find data/archive/db_backups/ -name '*.db' -mtime +30 -delete
```

For off-box durability, also copy to S3 (attach an IAM role with write access to
the bucket, install `awscli`):

```bash
30 2 * * *  aws s3 cp /opt/nse-data-service/data/nse.db s3://<your-bucket>/nse.db.$(date +\%F)
```

---

## Updating a running deployment (continuous development)

Code is replaceable; data is durable. `data/` and `.env` are gitignored, so a
`git pull` updates code **without touching the SQLite DB or secrets** — the DB
keeps accumulating across deploys. One command:

```bash
./scripts/deploy.sh ubuntu        # 'ubuntu' = the systemd instance user
```

That script backs up `nse.db` (keeps the last 30), `git pull --ff-only`s, syncs
deps, applies pending migrations, and restarts the services. On-boot catch-up
recovers anything missed during the few-second restart.

Rules that keep this safe:

- **Never edit code on the server** — it's a deploy target. Changes flow through
  git (develop locally → commit → push → pull on the server).
- **Migrations are forward-only.** Add a new `migrations/0NN_*.sql`; it applies
  once (idempotent, on boot and via `scripts/migrate.py`). `deploy.sh` snapshots
  the DB first — rollback = `git checkout <previous-tag>` **and** restore that
  `data/archive/db_backups/` snapshot, then restart.
- **Deploy tags, not WIP commits.** Tag releases (`git tag v0.x && git push
  --tags`) and pull those, so the server runs known-good points.

### Optional: auto-deploy on push (GitHub Actions)

Once manual `deploy.sh` feels solid, a workflow on push to `main` (or a `release`
branch / tags) can SSH to the box and run it — turning `git push` into a deploy.
Ask and I'll add `.github/workflows/deploy.yml`.

---

## WSL note (why the laptop can't be the host)

WSL2 only runs systemd if enabled (`/etc/wsl.conf` → `[boot] systemd=true`), and
the distro still stops when Windows sleeps or WSL shuts down — so WSL is **not**
always-on. That's exactly the blocker this week removes. For local development,
rely on the on-boot catch-up; for the real 24×7 collection, use the VPS above.
