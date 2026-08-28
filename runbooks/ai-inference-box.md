# AI Inference Box (`<host>`) - Build & Operations Runbook

> **Public version.** Network specifics are genericized. Placeholders: `<box-ip>` is the box's LAN address, `<host>` its hostname, `<services-vlan>` the isolated services VLAN, `<user>` the service account. Real values, firewall rules, detailed security posture, and the community's verified puzzle data (verses, litany, author hints, per-city values) are kept out of this document.

**Host:** `<host>` at `<box-ip>` (`<services-vlan>`)
**Purpose:** always-on local LLM inference behind a RAG chatbot for *The Secret* treasure-hunting community, plus a private chat hub and a future read-only ops reader.
**Status:** operational. Engine and private hub done. The custom three-collection RAG service (serve.py) is live and wired into Open WebUI, and the deterministic city registry now runs inside the serve pipeline. Discord bot and ops reader still pending.
**Last updated:** 2026-08-27

---

## 1. Overview

One inference box, one model endpoint, several thin front-ends hung off it. The box is the engine. Each use case is a separate steering wheel pointed at the same Ollama endpoint. Front-ends never share tool sets, so the community bot gets RAG and nothing else.

Retrieval, as of 2026-08-27, runs through the custom `serve.py` service (FastAPI, Chroma, bge-m3), not Open WebUI's built-in document RAG. serve.py owns embedding, retrieval, tiering, and the grounding prompt. In front of retrieval sits a deterministic city registry (`secret_registry.py`). When a question names a city, or references a verse or image number, the verified facts for that casque get injected above the retrieved passages and marked authoritative. Open WebUI is just a front-end that talks to serve over an OpenAI-compatible connection. Its own knowledge-base RAG for this model is detached and unused (see section 6). The older OWUI-native RAG approach is kept as history in section 5-legacy, but it is not the live path anymore.

**Consumers**

