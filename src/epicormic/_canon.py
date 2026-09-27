"""Canonical serialization for identity payloads, mirroring pollard.

pollard commits node identity to the canonical bytes of an identity value:
UTF-8 JSON with keys sorted, separators ``,`` and ``:`` with no added
whitespace, and non-ASCII text preserved rather than escaped. Allowed values
are null, strings, booleans, integers, lists of allowed values, and objects
whose keys are strings and whose values are allowed. Floats, bytes,
non-string object keys, and other Python values are rejected. Booleans remain
booleans even though Python treats ``bool`` as an ``int`` subtype.

This module reimplements that documented contract (pollard
docs/api-stability.md, "Canonical identity serialization") so that
epicormic never imports pollard's private ``_canon`` module. A conformance
test compares byte output against pollard's implementation over a
property-based corpus; if pollard exports ``canonical_bytes`` publicly in a
later release, this module becomes a re-export and the test stays as a
guard (docs/PLAN.md, D7).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeAlias

IdentityValue: TypeAlias = (
    dict[str, "IdentityValue"] | list["IdentityValue"] | str | int | bool | None
)

__all__ = ["IdentityValue", "canonical_bytes", "domain_digest", "validate_identity_value"]


def canonical_bytes(value: IdentityValue) -> bytes:
    """Return the stable UTF-8 JSON bytes of an identity value.

    Raises ``TypeError`` for any value outside the identity grammar, naming
    the JSON path of the offending element.
    """

    validate_identity_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def domain_digest(domain: bytes, value: IdentityValue) -> str:
    """Return the hex SHA-256 of ``domain`` followed by the canonical bytes of ``value``.

    Every epicormic digest is domain-separated the way pollard node ids are
    (``pollard/v1\\n`` plus canonical bytes), so a panel digest can never
    collide with a probe digest of the same content.
    """

    if not isinstance(domain, bytes) or not domain.endswith(b"\n"):
        raise ValueError("domain must be bytes ending in a newline")
    return hashlib.sha256(domain + canonical_bytes(value)).hexdigest()


def validate_identity_value(value: Any, path: str = "$") -> None:
    """Raise ``TypeError`` unless ``value`` is within the identity grammar."""

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise TypeError(f"floats are not allowed in identity payloads at {path}")
    if isinstance(value, bytes):
        raise TypeError(f"bytes are not allowed in identity payloads at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_identity_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"identity object keys must be str at {path}")
            validate_identity_value(item, f"{path}.{key}")
        return
    raise TypeError(f"unsupported identity value {type(value).__name__} at {path}")
