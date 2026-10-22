# Consensus follow-up: model challengers, crypto signals and valid comparisons

Date: 2026-10-18. Scope: focused supplementary research, not implementation or automatic architecture/model admission. The original [17-source review](ACADEMIC_EVIDENCE.md) remains the starting point. This supplement adds seven fetched studies across five searches; [metadata, queries, ranks, corrections and read depth](consensus/FOLLOWUP_RECORDS.json) are retained without full abstracts.

## What materially changes the research shortlist

- Keep GBM as a strong baseline, but add **a pinned TabPFN-2 experiment** to the candidate investigation list if its checkpoint rights/runtime are admitted. Generic tabular evidence has advanced since the 2022 comparisons; neither result establishes crypto-return skill.
- Treat **dated-futures basis** as a specific later signal hypothesis, not a synonym for perpetual funding. Evaluate each horizon and instrument class independently.
- Require a **statistical-method applicability record**. The original Hansen SPA framework is not automatically valid for expanding-window parameter refits.
- For LLM-derived news features, test identifier masking and retain model/time provenance. Masking is not a certificate of contamination removal.

These are recommendations for the existing PI07–PI11 research work, not additional implementation milestones. M1's hours/days/weeks priority, sealed final data and early trial accounting remain unchanged.

## Popularity is a discovery aid

Citation counts below are **Consensus snapshots**, not independently audited counts or quality rankings. Search relevance is not bibliometric rank. Low-citation studies were deliberately retained where they test an important limitation. Different provider indexes and paper versions produce different counts.

| Fetched study | Provider citations | Publication/version actually verified |
|---|---:|---|
| Liu, Tsyvinski & Wu — Common Risk Factors in Cryptocurrency | 514 | Published *Journal of Finance* 77(2), 1133–1177, 2022; fetched index record is the 2019 working paper. |
| Jung & Park — Common factors in the returns on cryptocurrencies | 1 | *Finance Research Letters* 65, 105485, 2024; publisher metadata identifies Haerang Park. |
| Chi et al. — An empirical investigation on risk factors in cryptocurrency futures | 5 | *Journal of Futures Markets* 43, 1161–1180, 2023. |
| Grinsztajn, Oyallon & Varoquaux — tree models versus deep learning | 2,242 | NeurIPS 2022 Datasets and Benchmarks; official PDF inspected. |
| Hollmann et al. — small-data tabular foundation model | 1,611 | *Nature* 637, 319–326, 2025; TabPFN-2 study. |
| Glasserman & Lin — GPT sentiment look-ahead assessment | 53 | arXiv:2309.17322v1, September 2023; preprint in this review. |
| Hansen — A Test for Superior Predictive Ability | 1,285 | *Journal of Business & Economic Statistics* 23(4), 365–380, 2005. |

## Evidence and transfer limits

### 1. Crypto factors are useful controls, not ready-made price forecasts

The published Liu/Tsyvinski/Wu paper studies market, size and momentum as explanations of cross-sectional expected-return patterns. This supports simple factor/characteristic controls before complex models; it does not establish per-asset direction forecasting. Consensus's 2019 record describes nine successful characteristics/strategies; the final 2022 abstract describes ten. Do not combine their samples or numerical claims. Final abstract/metadata were read; full final methods were not verified. [Published DOI](https://doi.org/10.1111/jofi.13119).

Jung/Park find substantial common return variation beyond that representation. This concerns realized variation/co-movement, which differs from explaining expected-return spreads; it is not a clean contradiction or exact replication. The primary indexed abstract/conclusion were available, full methods were not. Consensus misidentifies Park's name; publisher/university metadata identifies **Haerang Park**. [Publisher](https://www.sciencedirect.com/science/article/pii/S1544612324005154), [university record](https://scholar.korea.ac.kr/item/6b645359-cc23-4a30-9f3a-d8cb427caf10).

### 2. A concrete daily signal hypothesis: spot–dated-futures basis

