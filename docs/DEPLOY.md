# Deployment — keeping the collector always-on

The collector is a single long-running process (`python -m nse_data.main`) that
schedules every enabled feed in `config/endpoints.yaml`. It only collects while
that process is alive. On a laptop that sleeps overnight, any **daily/weekly**
run scheduled for an off hour (e.g. `fii_dii` at 19:00, `surveillance_*` at
20:00) silently never fires — APScheduler can't trigger a job while the process
is dead. Intraday feeds self-heal on their next tick; once-a-day feeds don't.

Two layers address this:

1. **Catch-up on start** (already wired, no host needed). On every boot, `main.py`
   runs `scheduler.catchup.run_due`: any daily/weekly collector whose stored data
   lags its last expected run is run once immediately. So opening the laptop the
   next morning recovers the same-day miss automatically. Manual equivalent:
   ```
   python scripts/run_collectors.py --due           # run the stale ones now
   python scripts/run_collectors.py --due --dry-run  # just list them
   python scripts/run_collectors.py fii_dii          # run specific feeds
   ```
   ⚠️ This recovers a **missed schedule, not lost history**. NSE snapshot
   endpoints (e.g. `/api/fiidiiTradeReact`) serve only the latest day, so a run
   missed two days ago captures *today's* value — the gap stays gone.

2. **An always-on host** (the real fix). Run the process somewhere that doesn't
   sleep, so evening and weekend runs actually fire. Cheapest options: a small
   VPS (~₹350–500/mo), a Raspberry Pi at home, or any always-on Linux box.

## AWS (EC2) — step by step

A single small EC2 instance in **Mumbai (ap-south-1)** runs the whole thing.
Mumbai matters: NSE is sensitive to non-Indian IPs, so collect from an Indian
region.

### Step 0 — push the code to GitHub (prerequisite)

The server pulls from `origin`. Make sure your latest code is committed and
pushed first:
```bash
# on your laptop
git add -A && git commit -m "…" && git push origin main
```
Nothing below works against stale/unpushed code.

### Step 1 — launch the instance

EC2 → Launch instance:
- **Region:** ap-south-1 (Mumbai) — top-right selector, *before* launching.
- **AMI:** Ubuntu Server 24.04 LTS.
- **Type:** `t3.small` (2 GB, x86 — widest wheel compatibility). `t4g.small`
  (ARM/Graviton) is cheaper and works too; `t3.micro` is free-tier but 1 GB is
  tight with the dashboard + backfill.
- **Key pair:** create one, download the `.pem` (this is your SSH login).
- **Storage:** 30 GB gp3 (a 1000-symbol × 6-month minute backfill alone is
  several GB).
- **Security group:** allow **SSH (22) from *My IP* only**. Do **not** open 8000
  — the dashboard has no auth; reach it over an SSH tunnel (Step 8).

### Step 2 — connect

```bash
chmod 400 ~/Downloads/nse-key.pem
ssh -i ~/Downloads/nse-key.pem ubuntu@<EC2_PUBLIC_IP>
```

### Step 3 — system packages

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git build-essential
```

### Step 4 — get the code into /opt

```bash
sudo mkdir -p /opt/nse-data-service && sudo chown "$USER" /opt/nse-data-service
git clone https://github.com/jayasati/nse-data-service.git /opt/nse-data-service
cd /opt/nse-data-service
```
Private repo? Use a **read-only deploy key**: `ssh-keygen -t ed25519 -f
~/.ssh/deploy -N ""`, add `~/.ssh/deploy.pub` to the repo (Settings → Deploy
keys, read-only), then clone the `git@github.com:…` URL with that key.

### Step 5 — venv + install

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dashboard,broker]"      # add ,redis only if you run Redis
```

### Step 6 — secrets + config

```bash
cp .env.example .env && nano .env        # set GROWW_API_KEY / GROWW_TOTP_SECRET etc.
```
`config/endpoints.yaml` is in the repo; tune `enabled:` flags there if needed.

### Step 7 — create the DB (apply migrations)

```bash
mkdir -p data
.venv/bin/python scripts/migrate.py          # applies all 0NN_*.sql onto data/nse.db
.venv/bin/python scripts/migrate.py --status  # confirm: all applied, none pending
```
(The collector also applies pending migrations on boot, so this is just to
verify up front.)

### Step 8 — install + start the services

```bash
sudo cp deploy/nse-collector.service /etc/systemd/system/
sudo cp deploy/nse-dashboard.service /etc/systemd/system/   # optional UI/API
sudo systemctl daemon-reload
sudo systemctl enable --now nse-collector@ubuntu
sudo systemctl enable --now nse-dashboard@ubuntu            # optional
```
The unit files already point at `/opt/nse-data-service` and run as the `%i`
user (`ubuntu` here). Verify:
```bash
systemctl status nse-collector@ubuntu
journalctl -u nse-collector@ubuntu -f       # JSON logs: collector_run, catchup_*
```

