"""Gunicorn settings for a deployed Res-Q API.

    gunicorn -c gunicorn_config.py wsgi:application

Every number here is a choice about *this* API rather than a default worth inheriting:

One worker. `config.py` keeps demo accounts, sessions and the logged history in this
process's memory, so a second worker would be a second, separate copy of all of it: sign in
through one and the next request could reach a worker that has never heard of that session.
Threads, not workers, are how this service takes more than one request at once — several of
these routes wait on Open-Meteo, and a thread waiting on the network should not hold up a
page load. If the service ever moves fully to persistent storage, that is the point at
which raising `workers` becomes a real option, and it has to be raised here rather than in
the host's dashboard so the reason stays attached to it.

`$PORT` is what a host hands over, and `wsgi.py` reads the same variable, so nothing has to
be told the port twice. Run by hand with no `$PORT` set, it binds the loopback address on
8080 — the same place `python3 backend/server.py` puts the API — so the pages beside it
find it either way.
"""

import os

# Where the API's code lives. Gunicorn is normally started from the repository root.
chdir = os.path.dirname(os.path.abspath(__file__))

# `$PORT` counts only when it is a real port number. An empty value, a non-number, or `0`
# — which asks the operating system to pick a port — is somebody else's environment
# variable rather than a host's instruction, and binding the address a host asked for
# because of it would put the API somewhere nothing is looking. `server.py` keeps the
# same rule, so the two entry points agree on where they are.
_requested = (os.environ.get("PORT") or "").strip()
_hosted_port = _requested if _requested.isdigit() and int(_requested) > 0 else None

_host = "0.0.0.0" if _hosted_port else "127.0.0.1"
_port = _hosted_port or os.environ.get("RESQ_PORT") or "8080"
bind = "%s:%s" % (_host, _port)

workers = 1
worker_class = "gthread"
threads = 4

# A request is allowed to be slow rather than cut off: `/api/surplus-forecast` trains the
# model on first use and `/api/surplus-outlook` reads eight days from Open-Meteo. Thirty
# seconds — Gunicorn's own default — is not enough headroom for a cold start on a slow
# network, and a killed worker loses the in-memory state this deployment runs on.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
