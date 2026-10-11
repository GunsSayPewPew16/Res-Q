/* Where the API lives.
 *
 * Every page here calls the API by path — `/api/me`, `/api/weather?lat=…` — and this file
 * is the one place that decides which origin those paths are asked of. Loaded first, it
 * defines `apiUrl()`, and every call is written as `fetch(apiUrl('/api/…'))`.
 *
 * It carries the API's own origin, because the pages are served by Vercel while the API
 * is served by Render: one repository, one set of files, but two hosts, so a path like
 * `/api/me` has to be asked of the Render service rather than of whatever host handed
 * the page over.
 *
 * That origin has to be named in the API's RESQ_ALLOWED_ORIGINS as well (see the README's
 * deployment section), because a browser will not let one site read another's API unless
 * the other site says it may: this line is the ask, that variable is the answer, and
 * getting one without the other is the CORS line in the console.
 *
 * Local development is the second half of the same thing: open the pages from the API's
 * own process (`python3 backend/server.py`, which serves them on 127.0.0.1:8080) and this
 * line sends the calls across origins anyway, so that origin has to be allowed too
 * (`http://localhost:8080` and `http://127.0.0.1:8080` are two different origins to a
 * browser). To talk to a local API instead, empty it — `var RESQ_API_BASE = '';` means
 * "the API is on this same origin", which is how it runs when one server answers both.
 *
 * A page can also set `window.RESQ_API_BASE` in an inline script before this file loads,
 * which wins over the value below — that is how a preview deployment can be pointed at a
 * staging API, or a local page back at a local API, without editing this file.
 */

var RESQ_API_BASE = 'https://res-q-api.onrender.com';

(function () {
    var base = (window.RESQ_API_BASE || RESQ_API_BASE || '').replace(/\/+$/, '');
    window.RESQ_API_BASE = base;

    window.apiUrl = function apiUrl(path) {
        if (!path) {
            return base;
        }
        // An absolute URL is already somewhere specific — the geocoder, say — and is
        // handed back untouched.
        if (/^https?:\/\//i.test(path)) {
            return path;
        }
        return base + (path.charAt(0) === '/' ? path : '/' + path);
    };
})();
