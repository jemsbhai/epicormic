# Statistics

This document derives every test epicormic runs and states what each
guarantees. Layer A (window comparison) is implemented in `epicormic.stats`
and tested against SciPy; Layer B (sequential monitoring) is specified here
and in docs/PLAN.md section 8.2 and arrives with `epicormic.stats.sequential`.
Symbols: `B` is the pooled baseline sample of one probe and scorer, `C` the
current sample, `n_b` and `n_c` their sizes, `N = n_b + n_c`.

## 1. The question being asked

For a fixed probe request `r`, let `P_w(r)` be the distribution of the
provider's observable result during window `w`. Drift between windows `b`
and `c` is any difference between `P_b(r)` and `P_c(r)` in a monitored
functional (docs/PLAN.md, section 4). Every test below is a test of the
null hypothesis that the samples of `b` and `c` are exchangeable within
each probe. Nothing assumes the provider is deterministic, and nothing
pools raw values across probes, because probes differ in location by
construction.

## 2. Indicator scorers: Fisher's exact test

An indicator scorer (normalized match, exact match, refusal, truncation)
yields ones and zeros. With `a` ones among `n_b` baseline values and `c`
ones among `n_c` current values, the 2 by 2 table `((a, n_b - a), (c,
n_c - c))` has fixed margins under the null, and the count `a` follows the
hypergeometric distribution

    P(a = k) = C(n_b, k) C(n_c, m - k) / C(N, m),   m = a + c.

The two-sided p-value is the sum of `P(a = k)` over every `k` whose
probability does not exceed the observed one by more than a relative
tolerance of `1e-7`, the convention of R's `fisher.test`. Probabilities are
computed in log space with `lgamma`. Effects reported: the risk difference
`c / n_c - a / n_b` and the log odds ratio with the Haldane-Anscombe
correction (0.5 added to every cell), which keeps it finite when a cell is
empty. Reference: Fisher (1935).

Implementation: `epicormic.stats.exact.fisher_exact`, `indicator_test`.
Cross-checked against `scipy.stats.fisher_exact` to `1e-10` over 400
random tables.

## 3. Scalar scorers: Mann-Whitney U, Cliff's delta, Hodges-Lehmann

For a scalar scorer (output characters, tokens, latency, cost, energy,
tool-call Jaccard), the statistic is

    U = #{(c, b) : c > b} + 0.5 #{(c, b) : c = b},

the number of current-over-baseline pairs with ties counted half,
computed from midranks as `U = R_c - n_c (n_c + 1) / 2` where `R_c` is the
rank sum of the current sample in the pooled midranking.

Exact p-value. When both samples have at most 20 values and there are no
ties, the null distribution of `U` is computed by the counting recurrence

    count(m, n, u) = count(m - 1, n, u - n) + count(m, n - 1, u),

with `count(0, n, 0) = count(m, 0, 0) = 1`: the largest pooled value is
either a current value, contributing `n` pairs, or a baseline value,
contributing none. The two-sided p-value is `2 P(U <= min(U, n_b n_c - U))`
by symmetry of the null distribution about `n_b n_c / 2`, capped at 1, and
equal to 1 when `U = n_b n_c / 2`. A brute-force enumeration test confirms
the recurrence on small samples independently of SciPy.

Normal approximation. With ties or larger samples,

    z = (|U - n_b n_c / 2| - 0.5) / sigma,
    sigma^2 = n_b n_c / 12 * ((N + 1) - sum_t (t^3 - t) / (N (N - 1))),

where `t` runs over the sizes of tie groups, and the two-sided p-value is
`erfc(z / sqrt 2)`. When `sigma = 0` (all values tied) the p-value is 1.
Reference: Mann and Whitney (1947).

Effects. Cliff's delta `delta = (2U - n_b n_c) / (n_b n_c)` lies in
`[-1, 1]` and is the probability that a current value exceeds a baseline
value minus the reverse (Cliff, 1993). For indicator scorers it reduces
algebraically to the rate difference, which is why one pooled statistic
serves both scorer kinds (section 5). The Hodges-Lehmann shift is the
median of all `n_b n_c` pairwise differences `c - b`, in the scorer's units
(Hodges and Lehmann, 1963).

Implementation: `epicormic.stats.rank.mann_whitney`, `cliffs_delta`,
`hodges_lehmann`, `u_statistic`, `midranks`. Cross-checked against
`scipy.stats.mannwhitneyu` with `method="exact"` and with
`method="asymptotic", use_continuity=True` to `1e-12`.

