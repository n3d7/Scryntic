# Provider MCP and reference-project data patterns — 2026-10-20

Scope: operator-requested assessment of Bybit, Binance and Massive MCP offerings, followed by a data-acquisition/rights-focused pass over seven already reviewed reference projects. This supplements [REFERENCE_SYSTEMS.md](REFERENCE_SYSTEMS.md); it does not repeat the architecture audit, adopt dependencies, install/connect servers, change the roadmap or authorize trading. The local-only source-alternative recommendation remains the primary outcome.

## Three separate questions

1. Does a transport/tool simplify asking a provider for data?
2. Does the applicable data agreement permit retaining and processing those data for this pilot?
3. Can Scryntic record reproducible, point-in-time-qualified input with recovery and bounded resource use?

An MCP implementation can help with the first question. Neither its installation nor its open-source code licence resolves the second or automatically implements the third. An exchange listed as supported by a research/bot framework is also not a data-use grant.

## Provider MCP assessment

| Offering | Verified useful scope | Admission decision for Scryntic now |
| --- | --- | --- |
| Bybit trading-mcp | Public candle handler accepts bounded timestamps/limit and calls the ordinary V5 candle endpoint without authentication. Full server also registers account/trading/asset tools. | Useful market-only exploratory interface after rights qualification. Do not adopt the full trading server or provide credentials; it does not resolve the Bybit retention/ML question. |
| Binance official Agentic MCP | Documentation separates public market data from Account, Trade and Transfer scopes; full service supports real trades and internal sub-account transfers. | Public market-data capability can be useful for ad hoc inspection. Agentic account/trading integration is outside this task and current architecture. Archive dataset rights do not automatically cover MCP/live APIs. |
| Massive MCP | Generic endpoint discovery/GET and isolated in-memory SQL analysis across several data classes; source has endpoint and SQL controls. | Technically useful later for read-only research, but current standard individual market-data rights do not admit our ML archive. Requires appropriate non-display/retention entitlement before dataset use. |

### Bybit source-level evidence

