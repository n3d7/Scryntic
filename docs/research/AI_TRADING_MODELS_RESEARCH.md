# AI forecasting models and trading frameworks for Scryntic

Research snapshot: **19 October 2026**. Repository baseline: `8e1465fd389e440763ef69b7c59832ad2f8bd8de`, together with the existing, uncommitted architecture/research documents inspected in this workspace.

**Scope:** technical suitability, empirical evidence, deployment and future integration. At the operator's subsequent request, licensing is outside this report's assessment and does not affect the rankings below. This is documentation-only research: no model was installed or executed, no benchmark was reproduced, and no application, configuration, infrastructure, dependency, tracker or existing roadmap was changed by this research task.

Evidence labels used throughout:

- **Verified:** an artifact, interface, configuration or source statement was inspected. This does not independently verify the authors' training run or benchmark.
- **Author-reported:** training statistics, benchmark results or trading simulations reported by the technology's authors.
- **Independent:** a separate evaluation, with its sample and limitations stated. Independence does not imply replication, peer review or freedom from leakage.
- **Inference/proposal:** an engineering conclusion or proposed experiment for Scryntic.
- **Unknown:** not established by the inspected evidence, including target-hardware speed and most fine-tuning memory requirements.

## 1. Executive summary and recommendations

Scryntic should compare forecasting components inside its own data, evaluation and worker boundaries. Importing an autonomous trading platform would add considerably more coupling than demonstrated predictive value.

1. **Keep statistical baselines and add a supervised tabular baseline before declaring a foundation-model winner.** Zero-return/random-walk, historical mean, regularized linear/AR and simple volatility models remain necessary controls. LightGBM is a particularly relevant additional candidate for return, direction and volatility features. Existing financial research supports testing nonlinear interactions; it does not establish universal superiority or crypto profitability. Reuse the project's [academic evidence](ACADEMIC_EVIDENCE.md) and [focused supplement](CONSENSUS_FOLLOWUP_2026-10-18.md).
2. **Retain the existing TimesFM 2.5 integration as the operational control; evaluate Chronos-2 and TimesFM 3.0 as the first new foundation-model challengers.** Chronos-2 combines a relatively small model with native multivariate/covariate forecasting. TimesFM 3.0 adds native cross-series modelling and covariates to the TimesFM family. Neither is already interchangeable with the present close-only worker simply because a generic provider protocol exists. [Chronos-2][CHcard], [TimesFM-3][TFM3blog]
3. **Evaluate Kronos-base as a distinct OHLCV-specific hypothesis.** Its financial pretraining and candle-shaped outputs make it relevant to Bybit, but also require a richer input adapter and stronger stochastic-output validation. A small independent evaluation provides negative counterevidence; financial pretraining is not proof of an edge. [Kronos][KRpaper], [third-party evaluation][KRind]
4. **Use Moirai 2.0 as a small univariate probabilistic control; reserve TiRex-2 and small Toto 2.0 variants for targeted comparisons.** Moirai 2.0 does not retain its predecessor's cross-variate/covariate capability. TiRex-2's advertised streaming architecture must be separated from the public release's Pro-only incremental features. Toto 2.0's public fine-tuning and exogenous-variable support are not yet released. These distinctions materially change integration plans. [Moirai-2][MOpaper], [TiRex-2][TIcard], [Toto 2][TOrepo]
5. **Treat financial NLP as a separate, later signal family.** FinBERT is the simplest sentiment classifier to benchmark once a timestamped text dataset exists. FinGPT is useful for testing richer text features or narratives, with much larger runtime and reproducibility costs. Neither predicts calibrated crypto returns directly. [FinBERT][FBcard], [FinGPT Forecaster][FGcard]
6. **Postpone agent and trading platforms as core dependencies.** TradingAgents and FinRobot can assist bounded research; FinRL-X can inform later portfolio simulation; Trading-R1 remains a paper/watchlist candidate because a usable official checkpoint and executable release were not verified. Agent debate quality and appealing backtests do not establish net tradable alpha. [TradingAgents][TArepo], [FinRobot][FRrepo], [FinRL-X][FXrepo], [Trading-R1][TRrepo]

**Combination is worth testing, not presuming.** A strong tabular model, one probabilistic time-series model and, later, a text signal cover different information sources. Several similar foundation models may simply repeat the same errors. Add ensemble members only when out-of-fold and forward evidence shows incremental benefit after inference cost, calibration and turnover are counted.

No candidate has established a cost-complete, independently reproduced Bybit crypto/perpetual strategy for Scryntic. The practical decision is an experiment order, not a profitability ranking.

## 2. Current Scryntic capabilities and constraints

### 2.1 Existing implementation versus target design

The current [architecture](../../ARCHITECTURE.md) describes an approved target direction, rather than delivered functionality. Its first forecasting priority is hours, days and weeks, with proposed horizons of 1 hour, 1 day, 7 days and 28 days. Minute/second forecasting and trading are later, separately scoped work. The following distinctions come from selective source inspection and existing validation records, not a new repository-wide audit.

| Boundary | Existing, inspected capability | Consequence for model research |
|---|---|---|
| Market collection | [Bybit public REST and WebSocket kline collection](../data/BYBIT.md), bounded requests, finality/clock evidence and live/backfill recovery. Historical candles cover spot, linear and inverse; instrument metadata has broader categories. | Start with actual candle data. Instrument discovery is not proof that every product has a supported historical dataset. |
| Archive and datasets | Durable ingestion/publication/import and pinned provenance, as summarized in [current state](CURRENT_STATE.md). | Reuse snapshots, hashes and quality exclusions; do not let vendor loaders fetch a different dataset during evaluation. |
| Replay | [F19 replay](../data/REPLAY.md): explicit chronological cutoffs, availability-aware reading, label separation, overlapping-label exclusion, train-only feature scaling and persistence forecasts. | Useful foundation, but not an existing general walk-forward, strategy-execution or probabilistic-calibration engine. |
| Model execution | `select_provider` selects a reviewed definition/runtime; `LocalModelProvider` delegates to the isolated CPU worker. Current TimesFM adapter loads admitted local safetensors after isolation checks. | New adapters should preserve this authority boundary, rather than import model libraries into collector/coordinator processes. |
| Current TimesFM input/output | `TimesFMAdapter.infer` uses finite close values, context up to 512, configured model horizon 128, and only requested bounded point forecasts. Current F22 use is 1–24 output steps. | OHLCV, variable groups, quantile grids, sample paths and arbitrary fine-tuning are not enabled by switching the model name. |
| Application forecast interface | `ForecastRequest` contains target/frequency/horizon and covariate identifiers; `ForecastResult` contains finite point values and request/model bindings. | A field named `covariates` does not establish an implemented covariate data path. The inspected result is point-valued, not a distribution contract. |
| Qualification history | [F22 validation](../validation/F22_VALIDATION.md) records real CPU TimesFM execution and reproducibility checks on a bounded Bybit sample. | Operational success is evidence about the execution path, not independent holdout skill or profitable trading. |
| Target extensions | `TargetSpec`, `TrainingTask`, `ForecastRequestV2`, `ForecastDistributionV2`, qualification/registry and a pre-outcome forecast ledger are described in the target architecture. | Treat these as proposed or planned interfaces until their implementation and qualification exist. |
| Execution authority | [Future trading design](../architecture/FUTURE_TRADING.md) separates forecasting, strategy, risk and exchange execution. | A forecast, narrative or agent decision must never acquire account credentials or order authority through model integration. |

Relevant source: [provider selection](../../src/scryntic/models/provider.py), [TimesFM adapter](../../src/scryntic/models/adapters.py), [application contracts](../../src/scryntic/application/providers.py), [replay preparation](../../src/scryntic/replay/service.py). The graph trace connects provider selection to model review/runtime inventory and replay preparation to dataset pins/manifests/inspection and `CandleReplayReader`.

**Important version distinction:** the existing dedicated runtime pins Python 3.12.14, package `timesfm==3.0.2` and CPU-only Torch, but the checkpoint is **TimesFM 2.5**, revision `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`. Package version 3.0.2 must not be reported as an integrated TimesFM 3.0 model. See [runtime metadata](../../runtimes/forecast_cpu/pyproject.toml) and [F22 model identity](../operations/F22_MODELS.md).

### 2.2 Data and horizon limits that matter

The verified Bybit path is candle/kline-oriented. This review did not establish a qualified Bybit trades/order-book reconstruction, queue-position simulation, funding/open-interest feature stream or point-in-time financial-news dataset. Numeric model inputs could eventually consume derived features from these sources, but that is a data extension, not a pretrained model capability already available in Scryntic.

