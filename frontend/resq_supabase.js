/* ---------------------------------------------------------------------------
 * Res-Q · Supabase client and the `surplus_posts` calls both dashboards share.
 *
 * A donor logs a surplus item and a recipient claims it, and the row that
 * carries that between them lives in Supabase rather than in the local
 * backend: `supabase_posts` is the one table both dashboards talk to directly,
 * which is also what lets a post reach every open dashboard the moment it is
 * written.
 *
 * A post is claimed by more than one recipient, so the claims are rows of their
 * own in `surplus_claims`, one per recipient per post. That second table is what
 * lets the board answer the two questions surplus_posts.status cannot — *who* asked
 * for an item, and *how many* asked — and it is where the line the card carries
 * comes from: "Daniela Ruiz and 2 more claimed". A project that has not run the
 * claims migration yet is not broken by any of this: the claim falls back to the
 * older first-claim-wins update and the cards simply carry no claimant line.
 *
 * The key below is the project's *publishable* key. It is the key that is meant
 * to sit in a page: it can only ever act as the anonymous role, so what it may
 * do is decided by the table's row level security policies, not by keeping it
 * secret. The secret key is never used here.
 *
 * The library itself is fetched from a CDN the first time a dashboard actually
 * needs the database, the way MapLibre is fetched when the map overlay opens, so
 * a signed-in visitor who never opens the feed or the form loads none of it.
 * ------------------------------------------------------------------------- */
