"""Pure-Python statistics for window comparison and sequential monitoring.

Layer A (docs/PLAN.md, section 8.1): ``exact`` (Fisher's exact test for
indicators), ``rank`` (Mann-Whitney U, Cliff's delta, Hodges-Lehmann shift
for scalars), ``permutation`` (two-sample and stratified permutation tests),
and ``multiplicity`` (Benjamini-Hochberg and Holm). Layer B (section 8.2):
``sequential`` (CUSUM, Page-Hinkley, the betting e-process, the sign
transform). Nothing here imports NumPy or SciPy; SciPy is used only by the
test suite as a cross-check.
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
from .sequential import (
    CusumState,
    EProcessState,
    PageHinkleyState,
    SignStream,
    betting_eprocess,
    clipped_rate,
    cusum,
    page_hinkley,
    sign_transform,
)

__all__ = [
    "CusumState",
    "EProcessState",
    "FisherResult",
    "PageHinkleyState",
    "PermutationResult",
    "RankResult",
    "SignStream",
    "StratifiedResult",
    "benjamini_hochberg",
    "betting_eprocess",
    "cliffs_delta",
    "clipped_rate",
    "cusum",
    "fisher_exact",
    "hodges_lehmann",
    "holm",
    "indicator_test",
    "mann_whitney",
    "page_hinkley",
    "permutation_test",
    "rejected",
    "sign_transform",
    "stratified_permutation_test",
    "u_statistic",
]