Current replay documents 1–128-step labels and a 10,000-row bound. A 7-day target is 10,080 minute steps, 168 hourly steps or 7 daily steps. A 28-day target is 672 hourly steps or 28 daily steps. These are arithmetic illustrations, not implemented resampling support. The existing caps cannot silently become duration-based weekly/monthly forecasting. Frequency, contiguous history, matured samples and deterministic resampling must be qualified first.

Future multi-exchange work needs venue/product-specific identifiers, symbol and instrument-version mappings, listing/delisting history, units, calendars and source-availability semantics. Spot prices, dated-futures basis and perpetual funding are different inputs. Shared ticker text or timestamp alignment alone does not make two series economically comparable.

### 2.3 Reused evidence and inspection scope

This report reuses [reference systems](REFERENCE_SYSTEMS.md), [academic evidence](ACADEMIC_EVIDENCE.md), [follow-up research](CONSENSUS_FOLLOWUP_2026-10-18.md) and [current-state reconciliation](CURRENT_STATE.md). It does not repeat their full framework/security review or modify the [existing roadmap](../architecture/ROADMAP.md).

Repository evidence used Tier 2 graph discovery/traces/snippets and targeted direct reads. Coverage metadata generation was `2026-10-19T13:06:16Z`; relied-on paths had matching metadata and no recorded parse gaps. Scoped source exclusions were generated `__pycache__` directories. Coverage is best-effort, not proof of exhaustive implementation or correctness. Historical validation results below were read, not rerun.

## 3. Candidate technical analysis

### 3.1 TimesFM 2.5 — existing operational control

**Verified contract and architecture.** A patched decoder-only time-series transformer: the 200M-labelled backbone plus optional approximately 30M continuous quantile head. Current HF metadata counts **231,289,280 stored parameters**; the project-pinned safetensors file is approximately 925 MB. Upstream supports univariate numeric histories, point/quantile forecasts, up to **16k context observations** in the repository's wording and an optional continuous quantile head through roughly 1,000 forecast steps. These upstream limits are distinct from Scryntic's 512-context, point-only profile. XReg provides a covariate-regression path; this is not native joint variate attention. [Repository][TFMrepo], [card][TFM25], [checkpoint metadata][TFM25api]

**Training and evidence.** Public/synthetic time-series pretraining provides a general temporal prior. This is not crypto-specific representation learning, and the original TimesFM paper is not a complete experiment report for every 2.5 update. Public forecasting benchmarks measure forecast losses, not fees/funding-adjusted returns. A separate 2026 financial-return preprint finds useful rankings but small and sparse improvements over zero-return baselines across five US equities; it does not validate crypto. [Independent study][FINeval]

**Practical fit.** The only candidate here with an existing, historically qualified Scryntic adapter. Upstream LoRA examples exist, but current Scryntic providers are inference-only. Point forecasts are inexpensive to retain as a control; enabling quantiles, XReg or tuning requires new reviewed contracts and qualification. No native trades/LOB semantics or cross-asset joint distribution is established.

**Measured historical resource evidence, not a new benchmark:** F22 reports approximately 4.16–4.29 seconds of inference elapsed time and about 2.04 GiB `ru_maxrss` across 24 native executions on that specific CPU setup. Its six retained comparison cases are far too few to demonstrate forecasting advantage; the cgroup/RSS accounting discrepancy is also recorded. Do not extrapolate those measurements to new contexts, GPUs or other models. [F22 validation](../validation/F22_VALIDATION.md)

**Decision:** retain; extend only for a registered experiment. Low incremental cost for the existing path; medium effort for probabilistic/covariate features.

### 3.2 TimesFM 3.0 — high-priority multivariate challenger

**Verified availability:** `google/timesfm-3.0-pytorch` exists. Google describes a **330M-parameter** model trained on more than one trillion real/synthetic time points. Its patched decoder alternates causal temporal attention and full variate attention; contiguous patch masking supports multi-patch forecasting. The public config has 32-step input patches, 64-step output patches, 20 layers, width 1,280, nine deciles and a `max_variates` value of 32. The last value is configuration evidence, not a tested guarantee for every wrapper or group size. [Card][TFM3], [technical blog][TFM3blog], [config][TFM3config]

**Inputs/outputs.** Multiple numeric targets, past-only covariates and past-and-future covariates; median/point and quantile outputs. This can represent synchronized asset returns or lagged volatility/volume features. A horizon is a number of observations, not a duration promise. Context/horizon and batching must be pinned to an actual runtime configuration; no arbitrarily long forecast or fully calibrated joint path follows from multivariate point/quantile outputs.

**Evidence and limits.** Google reports leading aggregate results on GIFT-Eval, fev-bench and TIME. These are author results/submissions across general forecasting tasks, not an independent crypto trading ranking. Correlation between asset levels can improve a benchmark while contributing little to conditional future returns. OHLC constraints, trade ordering and order-book queues are not built-in market semantics.

**Deployment and fit.** About 1.32 GB of FP32 parameter payload by arithmetic, before activations/runtime. The public PyTorch path is a plausible 12GB evaluation candidate with bounded batches and variates; target-hardware latency and peak VRAM are unknown. Reuse the provider/worker pattern, not the current 2.5 adapter unchanged. A verified model-3-specific fine-tuning recipe on this hardware was not established; 2.5 LoRA examples must not be relabelled as such.

**Decision:** evaluate alongside Chronos-2 for the native multivariate/covariate hypothesis. Medium adapter effort and continuing upstream API/configuration maintenance.

### 3.3 Chronos-2 — first new general-purpose adapter candidate

**Verified availability and architecture.** `amazon/chronos-2`, approximately **119.5M parameters**. An encoder-only, T5-inspired transformer alternates temporal and group attention over patched targets/covariates, using robust scaling, masks and time-index features. It predicts multi-step quantiles directly; it is not the original Chronos value-token sampling model. Training combines real series and synthetic multivariate structure. [Card][CHcard], [paper v1][CHpaper], [metadata][CHapi]

**Contract.** Univariate/multivariate targets, historical real/categorical covariates and known-future real/categorical covariates. Documented maxima are **8,192 context observations and 1,024 prediction steps**. Optional cross-learning shares information across tasks; its behaviour depends on grouping/batching and is not guaranteed to improve accuracy. Fix these choices in the experiment manifest. Marginal quantiles are not a sampled joint return path.

**Evidence.** Author results on fev-bench, GIFT-Eval and Chronos Benchmark II support general-purpose evaluation. A separate financial panel study reports benefits from related multivariate inputs and deterioration when unrelated equity/rate groups are mixed. Its historical price/rate errors and assumed training cutoff do not prove clean crypto return skill. [Independent panel study][FINmv]

**Deployment/tuning.** CPU and GPU inference and fine-tuning interfaces are documented. The official 2.1.0 announcement fixes past-only-covariate fine-tuning issues, a concrete reason to pin the package and test covariate behaviour rather than reuse an old example unchanged. Tuning-method and memory feasibility must be checked for the pinned configuration; no 12GB tuning result was reproduced. Approximately 0.48 GB FP32 parameter payload is encouraging but not a VRAM bound. The paper's roughly 300 series/second A10G figure uses a 1,024-series batch, context 2,048 and horizon 64; it is not a 4070 Ti, CPU, cold-start or end-to-end Scryntic measurement. [Paper][CHpaper], [repository][CHrepo], [fine-tuning release][CHtuning]

**Fit and decision.** Strongest first new general-purpose adapter candidate because of its relatively small size, explicit covariate/group semantics and existing source-level research in Scryntic's reference review. Medium integration effort: explicit grouped input and distribution output, pinned runtime and independent calibration. It still needs features from Scryntic; it does not ingest exchange trades/LOB messages as meaningful events automatically.

### 3.4 Kronos-base — domain-specific candle challenger

**Verified architecture/contract.** `NeoQuasar/Kronos-base`: **102.3M predictor parameters plus a separate tokenizer**. A hierarchical discrete tokenizer encodes candles for autoregressive transformer generation. Historical OHLC is required; volume and amount are optional. The helper receives historical and future timestamps and returns forecast OHLCVA frames. Base context is **512 bars**; longer history is truncated. Horizon, temperature, nucleus sampling and sample count are generation controls, not guarantees of reliable long-range prediction. Batched assets are forecast independently. [Card][KRcard], [repository][KRrepo]

**Author evidence.** More than 12 billion financial candles from 45 exchanges, including crypto, are reported. The paper evaluates Binance spot data and financial forecasting/volatility tasks. Its equity backtests use simplified assumptions, including a reported 0.15% per-trade cost setting; this is not a Bybit execution model. [Paper v1][KRpaper]

**Counterevidence.** A separate two-instrument hourly BTC/SPY evaluation reports worse-than-random-walk forecasting and undercovered sample intervals. Scripts/results are available, but this review did not rerun or audit them. The result is a useful falsification lead, not a universal verdict. [Independent repository][KRind]

