# AI Inference Box (`<host>`) - Build & Operations Runbook

> **Public version.** Network specifics are genericized. Placeholders: `<box-ip>` is the box's LAN address, `<host>` its hostname, `<services-vlan>` the isolated services VLAN, `<user>` the serve service account, `<bot-user>` the Discord bot's service account. Real values, firewall rules, detailed security posture, the bot token, guild and channel IDs, and the community's puzzle data (verses, litany, author hints, per-city values, and the raw forum archive) are kept out of this document.

**Host:** `<host>` at `<box-ip>` (`<services-vlan>`)
**Purpose:** always-on local LLM inference behind a RAG chatbot for *The Secret* treasure-hunting community, plus a private chat hub and a future read-only ops reader.
**Status:** operational. Engine and private hub done. The custom four-collection RAG service (serve.py) is live under systemd and wired into Open WebUI, the deterministic city registry runs inside the serve pipeline, a global rules layer rides alongside it, a gated historical-forum tier is live, a deterministic book-text search and word counter run beside the registry, and the Discord bot is live. The ops reader is still pending.
**Last updated:** 2026-08-30

---

## 1. Overview

One inference box, one model endpoint, several thin front-ends hung off it. The box is the engine. Each use case is a separate steering wheel pointed at the same Ollama endpoint. Front-ends never share tool sets, so the community bot gets RAG and nothing else.

Retrieval runs through the custom `serve.py` service (FastAPI, Chroma, bge-m3), not Open WebUI's built-in document RAG. serve.py owns embedding, retrieval, tiering, and the grounding prompt. In front of retrieval sits a deterministic city registry (`secret_registry.py`). When a question names a city, or references a verse or image number, the verified facts for that casque get injected above the retrieved passages and marked authoritative. The same module also injects a small set of creator-stated global hunt rules (burial depth, excluded sites, the now-void mail-in claim process, and a field-guide no-clues disclaimer) when a question is about those, marked authoritative the same way (section 5.8). A fourth retrieval tier, the historical forum archive, is gated: it is queried only when a question is about the history of the search, and stays out of retrieval entirely otherwise (section 5.3). A deterministic book-text search (`secret_booksearch.py`, section 5.9) sits beside the registry: literal "is X in the book" and "what page is X on" questions inject an authoritative search block above the passages, and word-count questions short-circuit to an exact count before retrieval runs. Open WebUI is just a front-end that talks to serve over an OpenAI-compatible connection. Its own knowledge-base RAG for this model is detached and unused (see section 6). The older OWUI-native RAG approach is kept as history in section 5-legacy, but it is not the live path anymore.

**Consumers**

