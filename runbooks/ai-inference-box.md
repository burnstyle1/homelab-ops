# AI Inference Box (`<host>`) — Build & Operations Runbook

> **Public version.** Network specifics are genericized. Placeholders:
> `<box-ip>` = the box's LAN address · `<host>` = its hostname · `<services-vlan>` = the isolated services VLAN · `<user>` = the service account. Real values, network-layer access controls, detailed security posture, **and the community's verified puzzle data (verses, litany, author hints, per-city values)** live outside this document.

**Host:** `<host>` — `<box-ip>` (`<services-vlan>`)
**Purpose:** Always-on local LLM inference serving a RAG chatbot for *The Secret* treasure-hunting community, plus a private chat hub and a future read-only ops reader.
**Status:** Operational — engine + private hub done; **custom three-collection RAG service (serve.py) live and wired into Open WebUI; deterministic city registry (structured-fact lookup) live in the serve pipeline.** Discord bot and ops reader pending.
**Last updated:** 2026-08-27

---

## 1. Overview

One inference box, one model endpoint, several thin front-ends hung off it. The box is the engine; each use case is a separate steering wheel pointed at the same Ollama endpoint. Front-ends never share tool sets — the community bot gets RAG only and zero infrastructure access.

**Retrieval architecture (as of 2026-08-27):** the authoritative RAG path is the **custom `serve.py` service** (FastAPI + Chroma + bge-m3), not Open WebUI's built-in document RAG. serve.py owns embedding, retrieval, tiering, and the grounding prompt. Sitting in front of retrieval is a **deterministic city registry** (`secret_registry.py`): when a question names a city — or references a verse/image number — verified structured facts for that casque are injected *above* the retrieved passages, marked authoritative. Open WebUI is a thin front-end that reaches serve as an OpenAI-compatible connection; its own knowledge-base RAG for this model is **detached and unused** (see §6). The earlier OWUI-native RAG approach is retained here as history (§5-legacy) but is no longer the live path.

**Consumers**