### Step 9 — view the dashboard (no public port)

From your laptop, tunnel and open `http://localhost:8000`:
```bash
ssh -i ~/Downloads/nse-key.pem -L 8000:localhost:8000 ubuntu@<EC2_PUBLIC_IP>
```

### Step 10 — backups

Nightly copy of the DB to S3 (the truth source). Quick cron:
```bash
# crontab -e
30 1 * * *  aws s3 cp /opt/nse-data-service/data/nse.db s3://<your-bucket>/nse.db.$(date +\%F)
```
(Attach an IAM role to the instance with write access to that bucket; install
`awscli`.)

### Cost & ongoing

- t3.small on-demand in ap-south-1 ≈ $15–18/mo; t4g.small a few $ less; **Lightsail**
  is a fixed ~$5–10/mo VM alternative with the same systemd flow.
- Updates after this are one command — see **Updating a running deployment**.

## systemd setup (always-on Linux host)

Unit templates live in `deploy/`. They're `%i`-templated on the run user, so
enable them as `@<user>` instances.

```bash
# 1. Put the code somewhere stable and build the venv.
sudo mkdir -p /opt/nse-data-service && sudo chown "$USER" /opt/nse-data-service
git clone <repo> /opt/nse-data-service && cd /opt/nse-data-service
python -m venv .venv
.venv/bin/pip install -e ".[dashboard,broker]"      # add ,redis if using Redis
#   (secrets, if any) -> /opt/nse-data-service/.env  (GROWW_*, etc.)

# 2. Install the unit(s).
sudo cp deploy/nse-collector.service  /etc/systemd/system/
sudo cp deploy/nse-dashboard.service  /etc/systemd/system/   # optional UI/API
sudo systemctl daemon-reload

# 3. Enable + start as your user (replace `jay`).
sudo systemctl enable --now nse-collector@jay
sudo systemctl enable --now nse-dashboard@jay               # optional

# 4. Watch it.
journalctl -u nse-collector@jay -f      # JSON logs: collector_run, catchup_*
systemctl status nse-collector@jay
```

Adjust `WorkingDirectory` / `ExecStart` paths in the unit files if you don't use
`/opt/nse-data-service`.

## WSL note

WSL2 only runs systemd if enabled (`/etc/wsl.conf` → `[boot] systemd=true`), and
the distro still stops when Windows sleeps or WSL is shut down — so WSL is not
"always-on". For a laptop, rely on the catch-up-on-start above; for guaranteed
evening/weekend coverage, use a separate always-on host.

## Updating a running deployment (continuous development)

Code is replaceable; data is durable. `data/` and `.env` are gitignored, so a
`git pull` on the server updates code **without ever touching the SQLite DB or
secrets** — the DB keeps accumulating across deploys. A new version is just:
back up DB → pull → install → migrate → restart.

```bash
# on the server, in the repo dir:
./scripts/deploy.sh jay        # 'jay' = the systemd instance user
```

That script backs up `nse.db` (keeps the last 30), `git pull --ff-only`s,
syncs deps, applies pending migrations, and restarts the services. The on-boot
catch-up recovers anything missed during the few-second restart.

Rules that keep this safe:

- **Never edit code on the server** — it's a deploy target. All changes flow
  through git (develop locally → commit → push → pull on the server). This
  avoids merge conflicts and accidental `data/` clobbering.
- **Migrations are forward-only.** Add a new `migrations/0NN_*.sql`; it applies
  once (idempotent, on boot and via `scripts/migrate.py`). There are no
  down-migrations, so `deploy.sh` snapshots the DB first — **rollback** =
  `git checkout <previous-tag>` **and** restore that `data/archive/db_backups/`
  snapshot, then restart.
- **Deploy tags, not WIP commits.** Tag releases (`git tag v0.x && git push
  --tags`) and pull those, so the server runs known-good points, not mid-feature
  state.
- **Verify after deploy** via the health dashboard — feeds should stay green;
  a freshly-broken collector shows up as stale/down.

### Optional: auto-deploy on push (GitHub Actions)

Once manual `deploy.sh` feels solid, a workflow on push to `main` can SSH to the
box and run it — turning `git push` into a deploy. Keep it gated to tags or a
`release` branch so half-finished work doesn't ship. (Ask and I'll add the
`.github/workflows/deploy.yml`.)

## Verifying coverage

The health dashboard (`/`) shows each feed's freshness; a daily feed sitting in
the **stale/down** group after its run time means a missed run. `--due --dry-run`
lists exactly which collectors the catch-up considers overdue.