- Private Open WebUI chat and document RAG hub (built).
- "Ask Byron" community model, served by serve.py, surfaced in OWUI (built).
- Discord RAG bot for the community, RAG-only with no infra tools (built, calls serve's `/ask`). See section 7.
- Read-only ops reader for observability summaries, no command execution (pending).

**Design rules**

- No coding workloads. That goes to a hosted assistant. A 12 GB card is the wrong tool for it.
- No sudo, no shell, no command execution by any agent. Read-only.
- Isolation is structural, not prompt-based. The bot can't touch the network because it holds no tool that can, not because it was told not to.

---

## 2. Hardware & OS baseline

| Component | Detail |
|---|---|
| Motherboard | Gigabyte GA-Z270X-Gaming K7 |
| RAM | 64 GB DDR4 |
| GPU | NVIDIA RTX 3060 12 GB (GA106, LHR, irrelevant to inference) |
| OS | Ubuntu Server 24.04.4 LTS, bare metal |
| Driver / CUDA | NVIDIA 595.84 / CUDA 13.2 |

Capacity note: 12 GB of VRAM holds one ~14B model at a time (Q4 is about 9.3 GB of weights). Idle draw is around 15 W at 0% util. The GPU only spikes to ~100% for the seconds it spends generating, then drops back to idle. With the model pinned (section 3.4), it stays resident in VRAM between requests but the cores still idle at ~15 W; `100% GPU` in `ollama ps` means the whole model is *placed* on the card, not that it's *working*.

Embedder note for the serve.py stack: bge-m3 runs on CPU on purpose (`SECRET_EMBED_DEVICE=cpu`) to keep VRAM free for qwen3. CPU embedding is slow and silent. There is no progress bar inside `encode()`, so a batch can sit for a minute or more with no output. That is normal, not a hang. Confirm it with `top` (python pegging cores) or a moving mtime on `chroma_db/chroma.sqlite3`, not a frozen one. One-off bulk index builds (the forum archive is ~40k chunks) can be pointed at the GPU for the build only with `SECRET_EMBED_DEVICE=cuda`, with the serve unit stopped and the model unloaded first so they don't contend for the card; query-time embedding stays on CPU.

---

## 3. Build procedure (reproducible from bare metal)

### 3.0 BIOS
- Disable Secure Boot. Leave it on and the NVIDIA kernel module won't load, and the GPU disappears with no obvious error.

### 3.1 OS
- Install Ubuntu Server 24.04 LTS, minimal, with OpenSSH server.
- Give it a fixed address (DHCP reservation) on the services VLAN.

### 3.2 NVIDIA driver
```bash
sudo apt update && sudo apt full-upgrade -y
sudo ubuntu-drivers install
sudo reboot
# verify: expect the 3060 and 12288 MiB
nvidia-smi
```
Sanity check the card is on the bus before touching drivers: `lspci | grep -i nvidia`.

### 3.3 Ollama (native, systemd service)
```bash
curl -fsSL https://ollama.com/install.sh | sh   # auto-detects the GPU
ollama pull qwen3:14b
```

### 3.4 Ollama tuning (systemd override)
Needed for three reasons: the Dockerized front-end has to reach Ollama, a 16K context has to fit in 12 GB, and the model should stay resident so questions don't eat a cold load.
```bash
sudo systemctl edit ollama
```
Add under `[Service]`:
```
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_KEEP_ALIVE=-1"
```
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
# force the load now so no request eats the cold start, and confirm the pin:
ollama run qwen3-chat "warm" >/dev/null
ollama ps       # expect PROCESSOR 100% GPU, UNTIL "Forever"
ss -tlnp | grep 11434
```
- `OLLAMA_HOST=0.0.0.0` lets the container reach Ollama. Scope it at the network layer (see section 11). Loopback-only would be tighter; see the bind item in section 12.
- `OLLAMA_FLASH_ATTENTION=1` is faster and lighter with no quality cost, and it's a prerequisite for KV-cache quantization.
- `OLLAMA_KV_CACHE_TYPE=q8_0` halves KV-cache memory. That's what buys 16K context at 100% GPU instead of spilling to CPU.
- `OLLAMA_KEEP_ALIVE=-1` pins the model in VRAM instead of unloading it after the default 5-minute idle. On a single-purpose box this is the right trade: ~10 GB of the 12 GB card is spoken for permanently, but no request ever eats an idle-unload cold load. Watch the boundary: `-1` does **not** pre-load at boot. After a reboot Ollama starts empty and the model cold-loads on the *first* request, then stays. The `ollama run ... "warm"` line above is how you force that load yourself instead of handing it to the first caller. See the auto-warm item in section 12.

### 3.5 Custom model (context bump)
```bash
cat > qwen3-chat.Modelfile << 'EOF'
FROM qwen3:14b
PARAMETER num_ctx 16384
EOF
ollama create qwen3-chat -f qwen3-chat.Modelfile
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
- Browse to `http://<box-ip>:3000`. First account is admin, so claim it immediately.
- Ollama connection URL in OWUI is `http://host.docker.internal:11434`.

Docker networking caveat that bit us on this build: OWUI runs inside a Docker container, so `localhost:<port>` in its connection settings resolves to the container, not the host. Any host-side service OWUI needs to reach (serve.py, for one) has to be addressed by the box LAN IP `<box-ip>`, not `localhost`. If the LAN IP is unreachable, fall back to the Docker bridge gateway (usually `172.17.0.1`). The `--add-host=host.docker.internal:host-gateway` flag is what makes `host.docker.internal` resolve to that gateway from inside the container.

### 3.7 serve.py service account and systemd unit
serve runs as a dedicated loginless account out of `/opt`, same pattern as the bot, under a hardened unit. See section 5.6 for the full unit and the reasoning behind each directive, and section 5 for what serve is. The short version of the build step:

```bash
# loginless service account
sudo useradd --system --home-dir /opt/secretrag --shell /usr/sbin/nologin secretrag

# lay the code, venv, chroma store, and HF cache under /opt, owned by the account
sudo mkdir -p /opt/secretrag
sudo rsync -a  <source>/secretrag/            /opt/secretrag/
sudo rsync -a  <source>/.cache/huggingface/   /opt/secretrag/.cache/huggingface/
sudo chown -R secretrag:secretrag /opt/secretrag
sudo chmod 700 /opt/secretrag

# then install the unit from 5.6 and enable it
sudo systemctl daemon-reload
sudo systemctl enable --now secretrag
```

Two things that will bite on this step specifically, both learned the hard way (section 9):

- **A copied venv keeps the old interpreter path.** The console-script wrappers in `.venv/bin/` (uvicorn, pip, etc.) hardcode a shebang pointing at wherever the venv was *created*. Move the venv and those shebangs still point at the old path, which `ProtectHome=true` then hides from the service. Invoke uvicorn as a module through the venv's python (`.venv/bin/python -m uvicorn ...`) so the shebang is never consulted. The venv still runs only if its *base* interpreter is a system python (check `pyvenv.cfg` `home =` and `executable =` point outside a home directory); if it was built against a python living in a home dir, recreate it in place instead of copying.
- **The embedding-model cache has to move with the code.** bge-m3 is ~4.3 GB cached under the building user's `~/.cache/huggingface`. A fresh service account with `ProtectHome=true` can't see that, and `ProtectSystem=strict` won't let it re-download. Copy the cache into `/opt`, point `HF_HOME` at it, and give it a `ReadWritePaths` exception (section 5.6). Miss this and serve starts clean, then fails on the first query when it tries to embed.

> **Staging vs live.** Code is edited in a `<user>`-owned working tree and reaches the live service by an `rsync` into `/opt/secretrag` plus a `chown` to the service account and a unit restart. The two only match when you run that sync. Do it as one deliberate step (a `deploy.sh` is the open item in section 12) so "I edited the file" and "the running bot loads it" never drift apart. When in doubt about what is live, trust `curl localhost:8100/ask` and the `/opt` tree, not a CLI import of a working copy.

---

## 4. Configuration reference

| Item | Value |
|---|---|
| Ollama override | `/etc/systemd/system/ollama.service.d/override.conf` |
| Ollama endpoint (from container) | `http://host.docker.internal:11434` |
| Open WebUI | `http://<box-ip>:3000`, container `open-webui`, volume `open-webui` |
| Base model | `qwen3:14b` (4K default context, thinks by default) |
| Tuned model | `qwen3-chat` (16K context, pinned resident via `KEEP_ALIVE=-1`) |
| serve.py (custom RAG) | `http://<box-ip>:8100`, FastAPI under uvicorn, systemd unit `secretrag`, runs as `<user>` from `/opt/secretrag` |
| serve to OWUI connection | OpenAI connection, Base URL `http://<box-ip>:8100/v1`, dummy key |
| Chroma store | `/opt/secretrag/chroma_db` (`SECRET_CHROMA_DIR`, pinned absolute in the unit) |
| HF cache (embedder) | `/opt/secretrag/.cache` (`HF_HOME`, holds bge-m3) |
| Collections | `the_secret` (220), `vault_commentary` (329), `vault_transcripts` (1303), `vault_forum` (40116, gated) |
| Book text index | `/opt/secretrag/book.sqlite`, per-page book text plus verified folios, source for word search/count (`secret_booksearch.py`, section 5.9) |
| Book search module | `/opt/secretrag/secret_booksearch.py`, stdlib-only; presence/location injected, word-count short-circuited in serve (section 5.9) |
| Embedder | bge-m3 (`BAAI/bge-m3`), CPU, cosine space |
| City registry + rules | `/opt/secretrag/secret_registry.py`, 12-city structured-fact lookup plus a global hunt-rules layer (5.8), stdlib-only, imported by serve |
| Discord bot | `/opt/askbyron/askbyron_bot.py`, systemd unit `askbyron`, runs as `<bot-user>`, calls serve `/ask` on localhost (section 7) |

Thinking mode: Qwen3 narrates its reasoning by default. Turn it off with the Ollama API field `"think": false` (a real setting, not a prompt hack), or at the OWUI model-preset level (Workspace, Models). Don't do it in the per-chat Controls panel, which reverts on new chats.

---

## 5. RAG service - serve.py (current, authoritative)

This is the live retrieval path. Everything the community model answers with runs through it, whether the call comes in on `/ask` (Discord) or `/v1/chat/completions` (OWUI).

Location: `/opt/secretrag/` (service account `<user>`, venv `.venv`).
Shared module: `secret_common.py` holds the config, embedder, Chroma handles, retrieval, and the grounding prompt. `build_index.py`, `serve.py`, and the index scripts all import it, so nothing can disagree about model names, paths, or collection names. `secret_registry.py` is a sibling module that serve imports (section 5.7). `secret_booksearch.py` is another sibling, backed by `book.sqlite`, for deterministic book-text search and word counts (section 5.9).

### 5.1 Corpus & collections
All four collections live in one Chroma store (`chroma_db`) so retrieval can query across them. They all share bge-m3 and cosine space, which is the only reason cross-collection distances are comparable:

| Collection | Tier | Count | Source |
|---|---|---|---|
| `the_secret` | CANON (book text) | 220 | *The Secret* English + Japanese editions (built by `build_index.py`) |
| `vault_commentary` | COMMENTARY (wiki) | 329 | Cleaned Obsidian vault: 12Treasures / People / Topics notes. Re-chunked 2026-08-30 to strip cross-city verse dumps from City Guide notes (344 to 329). |
| `vault_transcripts` | COMMENTARY (podcast) | 1303 | 60 episode cards + 1243 ASR transcript chunks |
| `vault_forum` | COMMENTARY (forum, gated) | 40116 | Historical forum archive, ~40k posts across 309 threads, 2001-2019. Lowest-authority tier. Queried only on history/theory intent (5.3). |

The collection name is lowercase `the_secret`. Chroma is case-sensitive. The index-script comments say `The_Secret`, which is a doc typo. The live name is lowercase. Get it wrong in the merge and the book tier silently returns zero hits.

### 5.2 Indexing the collections
Scripts: `build_index.py` builds `the_secret` from the xlsx source, `vault_index.py` builds `vault_commentary`, `transcripts_index.py` builds `vault_transcripts`, and `forum_index.py` builds `vault_forum` from the forum CSV export. Each one drops and rebuilds only its own collection; the others are never touched. `forum_index.py` is stdlib-only for the parse (csv + datetime, no pandas): it strips BBCode `[QUOTE]` blocks (which duplicate other posts), drops trivial and quote-only stubs, windows long posts, and carries `date` / `title` (thread) / `author` metadata so citations convey chronology.

Config lesson that bit us: an index script that hardcodes a relative Chroma path (`./chroma`) builds into a separate empty store, and retrieval never sees it. No error, just silence. Every index script inherits the real path instead:
```python
import secret_common as sc
CHROMA_PATH = sc.CHROMA_DIR
```
That honors `SECRET_CHROMA_DIR`, which the unit pins absolute. Run index builds as the service account so files land with the right owner and the store resolves to `/opt`'s, and pass the env explicitly to be safe regardless of working directory:
```bash
sudo -u secretrag bash -c 'cd /opt/secretrag && \
  SECRET_CHROMA_DIR=/opt/secretrag/chroma_db HF_HOME=/opt/secretrag/.cache \
  ./.venv/bin/python vault_index.py'
```
For the big forum build, add `SECRET_EMBED_DEVICE=cuda` and stop the serve unit and unload the model first so the GPU is free; query-time embedding stays on CPU. Confirm co-location after any build:
```bash
sudo -u secretrag bash -c 'SECRET_CHROMA_DIR=/opt/secretrag/chroma_db /opt/secretrag/.venv/bin/python -c \
  "import chromadb; c=chromadb.PersistentClient(path=\"/opt/secretrag/chroma_db\"); print([(x.name,x.count()) for x in c.list_collections()])"'
```

**Restart after any re-index.** A rebuild drops and recreates the collection under a new internal UUID, but a running serve has the old collection handle cached from import. Until you restart, every query hits a collection that no longer exists and serve 500s on all of them with no other symptom. Treat the re-index and `sudo systemctl restart secretrag` as one operation (see the row in section 9).

Separately, `build_book_index.py` builds `book.sqlite` (the book-text search index, section 5.9) from the OCR'd PDF using PyMuPDF. It is a different kind of build: it runs off the box on the machine that holds the PDF, not as the service account against Chroma, and only the resulting `book.sqlite` (a few MB) is copied into `/opt/secretrag`, owned by the service account. It is not a Chroma collection and never touches the store. `secret_booksearch.py` opens `book.sqlite` at import, so replacing it also needs a serve restart.

### 5.3 Tiered retrieval (secret_common.py)
`retrieve()` queries the collections, merges by cosine distance, and returns the top N. The distances are comparable only because every collection shares bge-m3 and cosine space. The per-tier pull counts are deliberately lopsided: noisier tiers pull fewer so banter rarely wins a slot.

| Env var | Default | Meaning |
|---|---|---|
| `SECRET_N_BOOK` | 6 | book chunks queried |
| `SECRET_N_COMMENTARY` | 6 | wiki chunks queried |
| `SECRET_N_TRANSCRIPT` | 3 | transcript chunks queried (kept low, noisier tier) |
| `SECRET_N_FORUM` | 3 | forum chunks queried, but ONLY when the history/theory gate opens (below) |
| `SECRET_MERGE_TOP_N` | 8 | final merged chunks after distance sort |
| `SECRET_MAX_DISTANCE` | 2.0 | loose ceiling; tighten toward ~1.0 to drop weak hits |

Good queries land around 0.36 to 0.52, so the 2.0 ceiling currently does nothing. It's a safety net, not a filter. Tightening it is the lever if junk starts surfacing on vague queries.

`build_context()` labels each passage by tier so the model can see the authority level: `[CANON - book text - ...]`, `[COMMENTARY - community wiki - ...]`, `[COMMENTARY - podcast - ...]`, `[COMMENTARY - historical forum - ...]`.

**Forum tier, gated.** The `vault_forum` tier (5.1) is unverified community opinion, and it is the only tier not queried on every request. `retrieve()` includes it in the merge **only when `_forum_intent(question)` matches** - a precision-biased keyword trigger in `secret_common.py` that fires on history-of-the-search and old-theory questions ("what did people used to think", "early theories", "over the years", "timeline of", the word "forum" itself) and stays silent on fact and solve questions. When the gate is shut, forum is entirely absent from retrieval, so unverified forum opinion cannot leak into a factual answer - not even in the last slot. When the gate opens, forum competes in the distance merge normally, no penalty, capped at `SECRET_N_FORUM` candidates, and can legitimately rank near the top on a history question. The trigger is deliberately tight and fails toward silence: a miss just means a history question gets no forum (safe, the user can rephrase), whereas a false positive would put unverified opinion into a factual answer (not safe). `_FORUM_TRIGGERS` is a plain editable list; extend it as real questions reveal phrasings it misses, then restart serve.

### 5.4 System prompt (grounding + persona)
Lives in `secret_common.py` as `SYSTEM_PROMPT`. serve injects it, which means the OWUI model's own System Prompt field does nothing for this model. The current prompt covers:
- Persona: "Ask Byron", a warm, dry reference-librarian voice that's honest about ambiguity.
- Tier guardrail: CANON is authoritative fact, COMMENTARY is informed opinion. The model may synthesize and attribute theories, but it must not present commentary as settled fact.
- Numbering guardrail: no invented "casque 1/2/3" ordering. Casques are named by city, verse number, or image/painting number. Verse/image/city pairings are established and may be stated. For a found casque, the documented find location is fact. For an unsolved one, the model must not state or imply a dig location as correct.
- Synthesize, don't paste: answer in its own words, don't dump full verses or wiki cards verbatim unless asked, and drop material about other cities that wasn't asked about.
- Registry guardrail (added 2026-08-27): when an `=== AUTHORITATIVE CITY REGISTRY ===` block is present, it is the top authority. Its values override any conflicting passage value, including any stray "casque N" numbering. The synthesize-don't-paste rule is exempted for the registry, so its AUTHOR HINTS come through in the author's own words instead of paraphrased. Author hints are still clues toward an unsolved answer, relayed as hints, never as the settled solution or dig location. See section 5.7.
- Forum guardrail (added 2026-08-28): the historical forum tier (5.3) is the lowest-authority source. When a `[COMMENTARY - historical forum - ...]` passage is present it is unverified community opinion, frequently outdated or wrong, to be reported as history ("the forum argued...", "an early theory held...") and never treated as evidence for a factual claim. It never overrides the book, the wiki, or the podcast.
- Book-search guardrail (added 2026-08-30): when a block whose header begins `=== AUTHORITATIVE BOOK TEXT SEARCH` is present, it is a deterministic literal-text search of the book. Treat its FOUND/NOT FOUND verdict and page numbers as authoritative for whether specific words or phrases appear, and state the result plainly in the model's own voice without naming the search machinery. NOT FOUND means only that the exact text is absent from the searchable book text (paintings carry none), never a paraphrase or an image, so it is relayed as "those exact words do not appear", not "the book has nothing about that". See section 5.9.
-Image Contents Guardrail: "You cannot see the paintings, but it is an established fact that EVERY painting visually depicts its associated flower and birthstone/jewel. Other than the flower and jewel, you have no verified source for what they visually depict. You MAY state an image's registry facts plainly - its painting name/title, what it was inspired by."
-Identity Guardrail: "Never assign identities, names, or roles from the retrieved context to the user. If the user asks a conversational question or asks who they are, politely state that you are a reference librarian for The Secret and decline to answer."
-Memory & Tracking Guardrail: "MEMORY & TRACKING: You are a stateless system. You have no memory of past conversations and do not log, store, or track user questions. If asked about logging or memory, state clearly that you do not save questions and that every interaction is completely independent."

### 5.5 serve.py endpoints
| Endpoint | Use | Notes |
|---|---|---|
| `POST /ask` | Discord bot (simple JSON) | `{"question","edition"}` returns `{"answer","sources"}`. Supports explicit edition. |
| `POST /v1/chat/completions` | OWUI (OpenAI-compatible) | Single-turn by design. Takes the last user message, ignores prior turns. Edition is always auto-sniffed. |
| `GET /v1/models` | OWUI model discovery | advertises `secret-librarian`. |
| `GET /health` | health check | Counts `the_secret` only (220), predates the merge. Seeing 220 here does not mean the other collections are missing. Cosmetic, extend later. |

Both `/ask` and `/v1/chat/completions` run through the same `answer_question` chain: a deterministic word-COUNT short-circuit (`secret_booksearch.book_answer_for`, section 5.9) fires first and returns an exact count with no LLM call; otherwise retrieve, build_context, registry injection (section 5.7), book-search injection for presence/location questions (section 5.9), build_messages, Ollama. The registry and book-search blocks are prepended to the context inside `answer_question`, and the forum gate lives inside `retrieve()` (section 5.3), so both endpoints, and therefore both the Discord bot and OWUI, get the registry, the gated forum tier, and book search the same way, with no per-client code.

serve calls Ollama over a single httpx client with a 120s timeout (`serve.py`). That is wide enough for normal generation but not for a worst-case cold model load stacked on top of a first-request bge-m3 load; with the model pinned (section 3.4) that path is avoided in steady state. See the cold-load row in section 9.

### 5.6 Running serve
serve runs as a systemd unit (`secretrag`), not tmux. It survives reboot and restarts on failure on its own. The unit mirrors the bot's hardening, with the write and dependency exceptions serve actually needs.

Unit at `/etc/systemd/system/secretrag.service`:
```ini
[Unit]
Description=Secret RAG service (serve.py) for Ask Byron
After=network-online.target ollama.service
Wants=network-online.target
Requires=ollama.service

[Service]
Type=simple
User=secretrag
WorkingDirectory=/opt/secretrag
Environment="SECRET_CHROMA_DIR=/opt/secretrag/chroma_db"
Environment="HF_HOME=/opt/secretrag/.cache"
Environment="OLLAMA_URL=http://localhost:11434"
ExecStart=/opt/secretrag/.venv/bin/python -m uvicorn serve:app --host 0.0.0.0 --port 8100
Restart=on-failure
RestartSec=5

# Hardening - mirrors askbyron, plus the write exceptions serve needs
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths=/opt/secretrag/chroma_db /opt/secretrag/.cache

[Install]
WantedBy=multi-user.target
```

Why the unit differs from the bot's, directive by directive, since serve is not the pure dumb pipe the bot is:

- `Requires=ollama.service` + `After=... ollama.service`. serve is useless without Ollama, so it is ordered after it and stopped with it. The bot only needed `network-online` because it reaches serve over localhost. If you would rather serve stay up and retry across an Ollama restart than be stopped with it, use `Wants=` instead of `Requires=`; the trade is that serve then runs during a window where its backend is down and every request errors.
- The three `Environment=` lines. `SECRET_CHROMA_DIR` is pinned **absolute** on purpose: the in-code default is the relative `./chroma_db`, which resolves against the working directory and would silently follow the process wherever it ran. Absolute means the data path can't drift, and index builds must set the same value so they write the store serve reads (section 5.2). `HF_HOME` points the embedder cache at the copied-in bge-m3 (section 3.7). `OLLAMA_URL` is set explicitly even though it matches the default, because leaving tmux means nothing is inherited from a shell anymore; the unit documents its own dependencies.
- `ExecStart` runs uvicorn as a python module, not the `.venv/bin/uvicorn` wrapper, to sidestep the copied-venv shebang problem (section 3.7).
- `ReadWritePaths`. `ProtectSystem=strict` makes the whole filesystem read-only except a few system paths, so serve needs explicit write access to the two places it writes: the Chroma store and the HF cache. `book.sqlite` is opened read-only, so it needs no write exception. Omit `ReadWritePaths` and serve starts, then throws on the first query that touches the store.

Everyday operations:
```bash
sudo systemctl status secretrag
sudo systemctl restart secretrag
sudo journalctl -u secretrag -f
```

Restart after any edit to `secret_common.py`, `secret_registry.py`, or `secret_booksearch.py`, and after replacing `book.sqlite` or re-indexing any Chroma collection. The prompt, retrieval, registry, book search, and collection handles load at import plus `lru_cache`, so a running process keeps the old versions (and the old collection handle) until you restart it. Under systemd that is just `sudo systemctl restart secretrag`. Remember that edits land in the `<user>` staging tree first and only reach `/opt` via the sync step (section 3.7 staging note); restart the unit *after* syncing, or the running service reloads the old file.

> Because serve runs as a locked-down account in a `700` directory, reading or editing its files needs `sudo` (e.g. `sudo -u secretrag ...` to act as the service account). Your login user can't read into `/opt/secretrag` casually; that's the point. This also means a quick module test run as your login user against a `/opt` data file (e.g. `book.sqlite`) will silently see nothing; point such tests at your staging copy, and trust the live `/ask` endpoint for the real answer.

### 5.7 City registry - `secret_registry.py`

Why it exists: qwen3-14B is bad at emitting precise discrete facts (verse number, image number, gemstone, found-status) out of unstructured prose. It reaches for a number that looks about right. That's baked into generative RAG, not a context-size problem, and trimming context doesn't fix it. The registry turns those settled facts into a lookup and injects them as authoritative context, so the model reads the fact instead of guessing. Same move takes care of the discrete-fact confabulation, the "casque N" leak, and the status-driven confidence drift.

Shape: a closed set of 12 cities, one per casque. Stdlib-only, no external deps. Fields per city:

- `verse_no`, `image_no`: the verse and image/painting numbers
- `birthstone`: the stone, which is also the prize jewel you receive (one stone field, not two)
- `month`, `flower`, `nation`, `clock_time`, `lat_long`: decoded clue attributes. `lat_long` is a region band, not a dig location.
- `painting_name`, `painting_inspired_by`, `verse_image_line`: image metadata
- `litany_quote`, `verse_text`: canonical text
- `status`: `FOUND` or `UNSOLVED`
- `finder`, `year`, `location`: found casques only
- `hints`: creator-verified author hints
- `verified`: injection gate; a record only injects when this is `True`

The actual verified values (verses, litany, author hints, coordinates, find locations) are the community's ground-truth data and are not reproduced in this public runbook. They live in the deployed `secret_registry.py` on the box.

Matching and triggers, following how the community actually refers to casques:
- City name: a normalized, word-boundary matcher over the canonical names. Aliases are supported but mostly unused, since the community references by city, verse number, or image number.
- Number referents: `by_verse(n)` and `by_image(n)` resolve "verse 6" or "image 2" to the right city. The number-first form ("6th verse") requires an ordinal, so phrasing like "the first 6 verses" won't false-fire.
- Aggregate roster: when a question names no single casque but asks about the set ("how many are found", "which are unsolved", "list the casques"), `registry_block_for` injects a compact roster of all 12 with derived counts, so count and status answers can't confabulate. This is also what makes "how many casques are there" answer 12 instead of being poached by the book-search count gate (5.9).

`registry_block_for(question)` returns the per-city block(s) for any matched casque, or the roster when the question is aggregate, or an empty string. It runs independently of retrieval. Even if the vector search comes back with junk, the registry still injects. It also prepends any matching global rule block (section 5.8) above the city or roster output, under a shared authoritative header.

Injection point and precedence: inside `answer_question` in `serve.py`, right after `build_context()`:
```python
context = sc.build_context(hits)
import secret_registry as registry
_reg = registry.registry_block_for(question)
if _reg:
    context = _reg + "\n\n" + context   # registry sits ABOVE the passages
messages = sc.build_messages(question, context)
```
The block header is `=== AUTHORITATIVE CITY REGISTRY (overrides retrieved passages) ===`. Precedence is stated once in `SYSTEM_PROMPT` (section 5.4): the registry wins for its own fields, everything else stays RAG.

Guards, enforced in the formatter and not left to the prompt to remember:
- Unsolved casques never emit `finder`, `year`, or `location`, even if a value is sitting in the record. Dig locations for unsolved casques stay a RAG-plus-refusal problem.
- `lat_long` is labelled "region only, NOT a dig location."
- Known-unknowns render as "(not established)", which tells the model the value is genuinely open instead of leaving it to invent one. A blank field injects nothing at all, so RAG can still fill it.
- Author hints inject verbatim, under a status-aware label. For unsolved: "clues toward the still-unsolved answer, relay as hints, never assert the solution or dig location they imply." For found: "this casque is solved, the find location above is authoritative."
- If an author hint itself implies a location for an unsolved casque, it stays in the hints channel under that guard (relayed as the author's implication, never as a settled dig location) and never gets promoted into the `location` field.

Verbatim scope, worth knowing: the anti-paste exemption currently covers the whole registry block, so `verse_text` also comes through verbatim on a registry match, not just the hints. If verses should stay behind an explicit "show me the verse" ask, narrow the exemption to `hints` only.

Validation and self-test: `validate_registry()` checks for unique verse and image numbers across the set, found-casque completeness (finder, year, location all present), unsolved casques carrying no location, and lowercase keys. The self-test is stdlib-only and needs no venv:
```bash
cd /opt/secretrag && sudo -u secretrag python3 secret_registry.py
# expect: self-test OK - 12 cities; validate: clean
```

How it was wired: integration went through an anchored-splice patcher. It pre-flights every anchor (has to match exactly once or it aborts and touches nothing), timestamped-backs-up each target file, applies the edits, runs `py_compile`, and rolls back on failure. Keep future `secret_common.py`, `secret_registry.py`, and `secret_booksearch.py` edits the same way: anchored splices with a backup and a compile check.

### 5.8 Global rules layer - `secret_registry.py`

A sibling to the per-city registry (5.7), in the same module: creator-stated rules that apply to ALL twelve casques rather than to one city. Where the city registry answers "what are the facts for this casque", the rules layer answers "what are the standing rules of the hunt". Same delivery: deterministic text injected as authoritative context, above the retrieved passages, so the model reads the rule instead of reconstructing it from commentary.

Two rule blocks are live:

- **Burial, container, and excluded sites, plus the claim process.** The physical facts that hold for every casque (the burial-depth ceiling, the sealed protective box, the location types that are excluded) and the state of the mail-in claim process. The claim process is now void: the book's original mail-in route is defunct, and the only way to confirm a solution or claim a treasure is to physically recover the casque and key. This block leads with that verdict so the model states it plainly, with the defunct mailing history demoted to background it gives only if asked, never as a live option. That ordering was a deliberate fix. An earlier version that described the history first led the model to soften "void" into "you can mail it in, it just will not be verified", which is exactly the misleading answer the rule exists to prevent.
- **Field guide ("back of the book") no-clues disclaimer.** Searchers call the field guide the "back of the book". The book's creators have stated it holds no solving value, so a question about it should say so plainly rather than let the model read clues into it. This concerns the field-guide entries only: it does not imply the paintings are clue-free, and the block says so, because the paintings are the core clue source.

Structure and injection: each rule is a `(name, gate, body)` triple. `registry_block_for` (5.7) evaluates every gate against the question and prepends every fired block above the city or roster output, wrapped in one shared `=== AUTHORITATIVE RULES ===` header. Nothing in `serve.py` changed; the rules ride the existing registry injection point (5.7), so both endpoints and both front-ends get them the same way, with no per-client code.

Two gates, two risk postures, on purpose:

- The **burial/claim** gate is tuned generous. Over-firing only prepends a block whose every line is true, so an occasional off-topic injection is harmless. Better to answer the FAQ than to miss it.
- The **field-guide** gate is tuned for precision and fails toward silence, the opposite posture, because over-firing here can mislead. The paintings depict the fair folk, and the paintings are the clue source, so a disclaimer that fires on the wrong question could wave a searcher off a genuine clue. It fires on unambiguous field-guide phrasing, or on a named field-guide creature matched on normalized text so punctuation and case do not matter. A small number of creature names overlap with real-world symbols or historical referents that could legitimately appear in a painting; those are deliberately excluded from name-matching, as is one name common in ordinary bot-channel chatter, so the disclaimer can never fire on a real clue or on unrelated talk. The excluded names are listed in a comment in the module.

Content note, same as 5.7: the actual rule text, the mail-in address, and the field-guide creature roster are community and book content and are not reproduced in this public runbook. They live in the deployed `secret_registry.py`.

Wiring and verification: integrated with the same anchored-splice discipline as the registry (pre-flight every anchor to match exactly once or abort, timestamped backup, `py_compile`, auto-rollback). The stdlib-only self-test (5.7) still gates it. Smoke-test live on `/ask` (section 8): a mail-in question should lead with the void verdict and the physical-recovery path, and a field-guide question should return the no-clues disclaimer while still naming the paintings as the clue source.

### 5.9 Book text search - `secret_booksearch.py`

Why it exists: the model cannot reliably say whether a given word or phrase appears in the book, or count how often, or name the page. It guesses, and it guesses confidently ("field goal is not mentioned in The Secret" - it is, on page 88, in the Team Spirits field-guide entry). The registry fixed discrete per-city facts the same way; this fixes literal book-text questions. Deterministic search over the real book text, injected or returned as authoritative, so the model reports a lookup instead of inventing one.

Source of truth: `book.sqlite`, a `pages(pdf_page, printed, text, text_norm)` table built by `build_book_index.py` (PyMuPDF) from the OCR'd PDF. One row per PDF page. The printed folio is `pdf_page - 2`: the first two PDF pages are blank unnumbered leaves, and every physical page after them (paintings included) is numbered, so the offset is constant. That offset was consistency-checked against folios read from each page's bottom margin, with zero mismatches across all 226 numbered pages. `text_norm` is a normalized copy (NFKC, curly quotes and soft hyphens folded, line-break hyphenation joined, whitespace collapsed, lowercased) and is the field searched; the search term is normalized the same way so the two match. The build runs off the box on the machine holding the PDF (~66 MB, no need to move it), and only the resulting `book.sqlite` is copied into `/opt/secretrag`, owned by the service account. It is not a Chroma collection and never touches the store.

Three intents, two delivery paths. `secret_booksearch.py` (stdlib-only, sibling to the registry) classifies the question and routes it:

- Presence ("is X in the book", "does the book contain X") and location ("what page is X on") return an injectable block via `book_block_for(question)`. serve prepends it above the passages, under the header `=== AUTHORITATIVE BOOK TEXT SEARCH (overrides retrieved passages) ===`, and the model answers in Ask Byron's voice (section 5.4 guardrail). Like the registry, this block rides above the passages and does not appear in the `sources` array.
- Word count ("how many times does X appear", "how often is X used", "count the word X") is answered by `book_answer_for(question)`, which serve calls at the TOP of `answer_question` as a deterministic short-circuit: it returns the finished count string directly, no retrieval and no LLM, so the number is exact and instant.

Counting uses word-boundary matching (`\bX\b` over `text_norm`, `re.escape`d), not substring, so "the" does not count "them" and "giant" does not count "giants". Multi-word phrases are matched whole. Presence and location use substring, so a phrase split across pages still resolves.

Gates, fail toward silence. Both classifiers are anchored on question form so they do not poach non-lookup questions. The count gate fires only on an explicit counting frame WITH an extractable target word, so "how many casques are there" (a hunt-fact question, registry territory) correctly falls through rather than counting the token "casques". The presence gate fires on yes/no-plus-book-reference or "what page" phrasing; "found" was deliberately excluded from its signals because it collides with "the casque was found". A miss on either just falls back to normal RAG (the pre-existing behavior); a false fire is the thing to avoid. The frames are plain editable lists, same pattern as `_FORUM_TRIGGERS`; extend them as real phrasings show up, then restart serve.

Honesty built into the answer. A FOUND result is always a true literal hit; a NOT FOUND is always literally true (the exact text is absent), and the block says so explicitly: literal text match, not concept absence. Verse-page counts (printed 49 to 54) sit in OCR noise from the decorative verse banners, so counts there are flagged approximate. Everything is English-edition only (the Japanese pages are not in the PDF).

What it does NOT do: this is page-scoped, not verse-scoped. Pages 49 to 54 pack multiple cities' verses per page and are the OCR-dirtiest in the book, so "how many times does X appear in the Milwaukee verse" is still not answerable here. That remains the deterministic verse-scoped counter TODO (section 12), keyed off the registry's per-city `verse_text`, not this index.

Retirement of the old counter. This replaced `secret_lookup.py` + `secret_words.db`, an earlier single-word counter over a separate, less-trustworthy book index whose page numbers were the exact "no folios attached" problem `book.sqlite` solved. Its `try_lookup` short-circuit in `serve.py` was swapped for `book_answer_for`, its import dropped, and both files moved aside to `.retired.<timestamp>`. On the probe word "grey" the new counter matched the old exactly (2 times, pages 9 and 53) while fixing the substring over-counts, confirming a safe migration to one book tier over the verified source.

Wiring and verification: both edits (extend `secret_booksearch.py`, re-splice `serve.py`) went through the anchored-splice patcher (pre-flight each anchor to match exactly once or abort, timestamped backup, `py_compile`, auto-rollback), same discipline as the registry. `book.sqlite` and `secret_booksearch.py` load at import, so a serve restart is mandatory after replacing either. Smoke-test live on `/ask` (section 8).

---

## 5-legacy. OWUI-native document RAG (superseded, kept for history)

> This was the retrieval approach before serve.py. It is not the live path anymore. The "Ask Byron" model now routes to serve.py and this KB is detached (section 6). Kept for the H1-heading lesson and the corpus pipeline notes.

Source: 12treasures.com page data, 118 pages, exported to `.xlsx`. The `convert_secret.py` pipeline emitted one Markdown doc per page per edition (113 English, 91 Japanese). Formatting lesson: v1 wrote each page with a Markdown H1, and OWUI's header-aware splitter isolated that heading into a content-less chunk that matched "page N" queries but carried no text. The fix was folding the page identifier inline with no H1. OWUI retrieval settings that mattered: Top K 5-6, relevance threshold 0, Hybrid Search (BM25) available but off.

---

## 6. OWUI "Ask Byron" model - wiring to serve

The community-facing model surfaced in Open WebUI, now a thin front-end over serve.py.

To make it route through serve:
1. Add the OpenAI connection (Admin, Settings, Connections, OpenAI API): Base URL `http://<box-ip>:8100/v1`, key any non-empty string (serve ignores it). Use the box LAN IP, not `localhost`, because OWUI is containerized (section 3.6 caveat). `secret-librarian` then shows up in the model list.
2. Point the "Ask Byron" model at it (Workspace, Models): Base Model is `secret-librarian`.
3. Detach OWUI's own knowledge base by removing the attached `The_Secret` KB (the x next to it). This one matters: leave it attached and OWUI runs its own book RAG and stuffs those chunks into the message it sends serve, which then runs its own retrieval on top. Two stacked retrieval systems, with OWUI's book-only chunks polluting serve's tiered context. serve owns retrieval, OWUI has to be a dumb pipe.
4. The OWUI System Prompt field is dead weight now. serve injects `SYSTEM_PROMPT`.

---

## 7. Discord bot - Ask Byron in Discord

The community front-end. Lets a member ask Ask Byron a question in Discord and get the answer back in the channel. Same design rule as everything else hung off this box: it is a dumb pipe. Text in, `answer` out. It holds no tool that can touch the box, the network, or the shell, so the RAG-only isolation is structural, not a promise the prompt makes.

Location: `/opt/askbyron/askbyron_bot.py`, run by a dedicated loginless service account `<bot-user>` under a hardened systemd unit. Single file, two dependencies: `discord.py` and `httpx`. serve now runs under the same systemd pattern (section 5.6), so the bot and serve are peers, both real units, both surviving reboot on their own.

### 7.1 What it does
A slash command, `/askbyron`, with the question as a required string and two optional params: `edition` (Auto-detect, English, Japanese, default Auto) and `show_sources` (default off). On invoke it defers the interaction, POSTs `{"question","edition"}` to serve's `/ask` at `http://127.0.0.1:8100/ask`, and posts the `answer` back. Because it runs on the box, the serve call is localhost and never leaves the machine. Everything in serve's pipeline - the registry, the roster, the gated forum tier, and book search - applies to the bot automatically, since it flows through the same `/ask`.

Why slash and not a mention or prefix listener: slash commands give native deferral, which is exactly what a slow single-GPU backend needs (7.3), and they need no privileged intents. A mention or prefix bot would have to request the Message Content intent, meaning it reads every message in an 18k-member server. Slash reads nothing it is not explicitly invoked on. Fewer privileges, less surface, same result. The edition toggle and the sources toggle come along free as typed params, which a prefix parser would have to hand-roll.

### 7.2 Where it runs and how it reaches Discord
The box allows outbound traffic on the services VLAN but blocks inbound. That is fine for a gateway bot, and it is worth spelling out why, because it is a common point of confusion. A discord.py bot is not a web server. Nothing connects inbound to it. On startup the bot dials out to Discord's gateway and holds a WebSocket open. When a member runs the command, Discord pushes the interaction down that already-open outbound pipe. So the whole path is outbound: bot to gateway on startup, interaction down the held socket, bot to localhost serve, bot back to Discord to post the reply. The inbound block is never in the way, the same way a browser loads a page from behind the same firewall.

This settled an earlier design question. An off-box host reaching serve would have needed a cross-segment firewall rule, and a Cloudflare tunnel would only have helped if the bot lived off-box or used Discord's HTTP-interactions endpoint (a public URL, which does need inbound). Neither applies. On-box plus localhost is the simplest topology and opens nothing new. The one real trade is that the bot shares the card and the machine with serve and Ollama, so a crash loop lands on the inference host. It is a thin async process, so that risk is small, which is why the systemd unit is hardened (7.5).

### 7.3 Gotchas it has to handle (designed around from the start)
These come straight out of the box's constraints in sections 2 and 10.

- Latency and concurrency. The first `/ask` after a serve restart pauses several seconds while bge-m3 loads on CPU, and every generation after that takes a few seconds on the single resident 14B. The bot defers the interaction before touching the GPU (Discord shows "thinking...", which buys a 15-minute window to reply), and serializes generations behind an asyncio semaphore of 1 so it never fires several concurrent calls at one card. A queue guard (`MAX_QUEUE`) sheds load past a set depth instead of stacking a backlog that would blow the reply window, and a per-user cooldown (`USER_COOLDOWN_S`) stops one person spamming. Note the serialization lives in the bot only; serve itself does not serialize (section 10), but Ollama runs one inference slot (`OLLAMA_NUM_PARALLEL` unset, defaults to 1) and queues the rest, so a direct hit on serve degrades to latency rather than a GPU OOM. Word-count questions are exempt from generation entirely: they short-circuit in serve (5.9) and return instantly with no LLM call.
- Discord's 2000-character message limit. Registry answers can include verbatim verse or hint text and run long. The bot splits the reply on newline or space boundaries into sub-2000 pieces and sends them in order, so nothing truncates or throws.
- serve down. The bot catches connection-refused and posts a friendly message instead of hanging the interaction. Now that serve is a systemd unit that survives reboot and restarts on failure (section 5.6), it is a genuine fallback for the narrow windows serve is actually down (a deploy restart, a crash mid-recovery) rather than a stand-in for a missing supervisor.
- Think-mode. The bot posts whatever `answer` holds, so if serve ever leaks "Okay, the user wants..." the bot cannot scrub it. Confirmed clean by curling `/ask` before shipping. If it ever leaks, the fix is serve-side (`"think": false`), see section 4.
- Abuse surface. 18k members. The bot restricts to designated channels and an optional role, rate-limits per user, and keeps `sources` off by default (opt-in, and only the top one or two when on).

### 7.4 Question echo
Slash invocations are only visible to the member who ran them, so a public answer would otherwise float with no question attached. The bot echoes the question as the first line of its reply ("<name> asked:" then the question as a quote), with the display name escaped so a stray markdown character cannot break formatting, and the echo trimmed to 500 characters. The full question still goes to serve, only the displayed recap is capped. This opens one trade worth recording: the bot now rebroadcasts user-submitted text with a name on it, so in a server this size someone will eventually try to use it as a megaphone. The channel and role gates are the lever, and a profanity screen on the echo is the option if it becomes a problem (see also section 10).

### 7.5 Config and secrets
All config is environment, read from an `EnvironmentFile` the service account owns at `0600`. The bot token is a secret and lives only there, never in the repo. Keys:

| Env var | Meaning |
|---|---|
| `DISCORD_TOKEN` | bot token, secret |
| `DISCORD_GUILD_ID` | guild-scoped command sync, so the command appears in the server instantly instead of waiting on global propagation |
| `SERVE_URL` | serve's `/ask`, localhost on the box |
| `ALLOWED_CHANNEL_IDS` | comma-separated channel allowlist, blank means any channel |
| `ALLOWED_ROLE_ID` | single role gate, blank means anyone in an allowed channel |
| `MAX_QUEUE` | shed requests past this many waiting |
| `USER_COOLDOWN_S` | per-user cooldown |
| `HTTP_READ_TIMEOUT` | serve read timeout, 90s, wide enough to cover a cold bge-m3 load |

The unit is hardened along the lines of `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, and `PrivateTmp`, since the process needs none of that access. Invite the bot with both the `bot` and `applications.commands` scopes and just the Send Messages permission. No privileged intents.

### 7.6 Running the bot
```bash
sudo systemctl enable --now askbyron
journalctl -u askbyron -f    # want: logged in as ..., commands synced to guild ...
```
Restart after any edit to the bot file. Like serve now, it is a real systemd unit, so it survives reboot on its own.
```bash
sudo systemctl restart askbyron
```

---

## 8. Operations & verification

**Health checks**
```bash
nvidia-smi                         # GPU present, VRAM in use
ollama ps                          # model resident, PROCESSOR 100% GPU, CONTEXT 16384, UNTIL Forever
curl -I http://localhost:3000      # Open WebUI serving (200/307 = alive)
docker ps                          # open-webui Up / (healthy)
ss -tlnp | grep 11434              # Ollama port
ss -tlnp | grep 8100               # serve.py port
systemctl status secretrag         # serve.py unit active (enabled = survives reboot)
systemctl status askbyron          # Discord bot unit active
curl -s localhost:8100/v1/models | python3 -m json.tool   # serve advertises secret-librarian
journalctl -u secretrag --since "1 hour ago"  # serve up, no tracebacks
journalctl -u askbyron --since "1 hour ago"   # bot logged in, commands synced, no tracebacks
```

**Registry self-test** (stdlib-only, no venv needed):
```bash
cd /opt/secretrag && sudo -u secretrag python3 secret_registry.py
# expect: self-test OK - 12 cities; validate: clean
```

**Smoke-test the registry end to end** (proves injection is live, always hit the live endpoint, not a CLI import of a staging copy):
```bash
# a number-referent question: the registry should bind image to verse to stone to city
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"what verse number and gemstone go with image 2?","edition":null}' | python3 -m json.tool
```
The discrete facts should come back from the registry, correct and bound to the right city. The registry rides in the context above the passages, so it won't show up in the response's `sources` array.

**Smoke-test the forum gate** (proves the gate opens on history and stays shut on facts):
```bash
# history question: forum should appear in sources and be reported AS history
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"early forum theories about the Milwaukee verse fifth line","edition":null}' | python3 -m json.tool
# fact question: forum should be entirely absent from sources
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"what gemstone goes with image 2","edition":null}' | python3 -m json.tool
```
The first should carry `historical forum` entries near the top of `sources` and frame them as old theories; the second should have zero forum entries - the gate never queried the collection.

**Smoke-test the rules layer** (proves the global rules inject and lead correctly):
```bash
# claim question: answer should LEAD with mail-in being void and physical recovery being the only path
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"can I mail in my solution to claim a treasure?","edition":null}' | python3 -m json.tool
# field-guide question: answer should give the no-clues disclaimer AND still point to the paintings as the clue source
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"are the entries in the back of the book clues?","edition":null}' | python3 -m json.tool
```
The first must not offer a mail-in option even with a caveat; the second must not imply the paintings are clue-free. Like the registry, the rules block rides above the passages, so it will not appear in the `sources` array.

**Smoke-test book search** (proves presence/location inject in voice, and word-count short-circuits exact):
```bash
# presence: should answer FOUND, page 88, in voice (field goal is in the Team Spirits entry)
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"is \"field goal\" in the secret?","edition":null}' | python3 -m json.tool
# word count: should return an exact deterministic count, no LLM softening
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"how many times does grey appear in the book?","edition":null}' | python3 -m json.tool
# guard: a hunt-fact "how many" must NOT be poached by the count gate; the registry roster answers 12
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"how many casques are there?","edition":null}' | python3 -m json.tool
```
The first should say the phrase appears on page 88; the second an exact count with pages (grey is 2, on pages 9 and 53); the third "12" from the registry roster, proving the count gate does not poach hunt questions.

Two registry guard checks worth keeping in the smoke set:
- An unsolved casque whose author hints imply a location. The answer should relay it as the author's implication and say the casque is unsolved, never "it's buried at X."
- A hints question. The answer should quote the author's hint wording, not summarize it away. That confirms the anti-paste exemption is working.

**Restart services**
```bash
sudo systemctl restart ollama
sudo systemctl restart secretrag
docker restart open-webui
sudo systemctl restart askbyron
```
Note the restart order under `Requires=`: restarting Ollama does not restart serve, but serve's requests error for the few seconds Ollama is down, and a clean `systemctl stop ollama` will stop serve with it. Restart serve after Ollama if you bounce Ollama.

**Reboot verification.** The config is correct for surviving a reboot (both units enabled, `Requires=ollama.service`, absolute paths, a portable venv), but that is inference until it is watched. To convert it: `sudo reboot`, then after it comes back check `systemctl status secretrag askbyron`, `ollama ps` (model will cold-load on the first request unless warmed, section 3.4), and one `/ask`.

---

## 9. Troubleshooting (things that actually happened)

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` blank / model on CPU | Secure Boot on, or driver not built | Disable Secure Boot, reinstall driver, reboot |
| OWUI Ollama dropdown empty | Ollama bound to loopback | `OLLAMA_HOST=0.0.0.0` (3.4); point OWUI at `host.docker.internal:11434` |
| `ollama ps` CPU/GPU split | Model plus KV cache over 12 GB | flash attention plus `q8_0` KV cache (3.4) |
| Collection indexed but retrieval never sees it | Index script wrote to a relative `./chroma` while the store is elsewhere, or built into the wrong tree | Inherit `sc.CHROMA_DIR` and pass `SECRET_CHROMA_DIR` absolute (5.2); build as the service account into `/opt`'s store |
| serve 500s on every query right after a re-index | serve cached the old Chroma collection UUID at import; the rebuild dropped and recreated the collection under a new UUID, so queries hit a collection that no longer exists | Restart serve after ANY index rebuild. Staple `sudo systemctl restart secretrag` to the build step; the re-index and the restart are one operation (5.2) |
| Book search silent, or serve errors on a book question | `book.sqlite` unreadable: missing from `/opt/secretrag`, wrong owner, or a module test run as a non-service user against the `700` tree; or serve not restarted after the file was replaced | Confirm `/opt/secretrag/book.sqlite` exists and is owned by the service account; serve reads it as that account. It loads at import, so restart serve after replacing it (5.9) |
| Merge returns zero book hits | Queried `The_Secret` (capitalized); live name is lowercase `the_secret` | Use `the_secret` / `sc.COLLECTION` |
| `secret-librarian` missing from OWUI list | OWUI is containerized, so `localhost:8100` hits the container | Use Base URL `http://<box-ip>:8100/v1` (or the Docker bridge gateway) |
| Answers mix in irrelevant book-only chunks | OWUI KB left attached, double RAG | Detach `The_Secret` KB from the model (6) |
| Edits don't take effect in the live bot | Edited a staging copy the running service doesn't load, or serve holds old versions at import plus `lru_cache` | Sync to `/opt` and `sudo systemctl restart secretrag` (5.6); verify against `curl localhost:8100`, not a CLI import |
| Forum opinion showing up on a factual answer | `_forum_intent` false-fired, or a trigger term is too broad | Tighten `_FORUM_TRIGGERS` (5.3); the gate should fail toward silence |
| History question returns no forum | `_forum_intent` missed the phrasing (fails toward silence by design) | Add the phrasing to `_FORUM_TRIGGERS`, restart serve |
| Book word-count question answered in prose / wrong number | Count gate missed the phrasing so it fell through to RAG and the model guessed | Add the phrasing to the count frames in `secret_booksearch.py` (5.9), restart serve. A miss is safe (falls back to RAG); do not loosen the gate enough to poach hunt-fact "how many" questions |
| A hunt-fact "how many X" answered as a word count | Count gate poached it (extracted a target it should not have) | Tighten the count frame or add the noun to the exclusions in `secret_booksearch.py`; the gate must require a real word target and fall through otherwise (5.9) |
| Author hints get summarized instead of quoted | Registry anti-paste exemption missing from the live prompt, or serve not restarted | Confirm the registry clause is in `SYSTEM_PROMPT` (5.4), restart serve |
| Unsolved casque answer states a dig location as fact | Location leaked outside the guarded hints channel, or corrupted commentary carried another city's find location (see the 2026-08-30 SF/Langone fix) | Confirm the formatter suppresses `finder/year/location` for UNSOLVED; check the city's commentary chunk for cross-city contamination |
| Patch pre-flight fails (anchor found 0x) | Live file drifted from the expected text | Re-cut the anchor against the current file. Do not hand-edit the file to match the patcher |
| serve fails to start under systemd, exec error naming a python path | Copied venv's uvicorn wrapper shebang points at the old (home) interpreter, which `ProtectHome=true` hides | Run uvicorn as a module: `ExecStart=.../.venv/bin/python -m uvicorn serve:app ...` (3.7, 5.6) |
| serve starts clean, then errors on the first query | `HF_HOME` not pointed at the moved bge-m3 cache, or the cache isn't in `ReadWritePaths` | Copy the cache into `/opt`, set `HF_HOME`, add it to `ReadWritePaths` (3.7, 5.6) |
| First `/ask` after a reboot times out | Cold model load stacked on the first bge-m3 load | Pin the model with `KEEP_ALIVE=-1` and warm it at boot (3.4). Steady-state generations are seconds |
| serve can't write, or a write path follows the working dir | `ProtectSystem=strict` with no `ReadWritePaths`, or the relative `./chroma_db` default resolving against the wrong CWD | Pin `SECRET_CHROMA_DIR` absolute and grant `ReadWritePaths` (5.6) |
| `/ask` returns an empty body / JSON parse error in a test harness | Malformed request JSON (shell quoting mangled the payload), or the call raced a serve restart before it was ready | Build the payload with `python3 -c 'import json...'` rather than shell escaping; give the first post-restart call the full timeout |
| Model narrates "Okay, the user wants..." | Thinking mode on | `think=Off` at the preset; API callers pass `"think": false` |
| Bot: `ModuleNotFoundError: No module named 'discord'` under systemd | Dependencies never landed in the venv | Install into the venv's own pip explicitly, confirm the import, restart |
| Bot: command sync fails `403 / 50001 Missing Access` | Bot invited with the `bot` scope only | Re-invite with both `bot` and `applications.commands` scopes, restart |
| Bot: slash command never appears | Global propagation delay, or wrong guild | Guild-scoped sync via `DISCORD_GUILD_ID`, confirm the ID and the invite scope |

