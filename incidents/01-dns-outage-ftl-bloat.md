# Pi-hole drops DNS for ~45s on every restart

**Impact:** DNS would go dead across the whole network for about 45 seconds every time the primary Pi-hole restarted. Showed up to everyone else as random "can't reach this site" errors that fixed themselves a moment later.
**Affected:** Primary Pi-hole (Raspberry Pi 3), FTL query database, HA sync to the secondary.

## What happened
Every so often the house would lose DNS for a few seconds and then it'd come back on its own. No obvious trigger. Nothing was actually down either, which was the annoying part. Uptime Kuma showed the primary flapping rather than dead, and a flap is harder to catch than a clean failure.

## Digging in
Ruled out upstream first. pfSense and the ISP were fine, and the secondary Pi-hole never missed a query, so whatever this was, it was specific to the primary.

Started paying attention to *when* the drops happened and they lined up with FTL restarts. That changed the question. The box wasn't failing, it was just taking a long time to come back up and answer queries.

Looked at the FTL database. `pihole-FTL.db` was sitting at 869 MB, around 13.6 million rows. On a Pi 3 reading off an SD card, loading a database that size at startup took roughly 45 seconds, and FTL doesn't answer anything until it's finished loading. So every restart handed me a 45-second DNS blackout.

## Root cause
Query logging was keeping way more history than it needed to and nothing was capping the database. It just grew until the Pi 3 couldn't load it fast enough. Not really a bug, more a slow-motion hardware problem I'd been ignoring.

## Fix
- Cut query-log retention down to a sane window so the DB stops growing forever.
- Added zram so it isn't thrashing memory while it loads.
- Restart time went from ~45s to basically instant.

## Prevention
- Retention cap lives in the config now instead of being clicked into the UI where I'd forget it existed.
- Added a monitor on FTL startup time specifically. The old up/down check couldn't see "up but takes 45 seconds to answer," which was the entire failure mode.
- Honest root problem: a Pi 3 on an EOL OS doing production DNS. Reflashing/replacing it is on the list. The retention fix just bought breathing room.
