# VPS production deployment and operations

The environment for real dealership data. Independent of the Render demo. It builds on `deploy/` as it is today; items marked **[to build]** do not exist yet, so commands that use them work only after the production phase. **Nothing here has been executed.**

Targets: **RPO 15 minutes, RTO 4 hours.**

## 1. Layout

Diagram: `target-architecture.md` §8.

| Service | Image | Network | Published |
|---|---|---|---|
| `web` | Caddy + SPA | edge, internal | 80, 443 |
| `api` | backend | internal | none |
| `worker` **[to build]** | backend | internal, egress | none |
| `migrate` | backend, one-shot | internal | none |
| `db` | PostgreSQL 17 (Debian) + pgBackRest **[to build]** | internal, egress | none |

- OS: Ubuntu 26.04 LTS (released April 2026, [standard support to April 2031](https://documentation.ubuntu.com/release-notes/26.04/)) or 24.04 LTS.
- PostgreSQL 17 stays; plan a major upgrade well before its end of life (check [postgresql.org](https://www.postgresql.org/support/versioning/)).
- **Image change warning:** the stack uses `postgres:17-alpine`. A Debian image has different collation behaviour; never point it at an existing Alpine data volume. Move with dump and restore. Production has no data yet, so start on Debian.

## 2. Tiers

Estimates to be confirmed by the load tests in `performance-and-scalability.md`. A single server is appropriate at first: it is a single point of failure, and recovery within RTO is by restore.

| Tier | Assumption | Server | Database | Notes |
|---|---|---|---|---|
| Pilot | 1–5 dealers, ≤ 25 concurrent users | 2–4 vCPU, 4–8 GB, 80 GB | Same host | |
| Initial commercial | ≤ 50 dealers, ≤ 250 concurrent | 4–8 vCPU, 8–16 GB, 160–320 GB | Same host | Add a staging server |
| Growing | Beyond that, or tighter availability | 2 app servers + load balancer | Separate server (8 vCPU, 32 GB) or managed, with a standby | Object storage already external |

Move the database off when it competes for CPU or memory, when restore time threatens the RTO, or when customers need higher availability than restore-from-backup.

**Cost.** Indicative only: a shared-CPU 4 vCPU / 8 GB DigitalOcean droplet was reported at about US$48 per month ([pricing](https://www.digitalocean.com/pricing/droplets); figure from search results, regional prices not checked). Hetzner Singapore sources disagreed on price and availability. Also budget off-site storage, error tracking, uptime monitoring, email and a staging server. Choose provider and region (Singapore or India for latency, **not measured**) after legal advice on data location.

## 3. Host setup

```bash
# as root on a new server
adduser deploy && usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh      # then add authorized_keys
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
apt-get update && apt-get -y upgrade && apt-get -y install unattended-upgrades
# install Docker Engine and the compose plugin from Docker's official apt repository
```

**Firewall:** use the provider's cloud firewall. Allow SSH from administrator addresses only; 80 and 443 from [Cloudflare's ranges](https://www.cloudflare.com/ips/) only. Do not rely on `ufw` alone: Docker's published ports bypass it. Verify from outside that 5432 and 8000 are closed.

**Cloudflare:** DNS proxied; TLS mode *Full (strict)*; basic rate-limit rules on `/api/v1/auth/*`.

## 4. Caddy and client addresses **[to build]**

```caddy
{
	servers {
		trusted_proxies static <cloudflare ranges>
		client_ip_headers Cf-Connecting-Ip
		trusted_proxies_strict
	}
}

{$SITE_ADDRESS} {
	encode zstd gzip
	request_body { max_size 12MB }
	handle /api/* {
		reverse_proxy api:8000 {
			header_up X-Forwarded-For {client_ip}
		}
	}
	# security headers and static files as in deploy/Caddyfile, plus CSP
}
```

Standard Caddy ships only the `static` proxy source, so the ranges are listed and reviewed quarterly. Uvicorn trusts only the internal network's subnet, not `*`. **Test:** a forged `X-Forwarded-For` from outside never appears in audit rows.

## 5. Compose hardening **[to build]**

- Networks `edge` and `internal` (`internal: true`); only `db`, `worker` and `api` get an egress network where needed.
- `mem_limit`, `cpus`, `pids_limit`; `restart: unless-stopped`.
- Non-root users in every image (web and backup run as root today); `read_only` root filesystem where possible; `no-new-privileges`.
- Logs: `json-file` with `max-size: 20m`, `max-file: 5`.
- Images pinned by version, built in CI, tagged with the commit, pushed to a private registry; the server does not build.

## 6. Secrets

| Secret | Held by |
|---|---|
| Database passwords: owner, app, platform, backup | Files under `/opt/tradesphere/secrets`, mode 600 |
| `JWT_SECRET`, MFA encryption key | Same |
| Backup encryption passphrase | Server **and** an offline copy held by two people. Losing it loses every backup |
| Object storage keys | Server key may write but not delete history |

Application support for `*_FILE` variables is **[to build]**. Nothing secret goes in git or images. Rotate on staff change and yearly.

## 7. Database roles

Four roles from `database-transformation.md` §6, created by `deploy/db/init` for a new volume and by an idempotent provisioning script otherwise **[to build]**. Do not interpolate passwords into SQL text as `01-app-role.sh` does today. `POSTGRES_USER` (superuser) is used only for provisioning.

## 8. Release and rollback

Migrations run **once**, by the `migrate` service, before new containers start. API containers never migrate. Schema changes are expand-then-contract across two releases, so the previous image works with the new schema.

```bash
cd /opt/tradesphere/deploy
export TAG=<commit>
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml exec --user postgres db pgbackrest --stanza=main --type=diff backup   # [to build]
docker compose -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.prod.yml up -d api worker web
curl -fsS https://<site>/api/health
docker compose -f docker-compose.prod.yml run --rm api python -m app.cli reconcile --all-tenants  # flag [to build]
```

**Rollback:** `export TAG=<previous>` and `up -d api worker web`. Do **not** downgrade the schema on a live system. If data was damaged, restore (§10) and tell affected dealers what was lost.

A short interruption during restart is expected; zero-downtime is not promised. With several API instances: sessions, limits, idempotency and numbering live in PostgreSQL, so no sticky sessions are needed; the connection budget grows with each instance.

## 9. Backups **[to build]**

Replaces the nightly dump (TS-06).

| Item | Design |
|---|---|
| Tool | [pgBackRest](https://pgbackrest.org/) **2.59.3 or later** (earlier versions had a weak-randomness fault in encryption). The project was declared unmaintained in April 2026 and resumed in May with sponsors: review this dependency yearly; WAL-G is the alternative |
| Continuous | WAL archiving with `archive_timeout = 300` (keeps loss inside 15 minutes) |
| Full / differential | Weekly / daily |
| Encryption | `repo1-cipher-type=aes-256-cbc`; cannot be changed after the stanza is created |
| Off-site | S3-compatible bucket in another provider, versioned with object lock |
| Retention | 4 weekly fulls with their WAL; monthly logical dump kept 12 months |
| Logical dump | Nightly `pg_dump -Fc` as the backup role, encrypted before upload, for single-tenant recovery. Roles saved with `pg_dumpall --roles-only` |
| Uploaded files | Bucket versioning |
| Verification | `pgbackrest check` and `verify` daily; automated restore weekly on a scratch host with `reconcile`; timed manual drill monthly |
| Alerts | Backup or archive failure, and "no successful backup in 26 hours" |

## 10. Restore

```bash
docker compose -f docker-compose.prod.yml stop api worker
docker compose -f docker-compose.prod.yml stop db
docker compose -f docker-compose.prod.yml run --rm --user postgres db \
  pgbackrest --stanza=main --delta --type=time --target="<UTC time>" --target-action=promote restore
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml run --rm api python -m app.cli reconcile --all-tenants
docker compose -f docker-compose.prod.yml up -d api worker
```

**One tenant only:** never restore over production. Restore to a scratch database at the wanted time, export that tenant, review with the dealer, and re-enter or import corrections.

| Disaster | Recovery |
|---|---|
| Bad release | §8 rollback |
| Accidental data damage | Point-in-time restore, or single-tenant procedure |
| Server lost | New server from this document, restore from off-site, update DNS |
| Provider account lost | Backups are with a second provider; same procedure |
| Secrets leaked | Rotate, revoke sessions, review audit |

## 11. Monitoring and alerts **[to build]**

| Signal | Tool | Alert |
|---|---|---|
| Site up | External uptime check on `/api/health` and the home page | 2 failures |
| Errors | Error tracking (Sentry or a compatible self-hosted service), without personal data | New error, rate spike |
| Disk, memory, CPU | Host agent | Disk 80 % |
| Database | Connections, deadlocks, slow queries (`pg_stat_statements`), WAL archive lag | Any deadlock; lag over 10 min |
| Jobs | Oldest job age, failures | Over 10 min |
| Certificates | Uptime service | 14 days |
| Logs | Shipped to a central store, 30 days | |

Add `/api/live` (process) and `/api/ready` (database and migration head). A dashboard (Prometheus and Grafana, or hosted) can follow; alerts come first.

## 12. Upgrades and routine

Weekly: review alerts and job failures. Monthly: restore drill, dependency and image updates on staging first. Quarterly: access review, Cloudflare ranges, secret hygiene. Operating system security updates automatic, reboots in an announced window. PostgreSQL minor updates monthly; major upgrades by rehearsed dump or `pg_upgrade` on staging.

## 13. Incident response

Process in `security-and-compliance.md` §10. Keep outside the server: this runbook, contacts, provider logins, the backup passphrase procedure.

## 14. Acceptance

Built from an empty server by following this document; ports scan clean; deploy and rollback rehearsed; timed restore within RTO losing under 15 minutes; each alert triggered on purpose.
