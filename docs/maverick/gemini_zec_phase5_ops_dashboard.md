# Gemini ZEC V2 Phase 5 Ops Dashboard

Generated epoch: `1778530791`
Read: **stop condition** (`no_go`)

## Portfolio
- Total: **$648.11**
- USD: $328.31 (50.66%)
- ZEC: 0.573743 ZEC = $319.80 (49.34%)
- Gemini mid: $557.39; Coinbase mid: $556.63; basis: 13.65 bp

## Open Orders
- Buys: 0
- Sells: 0
- Stale/problem orders: none
- Total remaining: 0 ZEC

## Latest Live Summary
- Path: `logs/maverick_zec_v2_live_smoke_summary_20260511_201417.json`
- Stop reason: `deadman_exited_2`
- Final open orders: 0
- Trades: `{"buy_zec": "0.002", "count": 1, "fee_amount": "0", "fee_currency": "none", "net_zec": "0.002", "notional_usd": "1.11204", "sell_zec": 0, "volume_zec": "0.002"}`
- PnL / hold / alpha: -0.06374044500 / -0.06575044500 / 0.00201000000

## Watchdog / Heartbeat
- Status: `not_running_ok_no_live_process`
- Processes: `[]`
- Heartbeat: `{'path': 'logs/zec_v2_mm.heartbeat', 'exists': True, 'age_seconds': 30.154, 'stale': False, 'value': 'stopped 1778530761.677324'}`

## Alerts
- latest_error_present
- latest_summary_stop_reason:deadman_exited_2

## Operator Note — 2026-05-11 16:19 EDT supervised attempt
- Intended runtime: 60m max; actual runtime: ~5m05s.
- Two-sided LIMIT_MAKER quote evidence: SELL 0.002 ZEC @ $557.13 and BUY 0.002 ZEC @ $556.02 were created.
- Fill result: 1 maker BUY fill, 0.002 ZEC / $1.11204, $0 fees; remaining SELL was canceled during shutdown.
- Stop reason: deadman exited after heartbeat went stale because supervisor check/heartbeat interval was 300s while deadman stale threshold was 45s. Deadman cancel-all attempt returned Gemini HTTP 400; supervisor stop/cancel later confirmed final open orders = 0.
- Read: needs adjustment before any further live run. Fix heartbeat/deadman interval mismatch and cancel-all HTTP 400 handling; do not enable cron or scale.

## Cron Proposal (not enabled)
```cron
*/5 * * * * cd /Users/maverick/hummingbot && micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1
```

Keep cron disabled until Eric explicitly approves unattended production mini ops.
