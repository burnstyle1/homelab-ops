# Containers couldn't talk to their own host (macvlan)

**Impact:** Containers on a Docker macvlan network worked fine from every other machine but couldn't reach the host they were running on, and the host couldn't reach them. Broke anything that needed host-to-container traffic, mainly the proxy and monitoring.
**Affected:** Unraid Docker, macvlan/br0, NPM, monitoring stack.

## What happened
Containers were reachable from everywhere on the LAN except the one machine actually running them. Host couldn't hit them, they couldn't hit the host. Everything showed as "up," it just couldn't talk to itself.

## Digging in
The specific shape of it gave it away. Not "container unreachable," but "container unreachable *only* from its own host." That's not a firewall or routing mistake, that's macvlan doing exactly what it's designed to do.

macvlan hands each container its own MAC so it shows up on the network like a separate physical box, which is why the rest of the LAN reaches it fine. But the kernel deliberately blocks traffic between the macvlan parent interface and its own children. It's not a bug you route around, it's how the driver works. There's a shim-interface workaround but it's fiddly and I've watched it break before.

## Root cause
Host-to-container isolation is just how macvlan behaves. I'd picked a network mode that structurally can't do the one thing I needed, which was let the host talk to the containers.

## Fix
- Moved the affected containers over to ipvlan.
- ipvlan shares the host's MAC instead of handing out one per container, so the host isolation goes away but the containers are still reachable on the LAN.
- Proxy and monitoring came back without any shim-interface hack.

## Prevention
- Wrote down why ipvlan over macvlan so I'm not rediscovering it mid-outage next time. Short version: they differ on MAC handling and host isolation, and macvlan can also annoy some switches and APs. If the host needs to reach the containers, use ipvlan.
- Default to ipvlan for anything that has to talk to the host. Only reach for macvlan when I genuinely need separate MACs and don't care about host reachability.
