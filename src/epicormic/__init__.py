"""epicormic: provider drift detection and containment for pollard-governed AI systems.

The public API is assembled module by module as the build proceeds (see
docs/PLAN.md, section 18). Until then this package exposes only its version.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("epicormic")
except PackageNotFoundError:  # pragma: no cover - only when run from an unbuilt checkout
    __version__ = "0.0.0"

__all__ = ["__version__"]