**Practical fit.** More directly aligned with OHLCV than general telemetry models, but incompatible with close-only input without a richer adapter. Missing volume is not the same as measured zero volume; `amount` units must be mapped explicitly. Average generated paths are not calibrated quantiles, and candle validity must be checked. No native order-book, funding/news covariate or joint cross-asset contract was established.

**Deployment/tuning and decision.** PyTorch, tokenizer and predictor artifacts; public tokenizer/model fine-tuning and Qlib examples exist. Sampling multiplies latency. The predictor alone is about 0.41 GB FP32 by arithmetic; total memory includes tokenizer, activations and samples. No specific 12GB training configuration is established. Medium–high integration effort; prioritize a bounded OHLCV-versus-close ablation after the first general-purpose comparison.

### 3.5 Toto 2.0 — multivariate/scale ablation, not a default large model

**Verified family.** `Datadog/Toto-2.0-{4m,22m,313m,1B,2.5B}`. A patched decoder-only transformer alternates time and variate attention, with arcsinh normalization, quantile training and contiguous patch masking. Authors report internal Datadog telemetry and synthetic training, rather than public forecasting corpora in the base 2.0 training set. [Technical report][TOpaper]

**Contract and release gap.** Numeric `(batch, variates, time)` inputs with observation masks/series IDs; nine quantiles at 0.1–0.9, shaped `(9, batch, variates, horizon)`. Context/horizon vary; paper settings of 2,048/4,096 context are evaluation settings, not hard maxima. The public release explicitly lists **fine-tuning and exogenous-variable support as planned**. Jointly forecasting historical channels is not a released known-future covariate interface. Paper fine-tuned/ensemble results must be separated from downloadable base-model functionality. [Release documentation][TOrepo], [313M card][TOcard]

**Applicability/evidence.** Useful for comparing joint volatility/volume/return patterns and small-versus-medium model size. Telemetry pretraining does not establish crypto-domain competence or order-book semantics. BOOM is Datadog's benchmark; results on external benchmark suites remain author-produced evaluations. No independent crypto profitability replication was established.

**Deployment and decision.** Python 3.12+, PyTorch, documented CPU path, CUDA recommended. Start at **4M/22M**, then 313M only if the experiment calls for capacity. Nominal 2.5B FP32 parameters alone require about 10 GB decimal, leaving little 12GB GPU headroom. Published A100 forward-pass timings are not target-hardware or full-job measurements. Medium–high adapter/dependency effort; postpone large variants and tuning plans until a measurable reason exists.

### 3.6 Moirai 2.0 — lightweight univariate probabilistic control

**Verified identity:** `Salesforce/moirai-2.0-R-small`, **11,387,208 parameters**, around 45.6 MB FP32 payload. A small decoder-only model uses one patch size, multi-token prediction and direct quantile loss, replacing the earlier Moirai masked-encoder/mixture-distribution design. Authors report 36M series combining public, synthetic/Mixup and internal telemetry sources. [Card][MOcard], [paper v1][MOpaper]

**Critical version correction.** Moirai 2.0 explicitly **drops cross-variate forecasting and covariates**; multivariate datasets are processed as independent univariate tasks. Legacy Uni2TS arguments do not restore these capabilities. Public config has patch size 16, six layers, width 384, 512 patch positions and four predicted tokens. The product 512 × 16 = 8,192 scalar positions is a configuration interpretation, not a tested service-context guarantee. Outputs are nine deciles. [Paper][MOpaper], [config][MOconfig]

**Practical value.** An inexpensive candidate for per-series close/return/realized-volatility forecasts and calibration experiments on CPU infrastructure. It is unsuitable for an experiment whose central hypothesis is conditional cross-asset information or funding/news covariates. No crypto/LOB specialization or demonstrated trading benefit was established.

**Deployment/tuning and decision.** Uni2TS/PyTorch supports CPU selection. Generic training infrastructure exists, but a validated model-2-specific tuning recipe on the proposed hardware was not established. Low–medium adapter effort; include as a lightweight control when resource/latency comparison is material. Do not discard it merely because it is smaller than leaderboard leaders.

### 3.7 TiRex-2 — efficient recurrent multivariate alternative

**Verified architecture.** `NX-AI/TiRex-2`: **82,501,744 total parameters**, with approximately 38.4M active for univariate operation and another 44.1M for multivariate functionality. Twelve alternating mLSTM/sLSTM xLSTM blocks combine recurrent time processing and variate attention. The paper describes real/synthetic training, including synthetic multivariate coupling, with later long-context training. [Paper v1][TIpaper], [card][TIcard]

**Contract.** Multiple targets, optional historical and known-future covariates; output `(targets, 9 quantiles, prediction_length)`, with quantiles at 0.1–0.9. Paper training settings reach context 8,192/horizon 512, but are not universal API limits. Asymmetric attention is intended to prevent future-target leakage; it cannot prevent Scryntic from supplying an incorrectly timestamped covariate.

**Public-release distinction.** Introductory text describes a streaming architecture; detailed release documentation places incremental streaming updates, optimized hardware inference and fine-tuning in **TiRex-2 Pro**. Do not promise an available open incremental-state/tuning API. Safetensors and ONNX now appear in the public checkpoint; older search snippets showing only a checkpoint file are stale. [Repository][TIrepo], [current card/files][TIcard]

**Fit and decision.** A technically relevant alternative if context scaling, CPU/ONNX deployment or covariates become the measured bottleneck. CPU/CUDA inference is documented; fused CUDA kernels add compiler/toolkit compatibility work. Approximately 0.33 GB total FP32 payload does not establish total memory or latency. Benchmark-specific decontaminated variants exist and should be selected for matching benchmark replications. Medium–high runtime-qualification effort; near-future targeted evaluation, not a reason to redesign workers around streaming claims.

### 3.8 TradingAgents — research orchestration, not a pretrained forecaster

**Verified identity.** Tauric Research's LangGraph framework coordinates analyst tools, bull/bear research debate, trader and risk/portfolio roles. The current changelog identifies **v0.7.0, 16 October 2026**. Inputs include ticker/date, prices/indicators, news, fundamentals and optional holdings; outputs are reports and decisions. It supports local Ollama/OpenAI-compatible backends and now documents crypto Yahoo tickers such as `BTC-USD`. This is not native Bybit ingestion or an order-book model. There is no single framework parameter count or required pretrained TradingAgents checkpoint. [Repository][TArepo], [changelog][TAlog]

**Evidence and a consequential correction.** The original paper presents author-run US-stock simulations over June–November 2024. Current code is not that experiment. The v0.7.0 changelog changes scoring to the first close after the decision date because dated runs could consume after-close news; recent changes also address filing dates and crypto sentiment. Pinning the framework, data vintages, tool implementations, backbone and scoring convention is therefore essential. No independent cost-complete crypto replication was established. [Paper v1][TApaper], [changelog][TAlog]

**Resources/fit.** CPU orchestration is plausible; actual memory and latency depend on the chosen LLM, prompt length, tool calls and debate concurrency. Running a quantized 4–8B backbone on 12GB is a possibility, not evidence that it reproduces paper quality. Multi-agent debate increases tokens, latency and opportunities for nondeterministic revisions. Fine-tuning belongs to the selected backbone, not to an automatic framework capability.

**Decision:** optional offline analyst/report assistant, later shadow decision experiment. Medium–high tool/data/LLM maintenance. Do not replace Scryntic's provenance, replay, job state or future risk/execution boundaries with role names such as “risk manager.”

### 3.9 Trading-R1 — interesting training research, not yet an available component

**Paper versus release.** The paper describes Qwen3-4B with staged supervised fine-tuning and GRPO reinforcement learning for grounded investment theses and five decision categories. Approximately 100k training examples across 14 equities and 18 months are reported. Labels combine forward 3/7/15-day returns; the operating intent is roughly weekly, with 20–30k-token inputs and 6–8k-token outputs. Author training uses 8×H100 for SFT and 8×H200 for RL. These are author setups, not a requirement measured for a future inference adapter. [Paper v1][TRpaper]

The official repository still announces a forthcoming terminal; a usable official adapted checkpoint, dataset and executable training/inference release were **not verified**. An issue explicitly requests publication. Do not substitute an unrelated similarly named HF checkpoint. [Repository][TRrepo], [release request][TRissue]

**Applicability/evidence.** Its grounded-thesis training could inform a future text-feature experiment. Author stock backtests and a reward cost term do not establish crypto execution/funding realism. Forward-derived labels are legitimate supervised targets only under correct split/maturity rules; reverse-generated rationales and backbone pretraining require separate leakage scrutiny.

**Decision:** watchlist, not an integration commitment. Original training is beyond the proposed workstation. Hypothetical 4B inference cannot be sized from weights alone given the long context/output. CPU latency, adapted-model quality and maintenance cost remain unknown until artifacts exist.