---

## 10. Known limitations

- Discrete-fact confabulation, mitigated as of 2026-08-27. The city registry (5.7) turns settled per-city facts into an authoritative lookup, so the model reads them instead of guessing. What's left: it only covers city-, number-, or aggregate-anchored facts, so a question that anchors on none of those still falls back to RAG and can confabulate. And prompt-injected precedence is strong but not a hard guarantee at 14B. The only guaranteed fix is a deterministic post-generation validator (phase 2, section 12).
- Discrete-fact confabulation, mitigated as of 2026-09-01. The city registry (5.7) turns settled per-city facts into an authoritative lookup. A strict prompt guardrail now forces the model to ignore CANON verse text when a registry block is present, preventing multi-verse OCR bleed on pages 49-54. The remaining gap: questions that anchor on no registry facts still fall back to RAG and can confabulate. A guaranteed fix requires a deterministic post-generation validator (phase 2, section 12), not an LLM-based second pass.
- Forum tier is narrow-use by design. `vault_forum` is ~40k posts, roughly 21x the rest of the corpus combined, but it is gated (5.3) to surface only on history/theory questions, so on everyday factual and solve questions it is never queried and contributes nothing. That is the intended trade: unverified opinion stays out of factual answers. The cost is that the gate is a keyword heuristic that fails toward silence, so a history question phrased in words the trigger misses gets no forum. Tune `_FORUM_TRIGGERS` as misses show up; it will never be exhaustive.
- Book presence, location, and count are deterministic over `book.sqlite` (section 5.9), English edition, literal text match. Limits worth stating in answers: literal match only, so OCR mangling and line-break hyphenation can hide a word that is genuinely present; painting pages carry no searchable text, so a word rendered inside a painting will not be found; verse-page counts (printed 49 to 54) sit in OCR noise and are flagged approximate. Still page-scoped, so a true verse-scoped count ("X in the Milwaukee verse") is not answerable here and remains a TODO (section 12), keyed off the registry's per-city `verse_text`.
- Commentary can carry wrong facts in fluent prose. The 2026-08-30 cleanup fixed two of these (cross-city verse dumps in City Guides, and Boston's find location written into San Francisco's Overview), but a wrong fact in grammatical English will not trip a structural probe. The registry is the backstop for the fields it owns; the free-text Overviews have no such guard, so they need periodic human eyes. See section 12 for moving the cleaning upstream.
- Numbering-guardrail leak, mostly handled. The prompt and registry now push city/verse/image referents and forbid "casque N", but watch for the occasional echo that originates in the vault notes themselves.
- Meta-commentary about the machinery. The model still occasionally names its own sources ("the AUTHORITATIVE CITY REGISTRY", "a deterministic search of the book", "the passages provided") instead of just stating the fact in voice. Harmless but off-persona. A `SYSTEM_PROMPT` line to suppress it is a section 12 item.
- Persona versus accuracy, reduced but not gone. Unsolved casques carry `status: UNSOLVED` and their author hints are guarded as clues, which curbs the warm voice sliding into false certainty. It doesn't kill it. Keep an eye on confidence swinging with phrasing on unsolved casques.
- Verbatim-hint context cost. Author hints (and, under the current exemption scope, verse text) inject verbatim, so a single city block is chunky. Several cities named in one question can pressure the 16K window. Levers: narrow the anti-paste exemption to hints only, or gate verse and hints to inject only when the question needs them.
- Single-turn. `/v1/chat/completions` ignores prior turns by design, so there's no conversational memory in OWUI. The Discord bot is single-turn too: each `/askbyron` is independent, no thread memory.
- No serialization at serve itself. All backpressure (semaphore, queue guard, cooldown) lives in the bot, so anything hitting serve's `/ask` directly bypasses it. It degrades to latency rather than a crash because Ollama runs a single inference slot and queues the rest, but there is no per-client rate limit at serve. Adding one is optional (section 12).
- Cold load after reboot. `KEEP_ALIVE=-1` keeps the model resident through idle periods, but it does not pre-load at boot. After a reboot the model cold-loads on the first request. Optional auto-warm in section 12.
- Staging/live split. Code is edited in the `<user>` tree and only reaches the live `/opt` service by a manual sync + restart. Forget the sync and the running bot keeps loading the old file with no error. A one-command deploy step (section 12) closes this; until then, verify changes against the live endpoint, not a working-copy import.
- `/health` counts the book only (220). Cosmetic, predates the merge.
- Transcript tier is roughly 20% banter. Kept in its own collection with a low pull count so it rarely wins a slot. Stripping banter properly needs a semantic summarization pass, not rules.
- One 14B resident at a time. The always-on bot and interactive chat contend for the same card, so generations are effectively serialized. If community demand outgrows it, that is a second-card or smaller-model conversation, not a bot fix.
- Public question echo. The bot posts the asker's question above the answer (7.4), so it rebroadcasts user-submitted text with a name attached. In an 18k-member server the channel and role gates are load-bearing, and a profanity screen on the echo is the lever if it starts.
- Global rules layer, field-guide gate is precision by design. The field-guide no-clues disclaimer (5.8) fails toward silence: it fires on field-guide phrasing or a covered creature name, so an obscure creature asked about with neither gets no disclaimer (a safe miss, the user can rephrase). The trade is deliberate, since a false disclaimer could point a searcher away from a real painting clue. Widen the covered phrasings and names as real questions reveal gaps.

