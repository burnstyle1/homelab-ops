# Account attack that looked like a breach but wasn't

**Impact:** Someone was going after community member accounts in a targeted way. It looked like it might be a server compromise. It wasn't one, and figuring that out quickly was the whole game.
**Affected:** Community WordPress platforms, member accounts, private messaging.

## What happened
Started seeing targeted login attempts and messaging aimed at specific accounts. Targeted, not random botnet spray. It read like whoever was behind it knew the community, which is exactly the kind of thing that makes you jump to "my server's been owned."

## Digging in
First instinct in that situation is to assume the box is compromised and start pulling servers apart. I didn't actually have evidence for that, and chasing it would've burned time while the real hole stayed open.

Two things didn't fit a breach. First, nothing on the infrastructure looked wrong. No odd access at the server, database, or host level. Second, the attacker referenced something from a private message between two members. That's the part that clicked. A server breach explains leaked private content, sure, but so does one compromised account inside the group, and with the servers coming up clean the account explanation was far more likely.

Once I stopped treating it as a network problem it was traceable through the community itself. Who could see what, and where a single compromised account would account for everything I was seeing. That pointed me at the vector and at who was affected.

## Root cause
A compromised account inside the community's trust circle. Not my infrastructure. The leaked DM came through legitimate access that had ended up with the wrong person, which is why nothing ever showed up in my logs.

## Fix
- Told the site owner whose DM had been referenced so they could lock down their own account.
- Reset and secured the affected accounts and tightened auth on the platforms involved.
- Didn't waste time rebuilding servers that were never the problem.

## Prevention
- Wrote the reasoning down so next time something like this shows up I start by asking whether it's an actual breach or someone abusing trusted access, before I nuke anything.
- Put the effort into account-level stuff (credential hygiene, better auth), since that's where the actual threat was.