### 3.10 FinRL-X — a trading pipeline, not a universal pretrained policy

**Identity correction.** The verified implementation is **AI4Finance-Foundation/FinRL-Trading**, whose initial **v1.0.0 release is dated 25 March 2026**. Its pipeline covers selection, allocation, timing and risk overlays through target portfolio weights. It includes Yahoo/FMP/WRDS data paths, SQLite, `bt` backtesting, Alpaca execution and strategy/ML scaffolding. Do not infer that the FinRL name supplies a trained crypto policy. [Repository][FXrepo], [release][FXrelease]

**Evidence.** The paper reports US equity/ETF evaluation over January 2018–October 2025, 10 bps transaction costs per side and Alpaca paper trading over October 2025–March 2026. These author results are more specific than a cost-free toy return, but not a reproduced Bybit/perpetual/LOB evaluation. [Paper v1][FXpaper]

**Resources and fit.** Classical ML workflows may run on the CPU server; DRL cost depends on the algorithm, environment, state space and trial budget. There is no meaningful fixed parameter/VRAM number for the framework. Custom training is possible, but reward design and simulator errors can dominate the result. Historical candles cannot determine queue position, partial fills or intrabar liquidation order.

**Decision:** later reference for portfolio-weight contracts or an isolated strategy baseline. Whole-platform adoption has high migration and maintenance cost and duplicates Scryntic's data, state and evaluation responsibilities. No core replacement is justified by the inspected evidence.

### 3.11 FinRobot — document/research assistance with multiple generations

**Verified distinction.** The current repository contains V0 AutoGen educational/reference tooling, a V1 equity web application using OpenAI Agents SDK, and a V2 PydanticAI desktop research system, including a `desktop-v0.1.0` release. The authors' “production” description is not independent qualification. The 2024 paper does not validate every later generation. [Repository][FRrepo], [paper v1][FRpaper]

Inputs are filings, market data and research questions; outputs are valuation analyses, debate and reports. Current design separates deterministic Python financial calculations from LLM narration. This is useful for auditability, but equity filings/DCF tools are not naturally crypto-native signals. No calibrated return distribution, LOB predictor or Bybit execution capability was established.

**Deployment/training.** Framework CPU use is separate from its chosen model/API. Local-endpoint completeness and 4070 Ti quality were not verified. No single parameter count, inference latency or framework-specific fine-tuning guarantee exists. Multiple UI/orchestration generations and external data tools create substantial maintenance surface.

**Decision:** consider selected report/extraction patterns, not a second application platform. Medium–high integration effort. Any eventual document-derived feature must preserve source bytes, publication/receipt times, prompt/model identity and a bounded structured output rather than trust generated prose as evidence.

### 3.12 FinGPT — choose an actual task/checkpoint, not the umbrella name

**Verified artifacts.** FinGPT is a family of data/tooling and adapters. Examples include `fingpt-forecaster_dow30_llama2-7b_lora`, a 7B-base multitask adapter, and a Llama2-13B sentiment adapter. LoRA adapters are not complete standalone models; matching base/tokenizer/runtime artifacts are required. [Repository][FGrepo], [Forecaster card][FGcard]

The Forecaster uses preceding weeks' prices/news and optional fundamentals to produce positive developments, concerns and a next-week directional narrative. This is not a numeric OHLCV path or calibrated probability distribution. Its Dow30 dataset and training task are equity-oriented; crypto transfer is an experiment, not a documented property. Sentiment adapters classify text rather than forecast asset prices.

**Resources/tuning.** Transformers/PEFT/PyTorch/Accelerate and, for quantized paths, bitsandbytes introduce version and kernel work. FP16 7B parameters alone are about 14 GB decimal, beyond a 12GB card before KV cache; 13B is larger still. Quantized 7B inference is plausible with bounded context; CPU offload may fit RAM but changes latency. LoRA/QLoRA support does not establish that every released task can be trained on 12GB. [Setup documentation][FGsetup]

**Decision:** later text-feature/narrative challenger after a timestamped text baseline exists. Compare against FinBERT and simple lexical features on identical text snapshots. Medium–high runtime/data effort; no validated net-cost crypto advantage was established.

### 3.13 FinBERT — the simplest dedicated NLP baseline here

**Verified contract.** ProsusAI FinBERT is an English BERT-base classifier: 12 layers, width 768, 12 attention heads and approximately **110M parameters**. Financial-domain language pretraining and sentiment fine-tuning yield positive/negative/neutral scores. These are text-class probabilities, not probabilities of a positive future return. [Card][FBcard], [paper][FBpaper]

**Applicability.** Potentially useful for aggregating dated financial news into lagged asset-level sentiment, event intensity or disagreement. Crypto slang, social text, multilingual content and entity ambiguity create domain shift. Long documents need deterministic chunking/aggregation and deduplication; sentiment results do not supply publication-time correctness or evidence of economic usefulness.

**Deployment/tuning.** Approximately 0.44 GB FP32 parameter payload by arithmetic. Ordinary inference batches are a plausible CPU or 12GB-GPU task; no throughput or fine-tuning benchmark was run. Training code exists, but retraining and calibration must be separated from final evaluation. [Repository][FBrepo]

**Decision:** first dedicated sentiment baseline if/when Scryntic has a qualified point-in-time text dataset. Low technical adapter effort relative to LLM agents; data/availability/entity engineering remains substantial. No immediate value should be presumed while the verified source path is candle-only.

### 3.14 Additional candidate: LightGBM and small supervised baselines

LightGBM materially changes the comparison because it can learn directly from the return, volatility, range, volume and cross-asset feature table proposed for Scryntic; that rich table is not claimed to exist in the current implementation. It is histogram-based gradient boosting with task-specific regression/classification and quantile objectives. It has no mandatory pretrained corpus or universal parameter count. Training, feature engineering and inference are plausible CPU workloads; actual model size follows the registered tree/depth/leaf budget. The forecast horizon is defined by the supervised target, not a native model duration. [Official project][LGB]

A supervised tabular model provides a useful counterfactual: does a foundation model add value beyond simple features fitted only on allowed history? Separate quantile models may cross; probabilities and intervals require explicit validation/calibration. A small model is not automatically leakage-free.

**Decision:** high-priority baseline after return/feature/training contracts exist. Low–medium component effort, with feature/target validation shared by all models. The project's prior literature review already explains why tabular evidence does not prove financial superiority; do not repeat retail-demand benchmark rankings as crypto evidence.

### 3.15 Additional candidate: a pinned TabPFN-2 regression experiment

TabPFN-2 is a pretrained transformer performing in-context prediction over labelled tabular examples, with synthetic prior-data training. The 2025 study supports small/medium tabular comparisons, not a direct crypto forecasting claim. The regression checkpoint is a materially different experiment from a univariate time-series foundation model. [Model card][TP2], [Nature paper][TPpaper]

Construct a causal feature table; fit/condition only on labels matured at each origin. The model does not infer chronological splits from row order. Context rows/features, inference ensemble settings and cached training representations affect memory; current family-level GPU guidance is not a measured requirement for the exact version-2 checkpoint. CPU execution of large tables can be slow, and no target-machine throughput was established. Pin version 2 if reproducing version-2 evidence rather than silently selecting a newer family default. [Current project documentation][TPcurrent]

**Decision:** bounded small-data challenger after linear/GBM baselines. Medium runtime/memory effort. Extend to other architectures only if this study or measured residual errors identify a specific missing capability; there is no technical basis here for a catalogue of every available TSFM.

## 4. Comparative evaluation and decision matrix

### 4.1 Category, practical fit and experiment order

Effort is qualitative: **low** reuses an existing narrow boundary; **medium** needs an adapter/runtime and new contract fixtures; **high** crosses data, training, orchestration or execution boundaries. These are not person-day estimates or implementation commitments.

