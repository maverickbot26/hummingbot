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
- [ ] Run V2 live smoke — explicitly out of scope for this subagent.

## Live Gate Reminder
Before Phase 3 tiny V2 live smoke:
1. No Hummingbot process should be running.
2. Gemini open orders must be zero.
3. Re-run connector + controller tests.
4. Run V2 dry-run and inspect metrics.
5. Use tiny maker-only size only; do not leave live orders open.
