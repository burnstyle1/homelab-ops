# Runbook: Snipe-IT test deployment (CasaOS VM + Cloudflare Tunnel)

| | |
|---|---|
| **Date** | 2026-09-30 |
| **Status** | Test instance (fake data only), running |
| **Host** | Proxmox → CasaOS VM 102 (public-facing services VLAN) |
| **URL** | `https://assets.example.com` (placeholder; Cloudflare Tunnel + Access) |
| **Stack** | Docker Compose: `snipe/snipe-it:latest` (v8.0.0-pre at time of install) + `mariadb:11` |

## Purpose

Evaluate Snipe-IT as an asset-tagging / inventory system. Specifically testing:

- Bulk-created placeholder assets + sheet-printed QR labels ("stick first, document later")
- Phone scan → asset page → edit, reachable from off-network
- CSV import, label layout, file attachments, archived/disposal statuses

> **Data rule:** this instance lives on a disposable, internet-reachable VM. Fake data only. No real hostnames, serials, usernames, or inventory exports.

## Architecture

```
Phone / browser
   │ HTTPS
   ▼
Cloudflare edge (Access policy)
   │ tunnel
   ▼
cloudflared (CasaOS VM, host networking)
   │ HTTP  <casaos-vm-ip>:18085
   ▼
snipeit-app (Apache/PHP 8.3) ──► snipeit-db (MariaDB 11)
        compose network: snipeit_default
```

TLS terminates at Cloudflare. The origin hop is plain HTTP, so Snipe-IT must trust the proxy's `X-Forwarded-Proto` header.

## Deployment

### 1. Compose file

Deployed from the CLI, **not** the CasaOS importer (see Issue 1).

```bash
mkdir -p ~/snipeit && cd ~/snipeit
nano docker-compose.yml
docker compose config > /dev/null && echo "YAML ok"   # catches paste/indent damage
docker compose up -d
docker compose ps
```

```yaml
name: snipeit

services:
  db:
    image: mariadb:11
    container_name: snipeit-db
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: <generate: openssl rand -hex 16>
      MARIADB_DATABASE: snipeit
      MARIADB_USER: snipeit
      MARIADB_PASSWORD: <generate: openssl rand -hex 16>
    volumes:
      - /DATA/AppData/snipeit/db:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 5s
      timeout: 5s
      retries: 20

  app:
    image: snipe/snipe-it:latest
    container_name: snipeit-app
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "18085:80"
    environment:
      APP_ENV: production
      APP_DEBUG: "false"
      APP_KEY: <generate: echo "base64:$(openssl rand -base64 32)">
      APP_URL: https://assets.example.com
      APP_TIMEZONE: America/New_York
      APP_LOCALE: en-US
      APP_TRUSTED_PROXIES: "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
      APP_FORCE_TLS: "true"
      SECURE_COOKIES: "true"
      PHP_UPLOAD_LIMIT: "10"
      DB_CONNECTION: mysql
      DB_HOST: db
      DB_PORT: "3306"
      DB_DATABASE: snipeit
      DB_USERNAME: snipeit
      DB_PASSWORD: <same as MARIADB_PASSWORD>
    volumes:
      - /DATA/AppData/snipeit/data:/var/lib/snipeit
```

Notes:
- Bind mounts under `/DATA/AppData/snipeit` (CasaOS convention) so teardown is a single `rm -rf` and backups can see the data.
- Host port `18085`: only the left side of the mapping matters for collisions.
- Never commit real secrets. Values above are placeholders.

### 2. First boot

First start runs the full migration history before Apache starts. Expect **several minutes** of `Connection reset by peer` and Cloudflare **502** while this happens. Watch progress:

```bash
docker logs -f snipeit-app    # wait for supervisord "apache entered RUNNING state"
curl -sI http://localhost:18085 | head -1    # expect 302 → /setup
```

### 3. Cloudflare

- Tunnel public hostname: `assets.example.com` → `http://<casaos-vm-ip>:18085` (service type **HTTP**, not HTTPS: the container only speaks plain HTTP).
- Cloudflare Access application in front of the hostname.

### 4. Setup wizard choices

| Setting | Value | Why |
|---|---|---|
| Asset tag prefix | `TEST-` | Test labels can never be mistaken for real ones |
| Zerofill length | `5` | `TEST-00001` … 99,999 capacity |
| Auto-increment tags | On | Required for bulk placeholder workflow |
| Multiple Companies Support | Off | Adds permission scoping; not needed |

### 5. App configuration

