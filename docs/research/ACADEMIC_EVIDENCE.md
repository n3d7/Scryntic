# Academic evidence for a financial forecasting redesign

Research snapshot: 2026-10-18. This document is research and architecture guidance, not an implementation report or a claim of achievable trading returns. The initial product focus is forecasts over hours, days, and weeks; sub-second execution and high-frequency market making are outside this evidence review.

## Evidence rules and collection record

- Use primary papers, author manuscripts, official proceedings, and journal pages. Distinguish publication status from the version actually read.
- Consensus search and fetch were used for discovery; fetching sometimes returns only an abstract or no abstract. Discovery summaries are not full-text verification. Journal metadata takes precedence when the discovery index reports a working-paper year.
- Empirical results retain their asset universe, target, horizon, and evaluation context. Cross-sectional return ranking, aggregate market timing, volatility prediction, and generic demand forecasting are different tasks.
- Architecture recommendations below are design inferences, not conclusions that a paper directly tested on this project. No private project details were sent to public research tools.
- Collection is incremental. Entries explicitly marked abstract-only or partial-text must not be used for finer numerical or methods claims.

## Initial findings

Return predictability is conditional and fragile. Gu, Kelly, and Xiu find useful nonlinear interactions in monthly U.S. equity returns and report aggregate timing gains; Xu and Liu report that machine learning generally fails to beat the historical-average benchmark for aggregate equity-premium forecasts. The latter is consequential counterevidence, although the studies do not use an identical experimental design. A platform must retain simple baselines and evaluate each market and horizon separately. [1, 2]

## Source ledger

### [1] Nonlinear return models: useful evidence, restricted transfer

