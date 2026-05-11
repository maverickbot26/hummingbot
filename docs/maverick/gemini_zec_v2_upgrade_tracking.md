# Gemini ZEC V2 Upgrade Tracking

Local tracking doc used because the upstream `origin` repository is read-only and GitHub issues may be unavailable on the fork.

## Approval / Scope
- Eric approved continuing on 2026-05-11 through the Hummingbot ZEC V2 upgrade path toward production.
- Live gates remain small only after connector tests, paper/sim, and risk checks are clean.
- This doc tracks Phase 0 through Phase 2 work for the source dev stack.

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
- [ ] Before Phase 4, investigate why live V2 submitted only the buy side and logged recurring `last traded price` lookup warnings plus zero-fee `DivisionByZero` fee-display warnings.

## Live Gate Reminder
Before any next live phase:
1. No Hummingbot process should be running.
2. Gemini open orders must be zero.
3. Re-run connector + controller tests.
4. Run V2 dry-run and inspect metrics.
5. Use maker-only size caps; do not leave live orders open.
6. For Phase 4 specifically, prove two-sided V2 live order submission and clean up the live warning noise first.
