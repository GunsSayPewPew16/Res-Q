# Res-Q

Res-Q is the front end for a surplus-redistribution network. Local businesses
with unsold food and essential goods — supermarkets, restaurants, bakeries —
are profiled once and then matched against the shelters and community groups
that need those supplies, so redistribution stops depending on phone calls and
spreadsheets.

The front end is plain HTML, CSS and JavaScript styled with Tailwind CSS from a
CDN — no build step, no framework. Alongside it sits a small Python backend that
uses only the standard library: it serves the pages and handles the onboarding
API, either storing accounts in SQLite or, by default, running in a demo mode
that stores nothing. See [Backend and API](#backend-and-api).

## What the site does

The site serves two audiences through one flow:

- **Donors** — businesses with surplus. They describe where their surplus comes
  from and what they usually have spare.
- **Recipients** — shelters and community organisations. They describe their
  operation and what they need most.

Both roles walk the same path and end up with a profile that says who they are,
where they are, and what goods are involved. Copy and headings adapt to the
role chosen at the start.

## The onboarding flow

0. **Landing page** — the site's default document, shown whenever the site is
   opened or reloaded. It presents the pitch and the platform's capabilities,
   and its *Onboarding* button opens `res_q_homepage.html`, where the flow below
   begins. A role chooser modal still lives on the landing page but nothing
   opens it any more; it is kept for the wiring that is still pending.
1. **Role gate** — opens on the onboarding page. The visitor picks Donor or
   Recipient (the *Login* link is a placeholder). This choice drives the wording
   of every later step and is passed along in the URL.
2. **About You** — name, business or organisation name, a contact that can be
   either an email address or a phone number (toggleable in place), a password
   with live requirement checks, and a delivery address. Nothing invalid can be
   submitted.
3. **Goods profile** — the questions differ by role. A donor classifies their
   establishment as a *Retail Store* or an *Eatery / Restaurant* and picks their
   primary surplus category from Prepared Meals, Fresh Produce, Bakery Items,
   Packaged Goods, Dairy & Beverages and Household & Essentials. A recipient
   instead picks a preferred delivery day (Saturday through Friday) and their
   primary need from Prepared Meals, Fresh Produce, Dairy & Beverages, Household
   & Essentials and Packaged Goods. The selector shows three categories at a time
   and scrolls for the rest.

Completing the goods profile confirms that the profile is active. All
completion states are client-side only.

The onboarding page still carries its own copy of the marketing sections: it
introduces the pipeline, lists the platform's capabilities, shows a sample of
the matching logic, and invites visitors into the flow above.

## Pages

| File | Purpose |
| --- | --- |
| `frontend/index.html` | Landing page — the site's entry point and default document |
| `frontend/server.py` | Backend: serves the pages and the onboarding API (demo mode by default, SQLite when persistence is on) |
| `frontend/res_q_homepage.html` | Onboarding — the role gate and the About You form, plus a copy of the marketing sections |
| `frontend/res_q_surplus_profile.html` | The goods profile step both roles land on after onboarding |

## Running it locally

Start the backend, which serves the pages and the API on one origin:

```bash
cd frontend
python3 server.py                    # demo mode: nothing is stored
RESQ_PERSIST=on python3 server.py    # persistent: accounts go to frontend/resq.db
```

Then visit <http://localhost:8080/>. The server returns the landing page as the
default document, so reloading keeps the visitor there. The landing page's
*Onboarding* button opens `res_q_homepage.html`, and that form hands the visitor
on to `res_q_surplus_profile.html`.

Opening the pages straight from disk works for the layouts, but the forms cannot
save anything, because there is no server to talk to.

## Backend and API

The backend listens on `127.0.0.1:8080`. `GET /api/health` reports which mode it
is in.

### Demo mode (default)

Nothing is written to a database and no detail is ever rejected as a duplicate,
so the whole site can be walked through repeatedly without inventing a fresh
email address each time. Sessions and the details you type live in memory for
the life of the process; any email or phone plus any password signs you in, and
restarting the server forgets everything.

### Persistent mode

Started with `RESQ_PERSIST=on`, accounts are stored in `frontend/resq.db`
(override the path with the `RESQ_DB` environment variable). Two tables: `users`
and `sessions`. Restart the server for a mode change to take effect.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/register` | Create an account from the onboarding form. In persistent mode answers `409` when the email or phone is already registered |
| `POST` | `/api/login` | Sign in with an email or phone plus password |
| `GET` | `/api/me` | Return the signed-in user for a bearer token |
| `POST` | `/api/logout` | Drop the session |
| `POST` | `/api/profile` | Save the goods profile — establishment type and surplus category for donors, preferred delivery day and primary need for recipients |

How the pieces connect:

- **Saving.** The About You form posts its answers to `/api/register`; the goods
  profile posts to `/api/profile`. In persistent mode both update `users`; in demo
  mode they update the in-memory session only.
- **Duplicate details.** In persistent mode email addresses are compared
  case-insensitively and both the email and phone columns are unique, so
  re-entering details that already belong to an account answers `409` and the page
  shows that message inline under the form instead of moving on. Demo mode never
  answers `409` — that check is the first thing it switches off.
- **Being remembered.** A successful register or login returns an opaque session
  token, which the browser keeps in `localStorage` as `resqToken`. On load the page
  calls `/api/me` with it; if the token is still valid the role gate greets the
  visitor by name with a *Continue* shortcut, otherwise the token is discarded.
- **Passwords** are hashed with PBKDF2-HMAC-SHA256 and a per-user salt in
  persistent mode; only the hash is stored, and demo mode hashes nothing because it
  keeps no credentials at all.

## Design language

The site is deliberately dark and technical:

- Near-black background (`#0c0c0c`), raised card surfaces (`#141414`) and a
  single safety-orange accent (`#ff4500`) used for actions and labels.
- Tailwind tokens are declared inline in each page's `tailwind.config`, so all
  three pages share one palette.
- Notched cards (`clip-path` corner cuts), oversized black-weight headings in
  uppercase, and monospace micro-labels such as `// 01. SELECT FIRM
  CLASSIFICATION` carry the operator-console feel.
- Layouts use a split panel: a branded left half and a single-column form on
  the right.

## Status

A working prototype with both modes proven end to end: onboarding answers reach
the backend, a duplicate email is refused with an inline message when persistence
is on, and a returning visitor is recognised from their stored session. The
shipped default is demo mode, so out of the box nothing is saved and no credential
is needed twice; flip `RESQ_PERSIST=on` for the real thing.

What it deliberately lacks: email verification, password reset, rate limiting and
account recovery. The database is a local file, so taking this live would mean
moving to a managed database and giving the auth a real review. The matching
pipeline shown on the marketing page remains illustrative.

The layout of the site is still settling: the landing page keeps only teaser
content (the hero details, the capability cards and the footer) and hands off to
the onboarding page, whose role chooser modal is now unreachable from the UI, and
the onboarding page still carries its own copy of the marketing sections.
