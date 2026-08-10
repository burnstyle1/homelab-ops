# Runbook: Proxmox host / workload migration

**Point of this:** move workloads to new hardware with as little downtime as
possible and a way back if it goes sideways. Based on a migration I did onto a
new host with storage rebuilt on managed (non-passthrough) disks.

## Before you start
- [ ] Current, actually-restored backup of everything you're moving. A backup
      you've never test-restored is a guess, not a backup.
- [ ] Flag anything on raw/passthrough disks. Those don't back up or migrate
      cleanly, convert them to managed storage first.
- [ ] Write down current IPs, hostnames, and any static reservations.

## Doing it
1. Get the new host ready: base OS/hypervisor, storage, networking to match the
   old layout (or deliberately better).
2. Rebuild storage on managed disks, not passthrough, so backups stay
   consistent afterward.
3. Move workloads one at a time. Validate each before touching the next.
4. Repoint anything that depends on them (reverse proxy, DNS, monitoring) at the
   new addresses.
5. Leave the old host powered and intact until the new one's run clean for a set
   soak period. That's your rollback.

## After
- [ ] Backups repointed and a fresh one taken on the new host.
- [ ] Monitoring shows everything healthy.
- [ ] Retention/verify jobs re-enabled against the new host.
- [ ] Only decommission the old host once the soak period's passed.

## Lesson from doing this
One workload was on a raw passthrough disk and its backups were quietly broken.
Convert passthrough to managed *before* you're relying on it, because finding
out during a restore is the worst possible time.
