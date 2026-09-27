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
}

__all__ = ["__version__", *sorted(_EXPORTS)]

if TYPE_CHECKING:
    from .panel import PANEL_FORMAT as PANEL_FORMAT
    from .panel import Panel as Panel
    from .panel import PanelError as PanelError
    from .panel import Probe as Probe
    from .panel import load_panel as load_panel
    from .panel import save_panel as save_panel


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'epicormic' has no attribute {name!r}")
    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