Pinned commit [`363811eda6a5b3f863c9a76e0788227ba163f860`](https://github.com/bybit-exchange/trading-mcp/commit/363811eda6a5b3f863c9a76e0788227ba163f860), dated 2026-10-14, MIT code licence. Inspected [server.ts](https://github.com/bybit-exchange/trading-mcp/blob/363811eda6a5b3f863c9a76e0788227ba163f860/src/server.ts), [approval.ts](https://github.com/bybit-exchange/trading-mcp/blob/363811eda6a5b3f863c9a76e0788227ba163f860/src/utils/approval.ts), [getMarketKline.ts](https://github.com/bybit-exchange/trading-mcp/blob/363811eda6a5b3f863c9a76e0788227ba163f860/src/tools/market/getMarketKline.ts) and [rest-client.ts](https://github.com/bybit-exchange/trading-mcp/blob/363811eda6a5b3f863c9a76e0788227ba163f860/src/client/rest-client.ts).

The server lists imported tool definitions and validates inputs before calling approval and handlers. High-risk metadata triggers form elicitation; unsupported clients are rejected, and the approval request displays a canonical input digest. These are useful controls, but approval responses still depend on the client. Confirmation is not a substitute for server-side denial of forbidden operations. Our prediction components must possess neither trading tools nor account/execution credentials. The public candle handler has a maximum 1,000-row page and explicit trade-price versus mark/index-price distinction; no archive/PIT/trial guarantees follow from that handler alone. No vulnerability scan or exhaustive call-path verification is claimed.

### Binance official service and the installed plugin are distinct surfaces

Read the [operator-supplied introduction](https://developers.binance.com/en/docs/agent-native/mcp-server) and its [linked Agentic MCP guide](https://developers.binance.com/en/docs/agent-native/mcp-server/agentic). This is a hosted service described by current documentation, not a locally source-audited implementation. Public market data needs no authentication. The guide also describes authorization, agentic sub-accounts, trade/transfer scopes, confirmations and disconnection. The absence of external withdrawal scope does not make trading/transfer authority acceptable for Scryntic's current stage. Do not create or fund a sub-account, authenticate or register this endpoint in this task. Hosted implementation version and enforcement are unverified.

### Massive source and licensing evidence

Pinned code commit [`1e161a8c50b69bd467b092e0721e30908ab94eb6`](https://github.com/massive-com/mcp_massive/commit/1e161a8c50b69bd467b092e0721e30908ab94eb6), dated 2026-10-08; MIT code licence. The [README](https://github.com/massive-com/mcp_massive/blob/1e161a8c50b69bd467b092e0721e30908ab94eb6/README.md) calls the implementation experimental. Inspected [server.py](https://github.com/massive-com/mcp_massive/blob/1e161a8c50b69bd467b092e0721e30908ab94eb6/src/mcp_massive/server.py) `call_api` and [store.py](https://github.com/massive-com/mcp_massive/blob/1e161a8c50b69bd467b092e0721e30908ab94eb6/src/mcp_massive/store.py) SQL authorization. API calls are GET against a configured base URL with endpoint allowlist and parameter checks. SQL is over scratch data, not direct access to Scryntic's database; SELECT authorizer blocks ATTACH, unsafe functions and mutations, with controlled internal PRAGMAs. These are useful bounded-query patterns, not a completed security certification. Keys must remain broker-side, never AI tool arguments.

Read [Individuals Terms](https://massive.com/legal/individuals-terms-of-service), updated 2025-07-18, and the incorporated [Market Data Terms](https://massive.com/legal/market-data-terms-of-service), updated 2025-08-28. Personal use is subject to data restrictions: section2 defaults to display-only absent another agreement, section5 restricts non-display/derived uses without the relevant consent/agreement, and section8 requires cessation/deletion on termination, restriction or suspension. Those are concrete blockers for ML preparation and an immutable retained research corpus under ordinary individual terms. Do not infer that all Massive business/negotiated datasets are unusable; those entitlements were not qualified or purchased. No plan was activated and no API key requested or used.

### Actual connected metadata and registry limitations

At inspection, this session exposed five Bybit tools, 90 Binance tools with market-data getter names, and four Massive tools (`search_endpoints`, `call_api`, `query_data`, `workspace`). This is metadata inspection only, not an exhaustive permission/runtime validation. The installed connector surface is not identical to an upstream repository or hosted Agentic service. Massive workspace create/delete affects scratch tables, not an exchange account. No provider data/account/workspace tool was invoked.

The installed PolicyLayer CLI was used with public identifiers: `bybit-official-trading-server` returned discovered/scan queued, `binance-mcp` queued without published record, and `mcp_massive` unknown/unresolved. These are registry lookup limitations, not evidence of a clean scan or maliciousness. No new connection was made, so no approval or deny-rule change was needed. No hook, runtime or server installed. Exact source hashes and registry receipts are in [metadata receipt](data-sources/PROVIDER_MCP_REFERENCE_RECEIPT_2026-10-20.json).

## Seven reference projects: targeted follow-up, not another broad audit

Reused the commits and licensing review already in [REFERENCE_SYSTEMS.md](REFERENCE_SYSTEMS.md). Newly read each README at those exact commits, plus Qlib collector documentation and Freqtrade data-download documentation. These are pinned historical source observations, not claims about every current HEAD, linked third-party dataset or all repository files. Per-file URLs/hashes/read scope are in the receipt. No example downloader, model or bot was executed.

| Project / previously reviewed commit | Data-related finding in this follow-up | Action for Scryntic |
| --- | --- | --- |
| Qlib / `54355232463878d2eebb91fe0ee5fa7fa1f5976c` | README says its official dataset is temporarily disabled under a stricter data-security policy; points to community downloads and Yahoo collectors, warns about quality and recommends preparing one's own data. Collector docs separate features/calendar/instruments. | Reuse data/experiment separation. Do not treat community archive or crawler availability as source permission, provenance qualification or an untouched holdout. |
| Freqtrade / `84e5657e698ec6ff279c6a4c04c7591dfefaec74` | Dedicated download/convert/list commands support bounded pair/timeframe/range requests, incremental/prepend handling and trade-to-OHLCV conversion. | Borrow explicit inventory/gap/cursor UX and format conversion patterns; retain our raw immutable archive and availability contract. Do not adopt the execution bot or infer rights from supported exchanges. |
| NautilusTrader / `6875261266758e379d7af4213b327ea78fa8da01` | README separates venue/data-provider adapters behind a normalized domain model, including commercial data providers. | Keep source-specific identity and permission behind a normalized read-only adapter. Deterministic replay/catalogue patterns remain useful; a provider adapter does not grant its data licence. |
| cryptofeed / `ad986f01743ddbb81f65981ecf4946c8d629520e` | Standardizes exchange WebSocket/REST data into callbacks across venues. | Good later feed/gap/reconnect pattern, not the first historical download solution. Existing code licence review is **AGPL v3-or-later**, not assumed MIT/Apache. |
| Hummingbot / `9af100d6822da7d2d0291a906c730ef172284ee2` | Trading-oriented connector framework, Apache2 code, high-frequency focus. | Use conceptual connector/tracker boundaries only; its execution/high-frequency stack is not a prerequisite for our hourly forecasting pilot. |
| NeuralForecast / `6c11accfbcfdd62a40ef69a7c81e3d9c7f272682` | Example fits supplied `AirPassengersDF`; model library receives a dataset rather than licensing a financial feed. | Evaluate later on our admitted dataset and common folds. Apache2 code does not resolve source-data or model-selection gates. |
| Chronos / `1224fafa91e1ecd6674f5204066b159e40b5e4a1` | Example reads supplied electricity-price context/covariate files and removes target from future covariates. | Keep forecast-origin visibility explicit. Pretrained weight/corpus permissions and holdout contamination are separate from code licensing; no model exploration/download now. |

**Bounded conclusion:** inspected material supports provider adapters, explicit datasets and reusable research workflows. It supplies no provider authorization for our private retained Bybit/Massive corpus. This is a statement about inspected evidence, not an exhaustive assertion that no licence-related document exists anywhere in those repositories. None makes a third-party MCP the durable ingestion foundation by itself.

## Recommendation and sequencing

Continue the Binance Vision research-pilot candidate and its territorial/row/PIT admission checks. Use approved public market MCP tools for occasional human/AI inspection only after the specific data-use scope is established; ingestion should retain typed source requests, raw bytes, hashes, cursors and actual arrival times through existing services. The separately planned Scryntic MCP remains a later application-service adapter and gains no exchange execution authority. No scope, dependency, milestone or Done status changed in this research follow-up.
