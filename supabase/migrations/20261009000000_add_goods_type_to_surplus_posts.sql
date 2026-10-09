-- Res-Q · surplus_posts.goods_type
--
-- A post now says which of the six goods categories it is for, so a feed can be
-- read two ways: the whole board, or the board narrowed to the goods this
-- account picked in its goods profile — what it handles as a donor, what it
-- needs as a recipient. That narrowing is the *For You* tab, and the tag is the
-- column it reads.
--
-- Run it in Supabase → SQL Editor → New query → Run, the way the create script
-- was run, or keep the project linked with the Supabase CLI and let
-- `supabase db push` apply it. It is written to be safe to run more than once.
--
--   psql "$DATABASE_URL" -f supabase/migrations/20261009000000_add_goods_type_to_surplus_posts.sql


-- ---------------------------------------------------------------------------
-- The tag
-- ---------------------------------------------------------------------------
-- Nullable on purpose: rows written before the tag existed keep a null one and
-- still read correctly (an untagged post shows on Everyone/All posts and never
-- on For You), while every post the compose box writes from now on is tagged.
alter table public.surplus_posts
    add column if not exists goods_type text;

-- The same six categories the goods profile offers and the backend validates
-- against, so a post cannot be filed under a category nothing else knows.
alter table public.surplus_posts
    drop constraint if exists surplus_posts_goods_type_check;
alter table public.surplus_posts
    add constraint surplus_posts_goods_type_check
    check (
        goods_type is null
        or goods_type in (
            'prepared_meals',
            'fresh_produce',
            'bakery_items',
            'packaged_goods',
            'dairy_beverages',
            'household_essentials'
        )
    );

-- For You reads by category, so the tag is worth an index of its own.
create index if not exists surplus_posts_goods_type_idx
    on public.surplus_posts (goods_type);


-- ---------------------------------------------------------------------------
-- A claim still may only change the status
-- ---------------------------------------------------------------------------
-- The trigger from the create script freezes everything that identifies a post
-- so a claim cannot rewrite it. The tag joins that list: without it a claimed
-- post could be re-tagged after the fact, which would move it in and out of
-- somebody else's For You feed. The function is replaced rather than recreated,
-- because the trigger already points at it.
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
        or new.goods_type is distinct from old.goods_type
        or new.created_at is distinct from old.created_at then
        raise exception 'only the status of a surplus post can change';
    end if;
    return new;
end;
$$;


-- ---------------------------------------------------------------------------
-- Done
-- ---------------------------------------------------------------------------
select 'surplus_posts.goods_type is ready' as result;
