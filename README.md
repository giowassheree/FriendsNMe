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

Node local server:
http://localhost:3000/

Flask local server:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
AUTH_LOG_VERIFICATION_CODES=true SESSION_SECRET=development-test-secret-development-test-secret python accounts.py
```

Then open:
http://localhost:5000/

When testing locally, verification codes print in the Flask terminal.

Flask local network server:

Use this when people on the same Wi-Fi/network need to open the website from
their own devices.

```bash
source .venv/bin/activate
HOST=10.109.29.222 AUTH_LOG_VERIFICATION_CODES=true SESSION_SECRET=development-test-secret-development-test-secret python accounts.py
```

Then share this link with people on the same network:
http://10.109.29.222:5000/

Waitress local network server:

Use this instead of Flask's development server when sharing on the local
network.

```bash
source .venv/bin/activate
AUTH_LOG_VERIFICATION_CODES=true SESSION_SECRET=development-test-secret-development-test-secret waitress-serve --listen=0.0.0.0:5055 accounts:app
```

Then open locally:
http://localhost:5055/

Then share on the same network:
http://10.109.29.222:5055/

Vercel deployment:

This repository exposes a Vercel-compatible Flask entrypoint in `app.py`.
Import the GitHub repo into Vercel, or deploy from the Vercel CLI.

Required Vercel environment variable:

```text
SESSION_SECRET
```

Set this to a long random secret value.

Required for real emailed verification codes:

```text
SMTP_HOST
SMTP_PORT
SMTP_SECURE
SMTP_USER
SMTP_PASS
SMTP_FROM
```

Important: the current JSON data file is local-development storage. On Vercel,
temporary writes use `/tmp`, which is not a permanent database. Use a hosted
database before treating the deployed Vercel site as production.
