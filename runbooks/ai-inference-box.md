# AI Inference Box (`chat`) — Build & Operations Runbook

**Host:** `chat` — `192.168.50.143` (VLAN 50)
**Purpose:** Always-on local LLM inference serving a RAG chatbot for *The Secret* treasure-hunting community, plus a private chat hub and a future read-only ops reader.
**Status:** Operational — engine + private hub done; Discord bot and ops reader pending.
**Last updated:** 2026-08-21

---

## 1. Overview

One inference box, one model endpoint, several thin front-ends hung off it. The box is the engine; each use case is a separate steering wheel pointed at the same Ollama endpoint. Front-ends never share tool sets — the community bot gets RAG only and zero infrastructure access.

**Consumers**
- Private Open WebUI chat + document RAG hub (built).
- Discord RAG bot for the ~18k-member community — RAG-only, **no infra tools** (pending).
- Read-only ops reader — observability summaries, no command execution (pending).

**Design rules**
- No coding workloads (offloaded to a hosted assistant; a 12 GB card is the wrong tool for it).
- No `sudo`, no shell/command execution by any agent. Read-only only.
- Isolation is structural, not prompt-based: the bot can't touch the network because it holds no tools that can, not because it was told not to.

---

## 2. Hardware & OS baseline

| Component | Detail |
|---|---|
| Motherboard | Gigabyte GA-Z270X-Gaming K7 |
| RAM | 64 GB DDR4 |
| GPU | NVIDIA RTX 3060 12 GB (GA106, LHR — irrelevant to inference) |
| OS | Ubuntu Server 24.04.4 LTS, bare metal |
| NIC | `enp0s31f6` |
| Driver / CUDA | NVIDIA 595.84 / CUDA 13.2 |

**Capacity note:** 12 GB VRAM = one ~14B model resident at a time (Q4 ≈ 9.3 GB weights). Idle draw ~15 W / 0% util; the GPU spikes to ~100% only for the seconds it is generating, then returns to idle.

---

## 3. Build procedure (reproducible from bare metal)

### 3.0 BIOS
- **Disable Secure Boot.** Otherwise the NVIDIA kernel module won't load and the GPU silently disappears with no obvious error.

### 3.1 OS
- Install Ubuntu Server 24.04 LTS, minimal, with OpenSSH server.
- Assign a fixed address (DHCP reservation on pfSense) on VLAN 50.

### 3.2 NVIDIA driver
```bash
sudo apt update && sudo apt full-upgrade -y
sudo ubuntu-drivers install
sudo reboot
# verify: expect the 3060 and 12288 MiB
nvidia-smi
```
Card seen on the bus (sanity check before driver work): `lspci | grep -i nvidia`.

### 3.3 Ollama (native, systemd service)
```bash
curl -fsSL https://ollama.com/install.sh | sh   # auto-detects the GPU
ollama pull qwen3:14b
```

### 3.4 Ollama tuning (systemd override)
Required for two reasons: (a) the Dockerized front-end must reach Ollama, and (b) fitting a 16K context on 12 GB.
```bash
sudo systemctl edit ollama
```
Add under `[Service]`:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
ss -tlnp | grep 11434   # expect *:11434, not 127.0.0.1:11434
```
- `OLLAMA_HOST=0.0.0.0` — lets the container reach Ollama (loopback-only can't serve a bridged container).
- `OLLAMA_FLASH_ATTENTION=1` — faster, lower memory, no quality cost; prerequisite for KV-cache quantization.
- `OLLAMA_KV_CACHE_TYPE=q8_0` — halves KV-cache memory; this is what buys 16K context at 100% GPU instead of spilling to CPU.

### 3.5 Custom model (context bump)
```bash
cat > qwen3-chat.Modelfile << 'EOF'
FROM qwen3:14b
PARAMETER num_ctx 16384
EOF
ollama create qwen3-chat -f qwen3-chat.Modelfile
# verify: PROCESSOR 100% GPU, CONTEXT 16384, ~10 GB VRAM
ollama run qwen3-chat "warm" >/dev/null; ollama ps
```

### 3.6 Docker + Open WebUI
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # then log out/in
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui --restart always \
  ghcr.io/open-webui/open-webui:main
```
- Browse to `http://192.168.50.143:3000`. **First account created = admin** — claim it immediately.
- In Open WebUI set the Ollama connection URL to `http://host.docker.internal:11434` (Admin Panel → Settings → Connections). This keeps Ollama reachable by the container without extra exposure.