---

## 11. Security posture

The box sits on an isolated services VLAN. Ollama and serve.py both bind broadly (`0.0.0.0`) so the containerized front-end can reach them, and access is constrained at the network layer with host and segment-scoped firewall rules, not left open. That broad bind is a deliberate current state, not a target: it means the firewall is the only thing between the services VLAN and two unauthenticated compute endpoints (the Ollama API on its port, and serve on 8100). Tightening both to loopback is an open item (section 12); until then, the firewall rules are load-bearing and should be treated that way. The private knowledge base is attached to its model preset on purpose, not published. No agent holds infrastructure tools or shell access: the community bot is RAG-only, and the future ops reader is read-only.

serve runs as a dedicated loginless account (`<user>`) under a hardened systemd unit (`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges`, `PrivateTmp`, and the rest), out of `/opt/secretrag` owned by that account at `700`. It holds no shell and no infra tools. Its one authentication gap is unchanged: serve accepts an API key but does not verify it, so it relies entirely on network controls. Enforcing that key is an open item (section 12) and matters more while the port is still bound broadly.

Corpus data is kept out of this public document. The city registry (`secret_registry.py`) holds community-privileged puzzle content (author hints, verified pairings); treat its values like the private ops note. `book.sqlite` holds the full text of the book; it is community/book content and is not committed or reproduced here either. The historical forum archive (`vault_forum`) is drawn from a public community forum, but it carries usernames and years of unverified opinion, so the raw export and its collection are not reproduced here. None of these add network surface; they are in-process data files and collections.

