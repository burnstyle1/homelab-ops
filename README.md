# Homelab Operations & Incident Record

Self-hosted infrastructure that runs the production side of a few public-facing
community platforms, somewhere around 30,000 members total. Multiple hosts,
segmented network, monitoring, the usual. I run all of it solo.

This isn't a list of everything I own. It's a handful of things that actually
broke (plus one security cleanup), written up the way I'd write them at work:
what happened, how I chased it down, what I changed so it wouldn't happen again.

**Stack:** Proxmox + PBS · Unraid · Docker · pfSense · UniFi · Pi-hole (HA) ·
Grafana / Prometheus / netdata / Uptime Kuma · Tailscale · Cloudflare ·
CrowdSec · Nginx Proxy Manager · Ollama / Open WebUI (local LLM)

## IncidentsHomelab Operations & Incident Record

Self-hosted infrastructure that runs the production side of a few public-facing community platforms, somewhere around 30,000 members total. Multiple hosts, segmented network, monitoring, the usual. I run all of it solo.

This isn't a list of everything I own. It's a handful of things that actually broke (plus one security cleanup), written up the way I'd write them at work: what happened, how I chased it down, what I changed so it wouldn't happen again.

Stack: Proxmox + PBS · Unraid · Docker · pfSense · UniFi · Pi-hole (HA) · Grafana / Prometheus / netdata / Uptime Kuma · Tailscale · Cloudflare · CrowdSec · Nginx Proxy Manager · Ollama / Open WebUI (local LLM)

Incidents
#	Incident	Type	Short version
01	Pi-hole 45s DNS drops	Availability	Query DB grew unbounded; every FTL restart meant a 45s DNS blackout on a Pi 3.
02	WordPress compromise → static rebuild	Security	Wiped a popped CMS and rebuilt static instead of cleaning it.
03	Account attack that looked like a breach	Investigation	Ruled out a server breach, traced it to a compromised account in the community.
04	Perimeter cleanup	Hardening	Found and closed exposure I'd let creep in over time.
05	macvlan → ipvlan	Networking	Containers couldn't reach their own host. macvlan working as designed.
06	Accessibility iMac crash-loop	Endpoint	A crash-looping speech daemon made the Mac crawl. The "slow" disk everyone blamed was innocent.
Also here
Architecture overview — hosts, how the network's split up, why.
Runbooks: Pi-hole HA failover · Proxmox host migration · AI inference box
On the redactions

This is scrubbed on purpose. Internal addressing is shown as generic RFC1918, anyone else involved is anonymized, and there are no secrets or credentials in here. Nothing in this repo relates to any employer system.

| # | Incident | Type | Short version |
|---|----------|------|---------------|
| 01 | [Pi-hole 45s DNS drops](incidents/01-dns-outage-ftl-bloat.md) | Availability | Query DB grew unbounded; every FTL restart meant a 45s DNS blackout on a Pi 3. |
| 02 | [WordPress compromise → static rebuild](incidents/02-wordpress-compromise-static-rebuild.md) | Security | Wiped a popped CMS and rebuilt static instead of cleaning it. |
| 03 | [Account attack that looked like a breach](incidents/03-credential-attack-social-graph.md) | Investigation | Ruled out a server breach, traced it to a compromised account in the community. |
| 04 | [Perimeter cleanup](incidents/04-perimeter-security-audit.md) | Hardening | Found and closed exposure I'd let creep in over time. |
| 05 | [macvlan → ipvlan](incidents/05-docker-macvlan-ipvlan-migration.md) | Networking | Containers couldn't reach their own host. macvlan working as designed. |

## Also here

- [Architecture overview](architecture/overview.md) — hosts, how the network's split up, why.
- Runbooks: [Pi-hole HA failover](runbooks/pihole-ha-failover.md) · [Proxmox host migration](runbooks/proxmox-host-migration.md) · [AI inference box](runbooks/ai-inference-box.md)

## On the redactions

This is scrubbed on purpose. Internal addressing is shown as generic RFC1918,
anyone else involved is anonymized, and there are no secrets or credentials in
here. Nothing in this repo relates to any employer system.
