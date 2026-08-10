# Runbook: Pi-hole HA failover & sync

**Point of this:** keep DNS up if either resolver dies, and keep the two boxes
in sync.

## How it's set up
- Two resolvers. A **primary** (where all config changes happen) and a
  **secondary**.
- DHCP hands out both, so losing one doesn't take DNS down.
- Config syncs primary → secondary on a schedule (full sync). The secondary
  gets overwritten every run, so I never touch it directly.

## Rules
1. All changes go on the primary. Editing the secondary is pointless, the next
   sync wipes it.
2. After a change, check it actually made it to the secondary before calling it
   done.
3. If the primary's down for maintenance, the secondary keeps DNS alive. Just
   remember it's stale-by-design and shouldn't be edited.

## Checks
- [ ] Both resolvers handed out over DHCP.
- [ ] Secondary answers on its own with the primary stopped.
- [ ] Last sync run succeeded (check the timestamp/logs).
- [ ] Primary's query-log retention is capped. See incident 01 — unbounded
      growth here is what caused the 45s startup outage.

## Things to keep in mind
- The primary was running on a Pi 3 with an EOL OS, reflash/replace is tracked
  separately. Failover hides a single-node problem, it doesn't fix it.