---

## 4. Configuration reference

| Item | Value |
|---|---|
| Ollama override | `/etc/systemd/system/ollama.service.d/override.conf` |
| Ollama endpoint (host) | `127.0.0.1:11434` (bind `0.0.0.0`) |
| Ollama endpoint (from container) | `http://host.docker.internal:11434` |
| Open WebUI | `http://192.168.50.143:3000`, container `open-webui`, volume `open-webui` |
| Base model | `qwen3:14b` (4K default context, thinks by default) |
| Tuned model | `qwen3-chat` (16K context) |

### Thinking mode
Qwen3 is a hybrid reasoning model — it thinks (and narrates) by default. Disable **per request** via the top-level Ollama API field, not a prompt hack:
```bash
curl http://localhost:11434/api/chat -d '{
  "model": "qwen3-chat",
  "messages": [{"role":"user","content":"hi"}],
  "think": false,
  "stream": false
}'
```
In Open WebUI, set `think (Ollama) = Off` **at the model-preset level** (Workspace → Models), not in the per-chat Controls panel — the per-chat panel reverts to Default (thinking on) on every new chat.

---

## 5. RAG corpus — *The Secret*

**Source:** 12treasures.com page data, 118 pages, exported to `.xlsx` (one row per page: English text, a cover-to-cover Japanese-edition translation, scanned page-image URLs).

**Pipeline:** `convert_secret.py` emits one Markdown doc per page per edition into `secret_corpus/english/` and `secret_corpus/japanese/`. Current output: 113 English + 91 Japanese docs.

**Critical formatting lesson (v1 → v2):**
> v1 wrote each page with a Markdown H1 (`# The Secret — Page N: TITLE`). Open WebUI's header-aware splitter isolated that heading into its **own content-less chunk**. That chunk matched "page N" queries perfectly but carried no text, so page lookups returned "just the title" while the actual page body sat in a different chunk that never got retrieved.
>
> **Fix:** fold the page identifier inline into the body prose (no H1), so the page number travels with the content. See `convert_secret.py`.

**Corpus scope:** book **text only** — verses, story, creature entries. It contains **no** puzzle solutions, so the bot has no casque-location opinions to leak. This is intentional and safe for a public bot. Community theory/analysis (from the Obsidian vault) is to be layered in **later**, likely as a **separate** knowledge base so the "safe book text" KB stays clean.

**Image caveat:** *The Secret* is a visual puzzle (12 paintings). Text RAG conveys only what is written; painting interpretation must come from analysis notes, not the page images (which are binary and unreadable to the model). Image URLs are kept as metadata so answers can link the scan.

### Retrieval settings — Admin Panel → Settings → Documents
- **Top K:** 5–6 (query-time setting; no re-index needed to change).
- **Relevance threshold:** 0 (a stray threshold silently drops correct-but-borderline chunks).
- **Hybrid Search (BM25):** available, not enabled — the lever for keyword/exact-term matching.
- Changing **chunk size / embedding model** forces a full re-index; changing Top K does not.

---

## 6. Model preset — "Secret Librarian"

Built in Workspace → Models (that page lists custom **presets**, not base models — empty is expected until you Create one).

- **Base model:** `qwen3-chat` (inherits 16K context)
- **Advanced params:** `think = Off`, `num_ctx = 16384`
- **Knowledge:** attach the (v2) Secret knowledge base
- **Visibility:** Private KB, attached deliberately to this preset

### System prompt (grounding guardrail)
```
You are a knowledgeable guide to the 1982 puzzle book "The Secret" by Byron Preiss, serving a treasure-hunting community.

Answer ONLY from the book text provided to you in context. When you use it, cite the page number.

If the provided text does not contain the answer, say so plainly — "That's not in the book text I have" — and stop. Do NOT guess, and do NOT fill gaps from general knowledge.

Never invent or speculate about casque locations, puzzle solutions, or which image pairs with which verse. The book text does not contain solutions, and this community relies on accuracy. Offering a made-up solution is worse than saying you don't know.

When it matters, distinguish the English original edition from the Japanese edition. Keep answers grounded, concise, and useful to someone actually working the hunt.
```

