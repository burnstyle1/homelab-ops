# Runbook: Repurposing a 2017 iMac as a Hands-Free Accessibility Machine

**Status:** In progress (clean reinstall underway)
**Platform:** iMac (Retina 5K, 27-inch, 2017) — `iMac18,3`
**OS target:** macOS Ventura 13.7.8 (this model's ceiling)
**Author:** burnstyle
**Last updated:** 2026-08-21

---

## Why this exists

This isn't a homelab flex. It's a hand-me-down machine being rebuilt for a friend
with multiple sclerosis whose motor control has deteriorated to the point where a
keyboard and mouse aren't reliably usable — think Parkinson's-like tremor and loss
of fine control in the hands.

The goal is simple and human: give him back the ability to *use a computer* — email,
web, writing — entirely by voice, and where his hands won't cooperate, by head
movement. No budget. Repurpose what's already on hand rather than buy anything.

Every technical decision below was made in service of that. When you're building
something a person is going to *depend on* to communicate with the world, "it mostly
works" isn't good enough, and "cool demo" isn't the finish line — daily, boring,
reliable use is.

---

## The decisions, up front (TL;DR)

| Question | Answer | Why |
|---|---|---|
| Linux Mint or Windows? | **No — stay on macOS** | Hands-free voice control is the one thing Linux does *worst*. Windows Voice Access needs Win 11, which this hardware can't run. macOS **Voice Control** is best-in-class, free, and fully offline. |
| Which macOS? | **Ventura 13.7.8** | Hard ceiling for `iMac18,3`. Voice Control has existed since Catalina (2019), so Ventura is plenty. |
| Where to install? | **External SSD over USB 3** | Internal 1 TB is a slow Fusion/spinning volume. External SSD sidesteps both the slow drive *and* a glued-screen teardown. |
| Voice-only? | **No — pair voice with Head Pointer + Dwell** | MS fatigue is real. Talking to a computer all day is exhausting. Mixing input methods reduces strain. |

---

## Hardware / environment

- **Machine:** iMac 27" Retina 5K, 2017 (`iMac18,3`), Quad-Core i5 3.4 GHz, 16 GB DDR4, Radeon Pro 570.
  - Note: an *earlier* session logged this as 8 GB / 1 TB Fusion. The unit in front of me
    reads 16 GB. Trust the machine, not the old note.
- **Internal drive:** 1 TB, presents as spinning HDD / Fusion. **Left unused.**
- **Boot drive:** 250 GB SATA SSD in a cheap USB 3 enclosure.
  - Benchmarked (Blackmagic Disk Speed Test): **~310 MB/s write / ~415 MB/s read.**
    Respectable for SATA-over-USB. **The disk is not the bottleneck.** (Important — see below.)
- **Voice Control model:** on-device, downloads once per language. This detail becomes the whole story.

---

## Part 1 — The build (initial setup)

1. **Confirm the model.** Apple menu → *About This Mac*. Retina 27" + plain "iMac"
   (not "iMac Pro") = `iMac18,3` = Ventura ceiling.
2. **Get the Ventura installer** from a working macOS session (Internet Recovery gives
   you the *factory* OS, which on this machine is High Sierra — too old, no Voice Control):
   ```bash
   softwareupdate --fetch-full-installer --full-installer-version 13.7.8
   ```
3. **Prep the target drive** in Disk Utility (`View → Show All Devices`):
   - Erase → **APFS**, scheme **GUID Partition Map**. Name it clearly (e.g. `Ventura`).
4. **Install** "Install macOS Ventura" → at the disk picker, click **Show All Disks**
   and select the external SSD. Do **not** install to the internal drive.
5. **Set default boot:** System Settings → General → **Startup Disk** → external SSD.
   (Or hold **Option** at power-on to pick manually.)
6. **Enable accessibility:**
   - System Settings → Accessibility → **Voice Control** → on (downloads a language model once).
   - Accessibility → Pointer Control → **Head Pointer** and **Dwell** → enable to taste.

---

## Part 2 — The problem ("slow as dirt")

Came back to the machine and it felt unusable — sluggish, laggy. Initial instinct:
"the drive is slow, let's swap it to the internal bay / crack the screen open."

**We diagnosed before spending or cutting anything. Good thing.**

### Diagnostic trail

1. **Benchmark the disk** → 310/415 MB/s. Fine. **Disk was never the problem.**
   Any plan involving a screwdriver was killed here.
2. **Activity Monitor → CPU**, sorted by %CPU → **`ReportCrash` at 20% with 10m40s of
   CPU time.** `ReportCrash` is supposed to run for a fraction of a second. Ten minutes
   of it means **something is crash-looping.**
3. **Console → Crash Reports** → a wall of the same process dying over and over:
   ```
   Process:            com.apple.SpeechRecognitionCore.speechrecognitiond
   Exception Type:     EXC_CRASH (SIGKILL (Code Signature Invalid))
   Termination Reason: CODESIGNING 1 Taskgated Invalid Signature
   ```

### Root cause

`speechrecognitiond` is the **on-device speech engine that Voice Control depends on**
(distinct from Siri's cloud engine). It was failing its own code-signature check,
getting SIGKILL'd by the OS, relaunched by `launchd`, and dying again — an infinite
loop. Every death spawned a `ReportCrash`, which is what pegged the CPU and made the
*whole machine* feel like dirt.

**Key observation that cracked it:** the loop only fires **when Voice Control is enabled.**
Idle, the daemon is quiet. The moment Voice Control tries to load its speech model, the
daemon spins up, fails, and loops. Combined with a **stuck `English (United Kingdom)`
vocabulary** in the Voice Control language settings, this points at a **corrupt /
half-downloaded on-device speech model** as the most likely culprit — the daemon is
trying to load a broken asset and tripping its integrity check.

---

## Part 3 — The fix (procedure)

### Stop the bleeding first (GUI, do this before anything else)
1. System Settings → Accessibility → **Voice Control → OFF** (halts the loop at the source).
2. Activity Monitor → select **`ReportCrash`** → **✕ → Force Quit** (stops the CPU hammering).

### Fix attempt 1 — reinstall *over the top* ❌ (did not hold)
Reinstall-over-top re-lays Apple's system files but **leaves user data, caches, and the
wedged speech model in place.** The corrupt UK vocabulary survived, and the loop returned
the instant Voice Control was re-enabled. **Lesson: over-top reinstall does not clear
a bad on-device model.**

### Fix attempt 2 — swap the stuck language (fast diagnostic)
- Voice Control → **Language** → switch to **English (United States)** (or whatever matches
  the user's accent), toggle Voice Control off/on, let the *new* model download fully.
- If the crash stops with a different language, it confirms the fault was that one
  corrupt model — not the drive, not the OS.

### Fix attempt 3 — clean install ✅ (in progress)
Because there's nothing on the machine worth keeping, a **clean wipe-and-reinstall** is
the correct, decisive fix (unlike the over-top reinstall):
1. Ideally install to a **second drive** so the suspect USB SSD is out of the equation.
2. From a working macOS session, pull the installer:
   ```bash
   softwareupdate --fetch-full-installer --full-installer-version 13.7.8
   ```
3. Disk Utility (`Show All Devices`) → **Erase** target → **APFS / GUID**.
4. Run the installer → **Show All Disks** → pick the freshly-erased target.
5. First boot → set up **one** simple user account → enable Voice Control → pick the
   **correct language once** → let the model download **fully on wired Ethernet**,
   uninterrupted → test `Show numbers`, `Show grid`, and dictation.

> ⚠️ **Recovery gotcha:** `Cmd+R` (Internet Recovery) installs the machine's *original*
> factory OS = **High Sierra**, which has **no Voice Control**. Use **`Option+Cmd+R`** to
> get the *latest supported* OS (Ventura), or install from the full installer as above.

---

## Part 4 — The thing I'm still watching

Three integrity failures showed up on the original USB SSD over the course of this project:

1. A phantom `~/benchtest: No such file or directory` **and** `/tmp not found` during
   benchmarking (both should always exist).
2. A system binary failing its **code signature**.
3. An over-top reinstall that **didn't hold**.

One of those is bad luck. Three on one drive is a pattern that smells like **the SSD or
its cheap enclosure corrupting data on write.**

The clean install is therefore also a **test**:
- If it fixes it → the fault was a wedged speech model. Done.
- If the loop **returns on fresh storage** → the software was never the problem, and this
  drive/enclosure should **not** be the thing a disabled person depends on daily.

**Action item:** run a real SMART check on the SSD before trusting it long-term.
Disk Utility → `Show All Devices` → select the **physical device** → **S.M.A.R.T. Status**
should read *Verified*. For real numbers: `smartctl -a /dev/diskN` (via `smartmontools`).

---

## Lessons learned

- **Diagnose before you spend or cut.** The entire "swap the drive / open the glued
  screen" plan was chasing a disk that was doing 415 MB/s. The real culprit was a
  crash-looping daemon. A ten-minute benchmark and a look at Activity Monitor saved a
  screen teardown that would have fixed *nothing*.
- **`ReportCrash` eating CPU = something is crash-looping.** It's a symptom, not the disease.
  Go to Console → Crash Reports to find the actual process.
- **Reinstall-over-top ≠ clean install.** Over-top preserves the corrupt user-level state
  that's often the actual problem. When there's nothing to preserve, erase and reinstall.
- **On-device Voice Control models can wedge.** A half-downloaded / corrupt language model
  can crash `speechrecognitiond` in a signature-check loop. Let the model download *fully*,
  on a stable connection, uninterrupted.
- **`Cmd+R` vs `Option+Cmd+R` matters.** Factory OS vs. latest-supported OS. On a 2017
  machine that's High Sierra vs. Ventura — the difference between "no Voice Control at all"
  and "the feature this whole project exists for."
- **Match the Voice Control language to the user's accent.** Recognition accuracy depends
  on it — don't leave someone on US English as a permanent fix if they're British.

---

## Open items

- [ ] Confirm clean install clears the `speechrecognitiond` signature loop
- [ ] SMART check on the 250 GB USB SSD — decide if it's trustworthy for daily use
- [ ] Confirm Voice Control model downloads fully and `Show numbers` / `Show grid` work
- [ ] Enable + tune **Head Pointer** and **Dwell** control
- [ ] Write and print a one-page **Voice Control command cheat sheet** to tape next to the screen
- [ ] Sit with him for the first hour — the learning curve is front-loaded, then it's muscle memory