| Candidate | What it actually is | Main input → output | Cross-asset / covariates | Technical disposition | Effort |
|---|---|---|---|---|---|
| TimesFM 2.5 | Pretrained numeric forecaster | Scalar history → point/quantile forecasts upstream; points in Scryntic | Independent series; XReg path | **Current control**; extend only for a hypothesis | Low existing; medium extensions |
| TimesFM 3.0 | Pretrained multivariate forecaster | Target/covariate arrays → forecasts/quantiles | Native variate attention; past/future-known covariates | **First new comparison pair**, with Chronos-2 | Medium |
| Chronos-2 | Pretrained grouped numeric forecaster | Targets/masks/covariates → quantiles | Native groups; categorical/real covariates | **First new adapter candidate** | Medium |
| Kronos-base | Financial candle tokenizer + forecaster | OHLC(VA)/timestamps → stochastic candles | Independent batch items; fixed candle schema | **Near-future domain ablation** | Medium–high |
| Toto 2.0 | Multivariate pretrained model family | Masked numeric panels → nine quantiles | Joint historical channels; exogenous support unreleased | **Targeted small-size comparison**; postpone large defaults | Medium–high |
| Moirai 2.0 | Small univariate pretrained forecaster | Scalar histories → nine quantiles | Cross-variate/covariates dropped | **Lightweight probabilistic control** | Low–medium |
| TiRex-2 | Recurrent multivariate forecaster | Targets/covariates → nine quantiles | Native multivariate/covariates; public streaming gap | **Targeted efficiency/covariate alternative** | Medium–high |
| TradingAgents | LLM/tool orchestration framework | Dated market/text inputs → reports/decisions | Tool-mediated; not a joint return distribution | **Later offline/shadow assistant**; no core replacement | Medium–high |
| Trading-R1 | Paper-described trained reasoning model | Long asset-day context → thesis/action | Equity training; no verified usable release | **Watchlist**, unavailable as a reviewable component | Unknown/high |
| FinRL-X | Trading infrastructure/strategy pipeline | Features → portfolio weights/backtests/orders | Portfolio-oriented; venue adaptation needed | **Long-term reference**, reject whole-stack adoption now | High |
| FinRobot | Multi-generation research application/framework | Documents/data → valuation/reports | Tool-mediated equity research | **Later document assistant/pattern reference** | Medium–high |
| FinGPT | NLP adapters and training tooling | News/prices/prompts → sentiment/narrative | Entity/prompt aggregation; not native joint numeric forecasting | **Later text-feature challenger** | Medium–high |
| FinBERT | Sentiment classifier | English text → three sentiment scores | External aggregation by entity/asset | **First NLP baseline once text data exists** | Low model; higher data work |
| LightGBM | Supervised tabular learner | Causal features → regression/classes/quantiles | Via explicit panel features and labels | **High-priority baseline** | Low–medium |
| TabPFN-2 | Pretrained tabular in-context learner | Labelled feature table → predictions/distributions | Via explicitly causal feature table | **Bounded additional challenger** | Medium |

This order reflects interface fit, availability and evidence quality, not a measured ranking on Scryntic data.

### 4.2 Market-feature compatibility

| Feature or use case | Most relevant candidates | What must not be inferred |
|---|---|---|
| Close/return sequences | TimesFM 2.5/3, Chronos-2, Moirai-2, TiRex-2, Toto-2 | A price-level forecast win is not a return forecast win. |
| Joint candle generation | Kronos-base | Generated OHLCVA is not a calibrated execution scenario or a valid book path. |
| Volume/range/realized volatility | Numeric models as targets or historical channels; GBM features | Candle range is not a true high-frequency realized-variance observation. |
| Funding, basis, open interest | Explicit past covariates/features for Chronos-2, TimesFM-3, TiRex-2 or GBM | Future realized funding/volume is not a known-future covariate. Toto's public exogenous interface is unreleased. |
| Related assets/venues | Chronos-2, TimesFM-3, TiRex-2, Toto-2; explicit GBM panel features | Batching independent Kronos/Moirai forecasts does not learn cross-asset dependence. |
| News/sentiment | FinBERT, selected FinGPT adapters; bounded agent tools | Positive sentiment is not a calibrated probability of profitable buying. |
| Raw trades and order books | None established as a ready-made event/LOB solution in this review | Flattening depth into columns loses ordering, queue and execution semantics unless explicitly designed and tested. |
| Trade decisions and execution | FinRL-X/TradingAgents offer relevant higher-level abstractions | A framework decision or simulated order is not a reconciled exchange fill or independent risk approval. |

### 4.3 What the independent evidence changes

The broad GIFT-Eval project explicitly tracks training/test leakage metadata and distinguishes zero-shot submissions; “zero-shot” is not a universal certificate that a market period was absent from pretraining. Leaderboards are versioned task/loss comparisons, often submitted by model authors. They do not compare the full same-origin, same-cost Scryntic pipeline. [Benchmark documentation][GIFT]

Two independent 2026 preprints are particularly useful, without being decisive:

- **Financial returns:** Alonso/Franklin compare several TSFMs and locally trained neural models on five US equities under rolling origins and equal context. Strong relative ranks coexist with small/sparse improvements over a zero-return baseline and limited significance. This directly argues for retaining simple baselines. Its universe is not crypto, and it is not a cost-complete trading replication. [Version read][FINeval]
- **Related versus unrelated multivariate context:** a Chronos financial-panel study finds gains for related equity/rate series and deterioration when unlike domains are mixed. It reports forecast errors, not net trading performance. Its pre/post-2023 comparison does not establish the actual absence of those observations from the exact Chronos-2 pretraining corpus. Treat the leakage conclusion more cautiously than the authors do. [Version read][FINmv]

The independent Kronos BTC/SPY result adds a concrete counterexample to “financial pretraining implies superior market forecasting,” but its two-instrument scope and unreplicated implementation do not settle adoption. No reviewed source establishes a common, independently reproduced crypto-profitability ranking across all candidates. This is a limitation of this bounded review, not an exhaustive claim that no such study exists.

A focused Consensus search also identified a separate TimesFM realized-volatility study. Its primary v1 text studies 21 global equity indices over 2000–2021 and reports that incremental adaptation can outperform static pretraining and econometric controls under several volatility losses. It concerns an earlier TimesFM configuration, a different target and non-crypto data. It supports a **separate volatility experiment**, not a claim that current TimesFM 2.5/3.0 predicts returns profitably. [Primary version][VOLpaper]

## 5. Future applications and integration approaches

### 5.1 Preserve interchangeability at the semantic boundary

The existing provider/worker split is the starting point. Interchangeability means equivalent declared targets, information cutoffs and output semantics, not forcing every vendor API into the same arrays. Preserve the existing restricted native-decoder boundary; model workers should receive admitted bounded inputs rather than gain arbitrary Parquet, filesystem or network access through a vendor loader.

| Proposed application-owned boundary | Required behaviour | Candidate-specific adaptation |
|---|---|---|
| Dataset/feature snapshot | Immutable inputs, origin/availability times, units, masks, quality and universe/version mappings | Close-only views, OHLCVA views, grouped numeric panels and text views are distinct recipes over the same admitted evidence. |
| Task specification | Target definition, frequency, duration/step horizon, feature/covariate roles, context policy, seed and deadline | Reject a model lacking the requested capability; do not silently drop a covariate or shorten history. |
| Capability descriptor | Numeric versus text task, univariate/joint targets, masks, past/future covariates, quantiles/samples, tuning and runtime profile | Describe the actual pinned release. “Streaming architecture” or a legacy argument name is insufficient. |
| Isolated execution | Offline admitted artifacts/runtime; bounded CPU/GPU/RAM/scratch/time and cleanup | Vendor imports/loaders remain inside the worker. Provisioning and artifact transport remain separate. |
| Versioned result | Finite values, target/horizon bindings, units, quantile grid or sample-path semantics, provenance and abstention | Preserve point-only v1; use explicit new schemas for distributions and richer features. |
| Evaluation/publication | Paired scoring, calibration identity, exclusions, pre-outcome acceptance and matured outcomes | Model workers cannot change labels, scoring policy, registry state or acceptance authority. |

These are refinements of the target architecture's existing proposed v2 contracts, not newly delivered interfaces. Keep one durable job state machine, existing attempt fencing/cancellation and immutable result acceptance. A vendor framework's scheduler, experiment tracker or database should not become a second authority for the same task.

Numeric adapters should distinguish:

- **Historical covariate:** observed by the forecast origin, e.g. lagged volume or the last published funding estimate.
- **Known-future covariate:** genuinely known at the origin, e.g. calendar features. Realized future volume, price or funding does not qualify.
- **Forecast target:** an unknown variable to predict jointly. A model's attention mechanism cannot repair an incorrect availability label.

For multi-exchange panels, group by a declared economic hypothesis: related assets or the same instrument across venues after product/unit alignment. Record the complete group composition at every origin. Avoid selecting assets using the future winning universe, mixing assets with unequal information cutoffs or allowing later observations from one market into an earlier prediction for another.

### 5.2 Target and uncertainty translation

Start with a clearly defined target such as `r(t,h) = log(P(t+h) / P(t))`, using positive, finalized prices and explicit observation delay. Price projection and return forecast are related but different quantities.

- A monotone terminal-price transformation can convert positive terminal-price quantiles to terminal log-return quantiles when the origin price is known and fixed.
- `log(E[P]/P0)` is not generally the expected log return. Do not label transformed point means as expected return without justification.
- Marginal quantiles at several steps cannot be added to obtain a cumulative-return interval. Use an explicit cumulative target or a justified joint path model.
- A median or nine deciles does not automatically supply `Pr(return > 0)` or tail risk outside the covered grid. A calibrated CDF/interpolation assumption, distribution or sufficiently validated samples are required.
- Kronos sample paths require candle validity, repeatable sampling settings and empirical coverage checks. An average candle path is not a probability distribution.
- FinBERT sentiment scores and FinGPT/agent action labels must remain text/decision features unless a separate trained, calibrated mapping to future returns is evaluated.