Chi et al. use historical OKEx spot and current-quarter futures, resampled to daily/weekly/monthly intervals. Basis is stronger than momentum in their comparisons, with weaker evidence at longer holding periods. The sample is small, venue-specific and historical; displayed lookbacks were selected from several choices. These choices belong in a trial ledger. The published methods/results were inspected. This motivates a controlled ablation, not an expected return estimate for Scryntic. [Primary paper](https://onlinelibrary.wiley.com/doi/10.1002/fut.22425).

### 3. Strong tabular baselines, with a newer challenger

Grinsztajn et al. compare 45 heterogeneous tabular datasets with controlled tuning effort and medium-sized training sets. Tree ensembles are strong relative to the neural methods tested, particularly when compute cost is counted. This is neither a chronological financial benchmark nor a verdict on every subsequent architecture. [Official proceedings paper](https://papers.nips.cc/paper_files/paper/2022/file/0378c7692da36807bdec87ab043cdadc-Paper-Datasets_and_Benchmarks.pdf).

Hollmann et al. report strong TabPFN-2 performance for small/medium tables, including regression, using synthetic pretraining. This justifies a challenger experiment; row order does not supply temporal semantics. Benchmark limits, distribution shift and the exact checkpoint matter. The Nature text was inspected through indexed primary passages; direct article retrieval failed. No Scryntic runtime or weights were tested. [Nature article](https://www.nature.com/articles/s41586-024-08328-6).

### 4. News/LLM bias can have competing directions

Glasserman/Lin compare original and anonymized US-equity headlines. In their historical sample, anonymization can improve results; general company-knowledge distraction is distinct from memorized subsequent outcomes. The study does not show every reported LLM gain is leakage or that masking guarantees independence. It is a GPT-3.5-era preprint with a stated training-cutoff assumption; transfer to current models/crypto is unproven. Primary v1 methods and selected results were read. [Version read](https://arxiv.org/html/2309.17322v1).

### 5. Important statistical constraint: original SPA is not universal

Hansen tests a benchmark against a declared family under explicit loss/dependence assumptions. The original framework excludes recursive parameter estimation; fixed conditional and rolling schemes are discussed. An omnibus rejection does not identify a uniquely best candidate. The relevant published text was available through indexed paper passages; direct publisher/PDF retrieval failed and proofs/implementation were not fully reviewed. **Do not apply an unchanged 2005 SPA recipe to expanding-window refits.** [Published DOI](https://doi.org/10.1198/073500105000000063), [indexed original-paper copy](https://citeseerx.ist.psu.edu/document?doi=83d10adb98319ab3fcc15dfa44dd3f7ff80e4bda&repid=rep1&type=pdf).

## Proposed experiment and interface refinements

| Existing work | Refinement / validation requirement |
|---|---|
| PI07 features | Compare lagged market/size/momentum controls and factor-relative errors. A realized market factor over the target interval is ex-post attribution, **not** an available predictor. Use PIT universe/capitalization and train-only factor fitting. |
| PI06 / PI07 derivatives | A basis experiment needs spot/future identities, expiry/roll/unit policy and both legs available by the same origin. Separate carry/basis from realized/announced perpetual funding. Add only after M1 and data-rights/quality justification. |
| PI09 / PI11 model comparisons | Same feature snapshot, origin/labels, trial budget and evaluation periods for regularized models, boosting and a pinned tabular-foundation challenger. Count inference/startup/memory as well as fitting/tuning time. |
| PI08 / PI10 evaluation | Register candidate family, loss, fit scheme (fixed/rolling/expanding), dependence/overlap assumptions, bootstrap settings and multiple-testing policy. Validate the selected test under that scheme; no SPA p-value becomes a universal promotion certificate. |
| Later news features | Compare original/masked identifiers plus simple lexical/non-LLM controls; retain exact model/tokenizer/prompt/output and availability/rights. Frozen future forward evaluation remains necessary. No private/licensed text goes to a public research/model service without permission. |

## TabPFN admission caveat

The 2025 paper is about **version 2**, not an unpinned current default. The official repository separates code and model-weight licenses; its README distinguishes version-2 attribution terms from non-commercial later weights. The [version-2-era license](https://raw.githubusercontent.com/PriorLabs/TabPFN/49394b053a6759cfe68e90c21a2d51c31b396768/LICENSE) and [official current license guidance](https://github.com/PriorLabs/TabPFN) were checked. Exact weight hashes, permitted deployment, dependencies, offline provisioning, resource use and numerical behavior remain **unqualified**. No model was downloaded, installed, authenticated or admitted.

## Collection limits and actual changes

The user reported 12 remaining uses. Collection conservatively stopped at **12 Consensus tool calls: 5 searches and 7 fetches**. This is a call ledger, not a claim that all 12 consumed search credits: provider responses report search usage separately and included repeated counter values with unverified billing semantics. No extra quota was inferred. Forty-nine discovery identities/ranks and seven fetched records were preserved; no full abstracts/snippets are reproduced.

This is a targeted supplement, not a systematic review, citation leaderboard or replication. Some primary pages/PDFs were unavailable; read depth is explicit per record. No forecasting advantage, license eligibility or benchmark speedup is established for Scryntic. No Linear/Drive changes, product implementation, model execution, commit or push were performed. Recommendations remain research inputs to the proposed architecture.
