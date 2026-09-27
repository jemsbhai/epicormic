"""JSON-LD interoperability through the ``[jsonld]`` extra (docs/PLAN.md, D13 and D18).

Two directions. Inbound: a panel may be written as a JSON-LD document
under the epicormic vocabulary; ``panel_from_jsonld`` validates it with a
jsonld-ex shape, strips the JSON-LD keywords, and hands the plain document
to ``Panel``, so the panel digest is the same whichever form the panel
was loaded from. Outbound: ``verdict_to_jsonld`` wraps a recorded verdict
payload (value-free, decimal strings) in a JSON-LD document whose state
carries jsonld-ex provenance annotations and whose opinion is a jsonld-ex
``Opinion``, ``verdict_to_prov_o`` converts that to a PROV-O graph, and
``fuse_opinions`` applies jsonld-ex's cumulative or averaging fusion to
the opinions of several verdicts (several panels, several monitors).

Nothing in the core imports this module; importing it without jsonld-ex
installed raises ``ImportError`` naming the extra.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

try:
    from jsonld_ex import annotate, compute_integrity, validate_document, verify_integrity
    from jsonld_ex.confidence_algebra import Opinion as ExOpinion
    from jsonld_ex.confidence_algebra import averaging_fuse, cumulative_fuse
    from jsonld_ex.owl_interop import to_prov_o
except ImportError as error:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "epicormic.jsonld needs the [jsonld] extra: pip install 'epicormic[jsonld]'"
    ) from error

from . import __version__
from .opinion import Opinion
from .panel import PANEL_FORMAT, Panel, PanelError

__all__ = [
    "EPICORMIC_CONTEXT",
    "EPICORMIC_VOCAB",
    "PANEL_SHAPE",
    "VERDICT_SHAPE",
    "JsonLdError",
    "context_integrity",
    "export_verdict",
    "fuse_opinions",
    "load_panel_jsonld",
    "node_urn",
    "panel_from_jsonld",
    "panel_to_jsonld",
    "validate_verdict_jsonld",
    "verdict_to_jsonld",
    "verdict_to_prov_o",
    "verify_verdict_jsonld",
]

EPICORMIC_VOCAB = "urn:epicormic:vocab:"
EPICORMIC_CONTEXT: dict[str, Any] = {
    "@vocab": EPICORMIC_VOCAB,
    "epicormic": EPICORMIC_VOCAB,
    "jsonld-ex": "http://www.w3.org/ns/jsonld-ex/",
    "prov": "http://www.w3.org/ns/prov#",
}
PANEL_TYPE = "Panel"
PROBE_TYPE = "Probe"
VERDICT_TYPE = "Verdict"
VERDICT_METHOD = (
    "epicormic/verdict/v1: stratified permutation, fisher exact, mann whitney, "
    "cusum, page hinkley, betting eprocess"
)
PANEL_SHAPE: dict[str, Any] = {
    "@type": PANEL_TYPE,
    "format": {"@required": True, "@in": [PANEL_FORMAT]},
    "name": {"@required": True, "@minLength": 1},
    "probes": {"@required": True, "@minCount": 1},
}
PROBE_SHAPE: dict[str, Any] = {
    "@type": PROBE_TYPE,
    "probe_id": {"@required": True, "@minLength": 1},
    "payload": {"@required": True},
}
VERDICT_SHAPE: dict[str, Any] = {
    "@type": VERDICT_TYPE,
    "format": {"@required": True, "@in": ["epicormic/verdict/v1"]},
    "monitor_id": {"@required": True, "@minLength": 1},
    "panel_digest": {"@required": True, "@minLength": 64, "@maxLength": 64},
    "state": {"@required": True},
    "sequence_index": {"@required": True, "@minimum": 1},
    "opinion": {"@minCount": 1, "@shape": {"belief": {"@required": True}}},
}
_KEYWORDS = ("@context", "@type", "@id")


class JsonLdError(ValueError):
    """A JSON-LD document does not describe a valid epicormic object."""


# --- panels -------------------------------------------------------------------------


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in _KEYWORDS}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def panel_from_jsonld(document: Any) -> Panel:
    """Build a ``Panel`` from a JSON-LD panel document under the epicormic vocabulary."""

    if not isinstance(document, dict):
        raise JsonLdError("panel document must be a JSON object")
    if document.get("@type") != PANEL_TYPE:
        raise JsonLdError(f"panel document must have @type {PANEL_TYPE!r}")
    probes = document.get("probes")
    if not isinstance(probes, list) or any(
        not isinstance(p, dict) or p.get("@type") != PROBE_TYPE for p in probes
    ):
        raise JsonLdError(f"every probe must be an object with @type {PROBE_TYPE!r}")
    result = validate_document(document, [PANEL_SHAPE, PROBE_SHAPE])
    if not result.valid:
        problems = "; ".join(f"{e.path}: {e.message}" for e in result.errors)
        raise JsonLdError(f"panel document is invalid: {problems}")
    try:
        return Panel.from_document(_strip(document))
    except PanelError as error:
        raise JsonLdError(f"panel document is invalid: {error}") from error


def load_panel_jsonld(path: str | Path) -> Panel:
    import json

    return panel_from_jsonld(json.loads(Path(path).read_text(encoding="utf-8")))


def panel_to_jsonld(panel: Panel) -> dict[str, Any]:
    """The JSON-LD form of a panel; ``panel_from_jsonld`` gives back the same digest."""

    document: dict[str, Any] = dict(panel.to_document())
    probes = [{"@type": PROBE_TYPE, **probe} for probe in document.pop("probes")]
    return {
        "@context": dict(EPICORMIC_CONTEXT),
        "@type": PANEL_TYPE,
        "@id": f"urn:epicormic:panel:{panel.digest}",
        **document,
        "probes": probes,
    }


# --- verdicts -----------------------------------------------------------------------


def context_integrity() -> str:
    """The jsonld-ex integrity digest of the epicormic context, for pinning."""

    return str(compute_integrity(EPICORMIC_CONTEXT))


def _float(text: Any) -> float:
    return float(text) if text is not None else 0.0


def _ex_opinion(opinion: Mapping[str, Any]) -> ExOpinion:
    belief = _float(opinion.get("belief"))
    disbelief = _float(opinion.get("disbelief"))
    # the payload carries six-significant-digit decimal strings; jsonld-ex
    # requires exact additivity, so uncertainty is re-derived from b and d
    return ExOpinion(belief, disbelief, 1 - belief - disbelief, _float(opinion.get("base_rate")))


def node_urn(node_id: str) -> str:
    """The URN of a pollard node, the form the JSON-LD export links with."""

    return f"urn:pollard:node:{node_id}"


def verdict_to_jsonld(
    payload: Mapping[str, Any],
    *,
    node_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Wrap a recorded verdict payload in a JSON-LD document with jsonld-ex annotations.

    The payload is embedded unchanged (decimal strings and all). ``@id`` is
    the verdict node's URN when ``node_id`` is given, the baseline and
    current window roots are linked as node URNs, ``state`` becomes an
    annotated value whose confidence is the opinion's projected probability
    of drift (attributed to this epicormic version, method naming the tests
    and detectors, never human-verified), ``opinion`` gains a jsonld-ex
    ``Opinion`` beside the recorded numbers, and ``integrity`` is the
    jsonld-ex digest of the rest of the document, checked by
    ``verify_verdict_jsonld``. The export is derived evidence: the committed
    record is the pollard note it cites.
    """

    if payload.get("format") != "epicormic/verdict/v1":
        raise JsonLdError("payload is not an epicormic/verdict/v1 document")
    opinion = payload.get("opinion")
    if not isinstance(opinion, Mapping):
        raise JsonLdError("payload has no opinion")
    ex_opinion = _ex_opinion(opinion)
    document: dict[str, Any] = {
        "@context": dict(EPICORMIC_CONTEXT),
        "@type": VERDICT_TYPE,
    }
    if node_id is not None:
        document["@id"] = node_urn(node_id)
    document.update({k: v for k, v in payload.items() if k not in ("state", "opinion")})
    document["baseline_root_nodes"] = [
        {"@id": node_urn(str(root))} for root in payload.get("baseline_roots", [])
    ]
    if payload.get("current_root") is not None:
        document["current_root_node"] = {"@id": node_urn(str(payload["current_root"]))}
    document["state"] = annotate(
        payload["state"],
        confidence=ex_opinion.projected_probability(),
        source=f"urn:pypi:epicormic:{__version__}",
        method=VERDICT_METHOD,
        extracted_at=created_at,
        human_verified=False,
    )
    document["opinion"] = {**opinion, "subjective_logic": ex_opinion.to_jsonld()}
    document["context_integrity"] = context_integrity()
    document["integrity"] = str(compute_integrity(document))
    return document


