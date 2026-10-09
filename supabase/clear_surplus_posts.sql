-- Res-Q · emptying the board
--
-- Deletes every row in public.surplus_posts, so a demo can start from an empty
-- board after a day of testing. It is not a migration and nothing applies it
-- automatically — it is run by hand, on purpose.
--
-- Run it in Supabase → SQL Editor → New query → Run:
--
--   delete from public.surplus_posts;
--
-- The dashboards can read the board, add a post and claim one, and that is all:
-- the table's policies give the anonymous role no delete at all, which is why a
-- page (or a script holding only the publishable key) cannot clear the board
-- itself, and why this has to be run as the project owner.
--
-- Every open dashboard empties on its own within the poll interval, and at once
-- if it is subscribed to Realtime: a delete is as live as an insert.
--
-- Nothing here depends on the goods-category migration, so the two can be run in
-- either order, or this one alone.

delete from public.surplus_posts;

-- The editor prints the count that went, which is also a check that the delete
-- matched the whole board rather than a filter that caught nothing.
select count(*) as posts_left from public.surplus_posts;
