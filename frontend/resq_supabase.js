/* ---------------------------------------------------------------------------
 * Res-Q · Supabase client and the `surplus_posts` calls both dashboards share.
 *
 * A donor logs a surplus item and a recipient claims it, and the row that
 * carries that between them lives in Supabase rather than in the local
 * backend: `supabase_posts` is the one table both dashboards talk to directly,
 * which is also what lets a post reach every open dashboard the moment it is
 * written.
 *
 * The key below is the project's *publishable* key. It is the key that is meant
 * to sit in a page: it can only ever act as the anonymous role, so what it may
 * do is decided by the table's row level security policies, not by keeping it
 * secret. The secret key is never used here.
 *
 * The library itself is fetched from a CDN the first time a dashboard actually
 * needs the database, the way Leaflet is fetched when the map overlay opens, so
 * a signed-in visitor who never opens the feed or the form loads none of it.
 * ------------------------------------------------------------------------- */
(function (global) {
    'use strict';

    var SUPABASE_URL = 'https://paeshgdpqbjoagpbybhl.supabase.co';
    var SUPABASE_KEY = 'sb_publishable_pRaPy6MH_oBWtx6Ei2Uatw_VM9Z2y8w';
    // Pinned the way Leaflet is: the bundle is the UMD build, whose global is
    // `supabase` and whose entry point is `createClient`.
    var SUPABASE_JS = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.117.3/dist/umd/supabase.js';

    var TABLE = 'surplus_posts';
    // How often an open feed re-reads the table behind the live subscription, so
    // a project that has not put the table in the realtime publication yet still
    // shows new posts rather than nothing.
    var POLL_MS = 20000;
    var POST_LIMIT = 60;

    var client = null;
    var libraryLoading = null;

    // -------------------------------------------------------------------------
    // The client
    // -------------------------------------------------------------------------
    function loadLibrary() {
        if (global.supabase && global.supabase.createClient) {
            return Promise.resolve();
        }
        if (libraryLoading) { return libraryLoading; }
        libraryLoading = new Promise(function (resolve, reject) {
            var tag = document.createElement('script');
            tag.src = SUPABASE_JS;
            tag.async = true;
            tag.onload = function () {
                if (global.supabase && global.supabase.createClient) {
                    resolve();
                } else {
                    reject(new Error('the Supabase library loaded without its client'));
                }
            };
            tag.onerror = function () {
                reject(new Error('the Supabase library could not be loaded'));
            };
            document.head.appendChild(tag);
        });
        return libraryLoading;
    }

    // Sign-in belongs to Res-Q's own backend, so the client is told to leave
    // sessions alone entirely: no token in storage, no URL it might act on.
    function ready() {
        if (client) { return Promise.resolve(client); }
        return loadLibrary().then(function () {
            if (!client) {
                client = global.supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
                    auth: {
                        persistSession: false,
                        autoRefreshToken: false,
                        detectSessionInUrl: false
                    }
                });
            }
            return client;
        });
    }

    // -------------------------------------------------------------------------
    // Errors, said in the page's own terms
    // -------------------------------------------------------------------------
    // The one failure worth naming precisely is the table not being there yet:
    // it is the difference between "Supabase is unreachable" and "the migration
    // has not been run", and the dashboard can point at the second one.
    function describe(error) {
        var code = (error && (error.code || error.status)) || '';
        var message = (error && (error.message || error.error_description || error.hint || '')) || '';
        if (String(code) === 'PGRST205' || String(code) === '42P01'
            || /could not find the table|schema cache/i.test(message)) {
            return {
                missingTable: true,
                message: 'the surplus_posts table is not in the project yet'
            };
        }
        if (!message && error && error.name) { message = error.name; }
        return {
            missingTable: false,
            message: String(message || 'the request to Supabase did not go through')
        };
    }

    // A query builder is a promise-like: awaiting it answers { data, error }
    // rather than rejecting, so both sides are folded into one shape here and
    // no caller has to know which of the two happened.
    function reply(builder) {
        return Promise.resolve(builder).then(function (res) {
            if (res && res.error) { return { ok: false, error: describe(res.error) }; }
            return { ok: true, data: (res && res.data) || [] };
        }, function (err) {
            return { ok: false, error: describe(err) };
        });
    }

    function withClient(work) {
        return ready().then(function (db) {
            return work(db);
        }, function (err) {
            return { ok: false, error: describe(err) };
        });
    }

    // -------------------------------------------------------------------------
    // Reads and writes
    // -------------------------------------------------------------------------
    // The board, newest first. `status` narrows it to the open posts, and
    // `donorName` to the ones one donor logged.
    function listPosts(options) {
        var opts = options || {};
        return withClient(function (db) {
            var query = db.from(TABLE)
                .select('*')
                .order('created_at', { ascending: false })
                .limit(opts.limit || POST_LIMIT);
            if (opts.status) { query = query.eq('status', opts.status); }
            if (opts.donorName) { query = query.eq('donor_name', opts.donorName); }
            return reply(query);
        });
    }

    // A new post is written as `pending` and never as anything else: the default
    // lives in the database, and only a claim moves it on from there.
    function createPost(post) {
        return withClient(function (db) {
            return reply(db.from(TABLE).insert({
                donor_name: post.donorName,
                item_name: post.itemName,
                quantity: post.quantity,
                location: post.location
            }).select().single());
        });
    }

    // Claiming is the same update for everyone: the row is matched on being
    // still pending, so the second recipient to click loses the race cleanly
    // (no row comes back) instead of overwriting the first claim.
    function claimPost(id) {
        return withClient(function (db) {
            return reply(db.from(TABLE)
                .update({ status: 'claimed' })
                .eq('id', id)
                .eq('status', 'pending')
                .select()
                .maybeSingle());
        });
    }

    // -------------------------------------------------------------------------
    // Live posts
    // -------------------------------------------------------------------------
    // Every insert and every claim is pushed to the callback while the returned
    // function has not been called. A project whose table is not in the realtime
    // publication simply never calls it — the caller's own timer covers that, so
    // a subscription that cannot happen is not an error the page has to show.
    function watchPosts(onChange) {
        var channel = null;
        var stopped = false;
        ready().then(function (db) {
            if (stopped) { return; }
            channel = db.channel('resq-surplus-posts')
                .on('postgres_changes', {
                    event: '*',
                    schema: 'public',
                    table: TABLE
                }, function (payload) {
                    if (!stopped) { onChange(payload); }
                })
                .subscribe();
        }).catch(function () { /* the caller's poll is the fallback */ });

        return function () {
            stopped = true;
            if (channel) { channel.unsubscribe(); }
        };
    }

    // -------------------------------------------------------------------------
    // Rendering the rows, in the site's tone
    // -------------------------------------------------------------------------
    // Everything below is markup, so anything a donor typed goes through
    // escapeHtml first.
    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function statusChip(status) {
        return status === 'claimed'
            ? 'inline-flex items-center h-6 px-3 rounded-full border border-emerald-900/50 bg-emerald-950/60 font-mono text-[10px] uppercase tracking-widest text-emerald-400'
            : 'inline-flex items-center h-6 px-3 rounded-full border border-orange-900/50 bg-orange-950/60 font-mono text-[10px] uppercase tracking-widest text-[#ff9b73]';
    }

    function statusLabel(status) {
        return status === 'claimed' ? 'claimed' : 'pending';
    }

    // Posts are read the way the rail reads: a short age while the post is
    // fresh, and the date itself once "3 d ago" has stopped being useful.
    function formatWhen(iso) {
        var when = new Date(iso);
        if (isNaN(when.getTime())) { return ''; }
        var seconds = Math.floor((Date.now() - when.getTime()) / 1000);
        if (seconds < 60) { return 'just now'; }
        var minutes = Math.floor(seconds / 60);
        if (minutes < 60) { return minutes + ' min ago'; }
        var hours = Math.floor(minutes / 60);
        if (hours < 24) { return hours + ' h ago'; }
        var days = Math.floor(hours / 24);
        if (days < 7) { return days + ' d ago'; }
        return when.toISOString().slice(0, 10);
    }

    // The two lines every card carries: what the item is and how much of it,
    // then who is giving it away and where from.
    function postNeeds(post) {
        return post.quantity ? String(post.quantity) : 'quantity not given';
    }

    // One card per post, and the same card on both dashboards: the board is one
    // board, so a donor reading it and a recipient reading it see the same item
    // described the same way. What differs is the action. `canClaim` adds the one
    // action a recipient has on an open post — claiming it — and a donor's copy of
    // the board leaves it off, because taking surplus is not the donor's side of the
    // exchange. `ownName` marks the caller's own posts, so a donor scanning for what
    // they logged finds it without reading every name.
    function feedCard(row, options) {
        var opts = options || {};
        var claimed = row.status === 'claimed';
        var mine = !!opts.ownName
            && String(row.donor_name || '').trim() === String(opts.ownName).trim();
        return '<div class="p-4 rounded-2xl border transition-all ' +
                (claimed ? 'bg-black/20 border-neutral-800'
                    : 'bg-neutral-800/50 border-neutral-800 hover:border-neutral-700') + '">' +
            '<div class="flex flex-wrap items-start justify-between gap-3">' +
                '<div class="min-w-0">' +
                    '<span class="block text-sm font-black uppercase tracking-widest ' +
                        (claimed ? 'text-neutral-500' : 'text-[#f4f4f0]') + '">' +
                        escapeHtml(row.item_name) + '</span>' +
                    '<span class="block mt-1 font-mono text-[11px] text-neutral-400">' +
                        escapeHtml(postNeeds(row)) + '</span>' +
                '</div>' +
                '<span class="flex flex-wrap items-center gap-2 shrink-0">' +
                    (mine ? '<span class="inline-flex items-center h-6 px-3 rounded-full border ' +
                        'border-neutral-800 bg-black/20 font-mono text-[10px] uppercase ' +
                        'tracking-widest text-neutral-400">your post</span>' : '') +
                    '<span class="' + statusChip(row.status) + '">' +
                        statusLabel(row.status) + '</span>' +
                '</span>' +
            '</div>' +
            '<div class="mt-3 flex flex-wrap items-end justify-between gap-3">' +
                '<div class="font-mono text-[10px] text-neutral-600">' +
                    '<span class="block">FROM ' + escapeHtml(row.donor_name) + '</span>' +
                    '<span class="block mt-1">AT ' + escapeHtml(row.location) + '</span>' +
                    '<span class="block mt-1">LOGGED ' + escapeHtml(formatWhen(row.created_at)) +
                    '</span>' +
                '</div>' +
                (opts.canClaim && !claimed
                    ? '<button type="button" data-claim="' + escapeHtml(row.id) + '" ' +
                        'onclick="claimSurplus(this)" class="px-5 py-3 rounded-xl bg-[#ff4500] ' +
                        'text-black text-xs font-black uppercase tracking-widest ' +
                        'hover:bg-[#ff5a1f] transition-all disabled:opacity-40 ' +
                        'disabled:cursor-not-allowed">Claim</button>'
                    : '') +
            '</div>' +
        '</div>';
    }

    // The count that sits over the board: how much is still going, out of
    // everything published to it.
    function feedSummary(rows) {
        var open = rows.filter(function (row) { return row.status !== 'claimed'; }).length;
        return open + ' open / ' + rows.length + ' posted';
    }

    global.ResQDB = {
        url: SUPABASE_URL,
        table: TABLE,
        pollMs: POLL_MS,
        ready: ready,
        describe: describe,
        listPosts: listPosts,
        createPost: createPost,
        claimPost: claimPost,
        watchPosts: watchPosts,
        escapeHtml: escapeHtml,
        statusChip: statusChip,
        statusLabel: statusLabel,
        formatWhen: formatWhen,
        postNeeds: postNeeds,
        feedCard: feedCard,
        feedSummary: feedSummary
    };
})(window);
