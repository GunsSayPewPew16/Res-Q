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

    // The six goods categories, in the order the goods profile lists them. They live
    // here once: a post's tag, the compose box's picker and the feed's *For You*
    // filter all read this list, so a category cannot end up spelled two ways. The
    // labels, the emoji and the hint lines are the profile's own copy.
    //
    // The emoji is the category's icon wherever the category is shown: the row in the
    // picker, the chip on a tagged post, and the compose pill once something is picked.
    var GOODS_TYPES = [
        { value: 'prepared_meals', label: 'Prepared Meals', emoji: '\ud83c\udf72',
          hint: 'Cooked dishes, catered trays, hot meals' },
        { value: 'fresh_produce', label: 'Fresh Produce', emoji: '\ud83e\udd66',
          hint: 'Fruits, vegetables, leafy greens' },
        { value: 'bakery_items', label: 'Bakery Items', emoji: '\ud83e\udd50',
          hint: 'Breads, pastries, cakes, daily bake' },
        { value: 'packaged_goods', label: 'Packaged Goods', emoji: '\ud83d\udce6',
          hint: 'Canned food, dry goods, snacks' },
        { value: 'dairy_beverages', label: 'Dairy & Beverages', emoji: '\ud83e\udd5b',
          hint: 'Milk, yogurt, juices, bottled drinks' },
        { value: 'household_essentials', label: 'Household & Essentials', emoji: '\ud83e\uddf4',
          hint: 'Hygiene kits, soap, blankets, paper goods' }
    ];

    // The column the tag is written to, added by
    // supabase/migrations/20261009000000_add_goods_type_to_surplus_posts.sql.
    var GOODS_COLUMN = 'goods_type';
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
        // The column is asked about first, and deliberately: a missing column is
        // reported by PostgREST as PGRST204 — "Could not find the 'goods_type' column of
        // 'surplus_posts' in the schema cache" — and the words "schema cache" in that
        // message would be read as a missing table by the check below, which is a
        // different answer to a different problem.
        if (String(code) === '42703' || String(code) === 'PGRST204'
            || /column .*does not exist|column .*not found|could not find the .* column/i.test(message)) {
            return {
                missingTable: false,
                missingColumn: true,
                message: String(message || 'that column is not in this project yet')
            };
        }
        if (String(code) === 'PGRST205' || String(code) === '42P01'
            || /could not find the table/i.test(message)) {
            return {
                missingTable: true,
                missingColumn: false,
                message: 'the surplus_posts table is not in the project yet'
            };
        }
        if (!message && error && error.name) { message = error.name; }
        return {
            missingTable: false,
            missingColumn: false,
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
    //
    // The goods category is written with it. A project that has not run the tag
    // migration yet would refuse the whole row over that one unknown column, which
    // would take posting down to fix a tab — so an insert refused *only* for that
    // reason is retried without the tag, and comes back marked `untagged` so the
    // compose box can say the item is on the board but carries no category yet.
    function insertRow(db, row) {
        return reply(db.from(TABLE).insert(row).select().single()).then(function (res) {
            if (res.ok || !row[GOODS_COLUMN]) { return res; }
            if (!res.error || !res.error.missingColumn) { return res; }
            var untagged = {};
            Object.keys(row).forEach(function (key) {
                if (key !== GOODS_COLUMN) { untagged[key] = row[key]; }
            });
            return reply(db.from(TABLE).insert(untagged).select().single()).then(function (retry) {
                if (retry.ok) { retry.untagged = true; }
                return retry;
            });
        });
    }

    function createPost(post) {
        var row = {
            donor_name: post.donorName,
            item_name: post.itemName,
            quantity: post.quantity,
            location: post.location
        };
        if (post.goodsType) { row[GOODS_COLUMN] = post.goodsType; }
        return withClient(function (db) {
            return insertRow(db, row);
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
    // Claim records: who claimed which post, for the rationing pass.
    //
    // The board itself is one shared table — first claim wins and the post stops
    // being claimable — so a claim that only raced one claimant is already on
    // the board. What the board does NOT keep is *who* claimed a post: the
    // recipient's details are written to the claim recorder, a small companion
    // service beside the site's own backend, which keeps one record per claim
    // in backend/claim_records.json, each carrying the post id it claimed.
    // Multiple recipients reaching the same post across the post's life each
    // get their own record, all tied to that one post id — which is exactly
    // what the rationing pass joins on.
    // -------------------------------------------------------------------------
    var RECORDER_URL = 'http://127.0.0.1:8081/claims';

    function recordClaim(post, recipient) {
        if (!post || !post.id) {
            return Promise.resolve({ ok: false, error: { message: 'no post to record' } });
        }
        return fetch(RECORDER_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ post: post, recipient: recipient || null })
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                return { ok: !!data.ok, status: response.status, data: data };
            });
        }, function (err) {
            return { ok: false, error: { message: String(err && err.message || err) } };
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
            ? 'inline-flex items-center h-6 px-3 rounded-full border border-[#235347] bg-[#163832] text-[10px] uppercase tracking-[0.18em] text-[#8EB69B]'
            : 'inline-flex items-center h-6 px-3 rounded-full border border-[#235347] bg-[#051F20] text-[10px] uppercase tracking-[0.18em] text-[#DAF1DE]';
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

    // A feed is read at a glance, so the board's own age reads as a glyph-height
    // string — "9h" — the way the reference feed writes it. formatWhen still
    // serves the tables, where there is room to spell the age out.
    function feedWhen(iso) {
        var when = new Date(iso);
        if (isNaN(when.getTime())) { return 'now'; }
        var seconds = Math.floor((Date.now() - when.getTime()) / 1000);
        if (seconds < 60) { return 'now'; }
        var minutes = Math.floor(seconds / 60);
        if (minutes < 60) { return minutes + 'm'; }
        var hours = Math.floor(minutes / 60);
        if (hours < 24) { return hours + 'h'; }
        var days = Math.floor(hours / 24);
        if (days < 7) { return days + 'd'; }
        return when.toISOString().slice(0, 10);
    }

    // A post is signed with a donor's name, and the feed reads the signature the
    // way a handle is read: one machine-shaped word. Nothing is invented here —
    // the handle is the name folded down to lowercase and dashes.
    function feedHandle(name) {
        var slug = String(name || '').trim().toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return slug ? '@' + slug : '@donor';
    }

    // The category a post is filed under, looked up by its value. Anything the
    // project holds that is not one of the six — a null on an older row, say —
    // reads as no category rather than as a made-up one.
    function goodsType(value) {
        var wanted = String(value === null || value === undefined ? '' : value);
        for (var i = 0; i < GOODS_TYPES.length; i += 1) {
            if (GOODS_TYPES[i].value === wanted) { return GOODS_TYPES[i]; }
        }
        return null;
    }

    // Whether a post belongs to the person reading the board. The names are
    // compared trimmed, because a trailing space is not a different donor.
    function isOwnPost(post, name) {
        return !!name
            && String(post.donor_name || '').trim() === String(name).trim();
    }

    // The avatar the feed draws in place of a photograph: the same silhouette for
    // everybody, on a dark tint picked from the name so that two donors with two
    // posts do not read as one. None of this is anybody's text, so it is not
    // escaped — the name only picks a tint out of the list.
    var AVATAR_TINTS = ['#163832', '#1B473C', '#0F332C', '#235347', '#123A31'];
    function avatarMarkup(name) {
        var text = String(name || '');
        var seed = 0;
        for (var i = 0; i < text.length; i += 1) { seed += text.charCodeAt(i); }
        return '<span class="feed-avatar" style="background-color:' +
                AVATAR_TINTS[seed % AVATAR_TINTS.length] + '">' +
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
                '<circle cx="12" cy="8.2" r="4.2" fill="#8EB69B"></circle>' +
                '<path d="M3.2 24a8.8 8.8 0 0 1 17.6 0z" fill="#8EB69B"></path>' +
            '</svg>' +
        '</span>';
    }


    // One card per post, and the same card on both dashboards: the board is one
    // board, so a donor reading it and a recipient reading it see the same item
    // described the same way. What differs is the action. `canClaim` adds the one
    // action a recipient has on an open post — claiming it — and a donor's copy of
    // the board leaves it off, because taking surplus is not the donor's side of the
    // exchange. `ownName` marks the caller's own posts, so a donor scanning for what
    // they logged finds it without reading every name.
    // The card is laid out the way the reference feed lays a post out, in the
    // site's own palette: the notched frame, an avatar and a signed name over a
    // handle and an age, the item as the loud line, a second line with the pickup
    // point picked out in the accent the way the reference picks out a tag, and a
    // muted row along the bottom for the post's own facts and its one action.
    function feedCard(row, options) {
        var opts = options || {};
        var claimed = row.status === 'claimed';
        var mine = isOwnPost(row, opts.ownName);
        return '<article class="feed-card' + (claimed ? ' is-claimed' : '') + ' p-4 sm:p-5">' +
            '<div class="flex items-start gap-3">' +
                avatarMarkup(row.donor_name) +
                '<div class="min-w-0 flex-1">' +
                    '<div class="flex items-start justify-between gap-3">' +
                        '<div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1">' +
                            '<span class="text-sm font-medium text-[#DAF1DE] truncate">' +
                                escapeHtml(row.donor_name) + '</span>' +
                            '<span class="text-[11px] text-[#8EB69B] truncate">' +
                                escapeHtml(feedHandle(row.donor_name)) + ' \u00b7 ' +
                                escapeHtml(feedWhen(row.created_at)) + '</span>' +
                        '</div>' +
                        '<span class="flex items-center gap-2 shrink-0">' +
                            (mine ? '<span class="inline-flex items-center h-6 px-3 ' +
                                'rounded-full border border-[#235347] bg-[#051F20] ' +
                                'text-[10px] uppercase tracking-[0.18em] ' +
                                'text-[#8EB69B]">your post</span>' : '') +
                            '<span class="text-[13px] text-[#8EB69B] select-none" ' +
                                'aria-hidden="true">\u2298  \u22ef</span>' +
                        '</span>' +
                    '</div>' +
                    '<p class="mt-3 font-display text-xl leading-snug ' +
                        (claimed ? 'text-[#8EB69B]' : 'text-[#DAF1DE]') + '">' +
                        escapeHtml(row.item_name) + '</p>' +
                    '<p class="mt-2 text-[15px] leading-relaxed">' +
                        '<span class="text-[#DAF1DE] font-semibold">' +
                            escapeHtml(postNeeds(row)) + '</span>' +
                        '<span class="text-[#8EB69B]"> \u2014 ready for pickup at </span>' +
                        '<span class="text-[#8EB69B]/80">' + escapeHtml(row.location) +
                        '</span>' +
                    '</p>' +
                    '<div class="mt-4 pt-3 border-t border-[#235347] flex flex-wrap ' +
                        'items-center gap-x-5 gap-y-3">' +
                        '<span class="text-[11px] text-[#8EB69B]">\u23f1 ' +
                            escapeHtml(formatWhen(row.created_at)) + '</span>' +
                        // The goods category the post is for, when it has one. It is what
                        // the *For You* tab filters on, so it is worth showing on the card
                        // the filter is choosing between.
                        (goodsType(row.goods_type)
                            ? '<span class="inline-flex items-center gap-1.5 h-6 px-3 ' +
                                'rounded-full border border-[#235347] bg-[#051F20] ' +
                                'text-[10px] uppercase tracking-[0.18em] ' +
                                'text-[#8EB69B]"><span aria-hidden="true">' +
                                goodsType(row.goods_type).emoji + '</span>' +
                                escapeHtml(goodsType(row.goods_type).label) + '</span>'
                            : '') +
                        '<span class="' + statusChip(row.status) + '">' +
                            statusLabel(row.status) + '</span>' +
                        // The one action, wearing the site's hero-card treatment: dark with
                        // cream text at rest, filled with the accent and turned black while
                        // hovered or keyboard-focused — see .claim-btn in the dashboards.
                        (opts.canClaim && !claimed
                            ? '<button type="button" data-claim="' + escapeHtml(row.id) + '" ' +
                                'onclick="claimSurplus(this)" class="claim-btn ml-auto px-5 ' +
                                'py-2.5 rounded-full text-xs font-black uppercase ' +
                                'tracking-widest transition-all disabled:opacity-40 ' +
                                'disabled:cursor-not-allowed">Claim</button>'
                            : '') +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</article>';
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
        goodsColumn: GOODS_COLUMN,
        goodsTypes: GOODS_TYPES,
        goodsType: goodsType,
        goodsLabel: function (value) {
            var type = goodsType(value);
            return type ? type.label : '';
        },
        pollMs: POLL_MS,
        ready: ready,
        describe: describe,
        listPosts: listPosts,
        createPost: createPost,
        claimPost: claimPost,
        recordClaim: recordClaim,
        recorderUrl: RECORDER_URL,
        watchPosts: watchPosts,
        escapeHtml: escapeHtml,
        statusChip: statusChip,
        statusLabel: statusLabel,
        formatWhen: formatWhen,
        feedWhen: feedWhen,
        feedHandle: feedHandle,
        isOwnPost: isOwnPost,
        postNeeds: postNeeds,
        feedCard: feedCard,
        feedSummary: feedSummary
    };
})(window);
