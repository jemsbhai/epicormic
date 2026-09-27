"""pytest plugin entry point for epicormic.

Registered under the ``pytest11`` entry point group. The ``epicormic_state``
fixture and the ``epicormic_gate`` marker specified in docs/PLAN.md
(section 12.1) are added in Phase 7. Until then the module registers
nothing, so installing epicormic never changes how pytest behaves in other
projects. epicormic's own suite disables the plugin with ``-p no:epicormic``.
"""

from __future__ import annotations
