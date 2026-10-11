-- Res-Q · surplus_posts.allocation
--
-- The board's rows now carry the rationing pass's answer as well as the post itself: how
-- much of an item the shelter that claimed it will actually receive, what share of the post
-- that is, the weighted score the share came out of, who it is for, and when it was
-- published. Before this the share existed only inside the console that asked for it; this
-- is what puts it on the row the donor logged and the shelter claimed — the row both ends of
-- the delivery are already looking at.
--
-- Run it in Supabase → SQL Editor → New query → Run, the way the two scripts before it were
-- run, or keep the project linked with the Supabase CLI and let `supabase db push` apply it.
-- It is written to be safe to run more than once.
--
--   psql "$DATABASE_URL" -f supabase/migrations/20261011000000_add_allocation_to_surplus_posts.sql


-- ---------------------------------------------------------------------------
-- The columns
-- ---------------------------------------------------------------------------
-- All five are nullable, and that is the point: a post nobody has rationed yet has no
-- allocation, and null says so where a zero would read as "this shelter gets nothing".
alter table public.surplus_posts
    add column if not exists allocated_kg       numeric,
    add column if not exists allocation_percent numeric,
    add column if not exists allocation_score   numeric,
    add column if not exists allocated_to       text,
    add column if not exists allocated_at       timestamptz;

-- A share that is negative, over the whole post, or a weight below zero is not an answer
-- anyone should be able to store, whatever wrote it.
alter table public.surplus_posts
    drop constraint if exists surplus_posts_allocation_check;
alter table public.surplus_posts
    add constraint surplus_posts_allocation_check
    check (
        (allocated_kg is null or allocated_kg >= 0)
        and (allocation_percent is null
             or (allocation_percent >= 0 and allocation_percent <= 100))
        and (allocation_score is null or allocation_score >= 0)
    );


-- ---------------------------------------------------------------------------
-- Who may write it
-- ---------------------------------------------------------------------------
-- No new policy is needed here, and that is worth stating rather than leaving to be
-- discovered. The update policy from `20261008000000_create_surplus_posts.sql` already
-- allows this write: it is `using (true) with check (status in ('pending', 'claimed'))`, and
-- publishing an allocation sends the five columns above and nothing else — the status it
-- leaves behind is the one it found, so the check passes. A narrower policy cannot be
-- written instead, because row level security is checked per row rather than per column; the
-- column-level guard this table has is the trigger below, and it is deliberately not
-- extended.
--
-- The trigger freezes what identifies a post — its donor, item, quantity, pickup location,
-- its goods tag and its timestamp — and an allocation identifies nothing: it is the answer
-- this project computed *about* the post, so extending
-- `resq_surplus_posts_freeze_identity()` to cover the new columns would make publishing fail
-- on every row. Nothing to do here; the note is for whoever reads that function next.
--
-- The key matters as much as the policy. The pages hold the publishable key, and the write
-- back needs a key of its own on the API — `RESQ_SUPABASE_URL` and `RESQ_SUPABASE_KEY`, set
-- on the service's Environment page (see the README's deployment section). A publishable key
-- goes through the policy above; a secret (service_role) key bypasses row level security
-- entirely and is the one to use if these columns should later be writable by the API alone.
-- As it stands, any client holding the publishable key can write these five columns, exactly
-- as it can already claim a post — that is the stance the board has had from the beginning,
-- and moving a write behind the API is what narrows it.


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------
select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'surplus_posts'
  and column_name like 'allocat%'
order by column_name;

select 'surplus_posts.allocation is ready' as result;
