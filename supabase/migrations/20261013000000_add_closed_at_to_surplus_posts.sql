-- Res-Q · surplus_posts.closed_at
--
-- An item used to turn into a ration the moment somebody claimed it: the pass was read
-- against whatever claims a post had at that second, and the shelters on it were told what
-- they were getting while other shelters could still be reaching for the same pallet. The
-- share a shelter read could change under it, and the notification it arrived on was about
-- a split that was not finished.
--
-- Closing is the missing step, and it belongs to the kitchen that logged the item: the
-- donor reads their own claimed items on *Ongoing Deliveries*, picks one, and closes it —
-- from that moment no shelter may claim it again (`POST /api/posts/close`) and the shares
-- become the ration each shelter is told about. This is the column that carries that
-- moment on the row both ends of the delivery are already reading.
--
-- Run it in Supabase → SQL Editor → New query → Run, the way the scripts before it were
-- run, or keep the project linked with the Supabase CLI and let `supabase db push` apply
-- it. It is written to be safe to run more than once.
--
--   psql "$DATABASE_URL" -f supabase/migrations/20261013000000_add_closed_at_to_surplus_posts.sql
--
-- The API holds the same fact on its own (`closures`, in `backend/claims.py`) because it is
-- what decides a ration, and a board that has not been given this column yet must not stop
-- the ration from being worked out. The column is the copy both dashboards read: the
-- recipient's feed stops offering the claim action on a post that carries it, and the
-- donor's screen reads it back as closed.


-- ---------------------------------------------------------------------------
-- The column
-- ---------------------------------------------------------------------------
-- Null is the ordinary case and means what it says: the post is still taking claims. A
-- moment in it is when its donor closed it, which is the moment its shares stopped being a
-- reading and became a ration.
alter table public.surplus_posts
    add column if not exists closed_at timestamptz;

-- Nothing needs an index here. The feed reads the board as one page of rows and filters it
-- as it draws, and every other read of a closure is by post id, which is the primary key
-- already — an index on a column that is null on every open row would be paid for by every
-- write and earned by nothing.


-- ---------------------------------------------------------------------------
-- A closed post takes no more claims
-- ---------------------------------------------------------------------------
-- The pages hold the publishable key, so the claim action they draw is a client's to make:
-- a page that has not reloaded since an item closed would happily insert the row. The
-- policy on `surplus_claims` cannot say "unless the post is closed" — a policy is checked
-- against the row being written, not against another table — so the guard is the trigger
-- below, which is where the same rule is stated for anything that writes to the table,
-- this project's own dashboards included. The API refuses the same claim first, in
-- `api_claim_create`, and says which of the two the shelter is reading: there, an answer;
-- here, nothing written.
create or replace function public.resq_surplus_claims_refuse_closed()
returns trigger
language plpgsql
as $$
begin
    if exists (
        select 1
        from public.surplus_posts post
        where post.id = new.post_id
          and post.closed_at is not null
    ) then
        raise exception 'this item was closed by the kitchen that logged it, so no more shelters can claim it'
            using errcode = 'check_violation';
    end if;
    return new;
end;
$$;

drop trigger if exists surplus_claims_refuse_closed on public.surplus_claims;
create trigger surplus_claims_refuse_closed
    before insert on public.surplus_claims
    for each row
    execute function public.resq_surplus_claims_refuse_closed();


-- ---------------------------------------------------------------------------
-- Who may write it
-- ---------------------------------------------------------------------------
-- No new policy is needed, and that is worth stating rather than leaving to be found. The
-- update policy from `20261008000000_create_surplus_posts.sql` is
-- `using (true) with check (status in ('pending', 'claimed'))`, and closing a post sends
-- `closed_at` and nothing else — the status it leaves behind is the one it found, so the
-- check passes. The trigger that freezes what identifies a post does not name `closed_at`
-- either: closing is not an edit of the post, it is the end of the asking, and the row it
-- happens on stays the row it was.
--
-- The API writes this column with its own key (`RESQ_SUPABASE_URL` / `RESQ_SUPABASE_KEY` on
-- the service), which is the same stance the allocation columns were added under: a
-- publishable key can write what the API writes, exactly as it can already claim a post.


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------
select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'surplus_posts'
  and column_name = 'closed_at';

select 'surplus_posts.closed_at is ready' as result;