- Private Open WebUI chat and document RAG hub (built).
- "Ask Byron" community model, served by serve.py, surfaced in OWUI (built).
- Discord RAG bot for the community, RAG-only with no infra tools (pending, calls serve's `/ask`).
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

Capacity note: 12 GB of VRAM holds one ~14B model at a time (Q4 is about 9.3 GB of weights). Idle draw is around 15 W at 0% util. The GPU only spikes to ~100% for the seconds it spends generating, then drops back to idle.

Embedder note for the serve.py stack: bge-m3 runs on CPU on purpose (`SECRET_EMBED_DEVICE=cpu`) to keep VRAM free for qwen3. CPU embedding is slow and silent. There is no progress bar inside `encode()`, so a batch can sit for a minute or more with no output. That is normal, not a hang. Confirm it with `top` (python pegging cores) or a moving mtime on `chroma_db/chroma.sqlite3`, not a frozen one.

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
Needed for two reasons: the Dockerized front-end has to reach Ollama, and a 16K context has to fit in 12 GB.
```bash
sudo systemctl edit ollama
```
Add under `[Service]`:
```
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
ss -tlnp | grep 11434   # expect the port bound for container reachability
```
- `OLLAMA_HOST=0.0.0.0` lets the container reach Ollama. Scope it at the network layer (see section 10).
- `OLLAMA_FLASH_ATTENTION=1` is faster and lighter with no quality cost, and it's a prerequisite for KV-cache quantization.
- `OLLAMA_KV_CACHE_TYPE=q8_0` halves KV-cache memory. That's what buys 16K context at 100% GPU instead of spilling to CPU.

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

Docker networking caveat that bit us on this build: OWUI runs inside a Docker container, so `localhost:<port>` in its connection settings resolves to the container, not the host. Any host-side service OWUI needs to reach (serve.py, for one) has to be addressed by the box LAN IP `<box-ip>`, not `localhost`. If the LAN IP is unreachable, fall back to the Docker bridge gateway (usually `172.17.0.1`).

---

## 4. Configuration reference

| Item | Value |
|---|---|
| Ollama override | `/etc/systemd/system/ollama.service.d/override.conf` |
| Ollama endpoint (from container) | `http://host.docker.internal:11434` |
| Open WebUI | `http://<box-ip>:3000`, container `open-webui`, volume `open-webui` |
| Base model | `qwen3:14b` (4K default context, thinks by default) |
| Tuned model | `qwen3-chat` (16K context) |
| serve.py (custom RAG) | `http://<box-ip>:8100`, FastAPI, run under tmux session `serve` |
| serve to OWUI connection | OpenAI connection, Base URL `http://<box-ip>:8100/v1`, dummy key |
| Chroma store | `~/secretrag/chroma_db` (`SECRET_CHROMA_DIR`, default `./chroma_db`) |
| Collections | `the_secret` (220), `vault_commentary` (344), `vault_transcripts` (1303) |
| Embedder | bge-m3 (`BAAI/bge-m3`), CPU, cosine space |
| City registry | `~/secretrag/secret_registry.py`, 12-city structured-fact lookup, stdlib-only, imported by serve |

Thinking mode: Qwen3 narrates its reasoning by default. Turn it off with the Ollama API field `"think": false` (a real setting, not a prompt hack), or at the OWUI model-preset level (Workspace, Models). Don't do it in the per-chat Controls panel, which reverts on new chats.

---

## 5. RAG service - serve.py (current, authoritative)

This is the live retrieval path. Everything the community model answers with runs through it, whether the call comes in on `/ask` (Discord) or `/v1/chat/completions` (OWUI).

Location: `~/secretrag/` (service account `<user>`, venv `.venv`).
Shared module: `secret_common.py` holds the config, embedder, Chroma handles, retrieval, and the grounding prompt. `build_index.py`, `serve.py`, and the index scripts all import it, so nothing can disagree about model names, paths, or collection names. `secret_registry.py` is a sibling module that serve imports (section 5.7).

### 5.1 Corpus & collections
All three collections live in one Chroma store (`chroma_db`) so retrieval can query across them:

| Collection | Tier | Count | Source |
|---|---|---|---|
| `the_secret` | CANON (book text) | 220 | *The Secret* English + Japanese editions (built by `build_index.py`) |
| `vault_commentary` | COMMENTARY (wiki) | 344 | Cleaned Obsidian vault: 12Treasures / People / Topics notes |
| `vault_transcripts` | COMMENTARY (podcast) | 1303 | 60 episode cards + 1243 ASR transcript chunks |

The collection name is lowercase `the_secret`. Chroma is case-sensitive. The index-script comments say `The_Secret`, which is a doc typo. The live name is lowercase. Get it wrong in the merge and the book tier silently returns zero hits.

### 5.2 Indexing the vault collections
Scripts: `vault_index.py` builds `vault_commentary`, `transcripts_index.py` builds `vault_transcripts`. Each one drops and rebuilds only its own collection. `the_secret` is never touched.

Config lesson that bit us: the index scripts originally hardcoded `CHROMA_PATH = "./chroma"`, but the book store is `./chroma_db` (`sc.CHROMA_DIR`). Hardcoding the wrong path builds the new collections into a separate empty store, and retrieval never sees them. No error, just silence. The fix was to make both scripts inherit the real path:
```python
import secret_common as sc
CHROMA_PATH = sc.CHROMA_DIR
```
That also honors `SECRET_CHROMA_DIR`. Always run them from `~/secretrag` so the import resolves.

To (re)index:
```bash
cd ~/secretrag && source .venv/bin/activate
python vault_index.py
python transcripts_index.py
# confirm all three are co-located:
python -c "import secret_common as sc, chromadb; c=chromadb.PersistentClient(path=sc.CHROMA_DIR); print([(x.name,x.count()) for x in c.list_collections()])"
```

### 5.3 Tiered retrieval (secret_common.py)
`retrieve()` queries all three collections, merges by cosine distance, and returns the top N. The distances are comparable only because all three share bge-m3 and cosine space. The per-tier pull counts are deliberately lopsided: transcripts are noisier, so fewer get pulled and banter rarely wins a slot.

| Env var | Default | Meaning |
|---|---|---|
| `SECRET_N_BOOK` | 6 | book chunks queried |
| `SECRET_N_COMMENTARY` | 6 | wiki chunks queried |
| `SECRET_N_TRANSCRIPT` | 3 | transcript chunks queried (kept low, noisier tier) |
| `SECRET_MERGE_TOP_N` | 8 | final merged chunks after distance sort |
| `SECRET_MAX_DISTANCE` | 2.0 | loose ceiling; tighten toward ~1.0 to drop weak hits |

Good queries land around 0.36 to 0.52, so the 2.0 ceiling currently does nothing. It's a safety net, not a filter. Tightening it is the lever if junk starts surfacing on vague queries.

`build_context()` labels each passage by tier so the model can see the authority level: `[CANON - book text - ...]`, `[COMMENTARY - community wiki - ...]`, `[COMMENTARY - podcast - ...]`.

### 5.4 System prompt (grounding + persona)
Lives in `secret_common.py` as `SYSTEM_PROMPT`. serve injects it, which means the OWUI model's own System Prompt field does nothing for this model. The current prompt covers:
- Persona: "Ask Byron", a warm, dry reference-librarian voice that's honest about ambiguity.
- Tier guardrail: CANON is authoritative fact, COMMENTARY is informed opinion. The model may synthesize and attribute theories, but it must not present commentary as settled fact.
- Numbering guardrail: no invented "casque 1/2/3" ordering. Casques are named by city, verse number, or image/painting number. Verse/image/city pairings are established and may be stated. For a found casque, the documented find location is fact. For an unsolved one, the model must not state or imply a dig location as correct.
- Synthesize, don't paste: answer in its own words, don't dump full verses or wiki cards verbatim unless asked, and drop material about other cities that wasn't asked about.
- Registry guardrail (added 2026-08-27): when an `=== AUTHORITATIVE CITY REGISTRY ===` block is present, it is the top authority. Its values override any conflicting passage value, including any stray "casque N" numbering. The synthesize-don't-paste rule is exempted for the registry, so its AUTHOR HINTS come through in the author's own words instead of paraphrased. Author hints are still clues toward an unsolved answer, relayed as hints, never as the settled solution or dig location. See section 5.7.

### 5.5 serve.py endpoints
| Endpoint | Use | Notes |
|---|---|---|
| `POST /ask` | Discord bot (simple JSON) | `{"question","edition"}` returns `{"answer","sources"}`. Supports explicit edition. |
| `POST /v1/chat/completions` | OWUI (OpenAI-compatible) | Single-turn by design. Takes the last user message, ignores prior turns. Edition is always auto-sniffed. |
| `GET /v1/models` | OWUI model discovery | advertises `secret-librarian`. |
| `GET /health` | health check | Counts `the_secret` only (220), predates the merge. Seeing 220 here does not mean the vault collections are missing. Cosmetic, extend later. |

Both `/ask` and `/v1/chat/completions` run through the same `answer_question` chain: retrieve, build_context, registry injection, build_messages, Ollama. The registry block is prepended to the context inside `answer_question` (section 5.7), so both endpoints get the registry the same way.

### 5.6 Running serve
Currently run under a detached tmux session. Not a systemd service yet, so it dies on reboot (see TODOs).
```bash
tmux new -d -s serve 'cd ~/secretrag && source .venv/bin/activate && uvicorn serve:app --host 0.0.0.0 --port 8100'
tmux ls && ss -tlnp | grep 8100   # expect a listener on the serve port
```
Restart after any edit to `secret_common.py` or `secret_registry.py`. The prompt, retrieval, and registry load at import plus `lru_cache`, and `secret_registry` is imported once on the first `/ask` and then cached, so a running process keeps the old versions until you restart it:
```bash
tmux kill-session -t serve
tmux new -d -s serve 'cd ~/secretrag && source .venv/bin/activate && uvicorn serve:app --host 0.0.0.0 --port 8100'
```
> Mobile SSH note: the tmux prefix (Ctrl-b) doesn't come through reliably from mobile keyboards. Use `tmux new -d` (starts detached, no prefix needed) and just close the tab to leave a session. Don't foreground uvicorn in the SSH shell, it dies on disconnect.

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

`registry_block_for(question)` returns the union of the city-name and number-referent matches, formatted, or an empty string (a no-op) when nothing matches. It runs independently of retrieval. Even if the vector search comes back with junk, the registry still injects.

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
cd ~/secretrag && python3 secret_registry.py
# expect: self-test OK - 12 cities; validate: clean
```

How it was wired: integration went through an anchored-splice patcher (`go_live_patch.py`). It pre-flights every anchor (has to match exactly once or it aborts and touches nothing), timestamped-backs-up each target file, applies the edits, runs `py_compile`, and rolls back on failure. Two targets: the `serve.py` prepend above and the `secret_common.py` prompt clause. Keep future `secret_common.py` edits the same way: anchored splices with a backup and a compile check.

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

## 7. Operations & verification

**Health checks**
```bash
nvidia-smi                         # GPU present, VRAM in use
ollama ps                          # model resident, PROCESSOR 100% GPU, CONTEXT 16384
curl -I http://localhost:3000      # Open WebUI serving (200/307 = alive)
docker ps                          # open-webui Up / (healthy)
ss -tlnp | grep 11434              # Ollama port
ss -tlnp | grep 8100               # serve.py port
tmux ls                            # serve session alive
curl -s localhost:8100/v1/models | python3 -m json.tool   # serve advertises secret-librarian
```

**Registry self-test** (stdlib-only, no venv needed):
```bash
cd ~/secretrag && python3 secret_registry.py
# expect: self-test OK - 12 cities; validate: clean
```

**Smoke-test retrieval directly** (first call loads bge-m3, so a several-second silent pause is normal):
```bash
cd ~/secretrag && source .venv/bin/activate
python -c "import secret_common as sc; [print(h['tier'], round(h['distance'],3), h['cite']) for h in sc.retrieve('Roanoke Elizabethan Gardens')]"
```
Expect a mix of `canon`, `commentary`, and `transcript`, distance-sorted. All `canon` means the vault collections aren't being reached.

**Smoke-test the registry end to end** (proves injection is live, first call pauses loading bge-m3):
```bash
# a number-referent question: the registry should bind image to verse to stone to city
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"what verse number and gemstone go with image 2?","edition":null}' | python3 -m json.tool
```
The discrete facts should come back from the registry, correct and bound to the right city, not a confabulated guess. The registry rides in the context above the passages, so it won't show up in the response's `sources` array (that list is the retrieved hits only).

Two guard checks worth keeping in the smoke set:
- An unsolved casque whose author hints imply a location. The answer should relay it as the author's implication and say the casque is unsolved, never "it's buried at X."
- A hints question. The answer should quote the author's hint wording, not summarize it away. That confirms the anti-paste exemption is working.

**Restart services**
```bash
sudo systemctl restart ollama
docker restart open-webui
# serve: see section 5.6
```

---

## 8. Troubleshooting (things that actually happened)

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` blank / model on CPU | Secure Boot on, or driver not built | Disable Secure Boot, reinstall driver, reboot |
| OWUI Ollama dropdown empty | Ollama bound to loopback | `OLLAMA_HOST=0.0.0.0` (3.4); point OWUI at `host.docker.internal:11434` |
| `ollama ps` CPU/GPU split | Model plus KV cache over 12 GB | flash attention plus `q8_0` KV cache (3.4) |
| Vault collections indexed but retrieval never sees them | Index script wrote to `./chroma` while book is in `./chroma_db` | Import `sc.CHROMA_DIR` in index scripts (5.2), run from `~/secretrag` |
| Merge returns zero book hits | Queried `The_Secret` (capitalized); live name is lowercase `the_secret` | Use `the_secret` / `sc.COLLECTION` |
| `secret-librarian` missing from OWUI list | OWUI is containerized, so `localhost:8100` hits the container | Use Base URL `http://<box-ip>:8100/v1` (or the Docker bridge gateway) |
| Answers mix in irrelevant book-only chunks | OWUI KB left attached, double RAG | Detach `The_Secret` KB from the model (6) |
| Prompt/retrieval/registry edits don't take effect | serve holds old versions at import plus `lru_cache` | Restart the tmux `serve` session (5.6). Required after `secret_common.py` or `secret_registry.py` edits. |
| Discrete facts still confabulated on a city question | Question named neither a city nor a verse/image number, so the registry didn't fire; or serve wasn't restarted after a registry edit | Confirm `registry_block_for(question)` is non-empty, restart serve |
| Author hints get summarized instead of quoted | Registry anti-paste exemption missing from the live prompt, or serve not restarted | Confirm the registry clause is in `SYSTEM_PROMPT` (5.4), restart serve |
| Unsolved casque answer states a dig location as fact | Location leaked outside the guarded hints channel | Confirm the formatter suppresses `finder/year/location` for UNSOLVED, keep any author-implied location in `hints` only |
| Registry patch pre-flight fails (anchor found 0x) | Live `serve.py` or `secret_common.py` drifted from the expected text | Re-cut the anchor against the current file. Do not hand-edit the file to match the patcher. |
| serve looks frozen mid-index | bge-m3 on CPU embeds silently, no progress bar | Confirm with `top` (python pegging cores) or a moving `chroma.sqlite3` mtime, then wait |
| serve gone after reboot / SSH drop | Runs in tmux, not systemd, or was foregrounded in SSH | Restart with `tmux new -d` (5.6). TODO: systemd unit. |
| Model narrates "Okay, the user wants..." | Thinking mode on | `think=Off` at the preset; API callers pass `"think": false` |

