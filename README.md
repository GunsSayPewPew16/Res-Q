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
   establishment as a *Retail Store* or an *Eatery / Restaurant* and picks up to
   **2** usual surplus categories. A recipient instead picks up to **2** scheduled
   delivery days (Saturday through Friday) and up to **2** required goods from
   Prepared Meals, Fresh Produce, Dairy & Beverages, Household & Essentials and
   Packaged Goods. Each option carries a small grey note reminding the visitor of
   the two-item limit; once two are chosen, picking a third does nothing until one
   is unchecked. The selector shows three categories at a time and scrolls for the
   rest.
4. **Dashboard** — saving the goods profile posts it to the backend and sends
   the visitor to the dashboard for their role: `res_q_dashboard_donor.html` for
   a donor, `res_q_dashboard_recipient.html` for a recipient. Both are layout
   skeletons for now — the boxes, the metric row, the queue rail and the map panel
   are in place but hold no data — except for the delivery card, which opens the
   map overlay described under [The delivery map](#the-delivery-map), and the
   donor's slim navigation rail, whose four rows are now labelled Dashboard, Feed,
   Surplus Analyzer and Current Orders, with Finished Orders in the first slot
   below the rail's divider; the slot under it stays a bare pipeline pulse.
   Dashboard is the default selection; picking any of the other four items — Feed,
   Surplus Analyzer, Current Orders or the Finished Orders slot — wipes the working
   area and opens a blank screen for that section, and picking Dashboard brings the
   dashboard back. The selected item fills with the site's accent orange and its label
   turns black, the way the landing cards invert when they are hovered, while every
   other item keeps the dark tone with a neutral label. The bell in the header
   opens a small notifications panel under it rather than a browser alert, and the
   account pill beside it opens the same kind of panel; nothing feeds either list
   yet, so both are left empty on purpose.

The category list follows the donor's firm classification: a retail store can
hand over all six categories, while an eatery is limited to the three it
actually produces — Prepared Meals, Bakery Items and Dairy & Beverages.
Switching to a classification that cannot offer the selected category clears
the selection rather than leaving an invalid one behind.

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
| `frontend/res_q_dashboard_donor.html` | Donor dashboard — post-profile landing page: a layout skeleton with a labelled navigation rail that swaps the dashboard for blank section screens, and one working part, the delivery-location card |
| `frontend/res_q_dashboard_recipient.html` | Recipient dashboard — the same page for recipients |

Every page's top-left Res-Q lockup is a link. It goes to the dashboard for the
visitor's role when signed in, and to the landing page otherwise. Each page works
this out from the stored token plus a cached `resqRole`, and confirms both
against `/api/me`, so an expired session falls back to the landing page rather
than stranding the visitor on a page they can no longer use.

Because a dashboard belongs to one role, each one checks the session on load: a
visitor with no token is sent to the landing page, and a signed-in visitor whose
account role does not match the page they opened is sent to their own dashboard.
So a recipient who follows a donor dashboard link lands on the recipient one
rather than a page built for somebody else.

## The delivery map

Both dashboards carry one card that acts: **confirm delivery location**. It opens
an almost full-screen overlay holding a map, a search bar and a pin. The card
itself rests dark on the same notched silhouette as the cards beside it and fills
with the accent orange while hovered — or while keyboard-focused — the way the
landing page's capability cards do.

- It opens **on the location the account confirmed**, with the pin already on it,
  zoomed in. An account that has not confirmed one yet opens on the address it gave
  at onboarding instead; an account with neither gets the map's default view and a
  line saying to search for one.
- Type an address and press **Find** (or Enter) for up to five matching places, then
  pick one to move the pin there.
- **Moving the pin names the place it landed on.** Dragging the pin, or tapping the
  map, looks that spot up and puts its address in the search bar and in the readout, so
  the field always describes where the pin is rather than where it last was.
- The readout keeps the pin's address and its own coordinates on separate lines.
- **Confirming saves the pin to the account.** The *Confirm location* button posts the
  pin's address together with its latitude and longitude to `/api/delivery-location`,
  and the badge beside it switches from *Not saved yet* to *Saved to your account*. The
  button stays disabled until a pin exists and its address has finished resolving, so a
  stale address can never be confirmed — and confirming again after a move overwrites
  the saved location rather than adding a second one. A failed save says so in the
  overlay instead of pretending it worked, and the badge falls back to *Not saved yet*
  whenever the pin no longer matches what was saved.
- Escape, the close button, or a click on the backdrop closes the overlay.

The confirmed location is what the dashboard reopens on, and the one place on the
account that a later routing pass would read: the pair of coordinates saved with it is
enough to drop a pin for either side of a delivery. Saving it leaves the address the
account originally onboarded with untouched, so the two never overwrite each other.

The map is [Leaflet](https://leafletjs.com) drawing **OpenStreetMap's own tiles**, and
both lookups — the address search and the reverse lookup behind a moved pin — are
OpenStreetMap's **Nominatim** service. All three are free and keyless, so the overlay
needs no account, no API key and no card. Leaflet is fetched from a CDN the first time
the overlay opens, so the dashboards themselves stay light.

Both OSM services are shared public infrastructure, so the overlay stays a good
citizen: lookups run once per search and once per settled pin movement rather than on
every keystroke (Nominatim's policy forbids type-ahead against their public instance,
and the reverse lookup is debounced behind the drag), the map keeps the attribution
Leaflet draws for the tiles, and heavy or automated use would need a self-hosted tile
server or a commercial provider rather than these endpoints.

If the library, the tiles or a lookup cannot be reached, the overlay says which one
failed instead of showing an empty panel or a stale address.

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
restarting the server forgets everything. A session keeps the whole onboarding
answer set, address included, and holds the delivery location a person confirms the
same way, so `/api/me` answers with the same shape it does in persistent mode — which
is what lets the dashboard map open on the address that was typed and, once someone
has confirmed a spot, on that spot instead.

### Persistent mode

Started with `RESQ_PERSIST=on`, accounts are stored in `frontend/resq.db`
(override the path with the `RESQ_DB` environment variable). Two tables: `users`
and `sessions`. Restart the server for a mode change to take effect. A database
created by an earlier version is migrated in place on startup: `init_db` adds any
missing `users` column — the delivery location's four among them — with
`ALTER TABLE` rather than asking for a new file.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/register` | Create an account from the onboarding form. In persistent mode answers `409` when the email or phone is already registered |
| `POST` | `/api/login` | Sign in with an email or phone plus password |
| `GET` | `/api/me` | Return the signed-in user for a bearer token |
| `POST` | `/api/logout` | Drop the session |
| `POST` | `/api/profile` | Save the goods profile — establishment type and up to 2 surplus categories for donors, up to 2 delivery days and up to 2 required goods for recipients. Answers beyond the limit are rejected with `400`. The page sends the visitor to the dashboard for their role once this returns 200 |
| `POST` | `/api/delivery-location` | Save the delivery location the dashboard pin points at, from an `address` plus `lat`/`lng`. Needs a bearer token (`401`), and answers `400` for an empty address, coordinates that are not numbers or are off the globe, or a malformed body. Overwrites any earlier confirmation and only ever writes the caller's own row; the refreshed user comes back in the response |

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
- **The confirmed delivery location.** `/api/delivery-location` writes four fields on
  the signed-in account: `delivery_address`, `delivery_lat`, `delivery_lng` and
  `delivery_confirmed_at`. The dashboard map reads them back from `/api/me` when it
  boots, which is why a returning visitor opens on the spot they confirmed rather than
  on their onboarding address. Those coordinates are the hook a routing pass would use
  to match a donor's pickup with a recipient's drop-off; the address travels with them
  so the place stays readable.
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

The dashboards are still skeletons: the boxes and the layout are in place, and the
donor's rail names its sections and switches between them, but every section screen
except Dashboard is blank and only the delivery card does anything. Its map needs
no key, so it works as soon as the server is running. That one card is wired all the
way through, though — pick a spot, confirm it, and the address and its coordinates
are stored on the account and waiting there the next time the map opens.

The layout of the site is still settling: the landing page keeps only teaser
content (the hero details, the capability cards and the footer) and hands off to
the onboarding page, whose role chooser modal is now unreachable from the UI, and
the onboarding page still carries its own copy of the marketing sections.
