# Perimeter cleanup: getting stuff off the public edge before it bit me

**Impact:** Not an incident. Just went through everything exposed to the internet and shut down what didn't need to be there.
**Affected:** pfSense, Nginx Proxy Manager, Vaultwarden, Tailscale.

## Why
Nothing prompted this. It's that when you stand services up over a couple of years, exposure creeps. Old rules, things you opened "just for now," stuff you forgot was even reachable. I'd rather find that myself than have someone else find it.

## What I found
Went through the edge one thing at a time asking "does this need to be exposed, and to who." A few answers were no:

- SSH on pfSense was reachable. No reason for the firewall's management to sit on the edge.
- A pile of stale firewall rules, holes pointing at services that didn't exist anymore.
- NPM proxy targets pointed at addresses that could change, so the proxy could quietly start sending traffic to the wrong place when a container IP moved.
- Vaultwarden was reachable from the internet. A password vault is about the last thing that should be publicly exposed.

## Fix
- Turned off SSH on pfSense.
- Deleted the dead firewall rules.
- Repointed NPM at stable host-mapped IPs so proxying is predictable.
- Restricted Vaultwarden to internal only.

## Prevention
- Baseline I hold to now: management interfaces don't face the internet, and secrets services are reachable internal only.
- Wrote down the cleaned-up state so I've got something to compare against when things drift later.
- Re-check exposure whenever I publish something new instead of assuming the edge stayed the way I left it.
