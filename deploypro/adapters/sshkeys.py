"""Making deploy keys.

Ed25519: short keys, fast, and accepted by GitHub, GitLab and every SSH server
from the last decade. Generated in-process with `cryptography` rather than by
calling ssh-keygen, so creating one needs no binary and no temporary file —
the private half exists only in memory until it is encrypted.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate(comment: str) -> tuple[str, str]:
    """(private key in OpenSSH format, public key line for authorized_keys)."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode()
    )
    return private, f"{public} {comment}"
