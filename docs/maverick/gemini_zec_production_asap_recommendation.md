# Gemini ZEC V2 Production-ASAP Recommendation

Updated: 2026-05-11 17:55 EDT

## Current supervised retry outcome

- Run: `20260511_205305`
- Command used:
  ```bash
  /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py \
    --runtime-seconds 3600 \
    --check-seconds 300 \
    --heartbeat-seconds 10 \
    --deadman-stale-seconds 45
  ```
- Result: `runtime_complete`
- Final Gemini ZEC-USD open orders: `0`
- Final bot/deadman processes: `0`
- Deadman heartbeat fix worked: independent 10s heartbeat stayed fresh during 300s monitor checks.
- Cancel fallback remains armed: stale heartbeat cancel uses `/v1/order/cancel/all`, then reconciles `/v1/orders` and individually cancels ZEC orders via `/v1/order/cancel` if needed.
- Trades: 1 maker BUY fill, `0.002 ZEC`, `$1.12044` notional, `$0` fee reported.
- Latest status: `Healthy`, no alerts.

## Minimum remaining blockers

1. Eric approval to proceed beyond one-hour supervised smoke into supervised production mini.
2. Keep size unchanged for the first production mini; no cron/unattended launch until Eric separately approves.
3. Run one longer supervised production mini with the same deadman settings and verify:
   - final open orders = `0`
   - no bot/deadman processes left
   - status monitor reports `Healthy`
   - no recurring Hummingbot/Gemini connector errors
4. Only after the longer supervised mini is clean: ask Eric for explicit approval before enabling unattended automation.

## Exact supervised production mini config

Use the existing tiny config unchanged:

- Script config: `conf/scripts/gemini_zec_v2_tiny_live.yml`
  - `script_file_name: v2_with_controllers.py`
  - controller: `market_making/gemini_zec_pmm_tiny_live.yml`
  - `max_global_drawdown_quote: 5`
  - `max_controller_drawdown_quote: 5`
- Controller config: `conf/controllers/market_making/gemini_zec_pmm_tiny_live.yml`
  - pair: `ZEC-USD` on `gemini`
  - one-shot mode: `true`
  - buy/sell spreads: `0.001` / `0.001`
  - `order_amount_base: 0.002`
  - `max_order_size_base: 0.002`
  - `target_base_amount: 0.568`
  - `max_inventory_deviation_base: 0.010`
  - `max_external_mid_deviation_pct: 0.01`
  - `volatility_pause_enabled: true`
  - `max_mid_move_pct: 0.02`
  - `zero_fee_accounting: true`
  - `skip_rebalance: true`
- Runtime bounds enforced by runner:
  - max open orders: `2`
  - max single order: `0.0025 ZEC`
  - max total remaining: `0.0055 ZEC`
  - heartbeat: `10s`
  - deadman stale threshold: `45s`

Recommended next supervised production mini command:

```bash
/opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py \
  --runtime-seconds 14400 \
  --check-seconds 300 \
  --heartbeat-seconds 10 \
  --deadman-stale-seconds 45
```

This is still the same tiny size; the only change is longer supervised runtime.

## Exact cron / automation proposal — not enabled

Status monitor cron only, after Eric approves monitoring automation:

```cron
*/5 * * * * cd /Users/maverick/hummingbot && /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-json logs/gemini_zec_phase5_status_latest.json --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1
```

Unattended tiny runner proposal, only after Eric explicitly approves unattended trading:

```cron
# Example: one tiny supervised-equivalent production mini at 09:35 ET on weekdays.
# This is intentionally disabled until explicit approval.
35 9 * * 1-5 cd /Users/maverick/hummingbot && /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py --runtime-seconds 14400 --check-seconds 300 --heartbeat-seconds 10 --deadman-stale-seconds 45 >> logs/gemini_zec_v2_tiny_unattended_cron.log 2>&1
```

## Go / no-go

- Supervised production mini at current size: **GO**, pending Eric approval.
- Scaling order size: **NO-GO**.
- Unattended cron/automation that can place orders: **NO-GO until Eric explicitly approves after reviewing this proposal and a clean longer supervised mini**.
