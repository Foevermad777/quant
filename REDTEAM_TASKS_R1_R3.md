# OpenClaw Red-Team Response R1-R3 (2026-07-08)

Source: OpenClaw review of the 2026-07-06 to 2026-07-07 replay data.

## Executive Decision

Accept all three objections as hard validation gates. The current two-day result may be used as an engineering/data-quality smoke signal only; it is not evidence of alpha, confidence calibration, or production tradability.

Important correction: the original DSA `600900` signal id 23 has `confidence=0.8`, but the 2026-07-07 G5 discipline completion for id 23 timed out and did not enter `paper.db.disciplined_signals`. The accepted disciplined `600900` entries currently visible are earlier ids 6 (`buy`, confidence 0.75) and 12 (`watch`, confidence 0.65). Treat OpenClaw's `0.8` challenge as a valid model-risk objection, not as a confirmed G5-accepted trading record.

Current live paper ledger has no trades for this window, so no performance claim should be made from executed PnL.

## Current Evidence

### Already Covered

- Execution-side same-day leakage is blocked by `SignalReader.active_signals_before()`: both raw and disciplined signal paths require `date(created_at) < execution_date`.
- Default entry fill is `next_open`; `entry_high` is no longer used as the default fill gate.
- Buy-side slippage already uses `slippage_rate * open_slippage_multiplier`, with defaults `0.001 * 2.0`.
- Weekly review includes a news timing audit that marks post-bar and post-decision news as ineligible for positive attribution.

### Not Yet Covered

- No physical OOS/Blind Test harness exists yet. The local DSA fixed-pool `stock_daily` coverage currently starts at 2025-03-14, so the requested 2024-2025 blind window cannot be run from the current local database alone.
- `backtest_results` and `backtest_summaries` are currently empty in the local DSA database.
- Technical indicators may legitimately include the completed day close in a post-close report, but the system does not yet persist an explicit `data_asof` / `bar_cutoff` contract proving that those indicators are only used for next-day decisions.
- No Walk-Forward runner, timestamp-shuffle test, doubled-friction stress report, or liquidity-impact model exists yet.

## R1 - OOS Blind Test Gate

Goal: prove the fixed strategy policy survives physically isolated historical regimes before treating confidence as calibrated.

Tasks:

1. Freeze a validation manifest containing prompt/skill version, G5 schema version, signal extraction policy, fill model, cost model, stock pool, and code commit.
2. Build an isolated OOS database under `runtime_data/oos/` with no writes to the live DSA or paper ledgers.
3. Load at minimum 2024-01-01 through 2025-12-31 daily bars and aligned news metadata for the fixed A-share pool; if 2024 data is unavailable, the run must fail closed and report the missing coverage.
4. Define regime slices before running: high-volatility drawdown, rebound/risk-on, sideways, and dividend defensive rotations.
5. Run Blind Test without changing prompt, gate, thresholds, or cost settings after inspecting results.
6. Report win rate, expectancy, max drawdown, alpha vs HS300 and equal-weight pool, and confidence-bucket calibration. A high confidence bucket must not be accepted unless it beats lower confidence buckets out of sample.

Exit criteria:

- OOS report exists with immutable manifest hash.
- Results are reproducible from a clean checkout and isolated DB.
- Any failure is recorded as a validation failure, not tuned away.

## R2 - Temporal Leakage And Walk-Forward Gate

Goal: prove signal generation, execution, and review attribution obey their time cutoffs.

Tasks:

1. Persist explicit temporal metadata for every disciplined signal: `decision_timestamp`, `market_phase`, `data_asof`, `bar_cutoff`, and `news_cutoff`.
2. Add a temporal audit that rejects or flags any signal whose technical inputs include bars later than the declared cutoff.
3. Add timestamp-shuffle tests: move signal timestamps across cutoff boundaries and assert same-day eligibility/outcome attribution changes only when it should.
4. Add disciplined-store unit coverage mirroring raw signal coverage: a signal created on execution date D must never be an open candidate for D.
5. Add a Walk-Forward runner with predeclared rolling windows and no parameter changes inside test windows.

Exit criteria:

- Same-day post-close reports are explicitly classified as next-trading-day candidates.
- Review attribution excludes post-decision/post-bar information for news and technical inputs.
- Walk-Forward report can be regenerated with a single command.

## R3 - Friction, Slippage, And Liquidity Stress Gate

Goal: prove narrow entry bands and market impact do not create a fake edge.

Tasks:

1. Add a stress runner that replays paper execution into temporary ledgers under base, 2x, and 4x buy-side slippage, plus doubled commission/tax settings.
2. Report per-signal `entry_band_width_pct`, `exec_price`, `exec_outside_band`, gross return, net return, and alpha decay vs baseline.
3. Add a conservative liquidity-impact assumption for A-share daily bars. Until a real order-book model exists, start with a fixed adverse bps impact per order and make it configurable.
4. For entry bands under 1%, label the signal "paper-tradable only" unless it remains positive under the stress scenario.
5. Keep `next_open` as the unbiased measurement fill; use entry-band checks as a separate production-tradability diagnostic, not as a backtest fill filter.

Exit criteria:

- Weekly review includes a stress table or links to a dated stress artifact.
- Signals that fail stress are still recorded, but cannot be used as evidence of deployable edge.

## Operating Rule Until R1-R3 Pass

The 2026-07-06 to 2026-07-07 data should be described as:

- useful for validating pipeline timing, G5 completion quality, S1 conflicts, and data availability;
- insufficient for OOS robustness, confidence calibration, or tradeability;
- not a basis for expanding capital, claiming alpha, or tuning parameters toward recent A-share structure.
