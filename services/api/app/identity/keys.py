"""Rotating asymmetric JWT signing keys (kid-keyed).

Access tokens sign with RS256 and carry a ``kid`` header; verification picks
the public key by kid, so keys rotate without invalidating unexpired tokens.
The default provider keeps PEM files under the runtime ``auth_keys/``
directory (0600); a KMS/Key-Vault-backed deployment implements the same
:class:`SigningKeyring` protocol and never touches this module's files.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class SigningKeyring(Protocol):
    """Asymmetric signing key provider (local files or KMS behind this)."""

    def active_kid(self) -> str: ...

    def signing_pem(self) -> str:
        """Active private PEM — PyJWT signs with it (RS256 + kid header)."""
        ...

    def public_pem_for(self, kid: str) -> str | None: ...


class LocalRsaKeyring:
    """File-backed RSA keyring: one active key, retired keys still verify.

    Layout under ``root``:
      active.json          — the signing key (private PEM, 0600)
      keys/<kid>.json      — every key ever generated (public+private PEM)
    Retiring = generating a new active key; old kids remain verifiable until
    an operator prunes them after all access tokens issued under them have
    expired (15 minutes by default).
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # --- paths ------------------------------------------------------------

    @property
    def _keys_dir(self) -> Path:
        return self._root / "keys"

    def _active_file(self) -> Path:
        return self._root / "active.json"

    # --- lifecycle ----------------------------------------------------------

    def _load_or_create_active(self) -> dict:
        active = self._active_file()
        if active.is_file():
            data = json.loads(active.read_text(encoding="utf-8"))
            if data.get("kid") and data.get("private_pem"):
                return data
        record = self._generate()
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        per_key = self._keys_dir / f"{record['kid']}.json"
        self._atomic_write(per_key, record)
        self._atomic_write(active, record)
        log.info("generated JWT signing key kid=%s", record["kid"])
        return record

    def _generate(self) -> dict:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private = rsa.generate_private_key(public_exponent=65537,
                                           key_size=2048)
        private_pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()).decode()
        public_pem = private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        return {
            "kid": f"k{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}",
            "private_pem": private_pem,
            "public_pem": public_pem,
            "created_at": time.time(),
        }

    def _atomic_write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    # --- SigningKeyring -----------------------------------------------------

    def active_kid(self) -> str:
        return self._load_or_create_active()["kid"]

    def signing_pem(self) -> str:
        return self._load_or_create_active()["private_pem"]

    def public_pem_for(self, kid: str) -> str | None:
        active = self._load_or_create_active()
        if kid == active.get("kid"):
            return str(active.get("public_pem") or "")
        per_key = self._keys_dir / f"{kid}.json"
        if per_key.is_file():
            data = json.loads(per_key.read_text(encoding="utf-8"))
            return str(data.get("public_pem") or "") or None
        return None


_DEFAULT_KEYRING: LocalRsaKeyring | None = None


def default_keyring() -> LocalRsaKeyring:
    """Process keyring rooted at the runtime auth_keys directory."""
    global _DEFAULT_KEYRING
    if _DEFAULT_KEYRING is None:
        from app.core import paths

        _DEFAULT_KEYRING = LocalRsaKeyring(paths.runtime_paths().auth_keys)
    return _DEFAULT_KEYRING


def reset_default_keyring() -> None:
    """Tests: drop the cached keyring (sandbox retargets the data root)."""
    global _DEFAULT_KEYRING
    _DEFAULT_KEYRING = None
