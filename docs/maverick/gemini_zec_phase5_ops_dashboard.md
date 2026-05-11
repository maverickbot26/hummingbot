# Gemini ZEC V2 Phase 5 Ops Dashboard

Generated epoch: `1778528856`
Read: **Healthy** (`go_for_supervised_mini_ops`)

## Portfolio
- Total: **$649.51**
- USD: $329.42 (50.72%)
- ZEC: 0.571743 ZEC = $320.09 (49.28%)
- Gemini mid: $559.85; Coinbase mid: $558.95; basis: 16.10 bp

## Open Orders
- Buys: 0
- Sells: 0
- Stale/problem orders: none
- Total remaining: 0 ZEC

## Latest Live Summary
- Path: `logs/maverick_zec_v2_live_smoke_summary_20260511_193630.json`
- Stop reason: `runtime_complete`
- Final open orders: 0
- Trades: `{"buy_zec": "0.002", "count": 1, "fee_amount": "0", "fee_currency": "none", "net_zec": "0.002", "notional_usd": "1.12232", "sell_zec": 0, "volume_zec": "0.002"}`
- PnL / hold / alpha: -1.38735549000 / -1.38447549000 / -0.00288000000

## Watchdog / Heartbeat
- Status: `not_running_ok_no_live_process`
- Processes: `[]`
- Heartbeat: `{'path': 'logs/zec_v2_mm.heartbeat', 'exists': True, 'age_seconds': 61.792, 'stale': False, 'value': 'stopped 1778528794.625024'}`

## Alerts
- none

## Cron Proposal (not enabled)
```cron
*/5 * * * * cd /Users/maverick/hummingbot && micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1
```

Keep cron disabled until Eric explicitly approves unattended production mini ops.