---

## 7. Operations & verification

**Health checks**
```bash
nvidia-smi                         # GPU present, VRAM in use
ollama ps                          # model resident, PROCESSOR 100% GPU, CONTEXT 16384
curl -I http://localhost:3000      # Open WebUI serving (200/307 = alive)
docker ps                          # open-webui Up / (healthy)
ss -tlnp | grep 11434              # *:11434
```

**Expected healthy state:** `qwen3-chat` resident at ~10 GB VRAM, 100% GPU, 16384 context; GPU idle (~15 W / 0% util) between queries.

**Restart services**
```bash
sudo systemctl restart ollama
docker restart open-webui
```

---

## 8. Troubleshooting (issues actually encountered)

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` blank / model runs on CPU | Secure Boot on, or driver not built | Disable Secure Boot; reinstall driver; reboot |
| Open WebUI model dropdown empty | Ollama bound to `127.0.0.1`; container can't reach it | Set `OLLAMA_HOST=0.0.0.0` (§3.4); point Open WebUI at `host.docker.internal:11434` |
| `ollama ps` shows `X%/Y% CPU/GPU` split | Model + KV cache exceeded 12 GB | Enable flash attention + `OLLAMA_KV_CACHE_TYPE=q8_0` (§3.4) |
| Context stuck at 4096 through the hub | Open WebUI overriding `num_ctx` | Set `num_ctx=16384` on the model preset; verify via `ollama ps` after a hub query |
| Model narrates "Okay, the user wants…" | Thinking mode on | Set `think=Off` at the **preset** level; API callers pass `"think": false` |
| Page-number query returns "just the title" | v1 corpus H1 heading became a content-less magnet chunk | Re-ingest v2 corpus (page number inline, no H1) |
| Page-number lookups generally unreliable | Semantic RAG is weak at identifier lookup | Use content questions; if needed, enable Hybrid Search and/or `ENABLE_KB_EXEC=True` |
| Workspace → Models empty | That page lists custom presets, not base models | Expected; use Create |

---

## 9. Known limitations
- **Page-number lookup** is inherently weak in semantic RAG. Content/concept questions are the reliable path.
- **Corpus = book text only.** No puzzle solutions (by design). Painting interpretation requires future analysis notes.
- **Single 14B resident.** One model in VRAM at a time; an always-on bot and interactive chat contend for the card unless a small second model is used for the bot.

---

## 10. Security notes
- **Ollama is exposed on VLAN 50** (`0.0.0.0:11434`) — required for the container. **TODO:** pfSense rule restricting `192.168.50.143:11434` to the box itself.
- Box lives on **VLAN 50** (isolated services segment) — correct home for an always-on box with a future public-facing bot.
- **Knowledge base is private**, attached deliberately to the preset — not "public."
- **No agent holds infra tools or shell access.** Community bot is RAG-only. Ops reader (future) is read-only.

---

## 11. Open TODOs
1. Re-upload **v2 corpus** to a fresh knowledge base (delete the v1 KB — it has the heading-magnet docs).
2. Lock **think-off** at the preset level and validate on a new chat.
3. Build & test the **Secret Librarian** preset with content questions.
4. **pfSense rule:** restrict `192.168.50.143:11434` to the box only.
5. Build the **Discord bot** (RAG-only, `"think": false`, no infra tools).
6. Layer in **community analysis** as a separate KB once the vault is cleaned.
7. Build the **read-only ops reader** (Prometheus / Uptime Kuma / CrowdSec → morning brief).

---

## 12. Changelog
- **2026-08-20** — Initial build: Ubuntu 24.04, driver 595.84, Ollama + `qwen3:14b`, `qwen3-chat` (16K), flash attn + q8_0 cache, Open WebUI. *The Secret* corpus v1 → v2 (heading-magnet fix). Retrieval Top K raised to 5–6.