- **Settings → Barcodes:** 2D (QR) only. 1D wastes label width and only encodes the tag.
- **Settings → Labels:** visible fields = **Asset Tag only**. Model/serial/name would go stale once records are filled in.
- **Status labels:** `Tagged - Needs Info` (type **Pending**, amber, not deployable), plus Archived-type statuses: `Recycled`, `Destroyed`, `Sold`, `Donated`, `Lost/Stolen`.

## Issues encountered

### Issue 1: CasaOS importer breaks multi-container DNS
**Symptom:** pre-flight: `getaddrinfo for db failed: Name or service not known`.
**Cause:** CasaOS "Custom Install" placed containers on Docker's default `bridge` network, which has no container-name DNS. It also may drop `depends_on` health conditions.
**Fix:** remove the app in CasaOS, `rm -rf /DATA/AppData/snipeit`, deploy with `docker compose up -d` from the CLI. Compose creates `snipeit_default` with name resolution.
**Lesson:** use the CasaOS importer for single-container apps only.

### Issue 2: Pre-flight reports `http://` instead of `https://`
**Symptom:** "Snipe-IT thinks your URL is https://… but your real URL is http://…"
**Cause:** Snipe-IT ignores `X-Forwarded-Proto` unless the request comes from a trusted proxy. `APP_TRUSTED_PROXIES: "*"` appears not to work as "trust all" (likely split into a list containing a literal `*`).
**Fix:** use explicit CIDRs (`10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), recreate the container.
**How to see the real source IP:** access log is a file, not stdout:
```bash
docker exec snipeit-app tail -3 /var/log/apache2/access.log
```
Tunnel traffic arrived from the VM's own LAN IP (cloudflared uses host networking).
**Note:** the pre-flight URL row is only shown on `/setup` and is reportedly unreliable behind reverse proxies. Verify functionally instead (see Verification).

### Issue 3: Cloudflare 502 after redeploy
**Cause:** migrations still running (Issue-free, just slow). ~200 ms+ per migration on this VM suggests slow storage.
**Fix:** wait. Confirm with `docker logs -f snipeit-app`.

### Issue 4: CSV import "This file has no data rows"
**Cause:** hand-made CSV saved with bad line endings/encoding.
**Fix:** generate the CSV programmatically (plain ASCII), upload without opening in Excel. Status names must match exactly (watch en dash vs hyphen). Status labels must exist before import; models/categories/manufacturers are auto-created.

Placeholder import format:
```csv
Asset Tag,Model,Category,Manufacturer,Status
TEST-00001,Unassigned Tag,Placeholder,Unknown,Tagged - Needs Info
```

### Issue 5: Upload limit shows 2 MB
**Cause:** PHP default `upload_max_filesize`. Snipe-IT just displays PHP's limit; there is no in-app setting.
**Fix:** `PHP_UPLOAD_LIMIT: "10"` (MB) in the app environment, recreate. Fallback if the env var is ignored: mount an ini file at `/etc/php/8.3/apache2/conf.d/99-uploads.ini` with `upload_max_filesize`/`post_max_size`.
**Ceiling:** Cloudflare free plan caps request bodies at 100 MB regardless.

## Verification checklist

- [ ] Log in, log out, log back in: no redirect loop
- [ ] Asset page links and QR codes use `https://assets.example.com`
- [ ] Phone **off Wi-Fi**: scan QR → Cloudflare Access → asset page
- [ ] Second scan does not re-prompt for login
- [ ] Edit a placeholder (model, serial, note, status) and time it: this × asset count = real rollout cost
- [ ] `/.env` returns 403 (pre-flight self-test confirms this)

## Operations

```bash
cd ~/snipeit
docker compose ps
docker compose logs --tail 50 app
docker compose up -d            # apply compose changes (recreates changed services)
docker compose restart app
```

## Teardown

```bash
cd ~/snipeit
docker compose down --rmi all
sudo rm -rf /DATA/AppData/snipeit ~/snipeit
```
Then in Cloudflare Zero Trust: delete the tunnel public hostname **and** the Access application (otherwise a dangling DNS record remains).

## Notes for a production deployment

- Run on a proper VM with backups, not spare hardware or a personal/public host.
- Use a short, **building-independent** internal hostname. QR codes bake in `APP_URL` permanently.
- Pin the image to a stable release instead of `latest` (this pulled a `-pre` build).
- Regenerate all secrets; nothing from the test instance carries over.
- Placeholder status must be Pending so blank tags can't be checked out.
- Archive, don't delete, disposed assets; attach per-serial certificates of destruction.
