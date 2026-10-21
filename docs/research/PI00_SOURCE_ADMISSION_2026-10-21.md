# PI00 source admission: bounded historical research

The [Binance public data terms](https://github.com/binance/binance-public-data/blob/d571690eb66760763b0bd6e27e8cd35603c7e244/TERMS_AND_CONDITIONS.md) provide a dataset licence for noncommercial historical research. General service terms and restrictions were reviewed separately. This checkpoint admits only the scoped Binance Vision historical reconstruction in [policy v2](../architecture/PI00_RESEARCH_POLICY_v2.json). It grants no live feed, production trading, commercial distribution, or exchange execution authority.

The bounded inspection covered monthly USD-M BTCUSDT and ETHUSDT 1h archives from 2020-01-01 through 2026-09-30 UTC. Each symbol had 81 ZIP archives and 59,160 ordered rows, without duplicate or missing hourly opens. The inspection checked archive SHA256 sidecars, schema, timestamp units, numeric shape, and interval continuity. Historical files were read for inspection; no model fitting or feature comparison occurred. Raw archives and detailed inspection evidence remain outside the repository.

Archive timestamps do not prove when each row first became available or whether it was later revised. This source cannot support an as-observed t+60s or live forward evaluation without separate evidence. The future final interval remains reserved. Bybit retention and research rights remain pending separately.

Policy v2 preserves policy v1's horizons, chronology, scientific thresholds, and resource ceilings. Its SHA256 is `d75c7c0278d011b38c5e2bfaff151e7f3071b56edca9dce3d1d883294f3a14b8`. An isolated admission check verified the development window, denial of later windows, durable restart, and exact policy binding. No PI02 work or market data import into Scryntic was performed.
