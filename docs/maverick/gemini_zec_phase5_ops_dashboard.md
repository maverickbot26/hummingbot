# Gemini ZEC V2 Phase 5 Ops Dashboard

Generated epoch: `1778544704`
Read: **Healthy** (`go_for_supervised_mini_ops`)
Live supervised: `False`

## Portfolio
- Total: **$649.16**
- USD: $326.07 (50.23%)
- ZEC: 0.577743 ZEC = $323.09 (49.77%)
- Gemini mid: $559.22; Coinbase mid: $558.37; basis: 15.22 bp

## Open Orders
- Buys: 0
- Sells: 0
- Stale/problem orders: none
- Total remaining: 0 ZEC

## Latest Live Summary
- Path: `logs/maverick_zec_v2_live_smoke_summary_20260511_231051.json`
- Stop reason: `runtime_complete`
- Final open orders: 0
- Trades: `{"buy_zec": "0.002", "count": 1, "fee_amount": "0", "fee_currency": "none", "net_zec": "0.002", "notional_usd": "1.11684", "sell_zec": 0, "volume_zec": "0.002"}`
- PnL / hold / alpha: -0.48827155000 / -0.48938155000 / 0.00111000000

## Watchdog / Heartbeat
- Status: `not_running_ok_no_live_process`
- Processes: `[]`
- Heartbeat: `{'path': 'logs/zec_v2_mm.heartbeat', 'exists': True, 'age_seconds': 38.948, 'stale': False, 'value': 'stopped 1778544665.0074'}`

## Alerts
- none

## Cron Proposal (not enabled)
```cron
*/5 * * * * cd /Users/maverick/hummingbot && /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-json logs/gemini_zec_phase5_status_latest.json --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1
```

Keep cron disabled until Eric explicitly approves unattended production mini ops.
