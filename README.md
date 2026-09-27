# FriendsNMe


Roles 
Main algorithm- this will be where calculations will be done for a user's gps location given from the website 
              - accounts and a buddy system (someone spli

alerts- once we receive the information from the calculations the alerts will connect back to an event that the website will go through 

UI- how the map will be set up basically front end (can be 2 or 3 people that works on this) 
  
- Duwayne: calculations/ alerts 
- Donavan: UI Design
- Gio: UI, Mapping
- Nymere: Accounts and Buddy System (Split)
- Austin: Calculations/alerts 

Flask local server:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
AUTH_LOG_VERIFICATION_CODES=true FRIENDSNME_DEBUG=true python server/data/app.py
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
$env:AUTH_LOG_VERIFICATION_CODES="true"
$env:FRIENDSNME_DEBUG="true"
python server/data/app.py
```

Then open:
http://localhost:5000/

When testing locally, verification codes print in the Flask terminal.

Environment variables:

- `SESSION_SECRET`: signs login cookies. If it is not set, the server picks a
  random one and everyone is signed out on restart. Generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"` and never commit it.
- `FRIENDSNME_DEBUG=true`: turns on the Flask debugger, the
  `/api/debug/users` endpoint and user details in the startup log. Only use it
  on your own machine, never when others on the network can reach the server.
- `AUTH_LOG_VERIFICATION_CODES=true`: prints verification codes in the
  terminal instead of emailing them.

Flask local network server:

Use this when people on the same Wi-Fi/network need to open the website from
their own devices. Leave `FRIENDSNME_DEBUG` off here.

```bash
source .venv/bin/activate
AUTH_LOG_VERIFICATION_CODES=true SESSION_SECRET=<your generated secret> python server/data/app.py
```

Then share this link with people on the same network (use your computer's IP):
http://10.109.29.222:5000/

Testing on phones (https):

Phone browsers only allow location on `https://` pages, so the plain
`http://` network link above can't track location. The easiest fix is a free
Cloudflare tunnel, which gives the laptop's server a temporary https link:

```bash
# install once: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:5000
```

Open the `https://....trycloudflare.com` link it prints on each phone.

How parties work (`server/data/party.py`):

- A party forms when 2+ phones are within 30 m of each other (chains count, so
  a line of people 20 m apart is one group). Separate groups form separate
  parties.
- The circle is the median position of the members inside it, with radius
  1.5 x their median distance from the center (30 to 150 m). Medians mean one
  person walking away can't drag the circle with them.
- The circle only moves or resizes while 3+ members are inside it; with 2 there
  is no way to tell who wandered, so it stays where it started. Changes are
  smoothed over ~30 s, so a group that walks somewhere together takes the
  circle along.
- Each phone's GPS accuracy (up to 25 m) is subtracted before calling it
  wandering; readings worse than 100 m are ignored. Locations older than
  2 minutes don't count.
- Overlapping parties merge. A party ends after 15 minutes without 2 members
  together. Parties live in memory, so restarting the server clears them.
- "Leave party" stops you being counted or re-added automatically. The party
  stays on your map in gray, and "Rejoin party" puts you back in as long as
  it's still going.
- Statuses: `INSIDE_PARTY`, `BUFFER_ZONE` (within 25 m of the edge),
  `WANDERING` (within 55 m), `FAR_FROM_PARTY`.
- Testing mode: while `SMALL_RADIUS_TESTING = True` in
  `server/data/calculations.py`, distances are roughly halved (15 m minimum
  radius, 20 m to form a party, buffer +10 m, wandering +25 m, GPS error
  forgiven up to 10 m). Set it to `False` for real use.

Wandering alerts (`Frontend/alerts.js`):

- When you or a friend in your party moves to `WANDERING` or `FAR_FROM_PARTY`,
  the page shows a banner, vibrates (Android) and sends a phone notification
  if you tapped "Turn on phone notifications". You also get one when they are
  back. Repeats for the same person are held back for 2 minutes.
- These only work while the page is open. Phones pause web pages when the
  screen is off or the browser is in the background; alerts in that case
  would need Web Push from the server.
- Notifications need https (or localhost), like location. On iPhone they only
  work if the site is added to the home screen.
- "Are you OK?" check: when *you* become `FAR_FROM_PARTY`, a popup asks if
  you're OK. If "I'm OK" isn't tapped within 5 seconds, the page tries to open
  the phone's dialer with the check-in number (`CHECK_IN_PHONE` in
  `alerts.js`). Browsers never place calls on their own, and many only open
  the dialer from a tap, so the popup keeps a "Call now" button that always
  works until "I'm OK" is pressed.

Location sharing is one-way: adding someone on the Sharing page lets them see
you on their map. They only appear on yours if they add you back.

Local data (`*.db`, `auth-db.json`) and `.venv` are in `.gitignore`. Do not
commit them: the database holds users' emails and locations.
