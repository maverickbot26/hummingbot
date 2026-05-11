# Gemini ZEC V2 Upgrade Tracking

Local tracking doc used because the upstream `origin` repository is read-only and GitHub issues may be unavailable on the fork.

## Approval / Scope
- Eric approved continuing on 2026-05-11 through the Hummingbot ZEC V2 upgrade path toward production.
- Live gates remain small only after connector tests, paper/sim, and risk checks are clean.
- This doc tracks Phase 0 through Phase 4 work for the source dev stack.

## Phase 0 — Freeze V1 Lessons and Connector Patches
- [x] Inspect local Gemini connector changes and V1 pilot logs.
- [x] Document nonce behavior and deterministic test expectations.
- [x] Document private stream fallback from Fast API private WS to legacy `/v1/order/events`.
- [x] Cover fill parsing, deduplication, order status mapping, and cancel-safety evidence.
- [x] Cover maker fee classification and zero-fee override evidence.
- [x] Preserve V1 tiny pilot guardrail findings.
- [x] Add targeted tests for legacy WS auth/event conversion and nonce monotonicity.

## Phase 1/2 — V2 Paper/Sim Baseline
- [x] Locate V2 controller architecture (`controllers/*`, `hummingbot/strategy_v2/*`, `scripts/v2_with_controllers.py`).
- [x] Add a Gemini ZEC-USD V2 tiny maker-only controller matching the V1 one-bid/one-ask shape.
- [x] Add paper/sim dry-run harness with duplicate-process guard and JSON metrics output.
- [x] Validate quote shape, hard order-size cap, inventory deviation guard, external-mid sanity pause, volatility pause, and metrics schema in unit tests.

## Phase 3 — Tiny V2 Live Smoke
- [x] Added a one-shot controller option so the Phase 3 live smoke cannot continuously recycle filled levels.
- [x] Added a V2-capable no-MQTT headless launch path and strict live supervisor (`run_gemini_zec_v2_tiny_live_smoke.py`).
- [x] Added local safe V2 config files: `conf/scripts/gemini_zec_v2_tiny_live.yml` and `conf/controllers/market_making/gemini_zec_pmm_tiny_live.yml`.
- [x] Re-ran tests and dry-run before live: 44 passed / 13 warnings; dry-run matched maker-only tiny shape.
- [x] Ran 12-minute supervised V2 live smoke on 2026-05-11 with deadman armed; final Gemini ZEC-USD open orders = 0.
- [x] Investigated Phase 3 blocker: live V2 controller inventory guard used V2 executor positions only, so a fresh live run saw effective ZEC as `0` against target `0.568` / lower bound `0.558` and intentionally suppressed SELL.
- [x] Fixed Phase 4 gate: controller now supports `starting_base_amount`; live supervisor injects current Gemini ZEC balance into runtime V2 config, emits controller quote preflight telemetry, and refuses one-sided quotes unless inventory suppression has an explicit reason.
- [x] Fixed recurring `last traded price` warning by using the generic Gemini ticker rate-limit id (`/v2/ticker/{}`) for pair-specific ticker requests instead of `/v2/ticker/zecusd`, which had no throttler entry.
- [x] Fixed zero-fee fee-display noise by skipping zero flat fees and returning `Decimal("0")` when conversion rates are zero/missing instead of allowing division/conversion errors to bubble into `InFlightOrder.cumulative_fee_paid`.
- [x] Validation after fixes: targeted controller/Gemini/fee tests `46 passed`; full Gemini connector tests `37 passed`; paper sim two-sided; live supervisor `--dry-run-preflight` read-only check showed two-sided runtime plan and legacy no-starting-balance plan reproducing BUY-only / SELL suppression.

## Phase 4 — Mini Operational Gate
- [x] Re-verified branch `pr-8027-gemini-connector` at blocker-fix commit `6ba03f1372adbfb8666aedf73afaa58abf79a09a` before running live.
- [x] Fixed the live supervisor duplicate-process check to ignore the full ancestor process chain, avoiding a false-positive against its own shell wrapper while still rejecting independent Hummingbot/V1/V2 runners.
- [x] Extended the paper sim harness with `--starting-base-amount` so the dry-run gate exercises the same inventory path as the live runtime config.
- [x] Phase 4 dry-run gate on 2026-05-11: targeted controller/Gemini/fee tests plus full Gemini connector tests passed (`69 passed / 13 warnings`); paper sim emitted two-sided BUY+SELL `LIMIT_MAKER` quotes at `0.002 ZEC`; read-only live supervisor preflight confirmed Gemini open orders `0`, two-sided runtime plan, and clean logs (no recurring last-traded-price warning, no zero-fee `DivisionByZero`).
- [x] Ran Phase 4 mini live for 15 minutes with the same tiny bounds: max `1 BUY + 1 SELL`, max single order `0.0025 ZEC`, max total remaining `0.0055 ZEC`, deadman watchdog armed.
- [x] Live results: two fills (`0.002 ZEC` sell at `$556.37`, `0.002 ZEC` buy at `$555.25`), total volume `0.004 ZEC` / `$2.22324`, `$0` fees confirmed, net ZEC unchanged.
- [x] Shutdown/reconciliation: runtime stop completed, deadman stopped, Hummingbot process absent, final Gemini ZEC-USD open orders `0`; final read-only reconcile also confirmed open orders `0` and two-sided quote plan.
- [x] Phase 4 PnL at live summary: portfolio `-$1.75256844` / `-0.270652%` during a ZEC move down; buy-and-hold benchmark `-$1.75480844` / `-0.270998%`; MM alpha `+$0.00224`.
- [x] Read: Phase 4 healthy as a bounded operational gate. Go for Phase 5 only if Phase 5 is still mini/conservative and adds production monitoring/dashboard/alerting before any scale-up.

## Live Gate Reminder
Before any next live phase:
1. No Hummingbot process should be running.
2. Gemini open orders must be zero.
3. Re-run connector + controller tests.
4. Run V2 dry-run and inspect metrics.
5. Use maker-only size caps; do not leave live orders open.
6. For Phase 4 specifically, run the tiny dry-run gate first; live can proceed only after Eric explicitly authorizes a new bounded smoke.
