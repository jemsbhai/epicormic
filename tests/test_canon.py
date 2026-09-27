"""Conformance of epicormic's canonical serialization with pollard's.

epicormic reimplements the documented identity grammar (docs/PLAN.md, D7).
These tests compare its bytes and its rejections against pollard's private
implementation over a property-based corpus, and lock known digests.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pollard import _canon as pollard_canon
from pollard.hashing import DOMAIN as POLLARD_DOMAIN
from pollard.hashing import node_id

from epicormic._canon import IdentityValue, canonical_bytes, domain_digest, validate_identity_value

leaves = st.none() | st.booleans() | st.integers() | st.text()
identity_values = st.recursive(
    leaves,
    lambda children: st.lists(children, max_size=5)
    | st.dictionaries(st.text(), children, max_size=5),
    max_leaves=30,
)

invalid_leaves = (
    st.floats()
    | st.binary()
    | st.tuples(st.integers())
    | st.sets(st.integers())
    | st.dictionaries(st.integers(), leaves, min_size=1, max_size=3)
)


@st.composite
def invalid_values(draw: st.DrawFn) -> Any:
    """A value with exactly one invalid element, wrapped in valid containers."""

    value: Any = draw(invalid_leaves)
    for _ in range(draw(st.integers(0, 4))):
        if draw(st.booleans()):
            value = [*draw(st.lists(leaves, max_size=3)), value]
        else:
            value = {**draw(st.dictionaries(st.text(), leaves, max_size=3)), draw(st.text()): value}
    return value


@given(identity_values)
def test_bytes_match_pollard(value: Any) -> None:
    assert canonical_bytes(value) == pollard_canon.canonical_bytes(value)


@given(identity_values)
def test_node_digest_matches_pollard_node_id(payload: Any) -> None:
    identity = {"a": 3, "k": "note", "p": "", "pl": payload}
    assert domain_digest(POLLARD_DOMAIN, identity) == node_id("note", None, 3, payload)


@given(invalid_values())
def test_rejections_match_pollard(value: Any) -> None:
    with pytest.raises(TypeError):
        pollard_canon.canonical_bytes(value)
    with pytest.raises(TypeError):
        canonical_bytes(value)


def test_known_bytes_are_locked() -> None:
    value: IdentityValue = {"b": 1, "a": [True, None, "\u00e9"], "c": {"z": -7, "y": ""}}
    assert canonical_bytes(value) == '{"a":[true,null,"\u00e9"],"b":1,"c":{"y":"","z":-7}}'.encode()


def test_known_digest_is_locked() -> None:
    domain = b"epicormic/panel/v1\n"
    value: IdentityValue = {"name": "demo", "probes": []}
    expected = hashlib.sha256(domain + b'{"name":"demo","probes":[]}').hexdigest()
    assert domain_digest(domain, value) == expected
    assert domain_digest(domain, value) == (
        hashlib.sha256(domain + canonical_bytes(value)).hexdigest()
    )


def test_domain_must_be_bytes_ending_in_newline() -> None:
    with pytest.raises(ValueError):
        domain_digest(b"epicormic/panel/v1", {})
    with pytest.raises(ValueError):
        domain_digest("epicormic/panel/v1\n", {})  # type: ignore[arg-type]


def test_booleans_stay_booleans_and_integers_stay_exact() -> None:
    assert canonical_bytes([True, False, 1, 0, 10**40]) == b"[true,false,1,0,1" + b"0" * 40 + b"]"


def test_non_ascii_is_preserved_not_escaped() -> None:
    assert canonical_bytes("\u65e5\u672c\u8a9e") == "\"\u65e5\u672c\u8a9e\"".encode()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"a": [1, 2.5]}, "floats are not allowed in identity payloads at $.a[1]"),
        ({"a": b"x"}, "bytes are not allowed in identity payloads at $.a"),
        ({1: "x"}, "identity object keys must be str at $"),
        ({"a": {"b": (1,)}}, "unsupported identity value tuple at $.a.b"),
    ],
)
def test_rejection_messages_name_the_path(value: Any, message: str) -> None:
    with pytest.raises(TypeError, match=message.replace("$", r"\$").replace("[", r"\[")):
        validate_identity_value(value)
