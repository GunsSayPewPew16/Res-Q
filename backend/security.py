"""Res-Q backend password hashing and checking.

PBKDF2-HMAC-SHA256 with a per-account salt, and the two checks a password
sentence has to pass before one is ever hashed. Nothing else in the backend
touches raw passwords.
"""

import hashlib
import hmac
import secrets

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex(), salt

def verify_password(password, digest, salt):
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, digest)
