# Source reliability (Prompt 117)

ACR records source class as provenance evidence, not as a declaration that a
claim is true. Schema version 67 adds nullable `source_class` to canonical
memory rows. The closed vocabulary is:

- `direct_observation`
- `repository`
- `official_documentation`
- `primary_research`
- `trusted_documentation`
- `secondary_source`
- `community_report`
- `model_inference`

`source_type` remains a free-form description of the concrete producer or
adapter, and `source_id` remains its concrete identifier. Source class does not
replace either field. Existing rows migrate with a null class because their
free-form source type is insufficient evidence for automatic reclassification.

## Retrieval prior

The default retriever gives source class a deliberately small, inspectable
weight of 0.03. Initial deterministic priors are:

| Source class | Prior |
| --- | ---: |
| direct observation | 0.85 |
| repository | 0.80 |
| official documentation | 0.85 |
| primary research | 0.80 |
| trusted documentation | 0.75 |
| secondary source | 0.60 |
| community report | 0.45 |
| model inference | 0.25 |
| unclassified | 0.50 |

These are conservative policy defaults, not measured probabilities of truth.
They influence ranking alongside relevance, scope, freshness, stored
confidence, utility, and importance. A high-class prior cannot confirm a
candidate, override a low confidence value, resolve a contradiction, bypass a
refresh requirement, grant authority, or suppress explicit invalidity.
Operator-supplied retrieval configurations may replace the priors, but values
remain bounded between zero and one and the selected value remains visible in
the score breakdown.

The retrieval cache identity is versioned so results produced under the old
free-form `source_type` mapping cannot be replayed after this change.

## Interfaces

Python writes use `SourceClass`; CLI writes use `--source-class`. The safe API
and memory inspector expose the nullable class so operators can distinguish an
explicit class from missing evidence.

```powershell
python -m acr_runtime.cli --db .acr/acr.db memory add semantic `
  "The repository uses schema version 67" --scope project:runtime `
  --source-class repository --source-type git-checkout `
  --source-id f4c1ec1 --evidence commit:f4c1ec1
```

## Research boundary

W3C PROV distinguishes a primary source from more general derivation while
keeping provenance separate from truth. Reliability-aware RAG research reports
that heterogeneous sources can benefit from reliability-sensitive retrieval,
and provenance-based fact checking evaluates whether output is supported by
retrieved context:

- <https://www.w3.org/TR/prov-o/>
- <https://aclanthology.org/2025.emnlp-main.1738/>
- <https://aclanthology.org/2024.emnlp-industry.97/>

ACR has not reproduced those papers' results. It does not implement learned
source estimation, cross-source voting, or factuality checking. The only
adaptation is a bounded metadata vocabulary and a low-weight deterministic
prior. A class-specific accuracy benchmark is required before changing the
priors or claiming quality improvement.

## Limitations

- Classification is caller supplied and is not cryptographically attested.
- Source class is too coarse to measure a specific publisher, repository, or
  observation process.
- Official and primary sources can still be wrong, outdated, incomplete, or
  irrelevant.
- An unclassified source receives a neutral prior, not an inferred class.
- No source-level outcome calibration exists yet.
