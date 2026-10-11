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

The surplus board — what donors have spare and who has claimed it — is the one
part that lives outside that backend, in a Supabase Postgres table the dashboards
read and write directly: see [The surplus board](#the-surplus-board-supabase).

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
   skeletons around the parts that are wired: the delivery card, which opens the
   map overlay described under [The delivery map](#the-delivery-map), the donor's
   compose box, which now opens the donor's *Feed* and writes a row into Supabase,
   and the screens that read that table back — the *Feed* both
   roles now share, and the donor's *Surplus Analyser*, all described under
   [The surplus board](#the-surplus-board-supabase) —
   while the boxes around them — the working board, the orders that have finished,
   the routing map — still hold no data of their own. Each page owns its own rail,
   markup and slots included, so relabelling one role's sections cannot move the
   other's: the rail runs **across the top** of the page as a row of tabs rather than
   down the side, and every tab names the section it opens in `data-view`, so the
   mapping from tab to screen is one table in each script. Both open with the same
   two tabs, Dashboard and Feed, and then name their own work: the donor's runs
   Surplus Analyser, Ongoing Deliveries, Finished Deliveries, and the recipient's
   runs Incoming Deliveries, Delivery Status, Received Deliveries.
   The tab being read is the one lit shape in the rail — filled with the mint accent,
   its label turned to ink — while every other tab is its label and nothing else,
   which is what keeps a five-tab rail from reading as five buttons. On a narrow
   screen the tabs wrap onto their own line and scroll sideways rather than out of
   reach. Dashboard is the default selection; Feed opens the shared
   board on either dashboard — with the donor's compose box above it, which is where
   a donor logs an item now — Surplus Analyser opens the predictor boxes and, under
   them, the donor's own logged surplus,
   and the remaining tabs — the recipient's two section tabs and its received-
   deliveries tab, the donor's Ongoing and Finished Deliveries — still wipe
   the working area for a blank screen of their own. Picking Dashboard brings the
   dashboard back.
   The Dashboard screen is one arrangement on both dashboards, borrowed from the
   reference layout: a title row with the single action the screen offers (the
   donor's *Log surplus* opens the Feed, the recipient's *Browse the board* does the
   same), the figures this role keeps beside the delivery card, then the filter row
   and the working board under it. The figures are a donor's impact record — surplus
   saved in kilos, meals served and orders completed, read from that donor's own
   stored profile, so the same numbers are there on every load — and the recipient's
   three counters, which hold placeholder bars because nothing reads them yet. The
   board under the filters is the **one light surface on the page**: three small
   filter labels over a mint box headed *Pending orders*, which holds the orders
   still waiting and, inset into its right-hand side, the selected order's details.
   Below it sit the routing map and a panel still waiting for content of its own, and
   under those the *Latest completed orders* cards — one per finished order, empty
   until an order finishes. The bell in the header
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
| `backend/server.py` | Backend: serves the pages out of `frontend/` and the onboarding API (demo mode by default, SQLite when persistence is on). The HTTP handler and the routes; everything it calls lives in the topic modules beside it |
| `frontend/res_q_homepage.html` | Onboarding — the role gate and the About You form, plus a copy of the marketing sections |
| `frontend/res_q_surplus_profile.html` | The goods profile step both roles land on after onboarding |
| `frontend/res_q_dashboard_donor.html` | Donor dashboard — post-profile landing page: a top rail of section tabs, the donor's own stored impact figures beside the delivery card, one light box headed *Pending orders* under its filter labels, the *Latest completed orders* cards, a *Feed* section that opens on the *Log surplus* compose box (item, quantity, the goods-category pill and the pickup address) and runs into the whole board read back, its three views being the whole board, a donor's own posts and the posts tagged with the goods they handle; the *Surplus Analyser* section reading only their own rows under the predictor layout's boxes, plus the delivery-location card |
| `frontend/res_q_dashboard_recipient.html` | Recipient dashboard — the same arrangement for recipients, carrying the same top rail under its own section names (*Incoming Deliveries*, *Delivery Status*, *Received Deliveries*) and the same Dashboard screen with its own counters; its *Feed* section is that same live board in three views (all posts, open, and the posts tagged with the goods this recipient needs), with a claim button on every open post |
| `frontend/resq_supabase.js` | The shared Supabase client and the `surplus_posts` calls both dashboards use: it lazy-loads the library on first use, reads, inserts and claims rows, streams changes, holds the six goods categories one time, and draws every post as the one shared card both roles see |
| `supabase/migrations/20261008000000_create_surplus_posts.sql` | The `surplus_posts` table with its row level security policies, its claim-only update guard and its realtime publication entry — run once against the project |
| `supabase/migrations/20261009000000_add_goods_type_to_surplus_posts.sql` | The `goods_type` tag column, its constraint to the six categories and the claim-guard update that freezes it — run once against the project, after the file above |
| `supabase/clear_surplus_posts.sql` | Empties the board between demos: one `delete` run by hand as the project owner, since the publishable key the pages hold cannot delete |

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
itself rests as a panel beside the figures — dark, with a line around it — and
fills with the mint accent while hovered — or while keyboard-focused — with
everything inside it turned to ink.

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
- **A spot with no street address is still a place.** Nominatim answers a reverse
  lookup with the place it has there and walks up its own hierarchy, so a pin that has
  no street still comes back as its region and a place that arrived without a one-line
  name has its parts stitched into one. Only a spot nothing is mapped at — open water,
  blank farmland — has no name to give; that pin is called by its **own coordinates**,
  and the readout adds a `NOTE` line saying why. A lookup that is merely throttled or
  unreachable is retried once before the coordinates stand in for it, under a note of
  its own.
- **Confirming saves the pin to the account.** The *Confirm location* button posts the
  pin's address together with its latitude and longitude to `/api/delivery-location`,
  and the badge beside it switches from *Not saved yet* to *Saved to your account*. The
  button stays disabled only while a lookup is still in flight, so a stale address can
  never be confirmed and no spot is a dead end — the coordinates are what a routing
  pass reads anyway. Confirming again after a move overwrites the saved location rather
  than adding a second one. A failed save says so in the overlay instead of pretending
  it worked, and the badge falls back to *Not saved yet* whenever the pin no longer
  matches what was saved.
- Escape, the close button, or a click on the backdrop closes the overlay.

The confirmed location is what the dashboard reopens on, and the one place on the
account that a later routing pass would read: the pair of coordinates saved with it is
enough to drop a pin for either side of a delivery. Saving it leaves the address the
account originally onboarded with untouched, so the two never overwrite each other.

The map is [MapLibre GL](https://maplibre.org) drawing **OpenFreeMap** vector tiles —
OpenStreetMap data, painted by a style that lives in the page rather than recoloured
afterwards: the ground is the ramp's sage, every road is one warm-white thread whose
weight alone says how big the street is, and the names carry a pale halo so they stay
readable where a white road runs under them. Painting the tiles feature by feature is
what the look needs — a picture of a map could only be filtered towards it. The
delivery pin is drawn in the page rather than fetched: an ink teardrop with a mint
ring and core, and MapLibre's own controls are cut to match — ink zoom buttons with
mint glyphs, an ink attribution strip. Both lookups — the address search and the
reverse lookup behind a moved pin — are OpenStreetMap's **Nominatim** service. All of
it is free and keyless, so the overlay needs no account, no API key and no card.
MapLibre is fetched from a CDN the first time the overlay opens, so the dashboards
themselves stay light.

Both OSM services are shared public infrastructure, so the overlay stays a good
citizen: lookups run once per search and once per settled pin movement rather than on
every keystroke (Nominatim's policy forbids type-ahead against their public instance,
and the reverse lookup is debounced behind the drag and asked for no more than two
attempts at one spot), the map keeps the attribution
MapLibre draws for the tiles, and heavy or automated use would need a self-hosted tile
server or a commercial provider rather than these endpoints.

If the library, the tiles or a lookup cannot be reached, the overlay says which one
failed instead of showing an empty panel or a stale address: a lookup that never
answers names the pin by its coordinates and says so, so even an offline overlay can
still hand a location to the account.

## The surplus board (Supabase)

The one thing the site keeps outside its own backend is the surplus board: the
items a donor has spare, and who has claimed them. It lives in a Supabase Postgres
table, `public.surplus_posts`, so an item is on every open dashboard the moment it
is logged rather than waiting for a page to be reloaded.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` | Primary key, defaulting to `gen_random_uuid()` |
| `donor_name` | `text` | Who is offering it, named the way the header names them |
| `item_name` | `text` | What the item is |
| `quantity` | `text` | How much of it, said the way a shop says it — "24 kg across 6 crates" |
| `location` | `text` | Where it can be collected from |
| `goods_type` | `text` | Which of the six goods categories the post is for, or `null` on a post logged before the category existed |
| `status` | `text` | `pending` or `claimed`, defaulting to `pending` |
| `created_at` | `timestamptz` | Defaulting to `now()`; the board reads newest-first |

Apply them once against the project, in order: paste
`supabase/migrations/20261008000000_create_surplus_posts.sql` into **SQL Editor →
New query → Run** in the Supabase dashboard, then
`supabase/migrations/20261009000000_add_goods_type_to_surplus_posts.sql` the same
way — or point the Supabase CLI at the project and run `supabase db push`. Both files
are written to be safe to run more than once. The second one only has to be applied
once: until it is, the compose box still logs posts, but it logs them untagged and
says so on the line under the fields, so a half-migrated project loses the category
rather than the ability to post.

Emptying the board is a separate, deliberate act rather than a migration:
`supabase/clear_surplus_posts.sql` deletes every row and is run by hand the same way.
It has to be run as the project owner, because the pages hold a key that may read the
board, add a post and claim one — and delete nothing — so no page and no script with
that key can clear the board on its own. Open dashboards empty themselves as soon as
the delete lands, the same realtime path a new post takes.

The board is the *Feed* on **both** dashboards — donors and recipients are looking at
the same list of everything published, newest first. The three screens that use it all
sit on a dashboard rather than behind the API:

- **Log surplus** — the compose box that opens the donor's own *Feed*, above the
  board it posts to, so posting and watching the post land are one screen. It is laid
  out as the reference's compose row: the silhouette avatar, the one line a post
  starts on, then a row of controls with the one button at the far right. The controls
  are the site's own — an item, a quantity, the goods category the post is for and a
  pickup location, the pickup address prefilled from the account's own confirmed
  location — and *Post* inserts the row. The category is the third pill in the row and
  the only one that opens anything: a long pill on the quantity and address silhouette
  that drops the goods profile's own list under it, the six categories with their
  emoji and their hint line. It is asked for rather than guessed, because an untagged
  post belongs to no category and so never reaches a *For You* view. Nothing is
  optimistic: the line under the fields says the item is on the board only once the
  database has answered with the row it wrote, a refusal is shown with its reason
  instead of a success message, and the board beside it refreshes at that moment
  rather than waiting for the subscription to echo the row back.
- **The board itself** — the *Feed* on either dashboard, drawn as a feed. Every post
  is a card on the site's own palette: the logging donor's avatar, then their
  name with a handle-and-age line beside it, the item as the card's heading, the
  quantity picked out in the accent above the muted pickup address, and a bottom row
  carrying the post's own facts — its age, its status — and the one action the reader
  has on it, a *Claim* that rests dark with cream text and fills with the accent on
  hover, the way the hero cards do. Above the cards sits the board's tab bar: three
  views of the same list — the whole board, a narrowed one by authorship or status
  (*My posts* for a donor, *Open* for a recipient) and *For You*, the posts tagged with
  the goods the reader picked in their goods profile — all filtered from the rows
  already read so switching is instant, while the chip at the right keeps counting the
  whole board either way. Each card carries the category it is filed under beside its
  status, which is what makes the *For You* view legible: the filter chooses between
  tagged posts, so it shows which tag it chose. A
  donor's own posts are marked *your post* so they are easy to pick out among everyone
  else's. The two copies differ only in what they offer: a recipient's card has one
  action, **Claim**, which
  writes `status = 'claimed'`, and a donor's has none, because taking surplus is the
  recipient's side of the exchange. A claimed card keeps its place with a claimed chip
  and no button, so the board reads as a record of what was offered rather than
  quietly emptying. A claim only ever matches a post that is still `pending`, so the
  second visitor to click the same item is told *already claimed* and the card is
  re-read instead of the claim being overwritten. A claim is also an accepted order,
  and it adds one to that donor's orders figure on their dashboard's effect panel —
  see [the impact figures](#how-the-pieces-connect).
- **Surplus Analyser** — the donor's own rows, the same table narrowed to that
  donor's name, with the status each one has reached. It is the donor's log rather
  than the shared board, which is why it lives beside the feed instead of in it.
  The screen opens on the surplus predictor's layout: the forecast banner and the
  weekly yield chart both carry live data, while the category split is still held open
  by bare placeholder bars. Running the analyser reads the **live weather** at the
  account's own address and forecasts **today's** surplus from that day, that sky and the
  days the kitchen has **logged** — the model's own defaults stand in only when there is
  no log to read — then reads the same point's seven-day outlook to draw the chart: one
  bar per day, today first, which is where the model's own weekend bump turns up. See
  [the live weather](#the-live-weather) and [the logged days](#the-logged-days). The
  section is the donor's own; the recipient dashboard has no analyser.

### For You, and what a post is tagged with

*For You* is the board read as one account's own slice of it. A post carries the goods
category it is for — one of the same six the goods profile offers, written into
`goods_type` by the compose box — and the tab keeps the posts whose category is one of
the two the reader picked in their profile: what they handle as a donor, what they need
as a recipient. A donor whose goods are fresh produce and packaged goods sees the board
narrowed to those two; anyone reading it with no categories picked is told to pick
them rather than shown an empty list, and a post logged before the tag existed belongs
to no category, so it stays on the whole board and out of every *For You*. The six
categories live once, in `resq_supabase.js`, so the compose box's picker, the card's
chip and the tab's filter cannot drift apart — and the table constrains the column to
the same six, so a post cannot be filed under a category nothing else knows.

Every one of those screens is live. While one is on screen it subscribes to Postgres
changes on the table over Supabase Realtime, so a post logged anywhere appears in an
open feed on its own and a claim flips the card in every other open feed — including
the donor's copy of the board, which is the same list being watched from the other
side. A timer
re-reads the table every 20 seconds as well, which is what covers a project where the
realtime publication was not set up — the board is then still live, just a little
later. The subscription and the timer both stop when the section is left.

The pages hold the project's **publishable** key, which is the key meant to sit in a
page: it can only ever act as the anonymous role, and what that role may do is decided
by the table's row level security policies. Those policies are exactly the three the
dashboard needs — anybody may read the board, add a post, and set a status — and
nothing else: no deletes, and no access to anything but this table. Because Res-Q runs
its own sign-in rather than Supabase Auth, a post is attributed to the name on the
donor's own profile rather than to a Supabase user, and the table's update trigger
refuses any change other than a status, so a claimed row cannot be rewritten into a
different one. The publishable key is safe to be seen; the secret key is never used by
the pages and is not in this repository. This is still a public board, though — a
deployment that has to know *who* posted would move the writes behind the backend,
where the secret key can be held and a rate limit put in front of it.

## Running it locally

Start the backend from the repository root; it serves the pages and the API on one
origin:

```bash
python3 backend/server.py                   # demo mode: nothing is stored
RESQ_PERSIST=on python3 backend/server.py   # persistent: accounts go to backend/resq.db
```

Demo mode seeds five test donors with six weeks of logs each on startup — see
[the logged days](#the-logged-days) for who they are and their password — so the analyser
has a real past to forecast from the moment the server is up. `RESQ_SEED=off` starts
without them, and with persistence on they are written only when `RESQ_SEED=on` asks.

To run it the way a host will, see [Deployment](#deployment):
`gunicorn -c gunicorn_config.py wsgi:application` from `backend/` is the same API behind
the same routes, serving the same pages, and it is worth running once before deploying.

Then visit <http://localhost:8080/>. The server returns the landing page as the
default document, so reloading keeps the visitor there. The landing page's
*Onboarding* button opens `res_q_homepage.html`, and that form hands the visitor
on to `res_q_surplus_profile.html`.

Opening the pages straight from disk works for the layouts, but the forms cannot
save anything, because there is no server to talk to.

The surplus board needs one step of its own, done once against your Supabase project:
apply `supabase/migrations/20261008000000_create_surplus_posts.sql`, as described
under [The surplus board](#the-surplus-board-supabase). Until it is applied, the donor's
form and both boards say so plainly — *surplus_posts is not in the project* — rather
than failing quietly. That board is read and written from the browser, so nothing about
it lives in the backend's database and it behaves the same in demo mode as it does with
persistence on.

## Deployment

The API and the pages are one service. `backend/server.py` serves the front-end from
`frontend/` and answers `/api/*` out of the same process, so a host that can run Python is
the whole of what this needs to be reachable on the internet — which is why `render.yaml`
in the repository root asks for exactly one web service, and why the URL it hands back is a
working site on its own: the landing page at `/`, the liveness probe at `/api/health`, the
rest of the API under `/api/`.

### The entry point a host uses

Run by hand, the API *is* the standard library's HTTP server (`python3 backend/server.py`).
A host brings its own server instead, and that server speaks WSGI, so `backend/wsgi.py`
adapts the same handler to it: every WSGI request is written back out as the HTTP request
the handler already parses, and what it answers is handed back as a WSGI response. Nothing
is reimplemented in there — no route, no static file, no CORS rule exists twice — so a
deployed API and a local one answer identically. Gunicorn then runs it:

```bash
cd backend
pip install -r requirements.txt
gunicorn -c gunicorn_config.py wsgi:application   # $PORT when set, else 127.0.0.1:8080
```

| File | What it is for |
| --- | --- |
| `render.yaml` | The Blueprint: one Python web service, with its build command, start command, health check and environment |
| `backend/requirements.txt` | The one dependency there is — Gunicorn; nothing in `backend/` imports anything else |
| `backend/gunicorn_config.py` | The server's settings, each one with the reason it holds the value it does |
| `backend/wsgi.py` | The API as a WSGI application, and the startup a worker does before its first request |
| `backend/.python-version` | The Python version the host builds with |
| `backend/Procfile` | The same start command, for a host that reads a Procfile instead of a Blueprint |

**One worker, and that is a requirement rather than a preference.** Demo mode keeps its
accounts, sessions and logged days in the process's memory, so a second worker would hold a
second, separate copy of all of it — sign in through one and the next request could reach a
worker that has never heard of that session. Requests are taken four at a time by threads
inside the one process instead, which is also what stops a route waiting on Open-Meteo from
holding up a page load. The count lives in `gunicorn_config.py` rather than in the host's
dashboard so that the reason stays attached to the number; it is the one setting to revisit
when the accounts move fully into a database.

### On Render

New → **Blueprint** → pick this repository → Apply. Render reads `render.yaml`, builds with
`pip install -r requirements.txt` from `backend/`, and starts it with
`gunicorn -c gunicorn_config.py wsgi:application`. Two values are deliberately not in the
file and are filled in on the service's **Environment** page:

| Variable | What it does |
| --- | --- |
| `RESQ_ALLOWED_ORIGINS` | Which origins may read this API from a browser — see below. Empty means none may, which is the right default for a service only its own pages call. |
| `RESQ_PERSIST` / `RESQ_DB` | Set to `on`, with `RESQ_DB` on a mounted disk, to store accounts in SQLite. Left off, as committed, the deployment runs in demo mode. |

`RESQ_SEED=on` — also in the file — is what makes a fresh deployment useful immediately:
the worker creates the five test donors and their six weeks of logged days as it starts, and
prints their addresses and shared password to the service log. The archive weather read is
made once per donor at that moment, so the first boot takes a few seconds longer than the
ones after it.

A **free** instance sleeps after fifteen minutes with no traffic and takes about a minute
to wake, and its disk is ephemeral: anything written is gone on the next deploy, restart or
wake-up. That is exactly why the deployment runs in demo mode — its state is the worker's
memory, and the seed is what refills it — and why turning `RESQ_PERSIST=on` there would
only appear to work. Persistent accounts want a paid instance with a disk mounted at
`RESQ_DB`'s path.

### The front-end on Vercel, talking back to Render

The pages can be served by Vercel instead of (or as well as) by the API process. Import the
repository, set **Root Directory** to `frontend`, leave the framework preset on *Other* and
the build command empty — there is no build step, the pages are the files. Then two things
have to agree:

1. **The pages have to know where the API is.** Every call is written as
   `fetch(apiUrl('/api/…'))`, and `frontend/resq_api.js` is the one line that decides which
   origin those paths are asked of. It is empty in the repository, which means "this same
   origin" — how it runs locally, and how a Render-only deployment works. On Vercel, set it
   to the API's own origin:

   ```js
   var RESQ_API_BASE = 'https://res-q-api.onrender.com';
   ```

2. **The API has to allow that origin.** A browser will not let one site read another's API
   unless the other site says it may, which is what `RESQ_ALLOWED_ORIGINS` is. It is a
   comma-separated list, and an entry is either an exact origin or a wildcard for one
   domain's subdomains — `*.vercel.app` is usually wanted beside the production URL, because
   Vercel gives every preview deployment its own hostname:

   ```
   RESQ_ALLOWED_ORIGINS=https://res-q.vercel.app,*.vercel.app
   ```

   A single `*` is honoured only when it is written out, and an empty list allows nothing:
   a deployment nobody configured stays same-origin only. What an allowed origin is granted
   is narrow — the origin itself in `Access-Control-Allow-Origin`, `GET, POST, OPTIONS`,
   and the two headers the pages actually send (`Authorization`, `Content-Type`) — and no
   `Allow-Credentials`, because the session is a bearer token in a header rather than a
   cookie, so a browser is never told to send one. `Vary: Origin` keeps a cache from serving
   one origin's answer to another. A `POST` carrying `Authorization` is asked about first
   with an `OPTIONS` preflight, which is answered for the same list and nothing else.

Getting either half wrong looks the same in the browser — a failed call and a CORS line in
the console — so the two are worth checking together: the origin in the console's error
message is the one to put in `RESQ_ALLOWED_ORIGINS`, and the base in `resq_api.js` is what
has to match it.

There is a third arrangement that needs neither half: leave `RESQ_API_BASE` empty on Vercel
and add a rewrite to `vercel.json` sending `/api/*` to the Render service. The pages then
call their own origin, Vercel forwards the calls, and no cross-origin rule is involved at
all. It costs a hop and hides the API's URL from the browser; naming the origin directly,
as above, is the shorter path when both are yours.

### Somewhere else entirely

Nothing in the deployment is Render-specific: any host that runs
`pip install -r requirements.txt` and then a process bound to `0.0.0.0:$PORT` will do, and
`backend/Procfile` carries the same start command for the ones that read one. The two
things a host has to get right are those — the `$PORT` it hands over, which both
`gunicorn_config.py` and `server.py` read, and one worker, for the reason above.

`backend/claim_recorder.py` is *not* part of this service and is not deployed with it: it
is a companion process on its own port that records which recipient claimed which post. It
lives in `backend/` because that is where this project's Python lives, and nothing in the
API imports it.

## Backend and API

The backend is split so it can be read a topic at a time. Only the HTTP handler and
the routes live in `backend/server.py` — everything it calls sits in the module that
owns the topic, and `config.py` owns every constant, limit, vocabulary and demo store:

| Module | What it holds |
| --- | --- |
| `backend/config.py` | Every constant the API reads and answers with — field lists, vocabularies, limits, the demo stores |
| `backend/db.py` | The SQLite connection (`connect`) and the schema plus in-place migration (`init_db`) |
| `backend/helpers.py` | Request cleaning: selections, stored JSON lists, contacts, ids, bounded query numbers, and how an account is named |
| `backend/security.py` | Password hashing (PBKDF2-HMAC-SHA256) and the password checks |
| `backend/geo.py` | Haversine distance, the bounding-box pre-filter, the demo offset point |
| `backend/accounts.py` | The donor impact figures (seed/fill), demo account and demo peer stores |
| `backend/matching.py` | What makes two accounts deliverable: shared categories, confirmed pins |
| `backend/orders.py` | The order's stored shape, shared by both modes |
| `backend/surplus.py` | The surplus calculator: the regression pipeline, trained once and cached, forecasting per request |
| `backend/history.py` | The logged days — one row per kitchen per day — and the features the model reads out of them |
| `backend/seed.py` | The five test donors and their six weeks of logs, with the weather taken from Open-Meteo's archive |
| `backend/weather.py` | The live conditions at a point — now, day by day for the week ahead, and the days behind it — and the one table that turns a WMO code into the model's Sunny/Cloudy/Rainy |
| `backend/wsgi.py` | The same handler as a WSGI application, which is how a host runs it; see [Deployment](#deployment) |

The server itself still uses only the standard library, so nothing has to be
installed for the site, the accounts and the matching to work — the one dependency in
`requirements.txt` is the server a host runs it on, not anything the API imports. The live
weather wants nothing installed either — `weather.py` calls Open-Meteo with `urllib` —
while the surplus forecast additionally wants pandas and scikit-learn: `surplus.py` imports
them inside its functions so the rest of the backend runs without them, and the endpoint
answers `503` with install instructions when they are missing.

Run by hand the backend listens on `127.0.0.1:8080`; under a host it listens on the
address the host asks for — `$PORT`, on `0.0.0.0` — and either can be overridden with
`RESQ_HOST` and `RESQ_PORT`. `$PORT` counts only when it is a port number: an empty value
or a `0` in the environment is somebody else's variable rather than a host's instruction,
and both entry points ignore it rather than binding somewhere nothing is looking.
`GET /api/health` reports which mode it is in.

### The surplus calculator

`backend/surplus.py` is the predictor the Surplus Analyser screen is built around,
kept as close to identical as possible to the original `surplus.py` notebook script
it came from: 200 synthetic days of kitchen history — customers served by day and
weather, a weekday rhythm with a weekend bump — ending in a random-forest regressor
through an impute → scale / one-hot pipeline, with the mean absolute error it scores
at printed when the file is run on its own (`python3 backend/surplus.py`).

The row it forecasts is **today's**: the day the kitchen is cooking on, and the surplus
it is holding now. The module exposes two calls:

- `train_surplus_model()` — the original script verbatim, seed and features and split
  unchanged, returning the fitted pipeline and its MAE (9.2 kg on the held-out tail).
- `forecast_surplus(**features)` — the call a request reaches. Every feature is
  optional; anything left out is filled with a usable default, and the labels default
  to **today's** own day and a clear sky. The clear sky is a fallback rather than a
  reading — the dashboard reads the real one at the account's address and passes it in
  as `weather` — so a request that omits it is one that had no address to read. The
  pipeline is trained on the first call and cached in the process, so the forest is not
  rebuilt per request.

`GET` or `POST /api/surplus-forecast` calls it. Accepts `day_of_week` (a day name),
`weather` (Sunny, Cloudy or Rainy) and the four numeric features
(`expected_customers`, `surplus_yesterday`, `surplus_last_week`, `surplus_rolling_14`),
any of them, in the query string or a JSON body. A label outside the vocabularies the
training data was built on is refused with `400` rather than forecast against
silently, and the answer carries the prediction, the MAE it comes with and the model
and features that produced it. With pandas or scikit-learn missing, it is `503`,
`model_dependencies_missing`, with the install line in the message.

### The live weather

The analyser forecasts today's surplus, so the sky in that row has to be today's too.
`GET /api/weather?lat=..&lng=..` reads it from [Open-Meteo](https://open-meteo.com/) —
keyless, like the map's vector tiles and the address lookups, so nothing has to be
registered to run this server. Coordinates are required and are checked the way the
delivery location checks them: a point the site itself would refuse to store is a point
nobody has an address for, so nothing is read there. The reading is today's own summary
— the WMO code the day is filed under — with the sky right now carried beside it, in
the address's own timezone, because "today" is the address's day and not UTC's.

The model was trained on three words for the sky, so the table in `backend/weather.py`
is the one place that turns a WMO code into them: clear and mainly clear are **Sunny**,
partly cloudy, overcast and fog are **Cloudy**, and everything that falls out of the sky
— drizzle, rain, showers, snow, thunderstorms — is **Rainy**. Snow answered as rain and
fog as cloud is information dropped, because the model has no word for either, so the
answer carries the raw code, its own summary and the day's rainfall beside the label for
anything that wants to show the weather rather than feed it to the model.

The dashboard reads it at the account's own address: the **delivery location confirmed
on the Dashboard** when there is one, and the address typed at onboarding — geocoded
with the same Nominatim lookup the map's search uses — when there is not. A run with no
address at all, or with the service unreachable (`502`, `weather_unavailable`), still
forecasts, and says on the banner and in the conditions panel that the sky could not be
read rather than passing a made-up one off as a reading.

The day the forecast row carries comes from that same reading: the date Open-Meteo files
today under **at the address**, so the day the kitchen is actually cooking on is the day
being forecast, and the reader's own clock is only the fallback for a run that had no
reading to take a date from.

**The week ahead** is the same point read again as a run of days:
`GET /api/surplus-outlook?lat=..&lng=..` asks for that daily outlook — seven days by
default, and never more than the sixteen the service will answer at all — then runs
**each day through the same model** under that day's own name and that day's own sky.
That is what gives the analyser's chart its shape: the sky moves from day to day, and
`Saturday` and `Sunday` carry the model's weekend bump by themselves. Nothing between the
bars is interpolated, averaged or smoothed — every bar is one forecast of one day — and
the reading behind each label travels with it (the WMO code, its summary, and the day's
high, low and rainfall) beside the prediction for anything that wants both.

The chart draws those bars across the week's **own** range rather than up from zero:
seven days of one kitchen's surplus sit within a few kilos of each other, so a
zero-based axis would draw seven identical bars and hide the shape the chart exists to
show. The range is named under the chart and the two bars that matter — today's and the
week's heaviest — carry their figures, which is what makes the axis readable from the
panel itself; a week that really is flat is drawn flat rather than exaggerated into a
shape it does not have.

### The logged days

A forecast is only as good as the past it is made from, so the past is stored: one row
per kitchen per day, holding what that kitchen served, the sky over it and the surplus
that came out of the day. In persistent mode those rows are the `surplus_history` table,
keyed by the account and unique per date, so a kitchen's own past cannot be logged twice;
in demo mode they live in the process, filed under the contact the account signed in with,
because demo users all carry id `0` and there is no table to key them by.

`backend/history.py` derives the model's own features from those rows rather than from a
second copy of the same week: the surplus **yesterday**, the same weekday's surplus a week
ago, the mean of the **fourteen days before** the day being forecast, and the customer
count that weekday has actually run at. Every one of them is read *before* the day being
forecast — the window the training data built them in — and a feature the log cannot
answer comes back as nothing rather than as a zero: a day that has not happened yet has no
yesterday to read, and the model's imputer is there for exactly that. A value the caller
sends still wins over the log, so an explicit `surplus_yesterday` is never overwritten.

The day itself can be named: `POST /api/surplus-forecast` takes an optional `on_date` (an
ISO day) and reads the log for *that* day rather than for the server's, which is what lets
the dashboard read the log for the date its own address is on. `GET /api/history` answers
the signed-in account's own days, newest first, with the summary and the derived features
beside them: what the log says, rather than making anything else derive it again.

**Five test donors ship with the server**, so the calculator can be exercised against a
real past instead of its own defaults. Each one has six weeks of days ending yesterday:

| Sign in as | Kitchen | Where | Serves per day |
| --- | --- | --- | --- |
| `dana@resq.test` | Whitfield Bakery | 60 Queen St W, Toronto | 170–250 |
| `omar@resq.test` | Haddad Grocers | 1180 Ste-Catherine O, Montréal | 200–300 |
| `priya@resq.test` | Raman Kitchens | 87 Elm St, Toronto | 110–190 |
| `luis@resq.test` | Ortega Deli | 601 Biscayne Blvd, Miami | 230–330 |
| `mei@resq.test` | Chen Noodle House | 55 Bay St, Toronto | 90–170 |

The password is `ResqDemo1!` for all five. They are created when the server starts in
demo mode — nothing is written to disk there — and with persistence on only when
`RESQ_SEED=on` asks for it, so a database nobody meant to touch is never written to
uninvited. `RESQ_SEED=off` turns it back off in demo mode. Each donor arrives working: a
goods profile, a confirmed pin at its own address and its impact figures, because a test
account that has to be walked through onboarding first is not one.

The **weather in those logs is real**: each donor's days carry the conditions Open-Meteo's
archive actually recorded at that donor's coordinates, so the history is a record rather
than an invention. If the archive cannot be reached the days fall back to a deterministic
pattern, and a day from that pattern carries no WMO code — which is what marks it as not a
measurement. The customers and the surplus are the fixture's own, generated from the same
shape the model was trained on (a weekday rhythm, the weekend bump, a little noise),
because a test that ran against some other shape would prove nothing about this model.
Everything is seeded per donor, so the same five kitchens come back the same way every
time; seeding again adds nothing, since a day already logged is left alone.

### Demo mode (default)

Nothing is written to a database and no detail is ever rejected as a duplicate,
so the whole site can be walked through repeatedly without inventing a fresh
email address each time. Sessions and the details you type live in memory for
the life of the process; any email or phone plus any password signs you in, and
restarting the server forgets everything. Signing out ends the session but not the
account, so the details you registered with sign back in as the role, the profile and
the metrics they registered with — a recipient signs back in as that recipient, not as
a fresh donor — which is what lets one machine walk the whole donor-then-recipient
journey. A session keeps the whole onboarding
answer set, address included, and holds the delivery location a person confirms, and a
donor's three impact figures, the same way, so `/api/me` answers with the same shape it
does in persistent mode — which
is what lets the dashboard map open on the address that was typed and, once someone
has confirmed a spot, on that spot instead.

### Persistent mode

Started with `RESQ_PERSIST=on`, accounts are stored in `backend/resq.db`
(override the path with the `RESQ_DB` environment variable). Two tables: `users`
and `sessions`. Restart the server for a mode change to take effect. A database
created by an earlier version is migrated in place on startup: `init_db` adds any
missing `users` column — the delivery location's four and the donor figures' three
among them — with `ALTER TABLE` rather than asking for a new file.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/register` | Create an account from the onboarding form. In persistent mode answers `409` when the email or phone is already registered |
| `POST` | `/api/login` | Sign in with an email or phone plus password |
| `GET` | `/api/me` | Return the signed-in user for a bearer token |
| `POST` | `/api/logout` | Drop the session |
| `POST` | `/api/profile` | Save the goods profile — establishment type and up to 2 surplus categories for donors, up to 2 delivery days and up to 2 required goods for recipients. Answers beyond the limit are rejected with `400`. The page sends the visitor to the dashboard for their role once this returns 200 |
| `POST` | `/api/delivery-location` | Save the delivery location the dashboard pin points at, from an `address` plus `lat`/`lng`. Needs a bearer token (`401`), and answers `400` for an empty address, coordinates that are not numbers or are off the globe, or a malformed body. Overwrites any earlier confirmation and only ever writes the caller's own row; the refreshed user comes back in the response |
| `GET` | `/api/matches` | Rank the counterparts near the signed-in account. Needs a bearer token (`401`), a confirmed delivery location and a saved goods profile (`400` with `no_location` or `no_categories` otherwise). A candidate is an account of the opposite role inside `radius_km` (default 25, ceiling 250) that shares at least one category with the caller; the nearest comes first and the list stops at `limit` (default 10, ceiling 50). Each match carries `distance_km`, `shared_categories` and the recipient's preferred `delivery_days` |
| `GET` | `/api/orders` | Every delivery order the signed-in account is one side of, newest first |
| `POST` | `/api/orders` | Bind the caller and one counterpart into a delivery order, from a `counterpartId` plus a `category` and an optional `scheduledFor`. Answers `404` `no_counterpart` for an id that names nobody, `400` for a same-role counterpart, a counterpart without a confirmed pin (`no_counterpart_location`), a category the two sides do not share (`not_shared`) or a day the recipient did not ask for (`not_preferred`), and `409` `duplicate_order` when that pair already has a live order for the category. The order snapshots both pins — the donor's pickup against the recipient's drop-off |
| `POST` | `/api/dev/switch-role` | **Temporary, demo mode only.** Hand back a session for the other dashboard, so both roles can be previewed from one sign-in. Answers `404` with persistence on, the way any unknown endpoint does, because it mints a session without a password |
| `GET` | `/api/history` | The signed-in account's own logged days, newest first, with the six-week window's summary and the features they answer. Needs a bearer token (`401` otherwise). A recipient's answer is an empty log rather than an error: the log belongs to the kitchen that cooked |
| `GET` | `/api/weather` | The real conditions at a point, in the analyser's own vocabulary. Requires `lat`/`lng`, and answers `400` for a missing, non-numeric or off-globe pair. Reads Open-Meteo — keyless — and returns the model's label for today, the WMO code and summary behind it, the day's high/low and rainfall, the current temperature and wind, the address's timezone and the source. Answers `502` `weather_unavailable` when the service cannot be reached, rather than a default sky |
| `GET` | `/api/surplus-outlook` | The week's surplus at a point, which is what the analyser's chart is drawn from: one forecast per day, today first. Requires `lat`/`lng` (`400` as above) and takes an optional `days` — a whole number, defaulting to 7 and pulled to the 16-day ceiling the weather service answers at, `400` when it is not a number. Each day carries its own date, day name, Open-Meteo conditions and the model's prediction, with the model and its MAE once for the whole week. Answers `502` `weather_unavailable` when the outlook cannot be read, and `503` `model_dependencies_missing` without pandas or scikit-learn |
| `GET`/`POST` | `/api/surplus-forecast` | Today's surplus forecast from `surplus.py`. Optional `day_of_week`, `weather`, `on_date` (the ISO day being forecast, which the log is read for) and up to four numeric features, in the query string or a JSON body. With a bearer token, any numeric feature the caller leaves out is **filled from that account's own logged days**, and the answer carries the log it read and which features came from it; an explicit value always wins. The dashboard reads the live sky first, through `/api/weather`, and passes its label as `weather`. Answers `400` for a label that is not a day name or one of Sunny/Cloudy/Rainy, a number sitting below zero or an unreadable `on_date`, and `503` `model_dependencies_missing` when pandas or scikit-learn is not installed |

### Matching and delivery orders

`GET /api/matches` is where the confirmed pins earn their keep: it measures the
distance from the signed-in account's pin to every account of the other role that has
a pin of its own and shares at least one category, then hands back the nearest first.
Candidates are filtered with a coarse latitude/longitude box and then measured
properly with the haversine distance; both figures are kilometres, rounded like every
other measurement here to two decimals. Only the confirmed pin counts — the address
typed at onboarding was never turned into coordinates — so an account with no pin is
told to confirm one (`400`, `no_location`) rather than matched against nothing.

`POST /api/orders` turns one of those matches into a delivery. The donor's pin becomes
the pickup point and the recipient's the drop-off, and both addresses are copied onto
the order beside the coordinates, so a routing or scheduling pass reads one row
instead of joining two profiles. Either side can open it — a donor names a recipient,
a recipient names a donor — and the two are stored on the correct sides whichever way
round the request came. An optional `scheduledFor` day is checked against the
recipient's own preferred days before it is booked. Orders start life `proposed`; the
transitions that move them on are not built yet, and until they are, the one-live-order
rule keeps the same goods from being put on the road twice.

In demo mode there is no second account to match against, so both endpoints answer
from a small set of fabricated counterparts — each one placed at a fixed distance and
bearing from the caller's own pin, with every donor category covered by a recipient
and every recipient need by a donor, so the pipeline can be walked through wherever the
pin was dropped. They carry `"demo": true` in the response and are never written
anywhere. Demo orders live on the session token, the way the demo sessions themselves
do, and go when the process does.

Nothing calls either endpoint yet: wiring the rails and the impact panel to
`/api/matches` and `/api/orders` is the next piece of work on the pages.

How the pieces connect:

- **Signing out.** The account menu in the dashboard header holds the two actions an
  account has. *Log out* posts to `/api/logout`, drops the stored token and role, and
  returns the visitor to onboarding; it clears them locally first, so nobody is left
  signed in on the page while the request is in flight. Beside it sits a temporary
  *Switch dashboard* button calling `/api/dev/switch-role`, which exists to make
  previewing both roles quick and works in demo mode alone — the note under the button
  says so when the backend refuses.

- **Saving.** The About You form posts its answers to `/api/register`; the goods
  profile posts to `/api/profile`. In persistent mode both update `users`; in demo
  mode they update the in-memory session only.
- **The surplus board.** What a donor logs is the one thing that does not go through
  this backend: the dashboards write to and read `public.surplus_posts` in Supabase
  directly, which is what lets an item appear in another signed-in visitor's feed
  without either page being reloaded. Sign-in, the profile and the confirmed delivery
  location stay where they are. The project URL and the publishable key sit in
  `frontend/resq_supabase.js`; that key is the anonymous one, so the table's row level
  security policies are what actually decide what it may do.
- **Duplicate details.** In persistent mode email addresses are compared
  case-insensitively and both the email and phone columns are unique, so
  re-entering details that already belong to an account answers `409` and the page
  shows that message inline under the form instead of moving on. Demo mode never
  answers `409` — that check is the first thing it switches off.
- **Being remembered.** A successful register or login returns an opaque session
  token, which the browser keeps in `localStorage` as `resqToken`. On load the page
  calls `/api/me` with it; if the token is still valid the role gate greets the
  visitor by name with a *Continue* shortcut, otherwise the token is discarded.
- **The donor's impact figures.** Three columns on the donor's own row:
  `surplus_saved`, a weight in kilograms kept to two decimals, and `meals_served` and
  `orders_completed` as whole counts. They belong to that profile and to no other role —
  a recipient's columns stay `NULL`, and the panel that shows them is on the donor
  dashboard only. A new donor profile starts from a plausible set, because nothing has
  come out of the pipeline yet, and a profile created before the columns existed is
  given its figures the first time it is read. That is what keeps the three figures the
  same on every load instead of changing under whoever is reading them.
  The orders figure is the one that then moves, because the board is where an order is
  accepted: **every post of that donor's a recipient claims adds one to it.** The count
  is read from the board's own rows rather than written back to the profile, so it
  needs no second write against another account, it cannot drift from the claims that
  were really made, and it behaves the same in demo mode as with persistence on. While
  Dashboard is the section in front of the donor it is watched and re-read like the
  board's own screens are, so a claim made in another browser moves the figure there
  without a reload.
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

Both dashboards wear the new palette and type, drawn from a swatch sheet and a
serif specimen the project owner supplied: they are the first pages rolled over,
and the landing and onboarding pages still carry the earlier dark-and-technical
treatment until the same pass reaches them.

The dashboards are dark, quiet and editorial rather than technical:

- One green ramp, six steps deep: page `#051F20`, frame `#0B2B26`, raised
  `#163832`, line `#235347`, sage `#8EB69B`, mint `#DAF1DE`. The mint is the
  accent — actions, the one lit rail tab, and the one light surface a screen is
  allowed — while the sage carries every secondary label, so nothing shouts twice.
- Tailwind tokens are declared inline in each page's `tailwind.config`, and the ramp
  is also re-declared over Tailwind's own neutral scale. The pieces drawn at runtime
  — a feed card, a status chip, the goods menu — are written with `neutral-*`, so
  re-hueing the page happens in one place instead of in every string.
- Type carries the hierarchy: a high-contrast display serif for names and headings
  and Inter for the body, the labels and the data. Crake is a commercial webfont, so
  the display stack asks for it first and falls back to Bodoni Moda, a free serif of
  the same family — a licensed copy dropped in beside the pages takes over with no
  further edit. No monospace micro-labels, no uppercase headline blocks.
- Notched corners are gone. One canvas holds the page, everything inside it inherits
  that radius minus the gutter so the corners stay concentric, and a panel's only
  hover gesture is a two-pixel lift by one rung of the ramp.
- Layouts use a split panel: a branded left half and a single-column form on
  the right.

The landing and onboarding pages are still deliberately dark and technical:

- Near-black background (`#0c0c0c`), raised card surfaces (`#141414`) and a
  single safety-orange accent (`#ff4500`) used for actions and labels.
- Notched cards (`clip-path` corner cuts), oversized black-weight headings in
  uppercase, and monospace micro-labels such as `// 01. SELECT FIRM
  CLASSIFICATION` carry the operator-console feel.

## Status

A working prototype with both modes proven end to end: onboarding answers reach
the backend, a duplicate email is refused with an inline message when persistence
is on, and a returning visitor is recognised from their stored session. The
shipped default is demo mode, so out of the box nothing is saved and no credential
is needed twice; flip `RESQ_PERSIST=on` for the real thing.

What it deliberately lacks: email verification, password reset, rate limiting and
account recovery. The accounts' database is a local file, so taking this live would
mean moving to a managed database and giving the auth a real review. The matching
pipeline is real on the backend now — `/api/matches` and `/api/orders` — but no page
calls it yet, so what the marketing page shows remains illustrative, and the donor's
impact figures are still placeholders rather than a count of the deliveries that ran.

The surplus board is further along than the rest: its table is a real Postgres one in
a managed project, it is the part of the site two visitors can already see at the same
time, and both roles read the same list of published posts rather than one seeing the
board and the other seeing only their own rows. It writes with the anonymous role and trusts the name on the profile, so
anything that had to belong to a particular account would need those writes moved
behind the backend first.

The dashboards are part wired, part skeleton: the rails name their sections and switch
between them, four parts are wired end to end — the delivery card, the donor's *Log
surplus* compose box on its feed screen, and the three screens that read the surplus board — and the boxes, metric
row and queue rail around them still hold no data of their own. The map needs no key,
so it works as soon as the server is running: pick a spot, confirm it, and the address
and its coordinates are stored on the account and waiting there the next time the map
opens. The surplus board needs its migration applied once before it will take a row.

The layout of the site is still settling: the landing page keeps only teaser
content (the hero details, the capability cards and the footer) and hands off to
the onboarding page, whose role chooser modal is now unreachable from the UI, and
the onboarding page still carries its own copy of the marketing sections.