- Private Open WebUI chat + document RAG hub (built).
- "Ask Byron" community model — served by serve.py, surfaced in OWUI (built).
- Discord RAG bot for the community — RAG-only, no infra tools (pending; calls serve's `/ask`).
- Read-only ops reader — observability summaries, no command execution (pending).

**Design rules**

- No coding workloads (offloaded to a hosted assistant; a 12 GB card is the wrong tool for it).
- No sudo, no shell/command execution by any agent. Read-only only.
- Isolation is structural, not prompt-based: the bot can't touch the network because it holds no tools that can, not because it was told not to.

---

## 2. Hardware & OS baseline

| Component | Detail |
|---|---|
| Motherboard | Gigabyte GA-Z270X-Gaming K7 |
| RAM | 64 GB DDR4 |
| GPU | NVIDIA RTX 3060 12 GB (GA106, LHR — irrelevant to inference) |
| OS | Ubuntu Server 24.04.4 LTS, bare metal |
| Driver / CUDA | NVIDIA 595.84 / CUDA 13.2 |

Capacity note: 12 GB VRAM = one ~14B model resident at a time (Q4 ≈ 9.3 GB weights). Idle draw ~15 W / 0% util; the GPU spikes to ~100% only for the seconds it is generating, then returns to idle.

**Embedder note (serve.py stack):** bge-m3 runs on **CPU** by design (`SECRET_EMBED_DEVICE=cpu`) to keep VRAM free for qwen3. CPU embedding is slow and silent (no progress bar inside `encode()`); a batch can take a minute-plus with no output. That is normal, not a hang — verify with `top` showing python pegging cores, not a frozen mtime on `chroma_db/chroma.sqlite3`.

---

## 3. Build procedure (reproducible from bare metal)

### 3.0 BIOS
- Disable Secure Boot. Otherwise the NVIDIA kernel module won't load and the GPU silently disappears with no obvious error.

### 3.1 OS
- Install Ubuntu Server 24.04 LTS, minimal, with OpenSSH server.
- Assign a fixed address (DHCP reservation) on the services VLAN.

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
- `OLLAMA_HOST=0.0.0.0` — lets the container reach Ollama. **Scope this at the network layer** (see §10).
- `OLLAMA_FLASH_ATTENTION=1` — faster, lower memory, no quality cost; prerequisite for KV-cache quantization.
- `OLLAMA_KV_CACHE_TYPE=q8_0` — halves KV-cache memory; buys 16K context at 100% GPU instead of spilling to CPU.

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
- Browse to `http://<box-ip>:3000`. First account = admin — claim it immediately.
- Ollama connection URL in OWUI = `http://host.docker.internal:11434`.

**Docker networking caveat (bit us this build):** OWUI runs **inside a Docker container**. `localhost:<port>` from OWUI's connection settings resolves to the *container*, not the host. Any host-side service OWUI must reach (like serve.py) must be addressed by the **box LAN IP** `<box-ip>`, not `localhost`. Fallback if the LAN IP is unreachable: the Docker bridge gateway (typically `172.17.0.1`).

---

## 4. Configuration reference

| Item | Value |
|---|---|
| Ollama override | `/etc/systemd/system/ollama.service.d/override.conf` |
| Ollama endpoint (from container) | `http://host.docker.internal:11434` |
| Open WebUI | `http://<box-ip>:3000`, container `open-webui`, volume `open-webui` |
| Base model | `qwen3:14b` (4K default context, thinks by default) |
| Tuned model | `qwen3-chat` (16K context) |
| **serve.py (custom RAG)** | **`http://<box-ip>:8100`** — FastAPI, run under tmux session `serve` |
| **serve → OWUI connection** | **OpenAI connection, Base URL `http://<box-ip>:8100/v1`, dummy key** |
| **Chroma store** | **`~/secretrag/chroma_db`** (`SECRET_CHROMA_DIR`, default `./chroma_db`) |
| **Collections** | **`the_secret` (220), `vault_commentary` (344), `vault_transcripts` (1303)** |
| **Embedder** | **bge-m3 (`BAAI/bge-m3`), CPU, cosine space** |
| **City registry** | **`~/secretrag/secret_registry.py`** — 12-city structured-fact lookup, stdlib-only, imported by serve |

**Thinking mode:** Qwen3 narrates its reasoning by default. Disable via the Ollama API field `"think": false` (not a prompt hack), or at the OWUI model-preset level (Workspace → Models), not the per-chat Controls panel (which reverts on new chats).

---

## 5. RAG service — serve.py (current, authoritative)

The live retrieval path. Everything the community model answers with flows through this, whether hit via `/ask` (Discord) or `/v1/chat/completions` (OWUI).

**Location:** `~/secretrag/` (service account `<user>`, venv `.venv`).
**Shared module:** `secret_common.py` — single source of truth for config, embedder, Chroma handles, retrieval, and the grounding prompt. `build_index.py`, `serve.py`, and the index scripts all import it so they can't disagree about model/paths/collection names. **`secret_registry.py`** is a sibling module imported by serve (see §5.7).

### 5.1 Corpus & collections
All three collections live in **one Chroma store** (`chroma_db`) so retrieval can query across them:

| Collection | Tier | Count | Source |
|---|---|---|---|
| `the_secret` | CANON (book text) | 220 | *The Secret* English + Japanese editions (built by `build_index.py`) |
| `vault_commentary` | COMMENTARY (wiki) | 344 | Cleaned Obsidian vault — 12Treasures / People / Topics notes |
| `vault_transcripts` | COMMENTARY (podcast) | 1303 | 60 episode cards + 1243 ASR transcript chunks |

**Collection name is lowercase `the_secret`** (Chroma is case-sensitive). The index-script comments say `The_Secret` — that's a doc typo; the live name is lowercase. Get it wrong in the merge and the book tier silently returns zero hits.

### 5.2 Indexing the vault collections
Scripts: `vault_index.py` (→ `vault_commentary`), `transcripts_index.py` (→ `vault_transcripts`). Both drop-and-rebuild only their own collection; `the_secret` is never touched.

**Critical config lesson (bit us):** the index scripts originally hardcoded `CHROMA_PATH = "./chroma"`, but the book store is `./chroma_db` (`sc.CHROMA_DIR`). Hardcoding the wrong path builds the new collections into a *separate empty store* and retrieval never sees them — a silent no-error failure. **Fix:** both scripts now do
```python
import secret_common as sc
CHROMA_PATH = sc.CHROMA_DIR
```
so they inherit the book's real path (and honor `SECRET_CHROMA_DIR`). Always run them from `~/secretrag` so the import resolves.

To (re)index:
```bash
cd ~/secretrag && source .venv/bin/activate
python vault_index.py
python transcripts_index.py
# confirm all three co-located:
python -c "import secret_common as sc, chromadb; c=chromadb.PersistentClient(path=sc.CHROMA_DIR); print([(x.name,x.count()) for x in c.list_collections()])"
```

### 5.3 Tiered retrieval (secret_common.py)
`retrieve()` queries all three collections, merges by cosine distance, returns top-N. Distances are comparable **only because** all three share bge-m3 + cosine. Per-tier pull counts are asymmetric on purpose — transcripts are noisier, so fewer are pulled so banter rarely wins a slot:

| Env var | Default | Meaning |
|---|---|---|
| `SECRET_N_BOOK` | 6 | book chunks queried |
| `SECRET_N_COMMENTARY` | 6 | wiki chunks queried |
| `SECRET_N_TRANSCRIPT` | 3 | transcript chunks queried (kept low — noisier tier) |
| `SECRET_MERGE_TOP_N` | 8 | final merged chunks after distance sort |
| `SECRET_MAX_DISTANCE` | 2.0 | loose ceiling; tighten toward ~1.0 to drop weak hits |

Observed distances on good queries land ~0.36–0.52, so the 2.0 ceiling currently does nothing — it's a safety net, not a filter. Tightening it is the lever if junk surfaces on vague queries.

`build_context()` labels each passage by tier so the model can see authority:
`[CANON — book text · ...]`, `[COMMENTARY — community wiki · ...]`, `[COMMENTARY — podcast · ...]`.

### 5.4 System prompt (grounding + persona)
Lives in `secret_common.py` as `SYSTEM_PROMPT`; **serve injects it, so the OWUI model's own System Prompt field is dead** for this model. Current prompt combines:
- **Persona:** "Ask Byron" — warm, dry reference-librarian voice, honest about ambiguity.
- **Tier guardrail:** CANON = authoritative fact; COMMENTARY = informed opinion. May synthesize and attribute theories; must not present commentary as settled fact.
- **Numbering guardrail:** no invented sequential "casque 1/2/3" ordering — casques are identified by city, verse #, or image/painting #. Verse↔image↔city pairings ARE established and may be stated. For a **found** casque the documented find location is fact; for an **unsolved** one, must not state/imply a dig location as correct.
- **Synthesize-don't-paste:** answer in own words; don't dump full verses/wiki cards verbatim unless asked; drop other-city material that wasn't asked about.
- **Registry guardrail (added 2026-08-27):** when an `=== AUTHORITATIVE CITY REGISTRY ===` block is present it is the **top authority** — its values override any conflicting passage value (including any stray "casque N" numbering). The synthesize-don't-paste rule is explicitly **exempted for the registry**, so its **AUTHOR HINTS are relayed in the author's own wording** rather than paraphrased. Author hints remain **clues toward a still-unsolved answer** — relayed as hints, never as the settled solution or dig location. (See §5.7.)

### 5.5 serve.py endpoints
| Endpoint | Use | Notes |
|---|---|---|
| `POST /ask` | Discord bot (simple JSON) | `{"question","edition"}` → `{"answer","sources"}`. Supports explicit edition. |
| `POST /v1/chat/completions` | OWUI (OpenAI-compatible) | **Single-turn by design** — takes last user message, ignores prior turns. Edition always auto-sniffed. |
| `GET /v1/models` | OWUI model discovery | advertises `secret-librarian`. |
| `GET /health` | health check | **Counts `the_secret` only (220)** — predates the multi-collection merge. Seeing 220 here does NOT mean the vault collections are missing. Cosmetic; extend later. |

Both `/ask` and `/v1/chat/completions` route through the same `answer_question` chain: `retrieve → build_context → **registry injection** → build_messages → Ollama`. The registry block is prepended to the context inside `answer_question` (see §5.7), so **both endpoints get the registry** identically.

### 5.6 Running serve
Currently run under a detached tmux session (not yet a systemd service — dies on reboot; see TODOs):
```bash
tmux new -d -s serve 'cd ~/secretrag && source .venv/bin/activate && uvicorn serve:app --host 0.0.0.0 --port 8100'
tmux ls && ss -tlnp | grep 8100   # expect a listener on the serve port
```
**Restart after any edit to `secret_common.py` OR `secret_registry.py`** — the prompt, retrieval, and registry are loaded at import + `lru_cache`, and `secret_registry` is imported once (then cached) on the first `/ask`, so a running process holds the old versions until restart:
```bash
tmux kill-session -t serve
tmux new -d -s serve 'cd ~/secretrag && source .venv/bin/activate && uvicorn serve:app --host 0.0.0.0 --port 8100'
```
> **Mobile SSH note:** the tmux prefix (Ctrl-b) is unreliable from mobile SSH keyboards. Use `tmux new -d` (starts detached, no prefix needed) and just close the tab to leave a session — don't foreground uvicorn in the SSH shell, it dies on disconnect.

### 5.7 City registry — `secret_registry.py` (structured-fact lookup)

**Why it exists.** qwen3-14B is unreliable at emitting *precise discrete facts* (verse #, image #, gemstone, found-status) out of unstructured prose — it approximates a number that "looks right." That is inherent to generative RAG, not a context-volume problem. The registry makes those settled facts a **deterministic lookup** and injects them as authoritative context, so the model reads the fact instead of guessing it. This one move addresses discrete-fact confabulation, the "casque N" leak, and status-driven confidence drift together.

**Shape.** A closed set of **12 cities** (one per casque), stdlib-only, no external deps. Fields per city:

- `verse_no`, `image_no` — the verse and image/painting numbers
- `birthstone` — the stone, which **is also the prize jewel** (single stone field)
- `month`, `flower`, `nation`, `clock_time`, `lat_long` — decoded clue attributes (`lat_long` is a **region band, explicitly not a dig location**)
- `painting_name`, `painting_inspired_by`, `verse_image_line` — image metadata
- `litany_quote`, `verse_text` — canonical text
- `status` — `FOUND` or `UNSOLVED`
- `finder`, `year`, `location` — **found casques only**
- `hints` — creator-verified **AUTHOR HINTS**
- `verified` — injection gate; a record only injects when `True`

> The actual verified per-city values (verses, litany, author hints, coordinates, find locations) are the community's ground-truth data and are **not reproduced in this public runbook.** They live in the deployed `secret_registry.py` on the box.

**Matching / triggers.** Two access paths, matching how the community refers to casques:
- **City name** — a normalized, word-boundary matcher over the canonical names (aliases supported but generally unused; the community references by city / verse # / image #).
- **Number referents** — `by_verse(n)` / `by_image(n)` resolve `"verse 6"` / `"image 2"` to the right city. Ordinals are required on the number-first form so phrasing like *"the first 6 verses"* does not false-fire.

`registry_block_for(question)` returns the union of city-name and number-referent matches, formatted, or an empty string (a no-op) when nothing matches. It is **independent of retrieval** — even if the vector search returns junk, the registry still injects.

**Injection point & precedence.** In `serve.py`'s `answer_question`, immediately after `build_context()`:
```python
context = sc.build_context(hits)
import secret_registry as registry
_reg = registry.registry_block_for(question)
if _reg:
    context = _reg + "\n\n" + context   # registry sits ABOVE the passages
messages = sc.build_messages(question, context)
```
The block is labelled `=== AUTHORITATIVE CITY REGISTRY (overrides retrieved passages) ===`. Precedence is stated once in `SYSTEM_PROMPT` (§5.4): registry wins for its fields; everything else stays RAG.

**Guards (enforced in the formatter, not just the prompt).**
- **UNSOLVED casques never emit `finder`/`year`/`location`** — even if a value is present in the record. Dig locations for unsolved casques stay a RAG + refusal problem.
- **`lat_long` is labelled "region only — NOT a dig location."**
- **Known-unknowns render as "(not established)"** so the model is told a value is genuinely open rather than left to invent one. (A *blank* field injects nothing at all — RAG can still fill it.)
- **AUTHOR HINTS are injected verbatim** (creator-verified), under a **status-aware label**: for UNSOLVED, "clues toward the still-unsolved answer — relay as hints, never assert the solution/dig location they imply"; for FOUND, "this casque is solved — find location above is authoritative."
- Where an author hint itself *implies* a location for an **unsolved** casque, it stays in the hints channel under the guard (relayed as the author's *implication*, never as a settled dig location) and is never promoted to the `location` field.

**Note on verbatim scope.** The anti-paste exemption is currently scoped to the whole registry block, so `verse_text` will also come through verbatim on registry-matched questions, not just hints. If verses should stay gated behind an explicit "show me the verse" ask, narrow the exemption to `hints` only.

**Validation & self-test.** `validate_registry()` checks: unique verse #s and image #s across the set, FOUND completeness (finder/year/location present), UNSOLVED carries no location, lowercase keys. Self-test (stdlib-only, **needs no venv**):
```bash
cd ~/secretrag && python3 secret_registry.py
# expect: self-test OK — 12 cities; validate: clean
```

**How it was wired.** Integration used an anchored-splice patcher (`go_live_patch.py`) that pre-flights every anchor (matches exactly once or aborts touching nothing), timestamped-backs-up each target, applies the edits, `py_compile`s, and rolls back on failure. Two targets: the `serve.py` prepend above, and the `secret_common.py` `SYSTEM_PROMPT` clause. Keep any future `secret_common.py` edits as anchored splices with a backup + compile-check.

---

## 5-legacy. OWUI-native document RAG (SUPERSEDED — kept for history)

> This was the original retrieval approach before serve.py. It is **no longer the live path** — the "Ask Byron" model now routes to serve.py and this KB is detached (§6). Retained for the H1-heading lesson and the corpus pipeline notes.

Source: 12treasures.com page data, 118 pages, exported to `.xlsx`. Pipeline `convert_secret.py` emitted one Markdown doc per page per edition (113 English + 91 Japanese). **Formatting lesson:** v1 wrote each page with a Markdown H1, and OWUI's header-aware splitter isolated the heading into a content-less chunk that matched "page N" queries but carried no text. Fix was to fold the page identifier inline (no H1). OWUI retrieval settings that mattered: Top K 5–6, relevance threshold 0, Hybrid Search (BM25) available but off.

---

## 6. OWUI "Ask Byron" model — wiring to serve

The community-facing model surfaced in Open WebUI. **Now a thin front-end over serve.py.**

Setup that makes it route through serve:
1. **Add the OpenAI connection** (Admin → Settings → Connections → OpenAI API): Base URL `http://<box-ip>:8100/v1`, key = any non-empty string (serve ignores it). Must be the **box LAN IP**, not `localhost` — OWUI is containerized (see §3.6 caveat). `secret-librarian` then appears in the model list.
2. **Point the "Ask Byron" model at it** (Workspace → Models): Base Model = `secret-librarian`.
3. **Detach OWUI's own knowledge base** — remove the attached `The_Secret` KB (the × next to it). **Critical:** if left attached, OWUI runs its own book RAG *and* stuffs those chunks into the message sent to serve, which then runs its own retrieval — two stacked retrieval systems, OWUI's book-only chunks polluting serve's tiered context. serve owns retrieval; OWUI must be a dumb pipe.
4. The OWUI System Prompt field is now dead weight — serve injects `SYSTEM_PROMPT`.

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
# expect: self-test OK — 12 cities; validate: clean
```

**Smoke-test retrieval directly** (loads bge-m3 on first call — several-second silent pause is normal):
```bash
cd ~/secretrag && source .venv/bin/activate
python -c "import secret_common as sc; [print(h['tier'], round(h['distance'],3), h['cite']) for h in sc.retrieve('Roanoke Elizabethan Gardens')]"
```
Expect a mix of `canon` / `commentary` / `transcript`, distance-sorted. All-`canon` means the vault collections aren't being reached.

**Smoke-test the registry end-to-end** (proves injection is live; first call pauses loading bge-m3):
```bash
# a number-referent question -> the registry should bind image -> verse -> stone -> city
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"what verse number and gemstone go with image 2?","edition":null}' | python3 -m json.tool
```
The discrete facts should come back from the registry (correct, bound to the right city), not a confabulated guess. The registry rides in the context above the passages, so it does **not** appear in the response's `sources` array (that list is the retrieved hits only).

**Guard checks worth keeping in the smoke set:**
- An **unsolved** casque whose author hints imply a location → the answer should relay it as the author's *implication* and state the casque is **unsolved**, never "it's buried at X."
- A **hints** question → the answer should **quote** the author's hint wording, not summarize it away (confirms the anti-paste exemption).

**Restart services**
```bash
sudo systemctl restart ollama
docker restart open-webui
# serve: see §5.6
```

---

## 8. Troubleshooting (issues actually encountered)

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` blank / model on CPU | Secure Boot on, or driver not built | Disable Secure Boot; reinstall driver; reboot |
| OWUI Ollama dropdown empty | Ollama bound to loopback | `OLLAMA_HOST=0.0.0.0` (§3.4); point OWUI at `host.docker.internal:11434` |
| `ollama ps` CPU/GPU split | Model + KV cache > 12 GB | flash attention + `q8_0` KV cache (§3.4) |
| Vault collections indexed but retrieval never sees them | Index script wrote to `./chroma` while book is in `./chroma_db` | Import `sc.CHROMA_DIR` in index scripts (§5.2); run from `~/secretrag` |
| Merge returns zero book hits | Queried `The_Secret` (capitalized); live name is lowercase `the_secret` | Use `the_secret` / `sc.COLLECTION` |
| `secret-librarian` missing from OWUI list | OWUI is containerized; `localhost:8100` hits the container | Use Base URL `http://<box-ip>:8100/v1` (or the Docker bridge gateway) |
| Answers mix in irrelevant book-only chunks | OWUI KB left attached — double RAG | Detach `The_Secret` KB from the model (§6) |
| Prompt/retrieval/registry edits don't take effect | serve holds old versions at import + `lru_cache` | Restart the tmux `serve` session (§5.6) — required after `secret_common.py` **or** `secret_registry.py` edits |
| Discrete facts still confabulated on a city question | Question named neither a city nor a verse/image number, so the registry didn't fire; or serve wasn't restarted after a registry edit | Confirm `registry_block_for(question)` is non-empty; restart serve |
| Author hints get summarized instead of quoted | Registry anti-paste exemption not in the live prompt, or serve not restarted | Confirm the registry clause is in `SYSTEM_PROMPT` (§5.4); restart serve |
| Unsolved casque answer states a dig location as fact | Location leaked outside the guarded hints channel | Confirm the formatter suppresses `finder/year/location` for UNSOLVED; keep any author-implied location only in `hints` |
| Registry patch pre-flight fails (anchor found 0×) | Live `serve.py`/`secret_common.py` drifted from the expected text | Re-cut the anchor against the current file; do **not** hand-edit the file to match the patcher |
| serve looks "frozen" mid-index | bge-m3 on CPU embeds silently (no progress bar) | Confirm via `top` (python pegging cores) / `chroma.sqlite3` mtime moving; wait |
| serve gone after reboot / SSH drop | Runs in tmux, not systemd; or was foregrounded in SSH | Restart in `tmux new -d` (§5.6); TODO: systemd unit |
| Model narrates "Okay, the user wants…" | Thinking mode on | `think=Off` at preset; API callers pass `"think": false` |

---

## 9. Known limitations

- **Discrete-fact confabulation — mitigated (2026-08-27).** The city registry (§5.7) makes settled per-city facts a deterministic authoritative lookup, so the model reads them instead of guessing. **Residuals:** (a) it covers only the closed set of *city- or number-anchored* facts — a question that names neither a city nor a verse/image number still falls back to RAG and can confabulate; (b) prompt-injected precedence is strong but **not a hard guarantee** at 14B — the only guaranteed fix is a deterministic post-generation validator (phase 2, §11).
- **Numbering-guardrail leak — mostly addressed.** Prompt + registry now enforce city/verse#/image# referents and forbid "casque N". Watch for residual echoes originating in the vault notes themselves.
- **Persona-vs-accuracy tension — reduced, still watch.** Unsolved casques carry `status: UNSOLVED` and their author hints are guarded as clues, which curbs warmth-driven overconfidence — but does not eliminate it. Keep an eye on confidence swinging with phrasing on unsolved casques, and on author hints being framed as if they were canonical verse text.
- **Verbatim-hint context cost.** Author hints (and, under the current exemption scope, verse text) inject verbatim, so a single city block is sizable. Several cities named in one question can pressure the 16K context. Levers: narrow the anti-paste exemption to hints only, or gate verse/hints to inject only when the question needs them.
- **Single-turn.** `/v1/chat/completions` ignores prior turns by design — no conversational memory in OWUI; follow-ups ("what about the Japanese edition of that?") lose the referent.
- **`/health` counts book only** (220) — cosmetic, predates the merge.
- **Transcript tier is ~20% banter-heavy.** Kept in a separate collection with low pull count so it rarely wins. Removing banter properly needs a semantic summarization pass, not rules.
- **Single 14B resident.** One model in VRAM; always-on bot and interactive chat contend for the card — generations are effectively serialized.

---

## 10. Security posture

The box lives on an isolated services VLAN. Inference (Ollama) and the RAG service (serve.py) bind broadly so the containerized front-end can reach them; access is constrained at the **network layer** (host/segment-scoped firewall rules), not left open. The private knowledge base is attached deliberately to its model preset, not published. No agent holds infrastructure tools or shell access — the community bot is RAG-only, and the future ops reader is read-only.

The city registry adds no network surface (it's an in-process module), but its data file contains **community-privileged puzzle content** (author hints, verified pairings). Treat `secret_registry.py`'s *values* like the private ops note — keep them out of public repos and paste-ables.

Detailed firewall rules, addressing, and hardening status are tracked in a private ops note, not here. **If you adapt this runbook, don't expose the inference or RAG ports beyond your trusted segment, and add authentication before any public-facing deployment** — the RAG service accepts but does not verify an API key, so it must sit behind network controls. When the Discord bot lands, the bot token is a secret (env/secret store, never committed), and the bot should reach serve over the trusted segment only.

---

## 11. Open TODOs (non-sensitive)

- [x] **City registry — DONE 2026-08-27.** `secret_registry.py`: 12-city closed-set structured-fact lookup, injected AUTHORITATIVE above retrieved passages on a city or verse/image-number match. Formatter guards (no dig location for unsolved; known-unknowns as "(not established)"; region-band lat/long). AUTHOR HINTS injected verbatim under a status-aware guard, exempt from the summarize/don't-paste rule. Wired via anchored-splice patcher with backup + `py_compile` + rollback. Verified live.
- [ ] **Discord bot** (immediate next) — RAG-only, `"think": false`, no infra tools, calls serve's `/ask`. Lets community members ask Ask Byron in Discord and get the answer back in Discord. (Handoff written separately.)
- [ ] **Phase-2 output validator** — deterministic post-generation check on the registry's discrete fields (verse/image/stone). Regenerate-with-the-value-pinned, **not** silent string-replacement (a corrector with false positives is worse than the confabulation). Scope: single-city questions, the three discrete fields only. Build **after** measuring how often injection alone actually misses in production.
- [ ] **systemd unit for serve.py** so it survives reboot (currently tmux-only).
- [ ] Extend `/health` to count all three collections.
- [ ] Optional: semantic show-notes pass over transcripts to kill interleaved banter, then embed summaries instead of raw windows.
- [ ] Re-visit persona voice knob if warmth keeps leaking into false certainty on unsolved casques.
- [ ] Optional: narrow the registry anti-paste exemption to `hints` only if verses shouldn't paste unprompted.

*(Network/firewall hardening tasks are tracked in the private ops note.)*

---

## 12. Changelog

- **2026-08-27 — City registry live.** Added `secret_registry.py` (stdlib-only, 12-city closed-set structured-fact lookup). Per-city verified facts inject AUTHORITATIVE above retrieved passages when a question names a city or references a verse/image number (number-referent triggers, ordinal-guarded). Formatter guards: unsolved casques never emit find location; known-unknowns render "(not established)"; lat/long labelled region-not-dig-site. AUTHOR HINTS injected verbatim under a status-aware guard (clues-not-settled for unsolved), exempt from the summarize/don't-paste rule so the author's wording reaches the seeker. `SYSTEM_PROMPT` updated: registry is top authority over passages + the anti-paste exemption. Wired via anchored-splice patcher (`go_live_patch.py`: pre-flight + timestamped backup + `py_compile` + rollback). `validate_registry()` enforces unique verse/image #s, found-completeness, unsolved-no-location. Verified live against serve on image→verse→stone binding, verbatim author hints, and author-implied-location-stays-hedged on an unsolved casque.
- **2026-08-27 — serve.py RAG pivot.** Pivoted retrieval to the custom `serve.py`/Chroma stack. Indexed two new tiers into the shared store: `vault_commentary` (344) and `vault_transcripts` (1303), alongside `the_secret` (220). Fixed index-script Chroma path to inherit `sc.CHROMA_DIR` (silent wrong-store bug). Rewrote `secret_common.py` retrieval into a three-collection distance merge with tier labels; added CANON/COMMENTARY + numbering + found-vs-unsolved + synthesize guardrails and merged in the "Ask Byron" persona. Started serve under tmux on the serve port; wired OWUI to it via an OpenAI connection on the box LAN IP and detached OWUI's native `The_Secret` KB. Identified discrete-fact confabulation → city registry is the next task.
- **2026-08-20 — Initial build.** Ubuntu 24.04, driver 595.84, Ollama + qwen3:14b, qwen3-chat (16K), flash attn + q8_0 cache, Open WebUI. *The Secret* corpus v1 → v2 (heading-magnet fix). Retrieval Top K raised to 5–6.