## 4. Two-sample permutation test

For any statistic `T(B, C)`, the permutation distribution under
exchangeability is obtained by reassigning the pooled values to the two
groups. When the number of assignments `C(N, n_c)` is at most 20,000 the
distribution is enumerated and the p-value is the exact fraction of
assignments with `|T*| >= |T|`. Otherwise `R` seeded random reassignments
are drawn and

    p = (1 + #{|T*| >= |T|}) / (R + 1),

the estimator of Phipson and Smyth (2010), which counts the observed
assignment among the permutations and therefore never reports zero. The
seed and `R` are recorded with the result. The default statistic is the
difference in means; any callable of the two samples can be used.

Implementation: `epicormic.stats.permutation.permutation_test`.

## 5. The pooled test: stratified permutation on mean Cliff's delta

The primary per-scorer test across a panel. With probes as strata, the
statistic is

    T = (1 / P) sum_p delta_p,

the mean over the `P` contributing probes of the per-probe Cliff's delta.
Under the null of exchangeability within each probe, labels are permuted
within each probe only: the pooled midranks of a probe are computed once,
each permutation shuffles them, and `U` is read off the partial sum of the
current group's ranks. This keeps the cost linear in the sample size per
probe and per permutation. The two-sided p-value is the Monte Carlo
estimator of section 4 with `R` permutations (default 2000).

Why this and not a pooled comparison of raw values: probes have different
locations by design (different prompts produce different lengths,
latencies, and match rates), so pooling raw values would confound probe
identity with window identity. Permuting within probe removes that
confound exactly. Two tests make this concrete: with identical groups
inside every probe and probe locations at 0, 100, and minus 50, `T` is
exactly 0 and `p` is exactly 1; and the null rejection rate over 400
seeded replications at level 0.05 falls in `[0.02, 0.08]`.

Probes with fewer than `n_min` values in either window are excluded from
`T` by the caller (the verdict layer) and listed in the evidence.

Implementation: `epicormic.stats.permutation.stratified_permutation_test`.

## 6. Multiplicity

A window produces one test per (probe, scorer) and one pooled test per
scorer. Across the (probe, scorer) family, Benjamini-Hochberg controls the
false discovery rate at `q`: with sorted p-values `p_(1) <= ... <= p_(m)`,
the adjusted value is `min_{j >= i} (m / j) p_(j)`, capped at 1 (Benjamini
and Hochberg, 1995). Across the pooled per-scorer family, Holm controls
the family-wise error rate at `alpha`: the adjusted value is
`max_{j <= i} (m - j + 1) p_(j)`, capped at 1 (Holm, 1979). Adjusted values
are never below the raw value (a one-ulp rounding of `p m / m` is clamped)
and are monotone in the raw ordering; a test is rejected when its adjusted
value is at most the level. Holm is at least as conservative as
Benjamini-Hochberg on every input.

Implementation: `epicormic.stats.multiplicity.benjamini_hochberg`, `holm`,
`rejected`.

## 7. Layer B: sequential monitoring (specified, not yet implemented)

The monitor chain gives an ordered sequence of current windows compared
against one pooled baseline. Two detectors run on the per-window pooled
statistic and one runs on the per-observation stream.

CUSUM (Page, 1954), two-sided on `z_w = T_w` per scorer:

    S+_w = max(0, S+_(w-1) + z_w - k),   S-_w = max(0, S-_(w-1) - z_w - k),

alarm when `max(S+_w, S-_w) >= h`. Initial defaults `k = 0.1`, `h = 0.4` on
the Cliff's delta scale, to be calibrated in EXP-C.

Page-Hinkley (Hinkley, 1971) on `z_w` with running mean `m_w`:
`PH_w = sum_(i <= w) (z_i - m_i - delta)`, `M_w = min_(i <= w) PH_i`, alarm
when `PH_w - M_w >= lambda`, with the mirrored form for decreases. Initial
defaults `delta = 0.05`, `lambda = 0.5`.

Betting e-process, anytime-valid. For an indicator scorer, order every
current observation across windows by (window, probe index, attempt) and
let `x_i` in `{0, 1}`, with `mu0` the pooled baseline rate. The upward
wealth is `K+_i = K+_(i-1) (1 + lam (x_i - mu0))` with `lam` in
`(0, 1 / mu0)`, the downward wealth is `K-_i = K-_(i-1) (1 - lam (x_i -
mu0))` with `lam` in `(0, 1 / (1 - mu0))`, each averaged over a fixed grid
of fractions of its maximal `lam` (an average of e-processes is an
e-process), and the two-sided e-value is `E_i = (K+_i + K-_i) / 2`. Under
the null `E[x_i] = mu0`, every `K` is a nonnegative supermartingale with
initial value 1, so by Ville's inequality (Ville, 1939)
`P(sup_i E_i >= 1 / alpha) <= alpha`: alarming when `E_i >= 1 / alpha` has
type I error at most `alpha` at any stopping time, however many windows
are inspected. This is the betting construction of Waudby-Smith and
Ramdas (2024); the game-theoretic framing is surveyed by Ramdas, Grünwald,
Vovk, and Shafer (2023). For scalar scorers the stream is the per-probe
sign transform `x_i = 1[score_i > baseline median of that probe]` with
ties excluded and counted, and `mu0 = 0.5`. Caveat: `mu0` for indicators
is estimated from the baseline; the guarantee is exact only when the
baseline is large relative to the current stream, and the verdict records
the baseline sample count. A two-sample sequential test that removes the
plug-in is the v0.2 research item.

The e-process state is recomputed from the store on every verdict, never
cached, so a verdict is a pure function of the observations and the
configuration.

## 8. The opinion (specified, not yet implemented)

Each verdict carries a Subjective Logic opinion `(b, d, u, a)` about the
proposition that the provider has drifted on the panel, derived from the
average two-sided e-value `E` across scorers and the number `m` of current
observations the e-processes consumed: `P = E a / (E a + (1 - a))` reads
the e-value as a conservative Bayes factor against the null, the
e-posterior interpretation (Grünwald, 2023); `u = W / (m + W)` with the
non-informative prior weight `W = 2`; `b = (1 - u) P`, `d = (1 - u)(1 -
P)`. The opinion is a derived summary for downstream fusion and never an
input to the state rules (docs/PLAN.md, section 10.1).

## References

- Benjamini, Y. and Hochberg, Y. (1995). Controlling the false discovery
  rate: a practical and powerful approach to multiple testing. Journal of
  the Royal Statistical Society, Series B, 57(1), 289-300.
- Cliff, N. (1993). Dominance statistics: ordinal analyses to answer
  ordinal questions. Psychological Bulletin, 114(3), 494-509.
- Fisher, R. A. (1935). The Design of Experiments. Oliver and Boyd.
- Grünwald, P. D. (2023). The e-posterior. Philosophical Transactions of
  the Royal Society A, 381(2247), 20220146. doi:10.1098/rsta.2022.0146.
- Hinkley, D. V. (1971). Inference about the change-point from cumulative
  sum tests. Biometrika, 58(3), 509-523.
- Hodges, J. L. and Lehmann, E. L. (1963). Estimates of location based on
  rank tests. Annals of Mathematical Statistics, 34(2), 598-611.
- Holm, S. (1979). A simple sequentially rejective multiple test
  procedure. Scandinavian Journal of Statistics, 6(2), 65-70.
- Mann, H. B. and Whitney, D. R. (1947). On a test of whether one of two
  random variables is stochastically larger than the other. Annals of
  Mathematical Statistics, 18(1), 50-60.
- Page, E. S. (1954). Continuous inspection schemes. Biometrika, 41(1/2),
  100-115.
- Phipson, B. and Smyth, G. K. (2010). Permutation p-values should never
  be zero: calculating exact p-values when permutations are randomly
  drawn. Statistical Applications in Genetics and Molecular Biology, 9(1),
  Article 39. doi:10.2202/1544-6115.1585.
- Ramdas, A., Grünwald, P., Vovk, V., and Shafer, G. (2023). Game-theoretic
  statistics and safe anytime-valid inference. Statistical Science, 38(4),
  576-601.
- Ville, J. (1939). Étude critique de la notion de collectif.
  Gauthier-Villars.
- Waudby-Smith, I. and Ramdas, A. (2024). Estimating means of bounded
  random variables by betting. Journal of the Royal Statistical Society,
  Series B, 86(1), 1-27. doi:10.1093/jrsssb/qkad009.

The three references with DOIs were verified against the publishers'
pages on 2026-09-27; the classical references are cited from their
standard bibliographic records and are to be re-verified when the paper's
bibliography is assembled.