Shihao Gu, Bryan Kelly, and Dacheng Xiu (2020), **Empirical Asset Pricing via Machine Learning**, *Review of Financial Studies* 33(5), 2223–2273. Peer-reviewed. [DOI: 10.1093/rfs/hhaa009](https://doi.org/10.1093/rfs/hhaa009); [author-hosted published PDF](https://dachxiu.chicagobooth.edu/download/ML.pdf). Consensus search/fetch completed; published PDF methods and empirical sections read.

Universe/horizon: approximately 30,000 U.S. equities, 1957–2016, monthly excess-return predictions, 94 stock characteristics plus macro interactions and industry indicators. Training starts in 1957–1974, validation in 1975–1986, and testing in 1987–2016, with annual refits and rolling validation. Trees and neural networks benefit from nonlinear interactions; momentum, liquidity, and volatility are prominent predictors. Transfer to shorter horizons, other markets, or live net performance is unproven. **Decision:** benchmark regularized linear models, boosted trees, and a small neural model; use lagged returns, liquidity, and volatility as initial feature groups. [Published paper, sections 2.1–2.2](https://dachxiu.chicagobooth.edu/download/ML.pdf)

### [2] Aggregate market timing: a negative result

Xingfu Xu and Wei-han Liu (2024), **Forecasting the equity premium: can machine learning beat the historical average?**, *Quantitative Finance* 24(10), 1445–1461. Peer-reviewed journal record; **abstract-only evidence**. [DOI: 10.1080/14697688.2024.2409278](https://doi.org/10.1080/14697688.2024.2409278); [Consensus fetched abstract](https://consensus.app/papers/forecasting-the-equity-premium-can-machine-learning-beat-xu-liu/7871071ac4af5052bf97061507b7e760/?utm_source=chatgpt). Direct DOI retrieval failed; DOI metadata was cross-checked in indexed bibliographic results.

The abstract describes aggregate equity-premium forecasts from macroeconomic and technical predictors: strong in-sample fits, particularly trees, generally do not beat the historical-average benchmark out of sample. Exact sample dates, implementation choices, and transaction-cost treatment remain unverified. **Decision:** historical mean and zero-return forecasts are mandatory; do not infer live edge from model complexity or in-sample accuracy.

### [3] The historical average is a serious competitor

Ivo Welch and Amit Goyal (2008), **A Comprehensive Look at The Empirical Performance of Equity Premium Prediction**, *Review of Financial Studies* 21(4), 1455–1508. Peer-reviewed; journal abstract/metadata read, not the complete final article. [DOI: 10.1093/rfs/hhm014](https://doi.org/10.1093/rfs/hhm014); [official journal abstract](https://academic.oup.com/rfs/article-abstract/21/4/1455/1565737?login=true).

Task: aggregate equity-premium prediction using previously proposed economic variables. The abstract reports unstable models and poor out-of-sample performance, including disappointing market timing with information actually available then. The final article's exact variable-by-period tables were not inspected here. **Decision:** compare every return model with an expanding historical average computed strictly from matured past outcomes. This is evidence against automatic optimism, not proof that returns are never predictable. [Journal abstract](https://academic.oup.com/rfs/article-abstract/21/4/1455/1565737?login=true)

### [4] Cryptocurrency needs its own feature hypotheses

Yukun Liu and Aleh Tsyvinski (2021), **Risks and Returns of Cryptocurrency**, *Review of Financial Studies* 34(6), 2689–2727. Peer-reviewed; Consensus abstract fetched and publication verified against the author's university publication list. Primary full text was unavailable through the attempted routes. [DOI: 10.1093/rfs/hhaa113](https://doi.org/10.1093/rfs/hhaa113); [author publication record](https://economics.yale.edu/people/aleh-tsyvinski/papers); [fetched abstract](https://consensus.app/papers/risks-and-returns-of-cryptocurrency-liu-tsyvinski/c1447310091a524cb2ec67d43aa765cf/?utm_source=chatgpt).

The abstract supports cryptocurrency-specific network/adoption effects, time-series momentum, and investor-attention hypotheses. The earlier NBER version names Bitcoin, Ripple, and Ethereum; its sample and conclusions must not silently substitute for the final journal version. Exact final sample dates and horizon coefficients remain unverified. **Decision:** use asset-class feature namespaces and ablations; treat attention/on-chain features as optional research until publication latency, revisions, data rights, and incremental out-of-time value are established. Evidence from early cryptocurrencies does not establish a current liquid-universe edge.

### [5] Statistical and hybrid baselines remain necessary

Spyros Makridakis, Evangelos Spiliotis, and Vassilios Assimakopoulos (2020), **The M4 Competition: 100,000 time series and 61 forecasting methods**, *International Journal of Forecasting* 36(1), 54–74. Peer-reviewed. [DOI: 10.1016/j.ijforecast.2019.04.014](https://doi.org/10.1016/j.ijforecast.2019.04.014). Consensus record fetched, but has no abstract; indexed primary journal body passages were read. Direct journal open failed.

Universe: mixed-domain univariate series, with horizons of 48 hourly, 14 daily, and 13 weekly observations, plus lower frequencies. The competition evaluates both point forecasts and intervals, and reports disappointing performance of the submitted pure ML methods relative to the stronger alternatives. It is not a benchmark of tradable asset returns, and method implementations and information availability differ across competitions. **Decision:** preserve naive, seasonal-naive where justified, ETS/ARIMA, and combination benchmarks. Its rankings cannot determine a financial production model. [Primary journal text](https://doi.org/10.1016/j.ijforecast.2019.04.014)

### [6] Boosted trees are a strong candidate, not a universal winner

Spyros Makridakis, Evangelos Spiliotis, and Vassilios Assimakopoulos (2022), **M5 accuracy competition: Results, findings, and conclusions**, *International Journal of Forecasting* 38(4), 1346–1364. Peer-reviewed; indexed primary introduction and results/conclusion passages read; direct open failed. [DOI: 10.1016/j.ijforecast.2021.11.013](https://doi.org/10.1016/j.ijforecast.2021.11.013).

Universe/horizon: Walmart hierarchical unit sales, 30,490 bottom-level and 42,840 total series; 28 daily steps. The top 50 submissions used LightGBM, while cheap statistical methods remained competitive at some aggregation levels. Explanatory variables and combinations mattered. Retail demand has calendar, hierarchy, and promotional structure absent from many asset-return problems. **Decision:** give tabular boosted trees a first-class benchmark position and compare their compute cost with sequence models. M4 versus M5 differences argue for target-specific evaluation rather than a universal statistical-versus-deep ranking. [Primary results and conclusions](https://doi.org/10.1016/j.ijforecast.2021.11.013)

### [7] Global probabilistic sequence models

David Salinas, Valentin Flunkert, Jan Gasthaus, and Tim Januschowski (2020), **DeepAR: Probabilistic forecasting with autoregressive recurrent networks**, *International Journal of Forecasting* 36(3), 1181–1191. Peer-reviewed; journal abstract/metadata and methods/dataset sections of the author preprint read. [DOI: 10.1016/j.ijforecast.2019.07.001](https://doi.org/10.1016/j.ijforecast.2019.07.001); [author preprint](https://arxiv.org/pdf/1704.04110).

DeepAR trains a shared autoregressive recurrent model across related series and produces distributions. The manuscript evaluates parts, electricity, traffic, and retail-style data, not a controlled asset-return trading benchmark. Forecast horizons and available covariates are dataset-specific. **Decision:** test a modest global sequence model only after tabular/statistical baselines; share learning across assets when justified, with asset identifiers, masks, and scale handling. Distributional outputs still require calibration tests on the financial target. [Author manuscript, methodology and experiments](https://arxiv.org/pdf/1704.04110)

### [8] Foundation models: useful challengers with provenance constraints

Abdul Fatir Ansari et al. (2024), **Chronos: Learning the Language of Time Series**. Published in *Transactions on Machine Learning Research*, October 2024; the version actually read was arXiv v2, 2024-05-02, including experiment/limitation sections. [Version read](https://arxiv.org/html/2403.07815v2); [official published record/PDF](https://openreview.net/pdf?id=gerNCVqqtR); [author publication page](https://dcmaddix.github.io/publications/chronos). OpenReview direct retrieval encountered a browser challenge; its indexed publication header and author record establish publication status.

The study tests probabilistic, pretrained univariate models on 42 datasets, distinguishing in-domain from unseen-dataset evaluation. It reports competitive zero-shot results but identifies quantization/range and extrapolation limitations. This is not evidence of net return predictability. **Decision:** admit frozen foundation models as challengers, recording checkpoint, training-corpus documentation, release date, context transformations, and inference cost. Unknown pretraining overlap prevents a clean historical out-of-sample claim; require post-release forward evaluation or explicitly qualify retrospective results. [Paper methods, experiments, limitations](https://arxiv.org/html/2403.07815v2)

### [9] Score the distribution honestly

Tilmann Gneiting and Adrian E. Raftery (2007), **Strictly Proper Scoring Rules, Prediction, and Estimation**, *Journal of the American Statistical Association* 102(477), 359–378. Peer-reviewed theoretical/review article; Consensus abstract fetched and indexed primary paper passages read. Direct author-PDF retrieval failed. [DOI: 10.1198/016214506000001437](https://doi.org/10.1198/016214506000001437); [author PDF](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf); [fetched record](https://consensus.app/papers/strictly-proper-scoring-rules-prediction-and-estimation-gneiting-raftery/cb608bdcfc4b52baa11789d9cfee6d2c/?utm_source=chatgpt).

Proper scoring rules reward truthful predictive distributions in expectation; the paper covers CRPS, logarithmic, categorical, quantile, and interval scores. This theory does not guarantee that an estimated forecast is calibrated or that good statistical scores create economic value. **Decision:** report CRPS or quantile scores, interval coverage and width, and Brier/log scores for declared events; do not rank models by interval coverage alone. [Primary paper](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf)

### [10] Adaptive calibration under changing conditions

Isaac Gibbs and Emmanuel Candès (2021), **Adaptive Conformal Inference Under Distribution Shift**, *NeurIPS 34*. Peer-reviewed proceedings paper; primary methods, guarantee, and financial example sections read. [Official paper](https://papers.neurips.cc/paper_files/paper/2021/file/0d441de75945e5acbc865406fc9a2559-Paper.pdf).

The financial example predicts daily squared stock returns using a rolling GARCH model. The distribution-free long-run coverage-frequency result is not a guarantee of conditional coverage for every asset, regime, or forecast origin; intervals can become uninformative. Four displayed stocks were selected from twelve because fixed calibration failed visibly. **Decision:** assess adaptive calibration against fixed and rolling alternatives, tracking recent and long-run coverage with interval width; never label an individual forecast guaranteed. [Paper, sections 2.2 and 4](https://papers.neurips.cc/paper_files/paper/2021/file/0d441de75945e5acbc865406fc9a2559-Paper.pdf)

### [11] Drift adaptation has tuning and local-coverage tradeoffs

Isaac Gibbs and Emmanuel Candès (2024), **Conformal Inference for Online Prediction with Arbitrary Distribution Shifts**, *Journal of Machine Learning Research* 25. Peer-reviewed; primary introduction/methodological guarantee discussion read. [Official paper](https://www.jmlr.org/papers/volume25/22-1218/22-1218.pdf).

This extends the preceding research line with adaptive step-size selection and local regret/coverage analysis; examples involve stock-market volatility and COVID counts. It is not independent corroboration of return forecasting. The paper distinguishes parameter choices that guarantee exact long-run coverage from choices favoring local adaptivity. **Decision:** keep calibration state versioned separately from model weights, evaluate reaction to abrupt changes, and expose the local-coverage/width tradeoff. Multi-step delayed outcomes require an explicit update protocol; the paper's online statements cannot simply be copied to overlapping weekly targets. [Primary introduction and methodology](https://www.jmlr.org/papers/volume25/22-1218/22-1218.pdf)

### [12] Random cross-validation has narrow valid cases

Christoph Bergmeir, Rob J. Hyndman, and Bonsoo Koo (2018), **A note on the validity of cross-validation for evaluating autoregressive time series prediction**, *Computational Statistics & Data Analysis* 120, 70–83. Peer-reviewed; author abstract and 2015 Monash manuscript assumptions read; Consensus fetched record has no abstract. [DOI: 10.1016/j.csda.2017.11.003](https://doi.org/10.1016/j.csda.2017.11.003); [author record](https://otext.robjhyndman.com/publications/cv-time-series/); [manuscript](https://www.monash.edu/business/econometrics-and-business-statistics/research/publications/ebs/wp10-15.pdf).

The result supports ordinary K-fold under restricted purely autoregressive settings with uncorrelated model errors. It does not license arbitrary shuffled validation of drifting asset panels, overlapping forward labels, revised features, or shared global models. **Decision:** use chronological walk-forward as the product's primary deployment simulation. Avoid the opposite overstatement that every use of random CV with time series is mathematically invalid. [Author abstract and manuscript assumptions](https://otext.robjhyndman.com/publications/cv-time-series/)

### [13] Multiple testing includes the experiments nobody publishes

Campbell R. Harvey, Yan Liu, and Heqing Zhu (2016), **… and the Cross-Section of Expected Returns**, *Review of Financial Studies* 29(1), 5–68. Peer-reviewed; author-hosted published methods and conclusion passages read. [DOI: 10.1093/rfs/hhv059](https://doi.org/10.1093/rfs/hhv059); [published PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF).

The study analyzes a literature containing at least 316 tested factors and argues that conventional single-test significance thresholds understate false discoveries. Its suggested t-statistic hurdle above three is context-dependent, not a universal platform acceptance constant. **Decision:** preserve the complete experiment family: feature sets, assets, horizons, seeds, architectures, hyperparameters, ensembles, and strategy thresholds, including failed runs. Correct for the declared family and dependence structure before interpreting a selected winner's significance. [Paper, conclusion and multiple-testing framework](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF)

### [14] Backtest selection can overfit an apparently held-out period

David H. Bailey, Jonathan M. Borwein, Marcos López de Prado, and Qiji Jim Zhu (2017), **The probability of backtest overfitting**, *Journal of Computational Finance* 20(4). Peer-reviewed journal publication; version actually read: author manuscript revised February 2015. [Journal DOI: 10.21314/JCF.2016.322](https://doi.org/10.21314/JCF.2016.322); [author manuscript](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf); [institutional publication record](https://escholarship.org/uc/item/4w1110bb). Consensus fetch retained its different 2016 discovery year.

The paper introduces PBO and combinatorially symmetric cross-validation to diagnose strategy-selection overfit; repeatedly inspecting a holdout can undermine its role. Examples and resampling assumptions do not establish a universally calibrated real-world failure probability. CSCV is not the same procedure as chronological deployment testing or combinatorial purged CV. **Decision:** reserve a final untouched period and forward paper evaluation; use selection diagnostics as supporting evidence, retaining all candidate returns and trial counts. [Author manuscript, sections 1–3](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

### [15] Published predictability can decay

R. David McLean and Jeffrey Pontiff (2016), **Does Academic Research Destroy Stock Return Predictability?**, *Journal of Finance* 71(1), 5–32. Peer-reviewed; primary journal abstract read, not the full methods/tables. [DOI: 10.1111/jofi.12365](https://doi.org/10.1111/jofi.12365); [official journal abstract](https://www.onlinelibrary.wiley.com/doi/full/10.1111/jofi.12365).

Universe: 97 published cross-sectional stock-return predictors. The abstract reports portfolio returns 26% lower after the original sample and 58% lower after publication, with both selection and publication-informed trading interpretations. These are study averages, not a forecast of the decay rate for a new model, and do not identify every source of concept drift. **Decision:** keep rolling performance and feature-ablation histories, test fresh forward periods, and retire or demote deteriorating models through a predeclared process. [Primary abstract](https://www.onlinelibrary.wiley.com/doi/full/10.1111/jofi.12365)

### [16] Compare losses with dependence-aware uncertainty

Francis X. Diebold and Roberto S. Mariano (1995), **Comparing Predictive Accuracy**, *Journal of Business & Economic Statistics* 13(3), 253–263. Peer-reviewed; primary journal abstract read. A university-hosted scanned PDF was reachable but its body was not text-extractable; it was not visually reviewed. [DOI: 10.1080/07350015.1995.10524599](https://doi.org/10.1080/07350015.1995.10524599); [primary journal abstract](https://doi.org/10.1080/07350015.1995.10524599).

The framework compares predictive losses and permits serially and contemporaneously correlated errors under its test assumptions. It is not a blanket correction for nested-model comparisons, model search, small samples, or arbitrary nonstationarity. **Decision:** preserve per-origin paired loss differences, report dependence-aware uncertainty and effective evaluation length, and use an appropriate comparison method rather than treating overlapping labels or correlated assets as independent repetitions. [Primary abstract](https://doi.org/10.1080/07350015.1995.10524599)

### [17] Economic value depends on implementability

Andrea Frazzini, Ronen Israel, and Tobias J. Moskowitz, **Trading Costs of Asset Pricing Anomalies**, author working paper; **December 5, 2012 draft actually read**, not a claimed peer-reviewed final article. Primary abstract, implementation-shortfall discussion, and portfolio-cost sections read. [Author-hosted draft](https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf).

Universe: institutional trades across 19 developed equity markets, 1998–2011; evaluates costs/capacity of equity anomaly strategies. Realized costs include price impact and the opportunity cost of unexecuted trades. Institutional execution, old market conditions, and selected strategies prevent direct transfer to retail crypto or derivatives. **Decision:** separate forecast scores from policy results and report gross/net outcomes, turnover, exposure, drawdown, financing/borrow/funding where applicable, execution delays, slippage/impact scenarios, and liquidity limits. Use venue-specific measured costs when available; the paper supplies a framework, not current cost estimates. [Author draft](https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf)

## Architecture decisions derived from the evidence

These are proposed research requirements. They have not been implemented or validated on project data.

| Area | Decision and reason | Evidence |
| --- | --- | --- |
| Target | Define return, excess return, volatility, threshold event, and cross-sectional ranking as distinct tasks. Prefer log/simple returns as declared targets; derive a price display from the forecast-origin price rather than selecting a model by price-level fit alone. | Task differences in [1–4]; design inference |
| Horizons | Start with a declared set such as 4h, 1d, 1w, with market-calendar and 24/7 semantics explicit. Direct per-horizon prediction is the first controlled comparison; any recursive or joint-horizon model must be tested separately. | Domain/horizon transfer limits in [1, 4–8]; proposed scope |
| Model ladder | Zero return/historical mean; simple statistical model; regularized linear model; boosted trees; modest global sequence model; frozen foundation challenger. Equal data cutoffs and a recorded tuning budget are prerequisites for comparison. | [1–8, 13–14] |
| Features | Start with lagged returns, trailing volatility/range, volume/liquidity, cross-asset market factors, and calendar metadata where justified. Distinguish observed covariates from values genuinely known in advance. Optional macro/news/attention/on-chain/order-flow/funding groups require point-in-time lineage and incremental ablations. The cited evidence does not establish all of these optional groups as useful. | Positive feature hypotheses [1, 4]; engineering inference |
| Output | Save quantiles/distribution or declared event probabilities alongside central estimates; store origin, target, horizon, units, asset, source snapshot, model and calibrator versions, and maturity time. | [7–11]; contract is design inference |
| Ensemble | Begin with an equal-weight combination; train any weights or stacking model from chronological out-of-fold predictions only. Compare against every constituent on later periods, and assess correlated errors, weight instability, cost, and recalibration. | Combination evidence [5–6]; leakage controls inferred |
| Drift | Monitor data validity, feature distributions, realized loss, coverage, interval width, and economic outcomes separately. Delayed labels mean feature alerts can precede performance evidence. Compare fixed, rolling, and expanding training windows before adding an automatic adaptation controller. | [10–11, 15]; operational design inference |
| Promotion | A model is promoted for a specified asset universe, horizon, target, data version, and policy; there is no blanket winner. Freeze acceptance metrics before the untouched final period. | Conflicting results [1–6], selection evidence [13–16] |

### Temporal evaluation contract

The following protocol is an engineering derivation from causal deployment constraints and the validation/selection evidence; no cited paper directly establishes this entire multiasset contract.

1. **Use common chronological cutoffs across assets.** A global model, cross-asset feature, preprocessing fit, calibrator, or ensemble may not train on asset B's future while asset A is evaluated earlier. An additional held-out-asset experiment answers a different transfer question; it does not replace a time split.
2. **Represent information intervals explicitly.** For each example retain forecast origin, feature availability timestamps, label end, and label availability/maturity time. At fit time T, training features and outcomes must actually have been available by T. Fit imputers, scalers, feature selection, factor construction, and hyperparameters inside each training/tuning fold. Revised economic data need vintages; membership must reflect listing/delisting and the universe known then.
3. **Purge overlapping forward labels.** Remove a training example if its target-information interval overlaps an evaluation target interval or if its outcome would not have matured at the simulated fit time. For example, a Monday 1-week label cannot train a model supposedly fitted on Tuesday. Compute overlap from actual timestamps and calendars rather than assuming a fixed row gap. A training window and an evaluation feature window sharing past observations is not, by itself, future leakage.
4. **Treat embargo as a declared extra buffer.** If a research split allows training observations after an evaluation block, remove a justified post-block buffer for dependence/information overlap, and retain an interval-overlap audit. This does not make future-trained folds a causal deployment simulation. In strict past-only walk-forward, there is no post-test training block to embargo within that fold; a pre-test gap may still be needed for outcome availability. Do not choose an unexplained percentage of dataset length or assume a gap guarantees independence.
5. **Separate selection, calibration, and final assessment.** Use inner chronological folds for tuning and outer later blocks for comparison. Keep a later final test untouched by feature/model/threshold selection, with a subsequent forward paper period. Calibrator and ensemble fitting use only earlier predictions with matured outcomes, not the outer test being reported. Any online update during assessment must be declared and replayed exactly as it would occur live.
6. **Account for overlapping horizons and shared shocks.** Preserve per-origin, per-asset losses and aggregate first by common calendar blocks when estimating uncertainty. Use justified dependence-aware methods, with sensitivity to block length/assumptions; do not count every asset-row and every overlapping weekly label as an independent observation. Report performance by horizon, asset, regime, and chronology, alongside aggregate results. [12–16]

### Statistical and economic acceptance

Predeclare a primary forecasting score per task, baseline comparison, acceptable calibration/width behavior, minimum chronological coverage, and cost scenarios. Numerical acceptance thresholds must be set from product requirements and data quality; the literature does not justify a universal accuracy, Sharpe, or significance target. For returns use paired MAE/RMSE and baseline-relative measures as appropriate; for distributions use proper scores and rolling coverage/width; for declared directional events include class frequencies and Brier/log scores. Avoid percentage errors with returns near zero. [3, 9–11, 16]

Report policy results separately with risk/exposure constraints, gross and net metrics, uncertainty, and sensitivity to fees, financing, slippage, delay, and capacity. Compare with suitable passive/no-trade policies under the same assumptions. An abstention/no-trade policy may be evaluated, but its threshold is another selected parameter and belongs in the experiment registry. Forecast skill, calibration, and economic usefulness are three separate claims. [13–14, 17]

## Limits and unresolved questions

### Subsequent focused Consensus review

The operator requested a further bounded literature check after the original architecture delivery. [The seven-study supplement](CONSENSUS_FOLLOWUP_2026-10-18.md) adds crypto-factor/basis evidence, a newer TabPFN-2 challenger, LLM-news contamination diagnostics and the original SPA test's estimation-scheme restriction. These are research refinements, not additional adopted models or implementation authorization. [Separate fetched metadata/call ledger](consensus/FOLLOWUP_RECORDS.json) preserves the original archive unchanged.

- This is a targeted evidence review, not a systematic review or an exhaustive survey. Search queries, fetched-record metadata, and analyst paraphrases are preserved in [Consensus SEARCH_LOG.md](consensus/SEARCH_LOG.md) and [PAPER_RECORDS.json](consensus/PAPER_RECORDS.json).
- Hours-to-weeks **financial return** evidence is substantially narrower here than generic forecasting evidence. The cited monthly equity and early-crypto studies do not settle 4-hour or weekly live predictability. A point-in-time benchmark on the chosen universe is the next research dependency.
- Several journal pages and PDFs were blocked or unavailable; abstract-only claims and manuscript/final-version differences are marked per source. No precise profit estimates, comparative rankings on this project, or current execution-cost values are inferred from them.
- M4/M5, DeepAR, and Chronos show methodological possibilities across different tasks. They do not establish that any architecture beats strong financial baselines after costs. The 2021/2024 conformal papers are one related research line, not two independent financial replications.
- Foundation-model pretraining overlap, point-in-time feature availability, survivorship/delisting coverage, delayed outcome handling, and current costs must be resolved before a clean live-performance claim. No paper or validation technique eliminates changing market behavior.
- Purging, embargo, and common multiasset cutoffs above are explicit proposed rules. This review did not retrieve and read the full original book chapter commonly associated with purged CV; it therefore does not present a secondary tutorial as primary empirical evidence.
- Firecrawl search returned insufficient credits and was not run. Consensus search/fetch and the available web tool supplied the retained evidence. Failed searches and rate limits are recorded; no external publication or project implementation was performed.