---

## 9. Known limitations

- Discrete-fact confabulation, mitigated as of 2026-08-27. The city registry (5.7) turns settled per-city facts into an authoritative lookup, so the model reads them instead of guessing. What's left: it only covers city- or number-anchored facts, so a question that names neither a city nor a verse/image number still falls back to RAG and can confabulate. And prompt-injected precedence is strong but not a hard guarantee at 14B. The only guaranteed fix is a deterministic post-generation validator (phase 2, section 11).
- Numbering-guardrail leak, mostly handled. The prompt and registry now push city/verse/image referents and forbid "casque N", but watch for the occasional echo that originates in the vault notes themselves.
- Persona versus accuracy, reduced but not gone. Unsolved casques carry `status: UNSOLVED` and their author hints are guarded as clues, which curbs the warm voice sliding into false certainty. It doesn't kill it. Keep an eye on confidence swinging with phrasing on unsolved casques, and on author hints being framed as if they were canonical verse.
- Verbatim-hint context cost. Author hints (and, under the current exemption scope, verse text) inject verbatim, so a single city block is chunky. Several cities named in one question can pressure the 16K window. Levers: narrow the anti-paste exemption to hints only, or gate verse and hints to inject only when the question needs them.
- Single-turn. `/v1/chat/completions` ignores prior turns by design, so there's no conversational memory in OWUI. Follow-ups like "what about the Japanese edition of that?" lose the referent.
- `/health` counts the book only (220). Cosmetic, predates the merge.
- Transcript tier is roughly 20% banter. Kept in its own collection with a low pull count so it rarely wins a slot. Stripping banter properly needs a semantic summarization pass, not rules.
- One 14B resident at a time. The always-on bot and interactive chat contend for the same card, so generations are effectively serialized.