Abstention, missing output capabilities and expired forecasts should remain explicit. Reject invalid quantiles/probabilities and unit mismatches rather than silently sorting, clipping or inventing uncertainty.

### 5.3 When combinations earn their complexity

The most useful initial combination hypothesis is **tabular features + one numeric probabilistic forecaster**, with a later independent text signal. Compare against the best single component under the same information set.

Begin with an equal-weight or simple train-only residual correction. Estimate any weights/gates from chronological out-of-fold predictions, using only outcomes matured by the fitting date. Freeze the ensemble before final qualification. Do not add multiple TSFMs merely because each leads a different published leaderboard.

Measure incremental return-loss skill, calibration, correlated errors, abstention, operational failure rate, full-job latency and economic turnover. A volatility specialist may improve interval width/risk estimates while adding no directional skill; that is a useful separate outcome. A regime-gating model must itself be trained causally and compared with a fixed ensemble. Keep the simpler model when improvements are small, unstable or outweighed by cost.

## 6. Hardware and deployment considerations

### 6.1 RTX 4070 Ti 12 GB and 128 GB system RAM

The following are **weight-payload arithmetic or documented model sizes**, not measured VRAM/RAM requirements. Entries use FP32 unless explicitly labelled FP16. FP32 payload is approximately `4 × parameter_count`; activations, attention/KV state, gradients, optimizer states, workspaces, tokenizer and runtime are additional. File-size units and nominal family names may differ from exact stored parameter counts.

| Candidate | Approximate raw parameter payload | Practical workstation interpretation |
|---|---:|---|
| TimesFM 2.5 | 0.93 GB, including stored head parameters | Small enough to justify a bounded GPU experiment; existing Scryntic profile is CPU-only. |
| TimesFM 3.0 | 1.32 GB at 330M | Plausible inference candidate; variate count/context/batch can dominate memory. |
| Chronos-2 | 0.48 GB | Plausible first inference/tuning investigation; training fit is not established. |
| Kronos-base | 0.41 GB predictor, tokenizer extra | Sampling count and autoregressive horizon affect latency/memory. |
| Toto 2.0 | Nominal 0.016/0.088/1.25/4/10 GB for 4M/22M/313M/1B/2.5B | Start small. The largest FP32 model leaves little headroom on 12GB. |
| Moirai 2.0 small | 0.046 GB | Strong lightweight-control candidate; runtime overhead exceeds the tiny weight payload. |
| TiRex-2 | 0.33 GB total | Plausible inference; assess ordinary public CPU/CUDA/ONNX paths separately. |
| FinBERT | 0.44 GB | Bounded sentiment inference is plausible; batching/text length still matters. |
| FinGPT 7B/13B bases | About 14/26 GB in FP16, adapters additional | FP16 exceeds 12GB. Quantization/offload needs a separately validated configuration. |
| Trading-R1 | A hypothetical 4B backbone is about 8 GB FP16 | Long context/output may dominate; no adapted release is available to qualify. |
| Agent frameworks | Determined by selected backbone and concurrent contexts | Prefer one shared, bounded backend with controlled concurrency over loading a model per role. |
| LightGBM / TabPFN-2 | Task/checkpoint/configuration dependent | Measure feature-table and context/ensemble memory, not a generic framework VRAM number. |

128GB host RAM helps artifact staging, feature batches and CPU offload; it does not become GPU memory at GPU bandwidth. Sequentially evaluate models before considering simultaneous serving. Include display/other-process headroom where applicable, and record actual dtype/device rather than assuming every model tolerates the same precision or quantization.

No fine-tuning capacity is guaranteed from inference payload. Optimizer states, trainable layers, backpropagation activations, sequence length and accumulation determine training memory. LoRA lowers some costs but does not eliminate activation memory. Large-model agent training and Trading-R1's reported original training are not sensible default workloads for this card.

### 6.2 Separate CPU-only infrastructure

Keep collection, durable coordination and artifact acceptance CPU-oriented. Existing qualified CPU TimesFM inference is an operational reference; each new runtime still needs its own resource/isolation evidence. Public CPU paths exist for the numeric candidates, but their usable latency on the intended server is unknown.

Prioritize statistical/LightGBM baselines, small Moirai/Toto variants and bounded text classification for CPU feasibility checks. Compare TiRex's actual available CPU/ONNX path rather than promising Pro optimizations. Larger LLM offload can consume host RAM and tie up cores for long periods; report orchestration is a more plausible role than latency-sensitive market decisions until measured.

Prefer batch/shadow forecasting for hour/day/week horizons. CPU fallback is a capability of a separately qualified profile, not an automatic downgrade from any GPU configuration. If a runtime/profile is unavailable, return an explicit unavailable/abstain result or select an already approved baseline; do not silently change numerical behaviour.

### 6.3 What a future resource experiment must measure

Use fixed snapshots and a grid of context length, horizon, number of variates, batch size, dtype and sample count. Report cold provisioning/load time, warm inference, complete job time, CPU/RSS/cgroup observations, peak GPU allocation and host staging/I/O. Include copying and isolated-worker startup: a vendor forward-pass benchmark omits these costs.

Measure median and tail latency over repeated runs, with versions, hardware/driver and thread settings. Separately exercise cancellation, deadlines, worker crash, malformed output, resource exhaustion and stale completion acceptance. Existing CPU qualification does not qualify GPU device/driver access. No driver, kernel, runtime or isolation changes are proposed as part of this document.

## 7. Reproducible evaluation methodology

### 7.1 Register the question before selecting a winner

For each experiment freeze the universe, source vintages, feature/target definitions, forecast origin, sampling frequency, duration horizon, context/grouping rules, model/checkpoint/runtime, seed policy, budgets, candidate family and stopping rule. Include failed trials and human/agent-directed changes. Reserve an untouched final chronological interval before any feature/model comparison; do not retroactively call an inspected period sealed.

Start with liquid Bybit candle series supported by actual available history. Universe selection must use point-in-time listing/liquidity information. Report exclusions, delistings, gaps, late backfills and finality uncertainty. A BTC-only success does not support a multi-asset claim; a convenient current top-coin list is not a survivorship-free historical universe.

### 7.2 Walk-forward, maturity and leakage controls

Use global timestamp cutoffs across assets. Within each development fold:

1. Fit transforms, feature selection and supervised models only on data/labels available by the fit cutoff.
2. Purge training samples whose label-information intervals overlap the evaluation interval or whose outcomes have not matured. Preserve F19's conservative boundary semantics; a row-count gap alone is insufficient for multiple horizons/calendars.
3. Use a later development/validation interval for hyperparameters and a distinct chronological calibration interval, or strictly matured out-of-fold calibration predictions. Refit only according to the registered rolling/expanding schedule.
4. Generate forecasts before reading their labels, score when outcomes mature, then advance the origin. Store predictions and all eligible/excluded/abstained cases.
5. Freeze the selected candidate and calibration for final qualification. Follow with prospective shadow forecasts durably accepted before outcomes are observable.

Do not pool panel rows and apply a random split. Shared asset histories, overlapping returns and common market shocks invalidate independent-row assumptions. Backfilled data and revised news/fundamentals must not receive an earlier availability timestamp. Deduplicate news/reprints and preserve first-seen, published and revised times separately.

Foundation-model contamination is a separate issue from Scryntic split correctness. Record documented pretraining cutoffs, benchmark exclusions, checkpoint publication dates and unknown corpus coverage. A public release before a prospective observation is stronger evidence than assuming all post-2023 data was unseen. Decontaminated benchmark checkpoints apply to their stated benchmark, not automatically to Bybit history.

### 7.3 Baselines and task-specific metrics

| Task | Required controls | Primary diagnostics |
|---|---|---|
| Return/terminal-price forecast | Zero return/random-walk price; train-only historical mean; regularized linear/AR; LightGBM | MAE/RMSE in return units, paired loss differences and skill `1 - loss(model)/loss(baseline)`; report nonpositive/undefined denominator handling. |
| Direction | Train-only class base rate and simple logistic classifier | Brier score, log loss, reliability/calibration and discrimination; accuracy alone can reward a majority class. |
| Probabilistic return forecast | A simple residual/volatility-scaled predictive distribution | Pinball loss; CRPS only with supported samples/distribution or a declared approximation; interval coverage **and width**, PIT/rank diagnostics where defined. |
| Volatility | Historical/EWMA and appropriate GARCH/HAR-style controls if inputs support them | Consistently scaled variance/volatility losses, including QLIKE for valid positive variance forecasts, plus stability across regimes. |
| Cross-sectional ranking | Simple lagged momentum/factor/rank controls | Per-origin IC/rank IC, dispersion, coverage and dependence-aware uncertainty; distinguish ranking from single-asset timing. |
| Text features | No-text model, lexical baseline and FinBERT before larger LLM/agents | Incremental forecast/calibration/economic value, not sentiment benchmark accuracy alone; original/masked-identifier ablations where relevant. |

