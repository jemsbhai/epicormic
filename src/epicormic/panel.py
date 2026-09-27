"""Probes and panels: the pinned canary requests epicormic observes.

A ``Probe`` is one identity payload that will be passed unchanged to
``pollard.Run.model_call``. A ``Panel`` is an ordered set of probes plus the
sampling configuration that is merged into each request at dispatch time.
Sampling parameters are floats in provider APIs and floats are not allowed
in identity payloads, so the panel keeps them as decimal strings, commits
to them through ``panel_digest``, and decodes them only inside the request
handed to the caller's step callable (docs/PLAN.md, section 5.2).
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._canon import IdentityValue, domain_digest, validate_identity_value
from ._decimal import parse_decimal_str

PANEL_FORMAT = "epicormic/panel/v1"
PANEL_DOMAIN = b"epicormic/panel/v1\n"
PROBE_DOMAIN = b"epicormic/probe/v1\n"

PROBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

_PROBE_KEYS = frozenset({"probe_id", "payload", "tags", "family"})
_PANEL_KEYS = frozenset(
    {
        "format",
        "name",
        "probes",
        "sampling",
        "seed_from_attempt",
        "seed_base",
        "seed_field",
        "min_samples",
    }
)

__all__ = [
    "PANEL_DOMAIN",
    "PANEL_FORMAT",
    "PROBE_DOMAIN",
    "PROBE_ID_PATTERN",
    "Panel",
    "PanelError",
    "Probe",
    "load_panel",
    "save_panel",
]


class PanelError(ValueError):
    """A panel or probe document violates the panel contract."""


@dataclass(frozen=True)
class Probe:
    """One pinned request. ``payload`` is the exact identity payload pollard records."""

    probe_id: str
    payload: dict[str, IdentityValue]
    tags: tuple[str, ...] = ()
    family: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.probe_id, str) or not PROBE_ID_PATTERN.match(self.probe_id):
            raise PanelError(
                f"probe_id must match {PROBE_ID_PATTERN.pattern}, got {self.probe_id!r}"
            )
        if not isinstance(self.payload, dict) or not self.payload:
            raise PanelError(f"probe {self.probe_id!r}: payload must be a non-empty object")
        try:
            validate_identity_value(self.payload, "$.payload")
        except TypeError as error:
            raise PanelError(f"probe {self.probe_id!r}: {error}") from error
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        if not isinstance(self.tags, tuple) or not all(isinstance(tag, str) for tag in self.tags):
            raise PanelError(f"probe {self.probe_id!r}: tags must be a tuple of strings")
        if self.family is not None and not isinstance(self.family, str):
            raise PanelError(f"probe {self.probe_id!r}: family must be a string or None")

    def to_document(self) -> dict[str, IdentityValue]:
        """Return the identity-valid document of this probe."""

        return {
            "probe_id": self.probe_id,
            "payload": copy.deepcopy(self.payload),
            "tags": list(self.tags),
            "family": self.family,
        }

    @property
    def digest(self) -> str:
        """Domain-separated SHA-256 of the probe document."""

        return domain_digest(PROBE_DOMAIN, self.to_document())

    @classmethod
    def from_document(cls, document: Any) -> Probe:
        """Build a probe from its document, rejecting unknown keys."""

        if not isinstance(document, dict):
            raise PanelError("probe document must be an object")
        unknown = set(document) - _PROBE_KEYS
        if unknown:
            raise PanelError(f"probe document has unknown keys: {sorted(unknown)}")
        if "probe_id" not in document or "payload" not in document:
            raise PanelError("probe document needs probe_id and payload")
        tags = document.get("tags", [])
        if not isinstance(tags, list):
            raise PanelError("probe tags must be a list")
        return cls(
            probe_id=document["probe_id"],
            payload=document["payload"],
            tags=tuple(tags),
            family=document.get("family"),
        )


@dataclass(frozen=True)
class Panel:
    """An ordered set of probes with the sampling configuration merged at dispatch."""

    name: str
    probes: tuple[Probe, ...]
    sampling: dict[str, str] = field(default_factory=dict)
    seed_from_attempt: bool = False
    seed_base: int = 0
    seed_field: str = "seed"
    min_samples: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise PanelError("panel name must be a non-empty string")
        if not isinstance(self.probes, tuple) or not self.probes:
            raise PanelError("panel needs at least one probe")
        if not all(isinstance(probe, Probe) for probe in self.probes):
            raise PanelError("panel probes must be Probe instances")
        seen: set[str] = set()
        for probe in self.probes:
            if probe.probe_id in seen:
                raise PanelError(f"duplicate probe_id {probe.probe_id!r}")
            seen.add(probe.probe_id)
        if not isinstance(self.sampling, dict):
            raise PanelError("sampling must be an object of decimal strings")
        for key, value in self.sampling.items():
            if not isinstance(key, str) or not key:
                raise PanelError("sampling keys must be non-empty strings")
            if not isinstance(value, str):
                raise PanelError(
                    f"sampling[{key!r}] must be a decimal string, got {type(value).__name__}"
                )
            try:
                parse_decimal_str(value)
            except (TypeError, ValueError) as error:
                raise PanelError(f"sampling[{key!r}]: {error}") from error
        object.__setattr__(self, "sampling", dict(self.sampling))
        if not isinstance(self.seed_from_attempt, bool):
            raise PanelError("seed_from_attempt must be a boolean")
        if isinstance(self.seed_base, bool) or not isinstance(self.seed_base, int):
            raise PanelError("seed_base must be an integer")
        if not isinstance(self.seed_field, str) or not self.seed_field:
            raise PanelError("seed_field must be a non-empty string")
        if (
            isinstance(self.min_samples, bool)
            or not isinstance(self.min_samples, int)
            or self.min_samples < 1
        ):
            raise PanelError("min_samples must be an integer of at least 1")
        reserved = set(self.sampling)
        if self.seed_from_attempt:
            reserved.add(self.seed_field)
        for probe in self.probes:
            clash = reserved & set(probe.payload)
            if clash:
                raise PanelError(
                    f"probe {probe.probe_id!r}: payload keys {sorted(clash)} are reserved by "
                    "the panel sampling or seed configuration"
                )

    def to_document(self) -> dict[str, IdentityValue]:
        """Return the identity-valid panel document (the plain JSON file form)."""

        return {
            "format": PANEL_FORMAT,
            "name": self.name,
            "probes": [probe.to_document() for probe in self.probes],
            "sampling": dict(self.sampling),
            "seed_from_attempt": self.seed_from_attempt,
            "seed_base": self.seed_base,
            "seed_field": self.seed_field,
            "min_samples": self.min_samples,
        }

    @property
    def digest(self) -> str:
        """Domain-separated SHA-256 of the panel document; probe order is significant."""

        return domain_digest(PANEL_DOMAIN, self.to_document())

    @classmethod
    def from_document(cls, document: Any) -> Panel:
        """Build a panel from its document, rejecting unknown keys and other formats."""

        if not isinstance(document, dict):
            raise PanelError("panel document must be an object")
        unknown = set(document) - _PANEL_KEYS
        if unknown:
            raise PanelError(f"panel document has unknown keys: {sorted(unknown)}")
        found = document.get("format")
        if found != PANEL_FORMAT:
            raise PanelError(f"panel format must be {PANEL_FORMAT!r}, got {found!r}")
        probes = document.get("probes")
        if not isinstance(probes, list):
            raise PanelError("panel probes must be a list")
        return cls(
            name=document.get("name", ""),
            probes=tuple(Probe.from_document(item) for item in probes),
            sampling=document.get("sampling", {}),
            seed_from_attempt=document.get("seed_from_attempt", False),
            seed_base=document.get("seed_base", 0),
            seed_field=document.get("seed_field", "seed"),
            min_samples=document.get("min_samples", 5),
        )

    def probe(self, probe_id: str) -> Probe:
        """Return the probe with ``probe_id``."""

        for probe in self.probes:
            if probe.probe_id == probe_id:
                return probe
        raise KeyError(probe_id)

    def decoded_sampling(self) -> dict[str, float | int]:
        """Return the sampling parameters as numbers for the provider request.

        A decimal string without a fractional part decodes to ``int``; any
        other decodes to ``float``.
        """

        decoded: dict[str, float | int] = {}
        for key, text in self.sampling.items():
            value = parse_decimal_str(text)
            if "." not in text and "e" not in text.lower() and value == value.to_integral_value():
                decoded[key] = int(value)
            else:
                decoded[key] = float(value)
        return decoded

    def request_for(self, probe: Probe, attempt: int) -> dict[str, Any]:
        """Return the request handed to the step callable for one sample.

        This is the only place epicormic touches a request: the identity
        payload pollard records stays equal to ``probe.payload``; the request
        adds the decoded sampling parameters and, when ``seed_from_attempt``
        is set, ``seed_field`` = ``seed_base + attempt``.
        """

        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        request: dict[str, Any] = copy.deepcopy(probe.payload)
        request.update(self.decoded_sampling())
        if self.seed_from_attempt:
            request[self.seed_field] = self.seed_base + attempt
        return request


def load_panel(path: str | Path) -> Panel:
    """Load a plain JSON panel file."""

    text = Path(path).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise PanelError(f"{path}: not valid JSON ({error})") from error
    return Panel.from_document(document)


def save_panel(panel: Panel, path: str | Path) -> None:
    """Write a panel as a readable JSON file whose canonical form is the digested document."""

    document = panel.to_document()
    Path(path).write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