Firewall rules, addressing, and hardening status live in a private ops note, not here. If you adapt this runbook, don't expose the inference or RAG ports past your trusted segment, and put authentication in front of anything public.

The Discord bot is live and follows the same posture. Its token is a secret in an env file owned by the service account at `0600`, never committed. It holds no infrastructure tools, and it reaches serve over localhost on the box rather than across the network, so no inbound rule or public exposure of serve was needed (section 7). Its only outbound paths are the Discord gateway and the localhost POST to serve. The box permits outbound on the services VLAN but blocks inbound, which is all a gateway bot needs.

---

## 12. Open TODOs (non-sensitive)

- [x] City registry. Done 2026-08-27. 12-city closed-set structured-fact lookup with an aggregate roster, injected authoritative above retrieved passages. Formatter guards (no dig location for unsolved, known-unknowns as "(not established)", region-band lat/long), author hints verbatim under a status-aware guard. See section 5.7.
- [x] Discord bot. Done 2026-08-28. discord.py slash command `/askbyron`, RAG-only, calls serve's `/ask` on localhost, hardened systemd unit. See section 7.
- [x] systemd unit for serve.py. Done 2026-08-28. serve relocated to `/opt/secretrag` under a loginless account, hardened unit, `Requires=ollama.service`, absolute `SECRET_CHROMA_DIR`, `HF_HOME` on the moved cache, `ReadWritePaths` for store and cache, `KEEP_ALIVE=-1`. See sections 3.7 and 5.6.
- [x] Historical forum tier (gated). Done 2026-08-28. `vault_forum`, ~40k cleaned posts (2001-2019), lowest-authority COMMENTARY, queried only when `_forum_intent` matches a history/theory question. Competes normally when the gate opens, absent otherwise. See sections 5.1-5.4.
- [x] Book text presence/location/count. Done 2026-08-30. `secret_booksearch.py` over `book.sqlite` (PDF-derived, verified folios on all 226 pages). Presence/location inject and answer in-voice; word-count short-circuits deterministic and exact. Retired `secret_lookup.py` + `secret_words.db`. See section 5.9.
- [ ] `deploy.sh` for staging-to-live. The single highest-value operational cleanup. Code is edited in the `<user>` tree and only reaches `/opt` by a manual rsync + chown + restart; miss it and the bot silently runs stale code. One script that rsyncs the `.py` files (and `book.sqlite`) to `/opt/secretrag`, chowns to the service account, and `systemctl restart secretrag` collapses "changed it" and "the bot runs it" into one command and removes that whole class of mistake. Should also refuse to run if the working tree fails `py_compile`, and should restart serve after any index rebuild it triggers.
- [ ] Deterministic verse-scoped word counter. Count occurrences of a named word or phrase in a named casque's verse in Python and inject the number as authoritative, so verse-scoped count questions stop being answered by the model, which cannot count reliably (section 10). Page-scoped book presence/location/count landed 2026-08-30 (`secret_booksearch.py`, `book.sqlite`, section 5.9), but verse-scoping still needs the registry's per-city `verse_text` as source, since printed pages 49 to 54 pack multiple cities' verses and are the OCR-dirtiest in the book. The hard part is extraction: pull the target word and resolve which verse with confidence, and fail toward silence on ambiguous phrasing, because an authoritative wrong count is worse than an honest-looking miscount. Default to whole-word, case-insensitive matching.
- [ ] Prompt: discourage meta-commentary about the machinery. The model still occasionally names its own sources ("the AUTHORITATIVE CITY REGISTRY", "a deterministic search of the book", "the passages provided"). One `SYSTEM_PROMPT` line to state results in-voice without naming the scaffolding would tighten the registry, book-search, and passage cases at once (section 10).
- [ ] Move corpus cleaning upstream. The 2026-08-30 vault verse-dump strip and the SF Langone fix were applied to the derived `vault_corpus.jsonl`; the source Obsidian notes still carry both defects, and no converter from notes to `.jsonl` exists in the tree. The next regeneration reintroduces both. Fix the source notes, or rebuild the converter with the strip baked in.
- [ ] Forum trigger tuning. `_FORUM_TRIGGERS` (5.3) is a keyword heuristic that fails toward silence. Watch for history questions that come back with no forum and add their phrasings; this is ongoing, not one-time. The same applies to the book-search count and presence frames (5.9).
- [ ] Loopback-bind Ollama and serve. Both bind `0.0.0.0` today so the OWUI container can reach them, leaving the firewall as the only guard on two unauthenticated ports (section 11). The fix is coupled: bind both to `127.0.0.1` and move OWUI to host networking (or reach the host via the docker bridge gateway) so the container still connects. Do it as its own change, after confirming what each client actually targets.
- [ ] Enforce serve's API key. serve accepts but ignores the key, relying on network controls alone. Verifying it removes the single-point-of-failure where one firewall slip exposes an open compute endpoint. Cheap, both clients are ours.
- [ ] Optional: auto-warm the model after boot. `KEEP_ALIVE=-1` keeps the model resident but doesn't pre-load it, so the first request after a reboot cold-loads (section 10). A boot-time warmup (a `systemd` oneshot or an `ExecStartPost` on Ollama) takes that hit off the first caller.
- [ ] Phase-2 output validator. A deterministic post-generation check on the registry's discrete fields (verse, image, stone). Regenerate with the value pinned, not silent string replacement. Scope it to single-city questions and those three fields, and build it only after measuring how often injection alone actually misses in production.
- [ ] Rate limiting at serve. All backpressure lives in the bot today, so a direct hit on serve bypasses it (section 10). Lower priority now that concurrency can't OOM the card, but still the right place for a per-client limit.
- [ ] Optional future corpora, following the `vault_forum` pattern (own collection, same embedder/space, indexed as the service account, gated or tiered as appropriate). Byron's pre-publication manuscript for comparing pre-pub changes, which must be a separate NON-canonical tier that never outranks the published book (published-wins guardrail), ideally a structured draft-vs-published diff rather than raw retrieval. A news/advertising archive for a public-history timeline. Both are additive and must not touch `the_secret` or the registry.
- [ ] Extend `/health` to count all four collections.
- [ ] Optional: semantic show-notes pass over transcripts to kill the interleaved banter.
- [ ] Revisit the persona voice knob if warmth keeps leaking into false certainty on unsolved casques.
- [ ] Optional: narrow the registry anti-paste exemption to `hints` only if verses shouldn't paste unprompted.
- [ ] Optional: profanity screen on the bot's question echo if the public rebroadcast (7.4) gets abused.