MASE requires a declared, nondegenerate training-only scale. MAPE on returns near zero is inappropriate. Aggregate price errors across differently priced assets can be misleading. Report per-asset/horizon/regime results, equal/declared weighting, baseline-relative improvements, exclusion counts and effective sample information. Do not select only the metric that makes one model appear best.

Calibration must be learned without final-test outcomes. Quantile coverage during one regime is not a guarantee under regime change. Conformal or residual corrections can be tested, but their exchangeability/dependence assumptions need explicit treatment; a method name is not a calibration certificate.

### 7.4 Statistical comparison and multiple testing

Use paired origin-level losses on the same eligible examples. Time-block/bootstrap or justified dependence-adjusted tests should preserve serial dependence and same-origin cross-asset relationships. Choose blocks/lag settings with the horizon and data dependence in mind. Show effect sizes and uncertainty, not only p-values.

Account for all models, features, horizons, seeds, prompt variants, ensembles and repeated looks. A model selected from many trials needs selection-bias-aware evidence. The project's prior supplement records an important restriction: the original Hansen SPA formulation is not automatically applicable to recursively expanding parameter refits. Specify fixed/rolling/expanding estimation and validate the chosen test's assumptions rather than attach an unqualified SPA p-value. [Existing statistical-method review](CONSENSUS_FOLLOWUP_2026-10-18.md), [original paper][SPA]

There is no universal minimum Sharpe, p-value, coverage tolerance or improvement percentage justified by these sources. Set meaningful margins and resource ceilings before the experiment, accounting for the number of effectively independent outcomes. Weekly/monthly claims may require a long forward observation period.

### 7.5 Economic diagnostics remain separate from forecasting skill

Freeze a simple forecast-to-exposure rule using development data. Compare it with flat exposure and suitable buy/hold or risk-matched controls under the same capital, leverage and execution assumptions. Report gross and net results, including maker/taker fees, bid–ask spread, slippage, latency, turnover, market impact and capacity. Perpetuals require funding cash flows and margin/liquidation mechanics; shorts may require borrow costs. Venue/instrument-specific schedules and historical changes must be recorded.

Use realistic decision-to-fill delay. A forecast using a bar's finalized close cannot necessarily trade at that same close. OHLC bars do not reveal the order of intrabar stops/targets, depth or queue priority. Report conservative alternatives or refuse the fill claim rather than invent an execution path.

Include net return, drawdown and recovery, volatility, Sharpe/Sortino with explicit annualization and risk-free convention, Calmar where meaningful, expected shortfall/tail losses, turnover, exposure, trade count, funding and cost attribution. Crypto's continuous calendar must not silently inherit a 252-day convention. Use dependence-aware confidence intervals and account for selection bias in attractive Sharpe ratios. Report stress scenarios and periods with no usable forecast.

These are research diagnostics. Forecast accuracy does not authorize trading, and a positive simulated net return does not establish live execution safety.

### 7.6 Reproducibility record

Retain snapshot/manifests and source hashes, feature/target/split/calibration versions, model and tokenizer revisions, runtime/dependency/source/config digests, sampling settings, grouping/batching, seeds, device/dtype/thread settings, hardware and resource observations. Persist predictions before attaching labels. Preserve trial lineage and both original/revised outcomes.

Differentiate deterministic canonical replay, same-environment numerical repeatability and cross-device tolerance. A seed alone does not guarantee identical GPU results or stochastic sampling across library versions. The current F22 TimesFM repeatability result is not a guarantee for another adapter, device or tuning pipeline.

## 8. Prioritized research/adoption order and unresolved questions

### 8.1 Suggested sequence, without changing the project roadmap

| Timing/suitability | Suggested research | Entry condition and decision rule |
|---|---|---|
| **Currently useful** | Existing TimesFM 2.5 control, current replay/provenance evidence and this source dossier | Use for operational comparison; make no new predictive-skill claim from F22's small sample. |
| **First evaluation layer** | Feasible return horizons; statistical/linear and LightGBM baselines; chronological calibration/forward records | Requires the target/training/evaluation extensions already described in the architecture. Retain the baseline on null evidence. |
| **First new foundation comparison** | Chronos-2 and TimesFM 3.0, initially small groups and matched scalar-input controls | Same target, cutoff, eligible samples and trial budget. Separate gains from extra information versus architecture. |
| **Near-future targeted comparisons** | Kronos OHLCV versus close-only; small Moirai/Toto; TiRex-2 efficiency/covariates; pinned TabPFN-2 | Add only when the capability tests a different hypothesis or resource bottleneck. No automatic progression through every model. |
| **Later complementary signals** | Volatility specialist, FinBERT then selected FinGPT/text features; small ensembles | Needs appropriate timestamped data and incremental out-of-time benefit after complexity/cost. |
| **Long-term possibilities** | Bounded TradingAgents/FinRobot research support; isolated FinRL-X portfolio reference; released Trading-R1 if it appears | Separate data/tool/LLM qualification and authorization. Execution remains a distinct program. |
| **Not worth adopting now** | Whole agent/trading frameworks as the Scryntic core; unreleased/proprietary-only feature assumptions; largest models by default | Duplicated authority, missing artifacts, unsupported interfaces and unmeasured benefit outweigh current evidence. |

This sequence aligns with the current architecture's baseline/target → point-in-time features → controlled model comparison → qualified workers progression. It creates no tickets, dates, delivery promises or new roadmap dependencies.

### 8.2 Gaps to resolve before implementation

1. **Targets and sample feasibility:** qualified resampling/duration labels, enough matured examples at each horizon, and return-aware scoring beyond current price persistence.
2. **Rich inputs/results:** OHLCVA and grouped covariate recipes, explicit masks/units/availability, quantile/sample contracts and calibration identity while preserving v1 readers/results.
3. **Training and qualification:** fixed bounded fit/evaluate tasks, trial/holdout access accounting, nested walk-forward evaluation and prospective ledger/registry. Forecast-only providers are not training interfaces.
4. **Data families:** actual trades/LOB, funding/basis/open interest or news pipelines if those hypotheses earn investigation. Model choice must not manufacture unavailable data.
5. **Hardware/runtime:** measured full-job resource profiles, supported precision/kernel paths, offline artifact loading and separate GPU qualification. Public CPU/GPU examples do not satisfy current effective-control checks automatically.
6. **Model-specific unknowns:** clean pretraining overlap, target-machine latency, tuning memory and calibration under regime change; public Toto exogenous/tuning support, TiRex incremental-state availability and Trading-R1 release status.
7. **Economics and execution:** a separately specified conservative simulator/strategy/risk/executor if later authorized. No current forecast benchmark supplies realistic Bybit fills by itself.

### 8.3 Principal technical risks

- **Target mismatch:** excellent price-level or general-domain forecast ranks with negligible return skill.
- **Leakage:** foundation pretraining overlap, late/revised features, survivorship, immature labels, asynchronous cross-asset information or strategy tuning on final outcomes.
- **False certainty:** marginal quantiles treated as joint paths; sentiment/action scores treated as return probabilities; nominal confidence intervals left uncalibrated.
- **Operational mismatch:** a small weight file mistaken for a bounded process; GPU/container assumptions substituted for effective isolation; worker-created outputs trusted without validation.
- **Framework coupling:** a second scheduler/registry or autonomous tool chain bypassing durable jobs, promotion or risk boundaries.
- **Maintenance drift:** model name, package version, checkpoint, prompt/data tool and scoring convention changing independently. TradingAgents' recent timing correction is a concrete example.

The appropriate stopping outcome is allowed to be “no new model adds measurable value.” Scryntic can improve its evidence, calibration, throughput and reproducibility without discovering profitable alpha.

## 9. Sources, versions and verification record

### 9.1 Retrieval and evidence depth

Public sources were retrieved primarily through the installed **self-hosted Firecrawl**, using search and targeted scraping; adequate primary-source browsing from the earlier, interrupted research was reused. Only public documentation/paper/model metadata was sent to retrieval services. No model weights or market datasets were downloaded, no models were executed, and no private repository contents or credentials were uploaded.

One focused Consensus search and two paper-record fetches supplemented independent-literature discovery. The realized-volatility paper was then read in primary HTML, rather than relying on its abstract. Existing project research was reused instead of repeating its academic/system review. This is a targeted technical review, not a systematic literature search or benchmark replication.

