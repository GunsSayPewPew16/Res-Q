"""The Res-Q API as a WSGI application, so a real server can host it.

`server.py` is the whole API, and it runs on the standard library's own HTTP server —
that is what `python3 backend/server.py` does. A host wants to bring its own server
instead, and that server speaks WSGI (Gunicorn, on Render), so this module is the adapter
between the two. Every WSGI request is written back out as the HTTP request the handler
already knows how to parse, the handler answers it exactly as it answers a local one, and
what it wrote is handed back as a WSGI response:

    gunicorn -c gunicorn_config.py wsgi:application

There is deliberately nothing else in here: no routes, no static-file logic and no CORS of
its own. Every request meets the same code it meets locally, in the same order, because
there is still only one implementation of it — this file only decides how the request
reaches it. That is also why the API and the pages are served by the same process: the
deployed URL serves the front-end *and* the API behind it, so a Render URL alone is a
working site (see the README's deployment section).

One worker, and threads. Demo mode keeps its accounts, sessions and logged history in this
process's memory, and a second worker would hold a second, different copy of them — a
sign-in could land on a worker that has never seen that account. The threads share the one
process, which is what keeps the slower routes from blocking the faster ones.

The environment, all of it read by `config.py`:

    PORT                    the port the host wants this bound to (Gunicorn reads it too)
    RESQ_PERSIST=on         store accounts in SQLite instead of this process's memory
    RESQ_DB                 where that database lives, when persistence is on
    RESQ_SEED=on|off        create the five test donors at startup
    RESQ_ALLOWED_ORIGINS    the origins a browser may read this API from, comma-separated
"""

import io
import os
import sys

# Gunicorn is started from the repository root, where nothing else is importable; this
# folder is the package the API's modules live in, so it is put on the path before any of
# them is imported.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import ResQHandler, prepare_state  # noqa: E402 — the line above must run first

# Tables, and the five test donors. Once per worker, before the first request is answered.
prepare_state()


class _ResponseBuffer(io.BytesIO):
    """A file the handler writes its answer into and closes without losing it.

    `http.server` closes its output stream as it finishes a request, and a closed `BytesIO`
    refuses to hand its bytes back — so the close is swallowed here and the answer is read
    out afterwards. Everything else is the buffer it already is.
    """

    def close(self):
        return None


class _RequestSocket:
    """The two ends of a connection, in memory — which is all the handler asks of one.

    `http.server` reaches for a socket for exactly two things: a file to parse the request
    from, and a file to write the answer into. Handing it buffers instead is what lets a
    request that arrived over WSGI be answered by that handler, and it is the only piece of
    socket behaviour this file has to provide.

    The handler writes through `wfile`, never straight to the socket, because `wbufsize` is
    set below: that is what keeps this to `makefile` and nothing else.
    """

    def __init__(self, incoming):
        self._incoming = incoming
        self.outgoing = _ResponseBuffer()

    def makefile(self, mode="rb", *args, **kwargs):
        return self._incoming if "r" in mode else self.outgoing

    # The handler's setup() may ask a socket for either of these; neither has a meaning
    # for a connection that never existed.
    def settimeout(self, value):
        return None

    def setsockopt(self, *args, **kwargs):
        return None

    def close(self):
        return None


class ResQWSGIHandler(ResQHandler):
    """The API's own handler, answering into a buffer rather than into a socket.

    `wbufsize = 1` is the whole of the difference: it makes `http.server` open a file for
    the answer instead of writing to the socket directly, which is what the buffer above
    stands in for. Nothing else about the handler changes — the routes, the CORS rules and
    the static pages are the same class the local server uses.
    """

    wbufsize = 1


# Headers that describe one connection rather than the answer itself. A WSGI server owns
# them, so passing ours on would be a second, contradicting set; PEP 3333 names them
# hop-by-hop headers and asks applications not to send them.
_HOP_BY_HOP = frozenset((
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
))

# Headers the HTTP server would otherwise treat specially while parsing what this file
# writes. `Expect: 100-continue` is answered by that parser itself, straight into the
# buffer, and its "100 Continue" would then be read back as the answer.
_PARSE_ONLY = frozenset(("expect",))


def _header_lines(environ):
    """The request's headers, as the lines an HTTP request carries them in."""
    for name, value in environ.items():
        if name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if value:
                yield "%s: %s" % (name.replace("_", "-").title(), value)
            continue
        if not name.startswith("HTTP_"):
            continue
        header = name[5:].replace("_", "-").title()
        if header.lower() in _PARSE_ONLY:
            continue
        # A header value cannot carry a line break: one arriving with either would end the
        # request line it is written into.
        yield "%s: %s" % (header, str(value).replace("\r", " ").replace("\n", " "))


def _raw_request(environ):
    """Rebuild the HTTP request that a WSGI environment was made from.

    The handler parses request lines, query strings, headers and bodies; writing them back
    in the shape it parses means there is one implementation of that work rather than a
    second, subtly different one here.
    """
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    lines = ["%s %s%s %s" % (
        environ.get("REQUEST_METHOD", "GET"),
        path,
        "?" + query if query else "",
        environ.get("SERVER_PROTOCOL", "HTTP/1.1"),
    )]
    lines.extend(_header_lines(environ))
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")

    length = (environ.get("CONTENT_LENGTH") or "").strip()
    if length.isdigit() and int(length) > 0:
        raw += environ["wsgi.input"].read(int(length)) or b""
    return raw


def _split_answer(raw):
    """The status line, headers and body out of what the handler wrote."""
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        # Nothing was written at all, which is what a handler that died mid-request leaves
        # behind. The exception itself has already been logged by whatever ran it.
        return "500 Internal Server Error", [], b""

    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ", 2)
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
        return "500 Internal Server Error", [], b""
    status = parts[1].decode("latin-1")
    if len(parts) > 2:
        status += " " + parts[2].decode("latin-1")

    headers = []
    for line in lines[1:]:
        name, found, value = line.partition(b": ")
        if not found or name.lower().decode("latin-1") in _HOP_BY_HOP:
            continue
        headers.append((name.decode("latin-1"), value.decode("latin-1")))
    return status, headers, body


def application(environ, start_response):
    """The API, as one WSGI callable — what `wsgi:application` names. One request, one answer.

    The handler is the local server's own, so a route that raises still raises here: a
    traceback lands in the host's error log and the 500 is the server's, which is the
    behaviour worth watching in a deployed process rather than an error swallowed here.
    """
    connection = _RequestSocket(io.BytesIO(_raw_request(environ)))
    ResQWSGIHandler(connection, ("0.0.0.0", 0), None)

    status, headers, body = _split_answer(connection.outgoing.getvalue())
    start_response(status, headers)
    return [body]
