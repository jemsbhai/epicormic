"""epicormic: provider drift detection and containment for pollard-governed AI systems.

Public names are imported lazily on first access so that importing
``epicormic`` stays cheap and optional extras are never imported by
accident. The ``TYPE_CHECKING`` block gives static checkers the same names.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

try:
    __version__ = version("epicormic")
except PackageNotFoundError:  # pragma: no cover - only when run from an unbuilt checkout
    __version__ = "0.0.0"

_EXPORTS: dict[str, str] = {
    "PANEL_FORMAT": "epicormic.panel",
    "Panel": "epicormic.panel",
    "PanelError": "epicormic.panel",
    "Probe": "epicormic.panel",
    "load_panel": "epicormic.panel",
    "save_panel": "epicormic.panel",
    "WINDOW_FORMAT": "epicormic.window",
    "WindowError": "epicormic.window",
    "WindowReport": "epicormic.window",
    "SampleEvent": "epicormic.window",
    "observe": "epicormic.window",
    "Observation": "epicormic.observation",
    "WindowView": "epicormic.observation",
    "find_window_headers": "epicormic.observation",
    "read_window": "epicormic.observation",
    "Drift": "epicormic.mock",
    "MockProvider": "epicormic.mock",
    "BUILTIN_SCORERS": "epicormic.scorers",
    "DEFAULT_SCORER_NAMES": "epicormic.scorers",
    "Reference": "epicormic.scorers",
    "ScoreTable": "epicormic.scorers",
    "Scorer": "epicormic.scorers",
    "build_references": "epicormic.scorers",
    "resolve_scorers": "epicormic.scorers",
    "score_window": "epicormic.scorers",
}

__all__ = ["__version__", *sorted(_EXPORTS)]

if TYPE_CHECKING:
    from .mock import Drift as Drift
    from .mock import MockProvider as MockProvider
    from .observation import Observation as Observation
    from .observation import WindowView as WindowView
    from .observation import find_window_headers as find_window_headers
    from .observation import read_window as read_window
    from .panel import PANEL_FORMAT as PANEL_FORMAT
    from .panel import Panel as Panel
    from .panel import PanelError as PanelError
    from .panel import Probe as Probe
    from .panel import load_panel as load_panel
    from .panel import save_panel as save_panel
    from .scorers import BUILTIN_SCORERS as BUILTIN_SCORERS
    from .scorers import DEFAULT_SCORER_NAMES as DEFAULT_SCORER_NAMES
    from .scorers import Reference as Reference
    from .scorers import Scorer as Scorer
    from .scorers import ScoreTable as ScoreTable
    from .scorers import build_references as build_references
    from .scorers import resolve_scorers as resolve_scorers
    from .scorers import score_window as score_window
    from .window import WINDOW_FORMAT as WINDOW_FORMAT
    from .window import SampleEvent as SampleEvent
    from .window import WindowError as WindowError
    from .window import WindowReport as WindowReport
    from .window import observe as observe


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'epicormic' has no attribute {name!r}")
    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