---

## 10. Security posture

The box sits on an isolated services VLAN. Ollama and serve.py bind broadly so the containerized front-end can reach them, and access is constrained at the network layer with host and segment-scoped firewall rules, not left open. The private knowledge base is attached to its model preset on purpose, not published. No agent holds infrastructure tools or shell access: the community bot is RAG-only, and the future ops reader is read-only.

The city registry adds no network surface (it's an in-process module), but its data file holds community-privileged puzzle content: author hints and verified pairings. Treat the values in `secret_registry.py` like the private ops note and keep them out of public repos and paste-ables.

Firewall rules, addressing, and hardening status live in a private ops note, not here. If you adapt this runbook, don't expose the inference or RAG ports past your trusted segment, and put authentication in front of anything public. serve accepts an API key but does not verify it, so it has to sit behind network controls. When the Discord bot lands, the bot token is a secret (env or secret store, never committed), and the bot should reach serve over the trusted segment only.

---

## 11. Open TODOs (non-sensitive)

- [x] City registry. Done 2026-08-27. `secret_registry.py`: 12-city closed-set structured-fact lookup, injected authoritative above retrieved passages on a city or verse/image-number match. Formatter guards (no dig location for unsolved, known-unknowns as "(not established)", region-band lat/long). Author hints injected verbatim under a status-aware guard, exempt from the summarize-don't-paste rule. Wired with an anchored-splice patcher (backup plus `py_compile` plus rollback). Verified live.
- [ ] Discord bot (next up). RAG-only, `"think": false`, no infra tools, calls serve's `/ask`. Lets community members ask Ask Byron in Discord and get the answer back in Discord. Handoff written separately.
- [ ] Phase-2 output validator. A deterministic post-generation check on the registry's discrete fields (verse, image, stone). Regenerate with the value pinned, not silent string replacement. A corrector with false positives is worse than the confabulation it's chasing. Scope it to single-city questions and those three fields, and build it only after measuring how often injection alone actually misses in production.
- [ ] systemd unit for serve.py so it survives reboot (tmux-only right now).
- [ ] Extend `/health` to count all three collections.
- [ ] Optional: semantic show-notes pass over transcripts to kill the interleaved banter, then embed the summaries instead of raw windows.
- [ ] Revisit the persona voice knob if warmth keeps leaking into false certainty on unsolved casques.
- [ ] Optional: narrow the registry anti-paste exemption to `hints` only if verses shouldn't paste unprompted.

*(Network and firewall hardening tasks live in the private ops note.)*

---

## 12. Changelog

- **2026-08-27, city registry live.** Added `secret_registry.py`, a stdlib-only 12-city closed-set structured-fact lookup. Per-city verified facts inject authoritative above the retrieved passages when a question names a city or references a verse/image number (number-referent triggers, ordinal-guarded). Formatter guards: unsolved casques never emit a find location, known-unknowns render "(not established)", lat/long is labelled region-not-dig-site. Author hints inject verbatim under a status-aware guard (clues-not-settled for unsolved), exempt from the summarize-don't-paste rule so the author's wording reaches the seeker. `SYSTEM_PROMPT` updated: registry is top authority over passages, plus the anti-paste exemption. Wired with `go_live_patch.py` (pre-flight, timestamped backup, `py_compile`, rollback). `validate_registry()` enforces unique verse and image numbers, found-completeness, and unsolved-no-location. Verified live against serve on image-to-verse-to-stone binding, verbatim author hints, and an author-implied location staying hedged on an unsolved casque.
- **2026-08-27, serve.py RAG pivot.** Moved retrieval to the custom `serve.py`/Chroma stack. Indexed two new tiers into the shared store, `vault_commentary` (344) and `vault_transcripts` (1303), alongside `the_secret` (220). Fixed the index-script Chroma path to inherit `sc.CHROMA_DIR` (the silent wrong-store bug). Rewrote `secret_common.py` retrieval into a three-collection distance merge with tier labels, added CANON/COMMENTARY, numbering, found-vs-unsolved, and synthesize guardrails, and merged in the "Ask Byron" persona. Started serve under tmux on the serve port, wired OWUI to it over an OpenAI connection on the box LAN IP, and detached OWUI's native `The_Secret` KB. Flagged discrete-fact confabulation, which set up the city registry as the next job.
- **2026-08-20, initial build.** Ubuntu 24.04, driver 595.84, Ollama plus qwen3:14b, qwen3-chat (16K), flash attn plus q8_0 cache, Open WebUI. *The Secret* corpus v1 to v2 (heading-magnet fix). Retrieval Top K raised to 5-6.
