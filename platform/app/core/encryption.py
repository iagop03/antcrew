"""AES-GCM field-level encryption for sensitive JSON columns.

Usage:
    Set ANTCREW_ENCRYPTION_KEY to a base64-encoded 32-byte key:
        python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"

When the key is not set, EncryptedJSON behaves exactly like a plain JSON column —
all existing data and tests remain unaffected.

Backward compatibility: on read, values that don't start with the sentinel prefix
are treated as plain JSON (data written before encryption was enabled).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Optional

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

log = logging.getLogger(__name__)

_SENTINEL = "antcrew:enc:v1:"
_KEY: Optional[bytes] = None

_raw_key = os.environ.get("ANTCREW_ENCRYPTION_KEY", "").strip()
if _raw_key:
    try:
        _decoded = base64.b64decode(_raw_key)
        if len(_decoded) not in (16, 24, 32):
            raise ValueError(f"Key must be 16, 24, or 32 bytes; got {len(_decoded)}")
        _KEY = _decoded
        log.info("encryption: AES-GCM field encryption enabled (%d-byte key)", len(_KEY))
    except Exception as _err:
        log.warning(
            "encryption: ANTCREW_ENCRYPTION_KEY is invalid (%s) — field encryption disabled",
            _err,
        )


def _encrypt(plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ciphertext = AESGCM(_KEY).encrypt(nonce, plaintext.encode(), None)
    payload = base64.b64encode(nonce + ciphertext).decode()
    return _SENTINEL + payload


def _decrypt(encrypted: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(encrypted[len(_SENTINEL):])
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(_KEY).decrypt(nonce, ciphertext, None).decode()


def is_encryption_enabled() -> bool:
    return _KEY is not None


class EncryptedJSON(TypeDecorator):
    """JSON column with optional AES-GCM-256 encryption.

    Transparently encrypts on write and decrypts on read when
    ANTCREW_ENCRYPTION_KEY is set.  Reads are backward-compatible:
    plain JSON values (written before the key was configured) are
    deserialized normally.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Optional[str]:
        if value is None:
            return None
        serialized = json.dumps(value, ensure_ascii=False)
        if _KEY:
            return _encrypt(serialized)
        return serialized

    def process_result_value(self, value: Optional[str], dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(_SENTINEL):
            if not _KEY:
                log.error(
                    "encryption: encrypted value in DB but ANTCREW_ENCRYPTION_KEY is not set — returning None"
                )
                return None
            return json.loads(_decrypt(value))
        if isinstance(value, str):
            return json.loads(value)
        return value  # safety: already a Python object (shouldn't happen with Text impl)