def verify_verdict_jsonld(document: Mapping[str, Any]) -> bool:
    """Whether the document's ``integrity`` digest matches the rest of the document."""

    declared = document.get("integrity")
    if not isinstance(declared, str):
        return False
    body = {k: v for k, v in document.items() if k != "integrity"}
    try:
        return bool(verify_integrity(body, declared))
    except ValueError:
        return False


def validate_verdict_jsonld(document: Mapping[str, Any]) -> list[str]:
    """Shape-validate a verdict document; returns the problems (empty when valid)."""

    result = validate_document(dict(document), [VERDICT_SHAPE])
    return [f"{e.path}: {e.message}" for e in result.errors]


def verdict_to_prov_o(document: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a verdict JSON-LD document to a PROV-O graph (jsonld-ex mapping)."""

    graph, report = to_prov_o(dict(document))
    if not report.success:  # pragma: no cover - jsonld-ex reports no failure for these documents
        raise JsonLdError(f"PROV-O conversion failed: {report.errors}")
    return dict(graph)


def fuse_opinions(
    opinions: Iterable[Opinion | Mapping[str, Any]], *, method: str = "cumulative"
) -> Opinion:
    """Fuse the drift opinions of several verdicts with jsonld-ex's operators.

    ``cumulative`` treats the opinions as independent evidence about the
    same proposition (several monitors of one provider), ``averaging`` as
    alternative appraisals of the same evidence. Returns an epicormic
    ``Opinion``.
    """

    converted = []
    for item in opinions:
        if isinstance(item, Opinion):
            converted.append(
                ExOpinion(item.belief, item.disbelief, item.uncertainty, item.base_rate)
            )
        else:
            converted.append(_ex_opinion(item))
    if not converted:
        raise JsonLdError("fuse_opinions needs at least one opinion")
    if method == "cumulative":
        fused = cumulative_fuse(*converted)
    elif method == "averaging":
        fused = averaging_fuse(*converted)
    else:
        raise JsonLdError("method must be 'cumulative' or 'averaging'")
    return Opinion(
        belief=fused.belief,
        disbelief=fused.disbelief,
        uncertainty=fused.uncertainty,
        base_rate=fused.base_rate,
    )


export_verdict = verdict_to_jsonld