(function (global) {
    'use strict';

    var SUPABASE_URL = 'https://paeshgdpqbjoagpbybhl.supabase.co';
    var SUPABASE_KEY = 'sb_publishable_pRaPy6MH_oBWtx6Ei2Uatw_VM9Z2y8w';
    // Pinned the way MapLibre is: the bundle is the UMD build, whose global is
    // `supabase` and whose entry point is `createClient`.
    var SUPABASE_JS = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.117.3/dist/umd/supabase.js';

    var TABLE = 'surplus_posts';

    // The claims a post carries, added by
    // supabase/migrations/20261012000000_create_surplus_claims.sql. One row per
    // recipient per post, which is what makes a post claimable by several recipients
    // and lets the board name and count them.
    var CLAIMS_TABLE = 'surplus_claims';

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
    // Claims are one small row each, so a board draws them all rather than asking per
    // post: the newest few hundred cover every post the feed shows.
    var CLAIM_LIMIT = 400;

    var client = null;
    var libraryLoading = null;

    // Whether the claims table is in the project, learned from the first read or
    // write that touches it: 'unknown' until then, 'yes' once a claim read or written
    // row comes back, 'no' when the table is not there. It is what decides between
    // the board's two claim behaviours — many claimants per post, or the older
    // first-claim-wins — and it is never guessed.
    var claimsSupport = 'unknown';

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
        // 23505 is the claims table's own unique index answering: this recipient has
        // already claimed this post. That is not a failure worth an error tone — the
        // card says so in a word — so it is named rather than left as a generic error.
        if (String(code) === '23505') {
            return {
                missingTable: false,
                missingColumn: false,
                duplicate: true,
                message: 'this recipient has already claimed that post'
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
            // `data` is an array for a list and a single row — or a null — for the
            // `.single()`/`.maybeSingle()` calls. That null is kept exactly as it comes
            // back: it is how an update that matched no row is told apart from one that
            // matched, which is what a claim raced by somebody else looks like.
            var data = res ? res.data : null;
            return { ok: true, data: data === undefined ? [] : data };
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

    // ---------------------------------------------------------------------------
    // Claiming a post
    // ---------------------------------------------------------------------------
    // The person behind a claim, and the organisation they claim for, both read off
    // the account profile the page holds: the contact's own name first, and the
    // business or organisation name when there is no person on the profile.
    function claimantName(recipient) {
        var person = [
            String((recipient && recipient.first_name) || '').trim(),
            String((recipient && recipient.last_name) || '').trim()
        ].join(' ').trim();
        return person || claimantOrg(recipient);
    }

    function claimantOrg(recipient) {
        return String((recipient && recipient.business_name) || '').trim();
    }

    // Claiming is a row of its own now, and that is the whole point: `surplus_claims`
    // keeps one row per recipient per post, so a shelter and a kitchen looking at the
    // same pallet of bread can both claim it and the donor can read back who asked.
    // The post's own status still settles to 'claimed' on the first claim — the
    // board's chips and figures read that — but it no longer closes the post to
    // anybody else, which is why the feed keeps the claim action on a claimed post.
    //
    // Two things fall back to the older first-claim-wins update, unchanged, rather
    // than writing something the project cannot hold: a project where the claims
    // table is not there yet, and a claim with no name to file it under.
    function claimPost(id, recipient) {
        var claimant = claimantName(recipient);
        var org = claimantOrg(recipient);
        return withClient(function (db) {
            function legacy() {
                // The row is matched on being still open, so the second recipient to
                // click loses the race cleanly (no row comes back) instead of
                // overwriting the first claim. `claimed` says which of the two happened,
                // because this path writes nothing when it loses: an answered null is a
                // claim that was not made, not one that was recorded without settling the
                // post.
                return reply(db.from(TABLE)
                    .update({ status: 'claimed' })
                    .eq('id', id)
                    .eq('status', 'pending')
                    .select()
                    .maybeSingle())
                    .then(function (res) {
                        if (res.ok) { res.claimed = !!res.data; }
                        return res;
                    });
            }

            if (!claimant) { return legacy(); }

            var row = { post_id: id, recipient_name: claimant };
            if (org) { row.recipient_org = org; }

            return reply(db.from(CLAIMS_TABLE).insert(row).select().single())
                .then(function (res) {
                    if (!res.ok) {
                        if (res.error && res.error.missingTable) {
                            claimsSupport = 'no';
                            return legacy();
                        }
                        return res;
                    }
                    claimsSupport = 'yes';
                    // The claim is on the board now. Settling the post itself is the
                    // second half and deliberately not a gate: whether it was open or
                    // already claimed by somebody else, this recipient's claim is
                    // recorded and the card is redrawn with one more name on it.
                    return reply(db.from(TABLE)
                        .update({ status: 'claimed' })
                        .eq('id', id)
                        .eq('status', 'pending')
                        .select()
                        .maybeSingle())
                        .then(function (settled) {
                            return {
                                ok: true,
                                claimed: true,
                                duplicate: false,
                                claim: res.data,
                                data: (settled.ok && settled.data) || null
                            };
                        });
                });
        });
    }

    // ---------------------------------------------------------------------------
    // The claims on the board
    // ---------------------------------------------------------------------------
    // Read newest-first so a busy board's most recent claims are never the ones
    // dropped by the limit; a group is then put back in claim order below, because
    // "who asked first" is the name the line leads with.
    function listClaims(options) {
        var opts = options || {};
        return withClient(function (db) {
            var query = db.from(CLAIMS_TABLE)
                .select('id,post_id,recipient_name,recipient_org,claimed_at')
                .order('claimed_at', { ascending: false })
                .limit(opts.limit || CLAIM_LIMIT);
            if (opts.postIds && opts.postIds.length) {
                query = query.in('post_id', opts.postIds);
            }
            return reply(query).then(function (res) {
                if (res.ok) {
                    claimsSupport = 'yes';
                } else if (res.error && res.error.missingTable) {
                    claimsSupport = 'no';
                }
                return res;
            });
        });
    }

    // The claims of one board, gathered per post: `{ post id: [claim, claim] }`, each
    // list in claim order so the first element is the recipient who asked first.
    function groupClaims(claims) {
        var byPost = {};
        (claims || []).forEach(function (claim) {
            var key = String((claim && claim.post_id) || '');
            if (!key) { return; }
            if (!byPost[key]) { byPost[key] = []; }
            byPost[key].push(claim);
        });
        Object.keys(byPost).forEach(function (key) {
            byPost[key].sort(function (one, other) {
                return String(one.claimed_at || '').localeCompare(String(other.claimed_at || ''));
            });
        });
        return byPost;
    }

    function claimsSupported() {
        return claimsSupport === 'yes';
    }

    // The names on a post's claims, in the order the claims were made.
    function claimNames(claims) {
        return (claims || []).map(function (claim) {
            return String((claim && claim.recipient_name) || '').trim();
        }).filter(function (name) { return !!name; });
    }

    // The line the reference feed draws under a title — "Followed by tryansylvania and
    // 4.7 M others" — with the claim in it instead: the first recipient to ask named,
    // and the rest counted. One claimant is named on their own, because "and 0 more"
    // is not how anybody says it. An unclaimed post says nothing at all.
    function claimHeadline(claims) {
        var names = claimNames(claims);
        if (!names.length) { return ''; }
        if (names.length === 1) { return names[0] + ' claimed'; }
        return names[0] + ' and ' + (names.length - 1) + ' more claimed';
    }

    // The same line as markup, on the card's own terms: the reference's bold light
    // text on the dark surface, and no avatar — the person's face is not what a
    // donor is reading here, the count is.
    function claimIndicator(claims) {
        var headline = claimHeadline(claims);
        if (!headline) { return ''; }
        return '<p class="mt-3 text-[15px] leading-snug font-bold text-[#DAF1DE]">' +
            escapeHtml(headline) + '</p>';
    }

    // Whether two names are the same person. The board compares names trimmed — a
    // trailing space is not a different recipient, and neither is a different case.
    function sameName(one, other) {
        return String(one === null || one === undefined ? '' : one).trim().toLowerCase()
            === String(other === null || other === undefined ? '' : other).trim().toLowerCase();
    }

    // -------------------------------------------------------------------------
    // Claim records: a second copy of each claim, for the rationing pass.
    //
    // Claim records: a second copy of every claim, filed with the site's own backend.
    //
    // The board keeps *who* claimed a post itself, since a claim became a row of its own
    // in `surplus_claims` — that is what the claimant line on a card is drawn from. This
    // is the same fact written a second time, on the rationing pass's own terms, because
    // the pass runs in the API rather than in a page: `POST /api/claims` takes the row
    // that was claimed and files it against the signed-in account — the token is what says
    // whose claim it is, never the body — one record per claim, every one of them carrying
    // the post id it claimed. Multiple recipients reaching the same post each get their own
    // record, tied to that one post id, which is what a rationing pass joins on; the board's
    // own rows carry the names the donor reads. A record therefore survives on a deployment
    // the way its accounts do, instead of living on the machine the page happens to be open
    // on, and it is deliberately not a gate on the claim: a claim the board has already
    // accepted is never held up by the second copy of it failing.

    // -------------------------------------------------------------------------
    var CLAIMS_URL = apiUrl('/api/claims');

    function recordClaim(post, recipient, token) {
        if (!post || !post.id) {
            return Promise.resolve({ ok: false, error: { message: 'no post to record' } });
        }
        var headers = { 'Content-Type': 'application/json' };
        if (token) { headers.Authorization = 'Bearer ' + token; }
        return fetch(CLAIMS_URL, {
            method: 'POST',
            headers: headers,
            // The profile travels with the claim because a claim is meant to be readable
            // on its own; the account it is filed under is the token's, so what arrives
            // in the body decides nothing about whose record it becomes.
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
    // The claims stream the same way, on their own channel: a claim landing anywhere
    // moves the donor's claimant line while the board is open. A project without the
    // claims table, or without it in the realtime publication, simply never calls
    // back — the caller's own timer covers it.
    function watchClaims(onChange) {
        var channel = null;
        var stopped = false;
        ready().then(function (db) {
            if (stopped) { return; }
            channel = db.channel('resq-surplus-claims')
                .on('postgres_changes', {
                    event: '*',
                    schema: 'public',
                    table: CLAIMS_TABLE
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
        // The claims this post already carries, and whether the person reading this
        // card is one of them. `multiClaim` says the project can hold more than one
        // claim, which is what keeps the action on a claimed post; without it the
        // card behaves exactly as it did — the action is only on an open one.
        var claimants = opts.claimants || [];
        var viewerClaimed = !!opts.viewerName && claimNames(claimants).some(function (name) {
            return sameName(name, opts.viewerName);
        });
        var claimable = opts.canClaim && !viewerClaimed
            && (!!opts.multiClaim || !claimed);
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
                    // Who has claimed this post, which is the one line a donor reads
                    // first: the first recipient to ask, named, and the rest counted.
                    claimIndicator(claimants) +
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
                        // No status chip. `claimed` used to mean "taken — nothing left here",
                        // which is what a chip is for saying; now a post can be claimed by
                        // several recipients and stays open to more, so the chip would be
                        // telling a reader to move on from a post they can still take. The
                        // line above the footer names the claimants, which is the fact the
                        // chip was standing in for, and the action beside it is what the
                        // reader does about it.
                        // The one action, wearing the site's hero-card treatment: dark with
                        // cream text at rest, filled with the accent and turned black while
                        // hovered or keyboard-focused — see .claim-btn in the dashboards.
                        // It stays on a claimed post, because more than one recipient may
                        // claim the same item; a recipient who has already claimed this one
                        // reads a chip in its place rather than a button that would only
                        // be refused.
                        (claimable
                            ? '<button type="button" data-claim="' + escapeHtml(row.id) + '" ' +
                                'onclick="claimSurplus(this)" class="claim-btn ml-auto px-5 ' +
                                'py-2.5 rounded-full text-xs font-black uppercase ' +
                                'tracking-widest transition-all disabled:opacity-40 ' +
                                'disabled:cursor-not-allowed">Claim</button>'
                            : '') +
                        (opts.canClaim && viewerClaimed
                            ? '<span class="ml-auto inline-flex items-center h-6 px-3 ' +
                                'rounded-full border border-[#235347] bg-[#051F20] ' +
                                'text-[10px] uppercase tracking-[0.18em] ' +
                                'text-[#8EB69B]">you claimed</span>'
                            : '') +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</article>';
    }

    // The count that sits over the board: everything published to it, and how much of
    // that already has somebody asking. There is deliberately no count of "open"
    // posts. It used to be the rows with nobody on them, back when the first claim
    // closed a post; a post now takes more than one, so one with claimants is not a
    // closed one and calling it anything but posted would read as a board with nothing
    // left. `claimsByPost` is the grouped claims the cards are drawn from, so the count
    // and the claimant lines under it can never disagree.
    function feedSummary(rows, claimsByPost) {
        var claims = claimsByPost || {};
        var taken = rows.filter(function (row) {
            return claimNames(claims[row.id]).length > 0;
        }).length;
        return rows.length + ' posted'
            + (taken ? ' \u00b7 ' + taken + ' with claimants' : '');
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
        claimsTable: CLAIMS_TABLE,
        claimLimit: CLAIM_LIMIT,
        ready: ready,
        describe: describe,
        listPosts: listPosts,
        createPost: createPost,
        claimPost: claimPost,
        listClaims: listClaims,
        groupClaims: groupClaims,
        claimNames: claimNames,
        claimHeadline: claimHeadline,
        claimIndicator: claimIndicator,
        claimantName: claimantName,
        claimantOrg: claimantOrg,
        claimsSupported: claimsSupported,
        sameName: sameName,
        recordClaim: recordClaim,
        claimsUrl: CLAIMS_URL,
        watchPosts: watchPosts,
        watchClaims: watchClaims,
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
