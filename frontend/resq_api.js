/* Where the API lives.
 *
 * Every page here calls the API by path — `/api/me`, `/api/weather?lat=…` — and this file
 * is the one place that decides which origin those paths are asked of. Loaded first, it
 * defines `apiUrl()`, and every call is written as `fetch(apiUrl('/api/…'))`.
 *
 * Empty, which is what is committed, means "the API is on this same origin" — how it runs
 * locally, where the backend serves these pages itself, and how a Render deployment works
 * too, since that single service answers both the pages and the API.
 *
 * Set it to the API's own origin when the pages are hosted somewhere other than the API —
 * the usual split being the pages on Vercel and the API on Render:
 *
 *     var RESQ_API_BASE = 'https://res-q-api.onrender.com';
 *
 * That origin then has to be named in the API's RESQ_ALLOWED_ORIGINS (see the README's
 * deployment section), because a browser will not let one site read another's API unless
 * the other site says it may. A page can also set `window.RESQ_API_BASE` in an inline
 * script before this file loads, which wins over the value below — that is how a preview
 * deployment can be pointed at a staging API without editing this file.
 */

var RESQ_API_BASE = '';

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
