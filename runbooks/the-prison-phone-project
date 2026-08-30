# Runbook: My Wintel Prison Phone to Grandstream ATA to FreePBX VM

## 0. Overview

I'm bridging an old Wintel analog prison handset onto the homelab through a
Grandstream FXS ATA (HT801/HT802) registered to a FreePBX/Asterisk VM on my
VoIP VLAN.

The phone itself is known and simple, so this is a boring build:

- Standard 2-wire loop-start
- Magnetic (reed) hookswitch
- DTMF keypad
- No ringer (Wintel inmate phones place calls, they don't receive them), so
  this is outbound-only

My order of operations: bench-test the phone against the ATA alone, then build
the PBX, then integrate. I'm not standing up a whole VM before I know the
handset seizes and releases the line cleanly.

---

## Phase 1: Bench-test the ATA and phone in isolation

Prove the electrical layer with no PBX in the picture.

1. Factory-default the HT801. Plug the phone into the **PHONE (FXS/RJ11)** port.
   Plug the ATA into my network.
2. Lift the handset. I should hear dial tone, and the ATA FXS status should show
   **off-hook**.
3. **Test the magnetic hookswitch on purpose.** This reed switch is my one real
   failure risk on an aged Wintel:
   - Lift: dial tone present, status off-hook.
   - Cradle firmly: dial tone stops, status flips to **on-hook / idle**.
   If cradling it doesn't drop the line, the reed is weak, misaligned, or stuck.
   That's a phone problem, not a config one. Don't go chasing FreePBX for it.
4. Find the ATA's IP from my **DHCP lease table**, not the phone keypad. (The
   keypad works, so `***` then `02` reads the IP back too, but the lease table
   is faster.)
5. Log into the ATA web GUI, confirm firmware is current.
6. Dial a few digits, confirm the ATA logs them as DTMF.

Goal: dial tone, clean off-hook AND on-hook detection, DTMF confirmed. If any of
that fails, no PBX config fixes it.

---

## Phase 2: Build the PBX VM

1. **Provision on Proxmox:** 1 vCPU, 2 GB RAM, 20 GB disk. Attach the NIC to my
   VoIP VLAN (bridge with the right tag, or access-mode).
2. **Install FreePBX** (FreePBX Distro ISO). Finish web-GUI init at the VM's
   VLAN IP.
3. **Firewall, the step I won't skip.** FreePBX's integrated firewall blocks SIP
   from untrusted hosts by default. Go to **Connectivity > Firewall** and add the
   ATA's IP (or the whole VoIP VLAN subnet) to the **Trusted** zone. Skip this
   and I get silent registration failures or one-way audio.
4. **Create a PJSIP extension** (Applications > Extensions > Add PJSIP):
   - Extension `101`
   - Strong secret
   - Default SIP port `5060`
5. **Create a second extension `102`** now, for a softphone to test against.

---

## Phase 3: Register the ATA to the PBX

In the ATA web GUI:

1. **Profile 1:**
   - Primary SIP Server = PBX VM's VLAN IP
   - SIP Transport = UDP
2. **FXS Port:**
   - SIP User ID / Authenticate ID = `101`
   - Authenticate Password = the extension secret
   - Preferred Vocoder = **PCMU (G.711 ulaw)** first, disable the rest
   - DTMF = RFC2833
   - Leave Offhook Auto-Dial blank. I have a keypad, so I dial normally.
3. **VLAN tagging:** only set an 802.1q tag on the ATA if its switch port is a
   **trunk**. If it's an **access** port on the VoIP VLAN, leave tagging off.
4. Apply, then Reboot.

The ATA and PBX share a subnet, so there's no NAT traversal and no SIP ALG
mangling to fight here. That only shows up later on the outbound trunk.

---

## Phase 4: Validate

1. **Registration:** ATA status shows FXS **Registered**, FreePBX shows `101`
   online.
2. **Echo test first:** lift the phone, dial `*43`, talk. I should hear myself.
   That proves audio both directions.
3. **Two-way call:** register a softphone (Linphone on the Mint box) as `102`.
   Call `101` to `102` and back. Confirm clean audio each way.
4. If audio is weak or distorted and the handset mic is carbon, that's the
   handset, not the config. Swap and retest.

Reminder: no ringer on this phone. A call to `101` connects the second I lift
it, but nothing announces it. Fine for outbound-only, which is the point.

---

## Phase 5: Outbound calling (optional, later)

1. **Real SIP trunk:** VoIP.ms, Flowroute, or Telnyx. Add as a PJSIP trunk,
   build an outbound route.
2. **Disable SIP ALG** on the edge router for the trunk leg. It mangles SIP
   crossing the WAN. (Doesn't touch the on-VLAN ATA-to-PBX traffic.)
3. **Google Voice:** no native SIP. Only path is a paid third-party gateway
   (simonics/gvgw). Cleaner to just use a real trunk provider.

---

## Quick failure map

| Symptom | Most likely cause |
|---|---|
| No dial tone on pickup | Magnetic hookswitch not sensing off-hook (Phase 1) |
| Line won't drop after cradling | Weak/misaligned/stuck reed switch (Phase 1) |
| ATA won't register | Firewall not trusting the ATA subnet (Phase 2.3) |
| Registered but no/one-way audio | Firewall zone, or codec mismatch (force ulaw) |
| Weak/distorted audio | Carbon mic handset, swap it |
