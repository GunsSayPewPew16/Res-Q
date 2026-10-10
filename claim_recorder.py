#!/usr/bin/env python3
"""
Res-Q claim recorder.

A separate companion service that records who claimed which surplus post.
It is deliberately OUTSIDE the backend/ folder: the rule for this feature is
that not one line of the backend's own code may be touched. This module adds
one extra port (8081) beside the site's own server, and the Recipient dashboard
posts every successful claim to it.

What it stores

    backend/claim_records.json

a JSON array, one record per claim:

    {
        "record_id":   "a2f0…",            # this record's own id
        "claimed_at":  "2026-10-10T14:05:29Z",
        "post": {                          # the surplus_posts row that was claimed
            "id":         "<post uuid>",   # the link back to the post on Supabase
            "donor_name": "…",
            "item_name":  "…",
            "quantity":   "…",
            "location":   "…",
            "goods_type": "…",             # null on an untagged post
            "created_at": "…"
        },
        "recipient": {                     # who claimed it, from /api/me
            "user_id": 0,                  # demo mode ids are 0; persistent ids are real
            "name": "…",                   # business name when there is one
            "email": "…", "phone": "…",
            "city": "…", "province": "…", "postal": "…",
            "delivery_address": "…",
            "delivery_lat": 40.73, "delivery_lng": -74.0,
            "delivery_confirmed_at": "…"
        }
    }

Multiple recipients claiming the same post each get their own record, all
carrying the same post id — which is what a rationing pass reads: one post,
many claimants.

Run it from the repository root (it does not need the site running, but the
dashboards only post to it while the claim flow is live):

    python3 claim_recorder.py            # port 8081
    python3 claim_recorder.py 8090       # any other port

Only the standard library is used.
"""

import datetime
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# The records live inside the backend folder — data, not code — so the rationing
# pass reads them from the place the site's own accounts database lives too.
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
RECORDS_PATH = os.path.join(BACKEND_DIR, "claim_records.json")

LOCK = threading.Lock()

# The keys a record is allowed to carry, and the ones copied verbatim off the
# post row and the recipient profile. Anything else the caller sends is dropped,
# so the file only ever holds claim-shaped records.
POST_FIELDS = ("id", "donor_name", "item_name", "quantity", "location",
               "goods_type", "created_at")
RECIPIENT_FIELDS = ("id", "first_name", "last_name", "business_name", "email",
                    "phone", "dial_code", "city", "province", "postal",
                    "delivery_address", "delivery_lat", "delivery_lng",
                    "delivery_confirmed_at")


def read_records():
    if not os.path.exists(RECORDS_PATH):
        return []
    try:
        with open(RECORDS_PATH, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (ValueError, OSError):
        return []
    return parsed if isinstance(parsed, list) else []


def write_records(records):
    os.makedirs(BACKEND_DIR, exist_ok=True)
    with open(RECORDS_PATH, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def picked(source, allowed):
    row = source if isinstance(source, dict) else {}
    return {field: row.get(field) for field in allowed if field in row}


class ClaimRecorderHandler(BaseHTTPRequestHandler):
    server_version = "ResQClaimRecorder/1.0"

    # ------------------------------------------------------------------ helpers
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The recorder runs beside the site's own origin, so the dashboard's
        # browser context has to be allowed to call it across ports.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ------------------------------------------------------------------- routes
    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/claims", "/claims/"):
            with LOCK:
                records = read_records()
            return self.send_json(200, {"ok": True, "count": len(records), "claims": records})
        if path == "/health":
            return self.send_json(200, {"ok": True, "records": RECORDS_PATH})
        return self.send_json(404, {"error": "Unknown endpoint. POST claims to /claims."})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/claims", "/claims/"):
            return self.send_json(404, {"error": "Unknown endpoint. POST claims to /claims."})

        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})
        if not isinstance(data, dict):
            return self.send_json(400, {"error": "Body has to be a claim object."})

        post = picked(data.get("post"), POST_FIELDS)
        recipient = picked(data.get("recipient"), RECIPIENT_FIELDS)

        # The link back to the post is the one thing a claim cannot lack: without
        # the post id the record could not be joined to anything on the board.
        if not post.get("id"):
            return self.send_json(
                400,
                {
                    "error": "A claim has to name the post it claimed: send post.id.",
                    "field": "post.id",
                },
            )

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        record = {
            "record_id": uuid.uuid4().hex,
            "claimed_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "post": post,
            "recipient": recipient or None,
        }

        with LOCK:
            records = read_records()
            # One recipient claims one post once: replaying the same pair
            # answers with the record already saved rather than a second one.
            existing = next(
                (row for row in records
                 if row["post"]["id"] == post["id"]
                 and row.get("recipient")
                 and recipient.get("id") is not None
                 and row["recipient"].get("id") == recipient["id"]
                 and recipient["id"] != 0),
                None,
            )
            if existing is not None:
                return self.send_json(200, {"ok": True, "duplicate": True, "claim": existing})
            records.append(record)
            write_records(records)

        return self.send_json(201, {"ok": True, "claim": record})

    def log_message(self, fmt, *args):
        pass  # quiet: the recorder sits in the background beside the site


def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    server = ThreadingHTTPServer(("127.0.0.1", port), ClaimRecorderHandler)
    print("Claim recorder on http://127.0.0.1:%d" % port, flush=True)
    print("  records: %s" % RECORDS_PATH, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
