# Gemini ZEC V1 Pilot Lessons

## V1 Pilot Summary (2026-05-11 08:07 EDT)
Source: `logs/maverick_zec_pilot_summary_20260511_120748.json`.

- Strategy: `conf_pure_mm_gemini_zecusd_tiny.yml` pure market making.
- Market: Gemini `ZEC-USD`.
- Shape: one bid and one ask, 10 bp per side, `0.002 ZEC` order amount, maker-only (`take_if_crossed: false`).
- Result: runtime completed, final open buy cancelled, final open orders = 0.
- Fills: 4 buy fills, `0.016 ZEC`, `$9.09952` notional, `$0` fees.
- PnL: portfolio `-$0.61268244`, buy-and-hold `-$0.59588244`, market-making alpha `-$0.0168`.
- Guardrail outcome: connector lifecycle, maker-only placement, zero-fee accounting, and shutdown cancel safety all worked at tiny size.

## Durable Connector Lessons

### Nonce behavior
- Gemini private REST and legacy WS auth require nonces close to wall-clock seconds.
- Integer-second nonces plus `+1` increments drift too far ahead after bursts; fractional-second nonces avoid that.
- Connector nonce generation should be strictly increasing, microsecond-granular, and reset if the class-level counter drifts far ahead of current time.
- Tests reset `GeminiAuth._last_nonce` to keep deterministic behavior across cases.

### Private stream fallback
- Gemini Fast API private WS requires specially provisioned account-scoped Fast API keys.
- The REST API key used here authenticates successfully with legacy `/v1/order/events`.
- The user stream now signs the legacy WS payload as `{"request":"/v1/order/events","nonce":...}` and subscribes with URL query params such as `symbolFilter=zecusd&heartbeat=true`.

### Fill parsing, deduplication, and status mapping
- Gemini WS `Z` is cumulative executed base quantity, not the last-fill amount.
- Hummingbot `TradeUpdate.fill_base_amount` is additive, so each WS fill must use `delta = cumulative_Z - tracked_order.executed_amount_base`.
- Fill events without a stable trade id are skipped; duplicate/stale fills with no positive delta are ignored.
- Legacy event types map to connector-style statuses:
  - `accepted` / `booked` / `initial` -> open
  - `fill` with remaining amount -> partial/full based on `remaining_amount`
  - `closed` + `is_cancelled` -> cancelled
  - `closed` with no remaining amount -> filled/closed

### Cancel safety
- Gemini cancel requests are synchronous in this connector.
- The V1 pilot shutdown path cancelled the remaining live buy and confirmed final open orders = 0.
- Phase 3 must repeat no-open-orders checks before and after any live smoke.

### Maker fee classification and zero-fee evidence
- `LIMIT_MAKER` must use maker-fee classification by default.
- The account observed zero fees during the V1 tiny pilot (`fee_amount=0`, `fee_currency=none`).
- The local fee override template includes Gemini maker/taker override keys; production config should keep Gemini maker/taker percent fees at zero only when verified for the account.

## V2 Baseline Requirements
- Reproduce the V1 tiny quote shape: one bid, one ask, 10 bp per side, `0.002 ZEC` size.
- Use maker-only executors (`LIMIT_MAKER`) and zero-fee accounting assumptions in paper/sim metrics.
- Enforce hard max order size, max inventory deviation, duplicate-process guard, external-mid sanity check, volatility pause hook, and JSON metrics output before any live V2 order.
