# Prompt 63: confidence calibration

ACR retains a confidence forecast separately from its eventual binary outcome,
then builds deterministic reliability curves for memory use, model routing, and
independent evaluation. Schema 43 adds one local ledger,
`confidence_predictions`.

## Outcome boundaries

- Memory confidence is snapshotted when a memory enters a compiled context.
  Conclusive attribution later resolves the forecast: contribution on a
  successful task is success; ignored, misleading, or failed contribution is
  failure. Uncertain attribution stays unresolved.
- Routing compares `RouteAttempt.confidence` with
  `verification_passed`. It deliberately does not use the route's derived
  `success`, because that field already includes the confidence threshold.
- Evaluation is tracked only when the caller supplies
  `--predicted-confidence` before the deterministic panel runs. A panel score
  is a rubric score, not an inferred probability, and is never relabelled as
  confidence. Its cohort hashes the ordered judge identities, judge kinds, and
  pass threshold so unlike policies are not silently pooled.

Predictions are unique within their domain and source. Resolution is one-way:
an unresolved forecast may receive one outcome, while replay or contradiction
is rejected. Memory confidence changes after context compilation cannot alter
the retained forecast.

## Reports and interpretation

```powershell
python -m acr_runtime.cli --db .acr/acr.db calibration report memory
python -m acr_runtime.cli --db .acr/acr.db calibration report routing `
  --group coding --bins 10
python -m acr_runtime.cli --db .acr/acr.db calibration interpret routing 0.9 `
  --group coding --minimum-samples 20
python -m acr_runtime.cli --db .acr/acr.db evaluate run case.json `
  --predicted-confidence 0.85
```

Reports contain an equal-width reliability curve, bin counts, mean confidence,
actual success rate, a 95 percent Wilson interval, absolute bin gap, binned
expected calibration error, maximum calibration error, and Brier score.
Unresolved forecasts are counted but excluded from outcome metrics. Confidence
`1.0` belongs to the final bin.

Interpretation is advisory. When the matching bin reaches the declared minimum
sample count, ACR returns its empirical success rate and uncertainty interval as
the interpreted confidence. Otherwise it returns
`insufficient_evidence` and no adjusted confidence. It never mutates memory
confidence, routing thresholds, model choice, or evaluation policy.

## Statistical limits

The fixed curve is a transparent first version, not a claim of complete
calibration. Reliability diagrams and binned ECE are standard diagnostics, but
the result depends on binning and sample size. Empty bins remain visible, and
every populated bin reports its count and uncertainty interval. Brier score
also measures forecast refinement, so it complements rather than replaces the
curve.

Primary references:

- Guo et al., [On Calibration of Modern Neural
  Networks](https://proceedings.mlr.press/v70/guo17a.html), ICML 2017.
- Kumar, Liang, and Ma, [Verified Uncertainty
  Calibration](https://proceedings.neurips.cc/paper/2019/hash/f8c0c968632845cd133308b1a494967f-Abstract.html),
  NeurIPS 2019.
- Roelofs et al., [Mitigating Bias in Calibration Error
  Estimation](https://proceedings.mlr.press/v151/roelofs22a.html), AISTATS 2022.
- Gneiting and Raftery, [Strictly Proper Scoring Rules, Prediction, and
  Estimation](https://doi.org/10.1198/016214506000001437), JASA 2007.

No report is used as automatic policy evidence. A policy change still requires
an explicit governed decision and independent verification.
