# Gemini ZEC V2 Phase 5 Ops Dashboard

Generated epoch: `1778536579`
Read: **Healthy** (`go_for_supervised_mini_ops`)
Live supervised: `False`

## Portfolio
- Total: **$647.81**
- USD: $327.19 (50.51%)
- ZEC: 0.575743 ZEC = $320.63 (49.49%)
- Gemini mid: $556.89; Coinbase mid: $555.94; basis: 17.00 bp

## Open Orders
- Buys: 0
- Sells: 0
- Stale/problem orders: none
- Total remaining: 0 ZEC

## Latest Live Summary
- Path: `logs/maverick_zec_v2_live_smoke_summary_20260511_205305.json`
- Stop reason: `runtime_complete`
- Final open orders: 0
- Trades: `{"buy_zec": "0.002", "count": 1, "fee_amount": "0", "fee_currency": "none", "net_zec": "0.002", "notional_usd": "1.12044", "sell_zec": 0, "volume_zec": "0.002"}`
- PnL / hold / alpha: -2.28517971000 / -2.27775971000 / -0.00742000000

## Watchdog / Heartbeat
- Status: `not_running_ok_no_live_process`
- Processes: `[]`
- Heartbeat: `{'path': 'logs/zec_v2_mm.heartbeat', 'exists': True, 'age_seconds': 181.127, 'stale': True, 'value': 'stopped 1778536398.312795'}`

## Alerts
- none

## Cron Proposal (not enabled)
```cron
*/5 * * * * cd /Users/maverick/hummingbot && /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-json logs/gemini_zec_phase5_status_latest.json --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1
```

Keep cron disabled until Eric explicitly approves unattended production mini ops.