*(Network and firewall hardening tasks live in the private ops note.)*

---

## 13. Changelog

- **2026-09-01, pipeline restoration and prompt hardening. Stripped the experimental ClaudeCode LLM verifier from serve.py to restore the single-pass generation pipeline, relying on structural isolation rather than prompt-chained validation to prevent hallucinations and latency spikes. Hardened the SYSTEM_PROMPT in secret_common.py with specific behavioral rules:
    Identity: Instructed the model to never assign community identities (e.g., "Seeds") to the user.
    Memory: Explicitly defined the bot as stateless, preventing it from claiming it logs questions for future seekers.
    Verse formatting: Forced exact preservation of original line breaks when quoting verses.
    Registry Verse Override: Banned the LLM from reading CANON verse text when a registry block is present, fixing the confabulation where Boston's facts were stitched to Houston's verse text.
    Image Contents: Authorized the model to recognize that every painting visually depicts its associated flower and jewel, fixing the false-negative defensive denials.
- **2026-08-30, corpus cleanup + deterministic book search.** Fixed cross-city verse contamination in `vault_commentary`: City Guide notes embedded all twelve cities' verses under one city's header (a scraped-page archive dump frozen into `vault_corpus.jsonl`), which let the model attribute Houston's "982" and New Orleans's "sovereign people" to New York. Cleaned the `.jsonl` (kept the one `## Overview` chunk per city, cut everything from the Legacy block down, dropped raw-scrape and dump-continuation rows, stripped the wrong `Gemstone:`/`Verse/Painting:` card fields the registry owns) and re-indexed; commentary dropped 344 to 329. Separately removed Boston's actual find location (Langone Park) from San Francisco's Overview, a solved casque's dig site contaminating an unsolved one. Registry: corrected "Soul of Clouds" to "Souls of Clouds" and reconciled the mail-in-void rule block between staging and live.
  Added deterministic full-book text search. New `book.sqlite` built from the OCR'd PDF (`build_book_index.py`, PyMuPDF): one row per PDF page, printed folio = pdf_page - 2 (two blank leaves lead; paintings are numbered, so the offset is constant and was consistency-checked across all 226 numbered pages). New stdlib-only `secret_booksearch.py` answers three intents off `book.sqlite`: presence ("is X in the book") and location ("what page is X on") inject an `=== AUTHORITATIVE BOOK TEXT SEARCH ===` block above the passages and are answered in-voice; word-count ("how many times does X appear") short-circuits in `serve.py` and returns a deterministic, boundary-matched (`\bX\b`) count directly, bypassing the LLM. Verse-page counts (49-54) are flagged approximate due to OCR noise. This retired `secret_lookup.py` + `secret_words.db` (single-word, separate/less-trustworthy index), folded into one book tier over the verified source. `SYSTEM_PROMPT` gained a guardrail for the book-search block. Note: this is page-scoped and does NOT deliver the verse-scoped counter (still TODO, keyed off registry `verse_text`). See section 5.9.
