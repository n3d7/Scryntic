# Consensus search and fetch log

Research date: 2026-10-18. Public queries used generic academic terminology; no repository content, private market data, credentials, or private project details were submitted. This log records this research subtask, not all activity against the shared account. Results are discovery evidence, not independent full-text review.

## Searches actually attempted

| Query | Outcome | Retained/fetched paper ids |
| --- | --- | --- |
| stock return predictability machine learning empirical asset pricing Gu Kelly Xiu out of sample | Success, 10 results; shared usage response 6/30 | `fc69e4f429c85a9abb78a8e6340dc58b`, `7871071ac4af5052bf97061507b7e760` |
| M4 Competition results statistical machine learning combination forecasting Makridakis Spiliotis Assimakopoulos | Success, 10 results | `dfbddb6c07f55669966ce290335bfd12` |
| probability of backtest overfitting Bailey Borwein Lopez de Prado Zhu 2017 | Rate limit; no evidence collected | none |
| Gneiting Raftery strictly proper scoring rules calibration prediction intervals | Rate limit; no evidence collected | none |
| Risks and Returns of Cryptocurrency Liu Tsyvinski momentum attention | Success, 10 results | `c1447310091a524cb2ec67d43aa765cf` |
| concept drift predictability published stock return predictors McLean Pontiff 2016 | Rate limit; no evidence collected | none |
| probability backtest overfitting Bailey Lopez de Prado 2017 | Success, 10 results; shared usage response 10/30 | `f5ef21e8509b544fba76ccc9aa2de55c` |
| Strictly Proper Scoring Rules Prediction and Estimation Gneiting Raftery 2007 | Success, 10 results | `cb608bdcfc4b52baa11789d9cfee6d2c` |
| A note on validity cross-validation evaluating autoregressive time series prediction Bergmeir Hyndman Koo | Success, 10 results | `fe5093b7ccba53cf812ebbb5a7505e42` |

## Fetches and limitations

Eight unique papers were fetched before use; five initial records and the Bergmeir record were fetched twice during collection/preservation. The additional fetched id `caad141443cf595ba4936f1afcf3c031` came from the successful M4 search and represents the fuller M4 article published in the 2020 issue, distinct from the preliminary 2018 report. The tool returned abstracts/metadata or abstract-unavailable notices; the archive retains metadata and analyst paraphrases, with no verbatim abstract excerpts. Both M4 records and the Bergmeir record explicitly say their abstracts are unavailable; no empirical claim is based on those notices. Consensus reports working-paper/online-first years for several papers; verified journal years and read-depth notes are stored separately. Citation counts are retained as returned metadata and are not evidence quality scores.

[PAPER_RECORDS.json](PAPER_RECORDS.json) preserves metadata for eight fetched papers, analyst-written paraphrases, separate DOI/publication/read-depth annotations, two explicit contradiction notes, and identities/ranks from four available successful search responses. Full returned abstracts and search snippets were removed; no verbatim abstract excerpts are retained. The table above is the complete attempt log for this subtask; the search-result archive is partial and does not claim to preserve every discovery response.

The directly relevant counterevidence is Xu/Liu: macro/technical ML aggregate equity-premium forecasts generally fail to beat a historical average, whereas Gu/Kelly/Xiu study a stock panel and cross-sectional returns. This is a difference in task and sample, not a controlled replication. Primary reading depth and design implications are recorded in [ACADEMIC_EVIDENCE.md](../ACADEMIC_EVIDENCE.md).

## Other retrieval route

Firecrawl search was attempted for `Empirical Asset Pricing via Machine Learning Gu Kelly Xiu pdf author` (four official-domain results requested), but returned HTTP 402 / insufficient credits; the request was not run. No retry was made. Primary evidence collection continued through the available web tool. Failed retrievals are not treated as source verification.

## Focused follow-up after the initial redesign

A bounded review was completed using the remaining allowance. Actual follow-up: **5 successful searches + 7 fetched paper records = 12 total Consensus tool calls**; no further Consensus call was made. Provider search counters are preserved, not treated as a billing audit. This supplement does not overwrite the original nine-attempt log.

| Query | Results | Reported searches used / limit |
|---|---:|---|
| Common Risk Factors in Cryptocurrency Liu Tsyvinski Wu cross section returns momentum daily weekly | 10 | 12 / 30 |
| Why do tree based models still outperform deep learning on typical tabular data Grinsztajn Ouillon Varoquaux | 9 | 14 / 30 |
| Accurate predictions on small data with a tabular foundation model TabPFN Hollmann Muller Purucker Nature 2025 | 10 | 15 / 30 |
| large language model financial forecasting news look ahead bias pretraining return predictability contamination | 10 | 15 / 30 |
| Hansen test superior predictive ability White reality check data snooping comparing many forecasting models 2005 | 10 | 17 / 30 |

Seven fetched records and 49 discovery identities/ranks are saved in [FOLLOWUP_RECORDS.json](FOLLOWUP_RECORDS.json); read-depth, version/author corrections, provider citation counts and analyst paraphrases remain distinct. Full abstracts/snippets are not copied. Findings and proposed experiment refinements: Consensus follow-up. No model/runtime, Linear or Drive mutation was part of this collection.
