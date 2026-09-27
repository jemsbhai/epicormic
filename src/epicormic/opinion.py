"""Subjective Logic opinion about drift (docs/PLAN.md, section 10.1).

An opinion ``(b, d, u, a)`` summarizes evidence strength and evidence mass
about the proposition "the provider has drifted on this panel relative to
the baseline". It is derived from the two-sided e-value ``E`` of the
sequential detectors and the number ``m`` of current observations they
consumed: ``P = E a / (E a + (1 - a))`` reads the e-value as a conservative
Bayes factor against the null (the e-posterior interpretation, Grünwald
2023), ``u = W / (m + W)`` with the non-informative prior weight ``W``, and
``b = (1 - u) P``, ``d = (1 - u)(1 - P)``. The opinion is a derived summary
for downstream fusion; it never feeds the state rules or the levers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["Opinion", "opinion_from_evidence", "opinion_from_log_evidence", "vacuous"]


@dataclass(frozen=True)
class Opinion:
    belief: float
    disbelief: float
    uncertainty: float
    base_rate: float

    def __post_init__(self) -> None:
        for name, value in (
            ("belief", self.belief),
            ("disbelief", self.disbelief),
            ("uncertainty", self.uncertainty),
            ("base_rate", self.base_rate),
        ):
            if not isinstance(value, float | int) or isinstance(value, bool):
                raise TypeError(f"{name} must be a number")
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must lie in [0, 1], got {value!r}")
        if abs(self.belief + self.disbelief + self.uncertainty - 1) > 1e-9:
            raise ValueError("belief, disbelief, and uncertainty must sum to 1")

    @property
    def projected_probability(self) -> float:
        return self.belief + self.base_rate * self.uncertainty


def vacuous(base_rate: float = 0.5) -> Opinion:
    """The opinion with no evidence: uncertainty 1."""

    return Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0, base_rate=base_rate)


def opinion_from_log_evidence(
    log_e_value: float,
    observations: int,
    *,
    base_rate: float = 0.5,
    prior_weight: float = 2.0,
) -> Opinion:
    """Opinion from the log of a two-sided e-value and the observations consumed.

    ``log_e_value`` may be ``inf`` (overwhelming evidence). With
    ``observations == 0`` the opinion is vacuous regardless of the e-value.
    """

    if isinstance(observations, bool) or not isinstance(observations, int) or observations < 0:
        raise ValueError("observations must be a non-negative integer")
    if not 0 < base_rate < 1:
        raise ValueError("base_rate must lie strictly between 0 and 1")
    if prior_weight <= 0:
        raise ValueError("prior_weight must be positive")
    if math.isnan(log_e_value) or log_e_value == -math.inf:
        raise ValueError("log_e_value must be finite or +inf")
    if observations == 0:
        return vacuous(base_rate)
    # P = E a / (E a + 1 - a) = sigmoid(log E + logit a), computed without overflow
    logit = log_e_value + math.log(base_rate) - math.log1p(-base_rate)
    if logit == math.inf:
        probability = 1.0
    elif logit >= 0:
        probability = 1 / (1 + math.exp(-logit))
    else:
        exp_logit = math.exp(logit)
        probability = exp_logit / (1 + exp_logit)
    uncertainty = prior_weight / (observations + prior_weight)
    mass = 1 - uncertainty
    return Opinion(
        belief=mass * probability,
        disbelief=mass * (1 - probability),
        uncertainty=uncertainty,
        base_rate=base_rate,
    )


def opinion_from_evidence(
    e_value: float,
    observations: int,
    *,
    base_rate: float = 0.5,
    prior_weight: float = 2.0,
) -> Opinion:
    """Opinion from a two-sided e-value (positive, possibly ``inf``)."""

    if math.isnan(e_value) or e_value <= 0:
        raise ValueError("e_value must be positive")
    return opinion_from_log_evidence(
        math.log(e_value) if e_value != math.inf else math.inf,
        observations,
        base_rate=base_rate,
        prior_weight=prior_weight,
    )