- **2026-08-29, global rules layer.** Added a creator-stated global hunt-rules layer to `secret_registry.py`, a sibling to the per-city registry (5.8): rules that hold for all twelve casques rather than one city. Two blocks are live: burial depth, container, and excluded sites plus the now-void mail-in claim process; and a field-guide ("back of the book") no-clues disclaimer. `registry_block_for` prepends every matching rule block above the city or roster output under one shared authoritative header, with no `serve.py` change, since the rules ride the existing registry injection point. Two gates with different risk postures: the burial/claim gate is tuned generous because over-firing only adds a block that is entirely true, while the field-guide gate is precision and fails toward silence because the paintings depict the fair folk and are the clue source, so a false disclaimer could wave a searcher off a real clue. The field-guide gate fires on field-guide phrasing or a named field-guide creature, and deliberately excludes names that overlap with real-world symbols or historical referents that could appear in a painting, plus one name common in bot-channel chatter. The claim block leads with the void verdict so the model does not soften it, with the defunct mailing history demoted to background stated only if asked. Also corrected a stale registry self-test assertion left over from an earlier data fill, so the self-test passes clean again. Verified live on `/ask`: mail-in answers lead with void and the physical-recovery path, and field-guide questions return the no-clues disclaimer while still naming the paintings as the clue source.
- **2026-08-28, historical forum tier (gated).** Added `vault_forum`, a fourth Chroma collection: ~40k cleaned posts from the community's forum archive (2001-2019, 309 threads), embedded with the same bge-m3/cosine as every other tier so distances stay comparable. It is the lowest-authority COMMENTARY source and is gated - `retrieve()` queries it only when `_forum_intent(question)` matches a history-of-the-search or old-theory question, so it stays entirely out of factual and solve answers and surfaces only where it belongs. When the gate opens it competes on distance normally, no penalty, capped at `SECRET_N_FORUM`. `build_context()` labels it `[COMMENTARY - historical forum - ...]` and `SYSTEM_PROMPT` frames it as unverified, often-outdated opinion that never overrides the book, wiki, or podcast. Indexer `forum_index.py` is stdlib-only (csv + datetime), strips BBCode quote blocks, drops trivial/quote-only posts, windows long posts, and carries date/thread/author metadata for citation. Built into `/opt`'s store as the service account. Verified live on the `/ask` endpoint: forum surfaces and is reported as history on a history query, and is entirely absent on a gemstone/fact query.
- **2026-08-28, serve.py under systemd.** Moved serve off tmux onto a hardened systemd unit (`secretrag`), closing the last "dies on reboot" gap. Relocated to `/opt/secretrag` under a dedicated loginless account, matching the bot's pattern. `Requires=ollama.service`; `SECRET_CHROMA_DIR` pinned absolute; `HF_HOME` pointed at the bge-m3 cache copied into `/opt`, with `ReadWritePaths` for the store and cache under `strict`; uvicorn launched as `python -m uvicorn` to dodge the copied-venv wrapper shebang. Added `OLLAMA_KEEP_ALIVE=-1` so the model stays resident, with the boundary that it still cold-loads once on the first request after a reboot. Confirmed live: unit enabled and active, `/health` clean, grounded answers with sources.
- **2026-08-28, Discord bot live.** Added a discord.py slash command (`/askbyron`) that forwards a member's question to serve's `/ask` and posts the answer back. Runs on the box as a dedicated loginless service account under a hardened systemd unit. RAG-only by construction. Handles the box's constraints: defers and serializes behind a semaphore of 1, a queue guard and per-user cooldown for the 18k-member abuse surface, sub-2000-character chunking, a friendly message when serve is down, and a question echo since slash invocations are otherwise private to the caller.
- **2026-08-27, city registry live.** Added `secret_registry.py`, a stdlib-only 12-city closed-set structured-fact lookup with an aggregate roster for count/status questions. Per-city verified facts inject authoritative above the retrieved passages on a city or verse/image-number match. Formatter guards: unsolved casques never emit a find location, known-unknowns render "(not established)", lat/long labelled region-not-dig-site. Author hints inject verbatim under a status-aware guard, exempt from the summarize-don't-paste rule. `SYSTEM_PROMPT` updated: registry is top authority. Verified live on image-to-verse-to-stone binding, verbatim author hints, and an author-implied location staying hedged on an unsolved casque.
- **2026-08-27, serve.py RAG pivot.** Moved retrieval to the custom `serve.py`/Chroma stack. Indexed `vault_commentary` (344) and `vault_transcripts` (1303) alongside `the_secret` (220). Rewrote `secret_common.py` retrieval into a distance merge with tier labels, added the CANON/COMMENTARY, numbering, found-vs-unsolved, and synthesize guardrails, and merged in the "Ask Byron" persona. Wired OWUI over an OpenAI connection and detached OWUI's native `The_Secret` KB.
- **2026-08-20, initial build.** Ubuntu 24.04, driver 595.84, Ollama plus qwen3:14b, qwen3-chat (16K), flash attn plus q8_0 cache, Open WebUI. *The Secret* corpus v1 to v2 (heading-magnet fix).