All newly retrieved external sources below were checked on **2026-10-19**; the reused Nature/TabPFN-2 and SPA evidence retains the project's **2026-10-18** read-depth record. Branch-based cards/READMEs are dated snapshots, not immutable artifact pins. Paper URLs specify the version read; a later paper version or release may differ. Failed/not-found pages were excluded as evidence, including an obsolete Chronos tuning-notebook URL. Its failure does not establish missing fine-tuning functionality.

### 9.2 Primary source ledger

| IDs and direct sources | What was inspected / evidence limit |
|---|---|
| [TFMrepo], [TFM25], [TFM25api] | Official TimesFM repository/card/metadata; existing Scryntic checkpoint is 2.5. Current API SHA matches the locally recorded revision. Original TimesFM paper is background, not a complete 2.5/3 benchmark report. |
| [TFM3], [TFM3blog], [TFM3config] | Public model-3 identity, architecture/training claims and config. Blog benchmark results are Google-produced, not reproduced here. |
| [CHrepo], [CHcard], [CHpaper], [CHapi], [CHtuning] | Chronos-2 interface, architecture, training/evaluation methods, metadata and official fine-tuning correction; source-level pinned pipeline review also reused from `REFERENCE_SYSTEMS.md`. |
| [KRrepo], [KRcard], [KRpaper] | Kronos contract, tokenizer/predictor, fine-tuning examples, financial training/experiments and limitations. |
| [KRind] | Separate BTC/SPY evaluation README/methods/results, dated run 2026-08-04; scripts were not executed or audited. |
| [TOrepo], [TOcard], [TOpaper] | Toto-2 public release, shapes/model sizes and paper architecture/evaluation/limitations. Distinguishes paper tuning results from unreleased public functionality. |
| [MOrepo], [MOcard], [MOpaper], [MOconfig] | Moirai-2-specific architecture/config and removal of covariates/cross-variate forecasting; generic Uni2TS interfaces are not capability proof. |
| [TIrepo], [TIcard], [TIpaper] | TiRex-2 public inference, current formats, multivariate architecture and public/Pro feature distinction. |
| [TArepo], [TAlog], [TApaper] | Current TradingAgents v0.7.0 and original paper; recent point-in-time/scoring changes are material to evaluation. |
| [TRrepo], [TRpaper], [TRissue] | Trading-R1 training/backtest paper and incomplete public release evidence. No usable official adapted checkpoint verified. |
| [FXrepo], [FXrelease], [FXpaper] | FinRL-X canonical repository, release and author equity/paper-trading evaluation. |
| [FRrepo], [FRpaper] | FinRobot generations and original paper. No full V2 source audit or independent production qualification. |
| [FGrepo], [FGcard], [FGsetup] | FinGPT family versus an actual Forecaster adapter, input/output task and runtime/tuning instructions. |
| [FBrepo], [FBcard], [FBpaper] | Official card/repository architecture and task; original paper identification/abstract only, without paper-methods/results review. No crypto trading benchmark inferred from sentiment scores. |
| [LGB], [TP2], [TPpaper], [TPcurrent] | Official additional-candidate interfaces checked 2026-10-19; Nature study evidence reused from the 2026-10-18 project review, which records indexed primary passages and unavailable direct article retrieval. Target-machine suitability remains unmeasured. |
| [GIFT] | Benchmark leakage/zero-shot metadata conventions, not a financial profitability leaderboard. |
| [FINeval], [FINmv], [VOLpaper] | Primary HTML methods/results/limitations for separate financial studies. Preprints/version-specific results; neither Scryntic nor crypto profitability replications. |
| [SPA] | Statistical-method constraint reused from the project's 2026-10-18 focused review; primary indexed text/partial read depth is recorded there, not newly represented as a full proof review. |

### 9.3 Exact model identities observed

| Checkpoint | HF revision observed on 2026-10-19 | Qualification |
|---|---|---|
| `google/timesfm-2.5-200m-pytorch` | `1d952420fba87f3c6dee4f240de0f1a0fbc790e3` | Same identity as existing F22 documentation; weights not downloaded in this task. |
| `amazon/chronos-2` | `29ec3766d36d6f73f0696f85560a422f50e8498c` | Current API metadata; no weight hash/inference validation performed. |
| `NeoQuasar/Kronos-base` | `2b554741eca47781b64468546e77fef3e85130e6` | Predictor identity; tokenizer is a separate dependency requiring its own pin. |
| `Datadog/Toto-2.0-313m` | `a7bab288f5e95f8606f8306f86659357e1c001ef` | This revision identifies the inspected 313M representative, not all five model sizes. |
| `Salesforce/moirai-2.0-R-small` | `30f43ff08c8494f4943ae1521e9d4e94a0fbb389` | Model-2-specific checkpoint. |
| `NX-AI/TiRex-2` | `30788a311699acba7e6393ad9700ea7b7cb9bb2d` | Current public metadata; format/support changes must still be reviewed at implementation. |

Other source identities in this report are explicit model IDs, paper versions or release tags. Do not treat live alias retrieval as a reproducible weight artifact; any future experiment must pin and verify all admitted files independently.

[TFMrepo]: https://github.com/google-research/timesfm
[TFM25]: https://huggingface.co/google/timesfm-2.5-200m-pytorch
[TFM25api]: https://huggingface.co/api/models/google/timesfm-2.5-200m-pytorch
[TFM3]: https://huggingface.co/google/timesfm-3.0-pytorch
[TFM3blog]: https://research.google/blog/timesfm-3-a-zero-shot-foundation-model-for-multivariate-forecasting/
[TFM3config]: https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/config.json
[CHrepo]: https://github.com/amazon-science/chronos-forecasting
[CHcard]: https://huggingface.co/amazon/chronos-2
[CHpaper]: https://arxiv.org/html/2510.15821v1
[CHapi]: https://huggingface.co/api/models/amazon/chronos-2
[CHtuning]: https://github.com/amazon-science/chronos-forecasting/discussions/390
[KRrepo]: https://github.com/shiyu-coder/Kronos
[KRcard]: https://huggingface.co/NeoQuasar/Kronos-base
[KRpaper]: https://arxiv.org/html/2508.02739v1
[KRind]: https://github.com/sumit-ai-ml/kronos-market-eval
[TOrepo]: https://github.com/DataDog/toto/tree/main/toto2
[TOcard]: https://huggingface.co/Datadog/Toto-2.0-313m
[TOpaper]: https://arxiv.org/html/2605.20119v1
[MOrepo]: https://github.com/SalesforceAIResearch/uni2ts
[MOcard]: https://huggingface.co/Salesforce/moirai-2.0-R-small
[MOpaper]: https://arxiv.org/html/2511.11698v1
[MOconfig]: https://huggingface.co/Salesforce/moirai-2.0-R-small/blob/main/config.json
[TIrepo]: https://github.com/NX-AI/tirex-2
[TIcard]: https://huggingface.co/NX-AI/TiRex-2
[TIpaper]: https://arxiv.org/html/2607.01204v1
[TArepo]: https://github.com/TauricResearch/TradingAgents
[TAlog]: https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/CHANGELOG.md
[TApaper]: https://arxiv.org/html/2412.20138v1
[TRrepo]: https://github.com/TauricResearch/Trading-R1
[TRpaper]: https://arxiv.org/html/2509.11420v1
[TRissue]: https://github.com/TauricResearch/Trading-R1/issues/1
[FXrepo]: https://github.com/AI4Finance-Foundation/FinRL-Trading
[FXrelease]: https://github.com/AI4Finance-Foundation/FinRL-Trading/releases
[FXpaper]: https://arxiv.org/html/2603.21330v1
[FRrepo]: https://github.com/AI4Finance-Foundation/FinRobot
[FRpaper]: https://arxiv.org/html/2405.14767v1
[FGrepo]: https://github.com/AI4Finance-Foundation/FinGPT
[FGcard]: https://huggingface.co/FinGPT/fingpt-forecaster_dow30_llama2-7b_lora
[FGsetup]: https://raw.githubusercontent.com/AI4Finance-Foundation/FinGPT/master/SETUP.md
[FBrepo]: https://github.com/ProsusAI/finBERT
[FBcard]: https://huggingface.co/ProsusAI/finbert
[FBpaper]: https://arxiv.org/abs/1908.10063
[LGB]: https://github.com/lightgbm-org/LightGBM
[TP2]: https://huggingface.co/Prior-Labs/TabPFN-v2-reg
[TPpaper]: https://www.nature.com/articles/s41586-024-08328-6
[TPcurrent]: https://github.com/PriorLabs/TabPFN
[GIFT]: https://github.com/SalesforceAIResearch/gift-eval
[FINeval]: https://arxiv.org/html/2606.27100v1
[FINmv]: https://arxiv.org/html/2605.21504v1
[VOLpaper]: https://arxiv.org/html/2505.11163v1
[SPA]: https://doi.org/10.1198/073500105000000063
