"""Pure-Python statistics for window comparison and sequential monitoring.

Layer A (docs/PLAN.md, section 8.1): ``exact`` (Fisher's exact test for
indicators), ``rank`` (Mann-Whitney U, Cliff's delta, Hodges-Lehmann shift
for scalars), ``permutation`` (two-sample and stratified permutation tests),
and ``multiplicity`` (Benjamini-Hochberg and Holm). Layer B (section 8.2)
arrives in ``sequential``. Nothing here imports NumPy or SciPy; SciPy is
used only by the test suite as a cross-check.
"""

from __future__ import annotations

from .exact import FisherResult, fisher_exact, indicator_test
from .multiplicity import benjamini_hochberg, holm, rejected
from .permutation import (
    PermutationResult,
    StratifiedResult,
    permutation_test,
    stratified_permutation_test,
)
from .rank import RankResult, cliffs_delta, hodges_lehmann, mann_whitney, u_statistic

__all__ = [
    "FisherResult",
    "PermutationResult",
    "RankResult",
    "StratifiedResult",
    "benjamini_hochberg",
    "cliffs_delta",
    "fisher_exact",
    "hodges_lehmann",
    "holm",
    "indicator_test",
    "mann_whitney",
    "permutation_test",
    "rejected",
    "stratified_permutation_test",
    "u_statistic",
]
