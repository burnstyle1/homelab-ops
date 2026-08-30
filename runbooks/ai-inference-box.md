# Runbook update - 2026-08-29 (global rules layer)

Drop-in edits for the AI Inference Box runbook. Applied as anchored splices so
untouched sections stay identical. Public-safe: no creature names, no rule text,
no mail-in address, no puzzle data. No em or en dashes, per doc convention.

Nine edits: header date and status, section 1, section 4 table row, a forward
reference in 5.7, a new section 5.8, a section 8 smoke test, two section 10
limitations, one section 12 TODO, and one section 13 changelog entry.

---

## Edit 1 - header (date + status)

**Find:**

> the deterministic city registry runs inside the serve pipeline, a gated historical-forum tier is live, and the Discord bot is live. The ops reader is still pending.
> **Last updated:** 2026-08-28

**Replace with:**

> the deterministic city registry runs inside the serve pipeline, a global rules layer rides alongside it, a gated historical-forum tier is live, and the Discord bot is live. The ops reader is still pending.
> **Last updated:** 2026-08-29

---

## Edit 2 - section 1 (Overview)

**Find the sentence ending:**

> When a question names a city, or references a verse or image number, the verified facts for that casque get injected above the retrieved passages and marked authoritative.

**Append immediately after it:**

> The same module also injects a small set of creator-stated global hunt rules (burial depth, excluded sites, the now-void mail-in claim process, and a field-guide no-clues disclaimer) when a question is about those, marked authoritative the same way (section 5.8).

---

## Edit 3 - section 4 (Configuration reference table)

**Find the row:**

```
| City registry | `/opt/secretrag/secret_registry.py`, 12-city structured-fact lookup, stdlib-only, imported by serve |
```

**Replace with:**

```
| City registry + rules | `/opt/secretrag/secret_registry.py`, 12-city structured-fact lookup plus a global hunt-rules layer (5.8), stdlib-only, imported by serve |
```

---

## Edit 4 - section 5.7 (forward reference)

**Find:**

> `registry_block_for(question)` returns the per-city block(s) for any matched casque, or the roster when the question is aggregate, or an empty string. It runs independently of retrieval. Even if the vector search comes back with junk, the registry still injects.

**Append immediately after it:**

> It also prepends any matching global rule block (section 5.8) above the city or roster output, under a shared authoritative header.

---

## Edit 5 - new section 5.8

**Insert as a new subsection at the end of section 5, immediately before the `---` that precedes `## 5-legacy.`**

```markdown
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
```

---

## Edit 6 - section 8 (Operations & verification)

**Find the end of the forum-gate smoke test, then the line that starts the registry guard checks:**

> The first should carry `historical forum` entries near the top of `sources` and frame them as old theories; the second should have zero forum entries - the gate never queried the collection.
>
> Two registry guard checks worth keeping in the smoke set:

**Insert this block between those two (after the forum paragraph, before "Two registry guard checks"):**

```markdown
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
```

---

## Edit 7 - section 10 (Known limitations)

**Find the last bullet in the list:**

> - Public question echo. The bot posts the asker's question above the answer (7.4), so it rebroadcasts user-submitted text with a name attached. In an 18k-member server the channel and role gates are load-bearing, and a profanity screen on the echo is the lever if it starts.

**Append these two bullets immediately after it:**

```markdown
- Global rules layer, field-guide gate is precision by design. The field-guide no-clues disclaimer (5.8) fails toward silence: it fires on field-guide phrasing or a covered creature name, so an obscure creature asked about with neither gets no disclaimer (a safe miss, the user can rephrase). The trade is deliberate, since a false disclaimer could point a searcher away from a real painting clue. Widen the covered phrasings and names as real questions reveal gaps.
- Count and tally questions are unreliable. "How many times does the word X appear in the verse" is answered by the model, which cannot count occurrences reliably even with the exact verse injected, so the number is often wrong. The registry injects the verse text but not a count. The fix is a deterministic verse-scoped counter (section 12); until then, treat any count answer as suspect.
```

---

## Edit 8 - section 12 (Open TODOs)

**Find the phase-2 validator item:**

> - [ ] Phase-2 output validator. A deterministic post-generation check on the registry's discrete fields (verse, image, stone). Regenerate with the value pinned, not silent string replacement. Scope it to single-city questions and those three fields, and build it only after measuring how often injection alone actually misses in production.

**Insert this item immediately after it:**

```markdown
- [ ] Deterministic verse-scoped word counter. Count occurrences of a named word or phrase in a named casque's verse in Python and inject the number as authoritative, so count questions stop being answered by the model, which cannot count reliably (section 10). The hard part is extraction: it has to pull the target word and resolve which verse with confidence, and fail toward silence on ambiguous phrasing, because an authoritative wrong count is worse than the current honest-looking miscount. Default to whole-word, case-insensitive matching.
```

---

## Edit 9 - section 13 (Changelog)

**Insert this entry at the top of the changelog list, immediately after `## 13. Changelog` and before the `2026-08-28, historical forum tier` entry:**

```markdown
- **2026-08-29, global rules layer.** Added a creator-stated global hunt-rules layer to `secret_registry.py`, a sibling to the per-city registry (5.8): rules that hold for all twelve casques rather than one city. Two blocks are live: burial depth, container, and excluded sites plus the now-void mail-in claim process; and a field-guide ("back of the book") no-clues disclaimer. `registry_block_for` prepends every matching rule block above the city or roster output under one shared authoritative header, with no `serve.py` change, since the rules ride the existing registry injection point. Two gates with different risk postures: the burial/claim gate is tuned generous because over-firing only adds a block that is entirely true, while the field-guide gate is precision and fails toward silence because the paintings depict the fair folk and are the clue source, so a false disclaimer could wave a searcher off a real clue. The field-guide gate fires on field-guide phrasing or a named field-guide creature, and deliberately excludes names that overlap with real-world symbols or historical referents that could appear in a painting, plus one name common in bot-channel chatter. The claim block leads with the void verdict so the model does not soften it, with the defunct mailing history demoted to background stated only if asked. Also corrected a stale registry self-test assertion left over from an earlier data fill, so the self-test passes clean again. Verified live on `/ask`: mail-in answers lead with void and the physical-recovery path, and field-guide questions return the no-clues disclaimer while still naming the paintings as the clue source.
```
