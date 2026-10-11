-- Res-Q · surplus_posts · the allocator's pick
--
-- A logged item is answered by the backend allocator with the shelter that should take
-- it, and that answer is now written back onto the post the feed just took — so the
-- recipient board can say who is coming for the goods instead of leaving the pick in
-- the donor's browser, where only the donor ever sees it.
--
-- Run it in Supabase → SQL Editor → New query → Run, or keep the project linked with
-- the Supabase CLI and let `supabase db push` apply it. It is written to be safe to run
-- more than once, and it is the only migration this feature needs on top of
-- 20261008000000_create_surplus_posts.sql, which has to have been run first.
--
--   psql "$DATABASE_URL" -f supabase/migrations/20261009000000_add_match_to_surplus_posts.sql


-- ---------------------------------------------------------------------------
-- Columns
-- ---------------------------------------------------------------------------
-- The three answers the route already returns: which shelter was picked, the score it
-- was picked on, and the road distance that score was measured over. All three are
-- nullable on purpose: a post written before this migration, or written while the
-- backend cannot be reached, simply carries no pick, and the board shows exactly the
-- row it always did. Nothing is backfilled — there is no second source to fill it from,
-- and an invented pick would read as a real one.
alter table public.surplus_posts
    add column if not exists matched_shelter text,
    add column if not exists match_score numeric,
    add column if not exists match_distance_km numeric;


-- ---------------------------------------------------------------------------
-- The freeze trigger, widened by exactly those columns
-- ---------------------------------------------------------------------------
-- The first migration froze the identity of a post: the donor, the item, the quantity,
-- the pickup point, the id and the timestamp cannot be rewritten after the fact. The
-- pick is not identity — it is the allocator's answer about the post, written once by
-- the page that submitted it — so it may be written, and only while the post is still
-- open. Once somebody has claimed the row, the pick is part of what they claimed and
-- stops changing. Every other column stays as frozen as it was.
create or replace function public.resq_surplus_posts_freeze_identity()
returns trigger
language plpgsql
as $$
begin
    if new.id is distinct from old.id
        or new.donor_name is distinct from old.donor_name
        or new.item_name is distinct from old.item_name
        or new.quantity is distinct from old.quantity
        or new.location is distinct from old.location
        or new.created_at is distinct from old.created_at then
        raise exception 'only the status of a surplus post can change';
    end if;
    if old.status = 'claimed'
        and (new.matched_shelter is distinct from old.matched_shelter
             or new.match_score is distinct from old.match_score
             or new.match_distance_km is distinct from old.match_distance_km) then
        raise exception 'a claimed surplus post keeps the match it was claimed with';
    end if;
    return new;
end;
$$;

drop trigger if exists surplus_posts_freeze_identity on public.surplus_posts;
create trigger surplus_posts_freeze_identity
    before update on public.surplus_posts
    for each row
    execute function public.resq_surplus_posts_freeze_identity();


-- ---------------------------------------------------------------------------
-- Who may write the pick
-- ---------------------------------------------------------------------------
-- No policy changes are needed here. The update policy's check passes for a row that
-- stays `pending` or `claimed`, whichever columns the statement names, and the grant on
-- the table was made table-wide in the first migration, so it already covers columns
-- added later. The publishable key in the pages can therefore set a pick on an open
-- post — the same trust level the claim already has, in a prototype whose board is
-- deliberately writable by the anonymous role.


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------
-- The editor prints this one row when the whole script went through.
select 'surplus_posts carries the allocator''s pick' as result;
