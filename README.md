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
   while the boxes, the metric row and the queue rail around them still hold no data
   of their own. Each page owns its own rail, markup and slot included, so
   relabelling one role's sections cannot move the other's: both open with the same
   two rows, Dashboard and Feed, and then name their own work. The donor's runs
   Surplus Analyser, Ongoing Deliveries, Finished Deliveries — the last in the slot
   below the rail's divider — and the recipient's runs Incoming Deliveries, Delivery
   Status, Received Deliveries, which takes that same slot position. The slot under
   whichever name is there stays a bare pipeline pulse.
   Dashboard is the default selection; Feed opens the shared
   board on either dashboard — with the donor's compose box above it, which is where
   a donor logs an item now — Surplus Analyser opens the donor's own logged surplus,
   and the remaining items — the recipient's two section rows, the donor's Ongoing
   and Finished Deliveries, or the recipient's Received Deliveries slot — still wipe
   the working area for a blank screen of their own. Picking Dashboard brings the
   dashboard back.
   The right-hand panel of the donor's lower grid carries the impact figures under
   a *Donor Metrics* heading — surplus saved in kilos, meals served and orders
   completed — read from that donor's own stored figures, so the same numbers are
   there on every load. The selected item fills with the site's accent orange and its label
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
| `backend/server.py` | Backend: serves the pages out of `frontend/` and the onboarding API (demo mode by default, SQLite when persistence is on) |
| `frontend/res_q_homepage.html` | Onboarding — the role gate and the About You form, plus a copy of the marketing sections |
| `frontend/res_q_surplus_profile.html` | The goods profile step both roles land on after onboarding |
| `frontend/res_q_dashboard_donor.html` | Donor dashboard — post-profile landing page: a labelled navigation rail, an impact panel showing the donor's own stored figures, a *Feed* section that opens on the *Log surplus* compose box (item, quantity, the goods-category pill and the pickup address) and runs into the whole board read back, its three views being the whole board, a donor's own posts and the posts tagged with the goods they handle; the *Surplus Analyser* section reading only their own rows, plus the delivery-location card |
| `frontend/res_q_dashboard_recipient.html` | Recipient dashboard — the same page for recipients, carrying the same rail skeleton under its own section names (*Incoming Deliveries*, *Delivery Status*); its *Feed* section is that same live board in three views (all posts, open, and the posts tagged with the goods this recipient needs), with a claim button on every open post |
| `frontend/resq_supabase.js` | The shared Supabase client and the `surplus_posts` calls both dashboards use: it lazy-loads the library on first use, reads, inserts and claims rows, streams changes, holds the six goods categories one time, and draws every post as the one shared card both roles see |
| `supabase/migrations/20261008000000_create_surplus_posts.sql` | The `surplus_posts` table with its row level security policies, its claim-only update guard and its realtime publication entry — run once against the project |
| `supabase/migrations/20261009000000_add_goods_type_to_surplus_posts.sql` | The `goods_type` tag column, its constraint to the six categories and the claim-guard update that freezes it — run once against the project, after the file above |

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

The map is [Leaflet](https://leafletjs.com) drawing **OpenStreetMap's own tiles**, and
both lookups — the address search and the reverse lookup behind a moved pin — are
OpenStreetMap's **Nominatim** service. All three are free and keyless, so the overlay
needs no account, no API key and no card. Leaflet is fetched from a CDN the first time
the overlay opens, so the dashboards themselves stay light.

Both OSM services are shared public infrastructure, so the overlay stays a good
citizen: lookups run once per search and once per settled pin movement rather than on
every keystroke (Nominatim's policy forbids type-ahead against their public instance,
and the reverse lookup is debounced behind the drag and asked for no more than two
attempts at one spot), the map keeps the attribution
Leaflet draws for the tiles, and heavy or automated use would need a self-hosted tile
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
  is a notched card on the site's own palette: the logging donor's avatar, then their
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

## Backend and API

The backend listens on `127.0.0.1:8080`. `GET /api/health` reports which mode it
is in.

### Demo mode (default)

Nothing is written to a database and no detail is ever rejected as a duplicate,
so the whole site can be walked through repeatedly without inventing a fresh
email address each time. Sessions and the details you type live in memory for
the life of the process; any email or phone plus any password signs you in, and
restarting the server forgets everything. A session keeps the whole onboarding
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
