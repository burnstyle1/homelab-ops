# Lodge site got popped, rebuilt it static instead of cleaning it

**Impact:** A community lodge site running WordPress got compromised. I took it down and rebuilt it as a static site rather than trying to clean the existing install.
**Affected:** WordPress (PHP/MySQL), the hosting account, DNS.

## What happened
The site started doing things nobody had logged in to do. Once WordPress is in that state I don't trust it anymore. You can scrub the obvious stuff and still miss a shell dropped in an uploads folder or a modified core file somewhere.

## Why I didn't just clean it
I could've spent an afternoon diffing core files and hunting for backdoors. Instead I stopped and asked what the site actually needed to do, and the answer was: show a few pages of static info. No logins, no comments, no reason for a database or a wp-admin page facing the internet.

So the whole WordPress stack was risk with basically nothing to justify it. A brochure running a full web app.

## Root cause
Under-maintained WordPress with the usual attack surface: plugins, PHP, an admin login open to the world. It wasn't a high-priority site so it wasn't getting patched regularly, which is how most of these end up.

## Fix
- Wiped the install instead of disinfecting it. Faster, and I actually trust the result.
- Rebuilt it on Cloudflare Pages. Static HTML, no PHP, no database, no login to brute-force.
- Repointed DNS at the new site.

## Prevention
- The rebuild is the fix. No application layer means there's nothing to pop.
- Content's in git now and deploys from there, so there's no live editor sitting exposed.
- Going forward: if a site doesn't actually need to be dynamic, it isn't.
