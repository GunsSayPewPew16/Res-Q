-- Res-Q · demo claims
--
-- The trial that shows the claim indicator on the board: one post that looks like a
-- real bake end, and three recipient profiles who all claim it. Read on the donor's
-- board, the post's own card says it in a line —
--
--     Daniela Ruiz and 2 more claimed
--
-- which is the line the feed draws from surplus_claims (see
-- supabase/migrations/20261012000000_create_surplus_claims.sql). Run that migration
-- first: this script writes into the table it creates.
--
-- Run it in Supabase → SQL Editor → New query → Run. It is written to be safe to run
-- more than once: the post is only written when the board does not already have it,
-- and the three claims are refused by the table's own unique index if they are there.
--
--   psql "$DATABASE_URL" -f supabase/seed_demo_claims.sql
--
-- To take the trial back off the board again — the board has no delete grant for the
-- pages, so this is a SQL-editor job, the way supabase/clear_surplus_posts.sql is —
-- delete the claims first, then the post:
--
--   delete from public.surplus_claims
--    where post_id in (select id from public.surplus_posts
--                      where donor_name = 'Foxglove Bakery & Café'
--                        and item_name  = 'Morning bake: sourdough loaves & pastries');
--   delete from public.surplus_posts
--    where donor_name = 'Foxglove Bakery & Café'
--      and item_name  = 'Morning bake: sourdough loaves & pastries';


-- ---------------------------------------------------------------------------
-- The post
-- ---------------------------------------------------------------------------
-- Logged by a donor, already `claimed` because three recipients have taken it up —
-- the state the first of those claims would have left it in anyway.
insert into public.surplus_posts (donor_name, item_name, quantity, location, status, goods_type)
select 'Foxglove Bakery & Café',
       'Morning bake: sourdough loaves & pastries',
       '18 loaves, 3 trays of pastries',
       '218 Westminster Street, Providence, RI 02903',
       'claimed',
       'bakery_items'
where not exists (
    select 1 from public.surplus_posts
    where donor_name = 'Foxglove Bakery & Café'
      and item_name  = 'Morning bake: sourdough loaves & pastries'
);


-- The state the first of those claims leaves the post in, written rather than leaned
-- on: the trial reads the same whether the post was written by this script or logged by
-- hand from a dashboard first.
update public.surplus_posts
   set status = 'claimed'
 where donor_name = 'Foxglove Bakery & Café'
   and item_name  = 'Morning bake: sourdough loaves & pastries';


-- ---------------------------------------------------------------------------
-- The three claims
-- ---------------------------------------------------------------------------
-- Each one is a recipient profile, the side of the exchange that claims: a contact
-- and the organisation they claim for. They are spread over half an hour so the line
-- reads the way it would if the claims had arrived one after another — the earliest
-- claimant is the one named, the rest are the count.
insert into public.surplus_claims (post_id, recipient_name, recipient_org, claimed_at)
select post.id,
       claim.name,
       claim.org,
       now() - (claim.minutes_ago || ' minutes')::interval
from public.surplus_posts as post
cross join (
    values ('Daniela Ruiz', 'Southside Community Kitchen', 34),
           ('Marcus Bell',  'Harbor Light Family Shelter', 21),
           ('Priya Nair',   'Riverside Mutual Aid',         9)
) as claim (name, org, minutes_ago)
where post.donor_name = 'Foxglove Bakery & Café'
  and post.item_name  = 'Morning bake: sourdough loaves & pastries'
on conflict do nothing;


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------
-- The editor prints what the board is now holding for the trial.
select post.item_name,
       count(claim.id) as claimants,
       min(claim.recipient_name) filter (where claim.claimed_at = (
           select min(inner_claim.claimed_at)
           from public.surplus_claims as inner_claim
           where inner_claim.post_id = post.id
       )) as first_claimant
from public.surplus_posts as post
left join public.surplus_claims as claim on claim.post_id = post.id
where post.donor_name = 'Foxglove Bakery & Café'
  and post.item_name  = 'Morning bake: sourdough loaves & pastries'
group by post.item_name;
