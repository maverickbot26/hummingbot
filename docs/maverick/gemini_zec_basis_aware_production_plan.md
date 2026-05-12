# Gemini ZEC V2 Tiny: Basis-Aware Production Mode Plan

Status: Step 2 implementation complete. Focused tests passed. No live trading run, no cron/unattended mode enabled, no capital scale-up.

## GitHub issue status / blocker

I inspected `/Users/maverick/hummingbot` on branch `pr-8027-gemini-connector`.

- `mav` remote: `https://github.com/maverickbot26/hummingbot.git`, viewer permission `ADMIN`, but GitHub Issues are disabled (`hasIssuesEnabled=false`).
- `origin` remote: `https://github.com/hummingbot/hummingbot.git`, viewer permission `READ`, Issues enabled but this work is mostly local ops/controller/runner/dashboard/test scope and is not appropriate for an upstream Hummingbot issue unless a connector defect is found.

Because the writable fork cannot accept issues and the upstream repo is read-only/inappropriate for this local production-ops change, this plan file is the Step 0/1 source of truth until Eric approves implementation.

## Goal

Replace the current too-conservative behavior where a wide Gemini/Coinbase external-mid basis simply blocks or stops the tiny Gemini ZEC V2 market maker. The new mode should be basis-aware:

- Normal basis: quote both sides as the existing tiny maker-only PMM does.
- Gemini rich vs Coinbase: quote sell-only / sell-skewed on Gemini using existing ZEC inventory.
- Gemini cheap vs Coinbase: quote buy-only / buy-skewed on Gemini.
- Extreme, stale, failed, or unconfirmed basis: halt / do not quote / cancel on live run.

This must preserve all existing tiny safety constraints:

- Order size: `0.002 ZEC`.
- Max single open order: `0.0025 ZEC`.
- Max total open remaining: `0.0055 ZEC`.
- Max open orders: 1 BUY + 1 SELL, 2 total.
- Maker-only via `LIMIT_MAKER`.
- Deadman cancel watchdog required for any live run.
- Supervised-only until Eric explicitly approves unattended mode.
- No capital scale-up.

## Scope split

### Local scope for this work

Expected implementation should stay local to Maverick-controlled production ops:

- `controllers/market_making/gemini_zec_pmm_tiny.py`
- `conf/controllers/market_making/gemini_zec_pmm_tiny_live.yml`
- `scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py`
- `scripts/maverick/gemini_zec_phase5_status.py`
- optional new pure policy helper, e.g. `scripts/maverick/gemini_zec_basis_policy.py`
- tests under:
  - `test/controllers/market_making/test_gemini_zec_pmm_tiny.py`
  - `test/scripts/maverick/test_gemini_zec_v2_ops.py`
  - `test/scripts/maverick/test_gemini_zec_phase5_status.py`

### Upstream Gemini connector scope

Do not include this in the upstream Gemini connector PR unless implementation uncovers a connector bug. This is not primarily connector behavior. The connector should remain limited to exchange API correctness. Basis-aware production policy belongs in the local controller/runner/dashboard/test layer.

If a connector defect appears, open a separate upstream-focused issue/branch with a minimal reproduction.

## Basis policy spec

Define signed basis in basis points:

```text
basis_bp = (gemini_mid - coinbase_mid) / coinbase_mid * 10000
```

Positive basis means Gemini is rich vs Coinbase. Negative basis means Gemini is cheap vs Coinbase.

Recommended initial thresholds for tiny supervised production:

| State | Condition | Action |
|---|---:|---|
| normal | `abs(basis_bp) <= 25 bp` | two-sided PMM (`BUY` + `SELL`) |
| rich candidate | `basis_bp >= +30 bp` | do not act until confirmed |
| rich confirmed | `basis_bp >= +30 bp` for 3 fresh samples spanning at least 30s | sell-only on Gemini; suppress BUY |
| cheap candidate | `basis_bp <= -30 bp` | do not act until confirmed |
| cheap confirmed | `basis_bp <= -30 bp` for 3 fresh samples spanning at least 30s | buy-only on Gemini; suppress SELL |
| directional exit / hysteresis | confirmed directional state returns inside `abs(basis_bp) < 20 bp` | stop directional mode; next run/preflight may return to two-sided |
| elevated-but-unconfirmed | `25 < abs(basis_bp) < 30 bp`, mixed signs, or too few confirming samples | halt/no quote |
| sustained wide | `abs(basis_bp) >= 75 bp` for 60s | halt/cancel live run |
| extreme | `abs(basis_bp) >= 100 bp` once | immediate halt/no quote |
| bad data | missing/zero/negative/non-numeric mid, HTTP/API error, stale sample, or timestamp gap | halt/no quote |

Rationale: current `50 bp` hard halt is too conservative because it discards useful signal. The directional band allows moderate, confirmed basis to choose the safer side while preserving a hard stop for extreme dislocation.

## Data freshness / confirmation rules

- Source mids from existing public helpers initially (`gemini_mid()` and `coinbase_mid()`) in the supervised runner.
- Each sample records `gemini_mid`, `coinbase_mid`, `basis_bp`, and local monotonic timestamp.
- A sample is fresh only if both mids were fetched successfully and are no older than 5 seconds at decision time.
- Preflight must collect at least 3 fresh samples over at least 30 seconds before selecting a directional mode.
- If samples disagree on sign, cross thresholds inconsistently, or fail freshness checks, preflight halts rather than guessing.
- During live supervised runtime, continue sampling. Stop and cancel if:
  - sample freshness fails,
  - basis becomes extreme,
  - basis remains sustained-wide for 60s,
  - basis sign flips against the active directional side,
  - a directional run falls back into normal/uncertain territory and active one-sided quote is no longer justified.
- Dashboard should show signed basis and policy state, not only absolute basis.

## Proposed implementation shape

### 1) Add pure basis policy helper

Add `scripts/maverick/gemini_zec_basis_policy.py` with no exchange side effects.

Suggested objects/functions:

- `BasisPolicyConfig`
  - `normal_bp=25`
  - `entry_bp=30`
  - `exit_bp=20`
  - `sustained_halt_bp=75`
  - `extreme_halt_bp=100`
  - `confirmation_samples=3`
  - `confirmation_seconds=30`
  - `max_sample_age_seconds=5`
  - `sustained_halt_seconds=60`
- `BasisSample(ts, gemini_mid, coinbase_mid, basis_bp)`
- `BasisDecision(state, allowed_sides, suppress_sides, reason, basis_bp, confirmed, halt)`
- `signed_basis_bp(gemini_mid, coinbase_mid)`
- `decide_basis_state(samples, now, previous_state=None, config=...)`

State values:

- `normal_two_sided`
- `gemini_rich_sell_only`
- `gemini_cheap_buy_only`
- `halt_unconfirmed_basis`
- `halt_stale_basis`
- `halt_extreme_basis`
- `halt_sustained_wide_basis`

### 2) Controller config and quote filtering

Update `controllers/market_making/gemini_zec_pmm_tiny.py`:

- Add optional config fields such as:
  - `basis_mode_enabled: bool = False`
  - `basis_allowed_sides: Optional[List[str]] = None` (`["BUY", "SELL"]`, `["SELL"]`, `["BUY"]`, or empty for halt)
  - `basis_decision_state: Optional[str]`
  - `basis_decision_reason: Optional[str]`
  - `basis_signed_bp: Optional[Decimal]`
- Bump `metrics_schema_version` to `2` for new basis fields.
- In `update_processed_data`, if basis mode is enabled and `basis_allowed_sides` is empty, set paused with reason `basis_policy_halt`.
- In `get_levels_to_execute`, combine inventory suppression and basis suppression:
  - Gemini rich sell-only suppresses `BUY`.
  - Gemini cheap buy-only suppresses `SELL`.
  - Inventory guard still wins. If basis wants `SELL` but inventory guard suppresses `SELL`, no quote / paused with an explicit reason rather than forcing a sell.
- Keep order amounts capped by `min(order_amount_base, max_order_size_base)`.
- Keep `ExecutionStrategy.LIMIT_MAKER` unchanged.
- Include basis policy details in `get_custom_info()`.

For the current one-level-per-side controller, “sell-skewed” means sell-only and “buy-skewed” means buy-only. If future multi-level sizing is added, skew can become amount/level weighting, but not in this tiny production step.

### 3) Supervised runner integration

Update `scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py`:

- Replace the startup absolute-basis hard refusal with the pure policy preflight.
- Add a dry-run/preflight path that logs:
  - signed basis samples,
  - basis decision state,
  - allowed/suppressed sides,
  - why the mode was selected or halted.
- Patch generated runtime controller config with basis decision fields in `build_runtime_v2_config`.
- Pass the same basis decision into `build_and_validate_preflight_plan` so the preflight validates the exact quote shape that will run.
- Update `validate_controller_quote_plan` to allow one-sided quotes when there is an explicit basis decision reason, not only inventory suppression.
- During runtime monitoring, keep signed basis samples and stop/cancel on stale/unconfirmed/extreme/sign-flip conditions from the policy helper.
- Summary JSON should include `basis_policy` fields and all safety bounds.

### 4) Dashboard/status integration

Update `scripts/maverick/gemini_zec_phase5_status.py` and docs/dashboard as needed:

- Show signed basis (`basis_bp`) with direction labels: Gemini rich / Gemini cheap / normal.
- Distinguish alerts:
  - `basis_policy_halt_extreme`
  - `basis_policy_stale`
  - `basis_policy_unconfirmed`
  - `basis_directional_sell_only`
  - `basis_directional_buy_only`
- Keep existing order bound alerts unchanged.
- Alert if a live supervised process exists without deadman.
- Alert if open orders violate allowed sides for the latest basis policy summary.

### 5) Config defaults

Update `conf/controllers/market_making/gemini_zec_pmm_tiny_live.yml` only with safe defaults:

```yaml
basis_mode_enabled: true
basis_allowed_sides: []     # runtime/preflight must fill this; empty means halt
basis_decision_state: null
basis_decision_reason: null
basis_signed_bp: null
metrics_schema_version: 2
```

Do not add any unattended cron/service config in this step.

## Acceptance criteria

Implementation is complete only when all of these are true:

- Normal confirmed basis produces exactly one `BUY` and one `SELL` `LIMIT_MAKER` action at `0.002 ZEC` each.
- Confirmed Gemini-rich basis produces exactly one `SELL` `LIMIT_MAKER` action at `0.002 ZEC`, with BUY explicitly suppressed by basis policy.
- Confirmed Gemini-cheap basis produces exactly one `BUY` `LIMIT_MAKER` action at `0.002 ZEC`, with SELL explicitly suppressed by basis policy.
- Extreme, stale, failed, or unconfirmed basis produces zero actions and an explicit pause/halt reason.
- Inventory guard still wins over basis policy and never allows selling below the configured lower inventory band or buying above the upper inventory band.
- Preflight rejects any one-sided quote that lacks an explicit basis-policy or inventory-suppression reason.
- Runtime monitor cancels and exits on stale basis data, sign flip against the active side, sustained wide basis, extreme basis, duplicate process, bounds breach, deadman failure, or log error.
- Dashboard reports signed basis direction and policy state.
- No live trading command is run by tests.
- No cron/unattended mode is enabled.
- All tiny bounds remain enforced: max 2 open orders, max single `0.0025 ZEC`, max total `0.0055 ZEC`, max one BUY + one SELL.

## Test plan

Run focused tests first, then broader smoke tests if needed:

```bash
python -m pytest test/controllers/market_making/test_gemini_zec_pmm_tiny.py
python -m pytest test/scripts/maverick/test_gemini_zec_v2_ops.py
python -m pytest test/scripts/maverick/test_gemini_zec_phase5_status.py
```

Add or update tests for:

- `signed_basis_bp` positive/negative/zero and invalid Coinbase zero.
- normal basis -> two-sided decision.
- rich confirmed -> SELL-only decision after 3 samples / 30s.
- cheap confirmed -> BUY-only decision after 3 samples / 30s.
- unconfirmed/elevated -> halt.
- stale/failed sample -> halt.
- sustained wide -> halt after 60s.
- extreme -> immediate halt.
- controller basis suppresses BUY for rich and SELL for cheap.
- controller basis halt produces no actions and clear metrics.
- inventory conflict with basis sell-only/buy-only results in no unsafe quote.
- preflight accepts one-sided basis-policy quote with explicit reason.
- preflight rejects one-sided quote without explicit reason.
- status alerts signed basis and invalid side exposures.

Optional local dry-run gate after tests, only if Eric approves Step 2 build and before any live run:

```bash
python scripts/maverick/run_gemini_zec_v2_tiny_live_smoke.py --dry-run-preflight
```

Do not start live trading as part of this planning step.

## Operational rollout after implementation

1. Step 2 build on a feature branch pushed only to `mav`, never `origin`.
2. Run focused tests above.
3. Run `--dry-run-preflight` and inspect basis decision/log output.
4. Step 3 independent review agent runs tests and reviews safety invariants.
5. Step 4 Maverick local validation reruns tests and dry-run preflight.
6. Only with Eric’s explicit approval, run a supervised tiny live session with unchanged capital/order limits and deadman enabled.
7. Review summary: stop reason, final open orders, fills, signed basis state, dashboard health, deadman logs.
8. Keep supervised-only. No unattended cron/service until Eric separately approves.

## Build checklist for future agents

- [x] Create feature branch from current work branch/state; do not commit to `master`/`main`.
- [x] Preserve existing uncommitted docs/status files unless Eric says otherwise.
- [x] Add pure basis policy helper with typed decisions and no live side effects.
- [x] Add unit tests for pure basis policy thresholds/freshness/confirmation.
- [x] Add basis config fields and metrics schema v2 to `GeminiZECTinyPMMConfig`/controller.
- [x] Combine basis suppression with inventory suppression safely.
- [x] Ensure `LIMIT_MAKER`, `0.002 ZEC`, max single `0.0025`, max total `0.0055`, max 2 orders remain unchanged.
- [x] Update runner preflight to collect confirmed signed basis samples.
- [x] Patch runtime controller config with the basis decision selected by preflight.
- [x] Update quote-plan validation to require explicit basis or inventory reason for one-sided quotes.
- [x] Update runtime monitor to stop/cancel on stale/unconfirmed/extreme/sign-flip basis.
- [x] Update status/dashboard basis labels and alerts.
- [x] Update or add tests for controller, runner preflight, ops, and status.
- [x] Run focused pytest commands and report exact results.
- [x] Do not run live trading, enable cron, scale capital, or mark production/unattended complete without Eric’s explicit approval.


## Step 2 implementation notes

- Added pure `scripts/maverick/gemini_zec_basis_policy.py` for signed basis decisions, confirmation, freshness, extreme/sustained-wide halts, and direction labels.
- Controller now supports runtime-filled basis policy fields, metrics schema v2, basis suppression, basis halt pauses, and inventory/basis conflict pauses without relaxing tiny order bounds.
- Supervised runner now uses basis-policy preflight, patches runtime controller configs with the exact decision, validates one-sided quotes only with explicit basis/inventory reasons, and monitors live basis state for halt/cancel conditions.
- Status monitor/dashboard generation now reports signed basis direction/policy and alerts on basis-policy states and open-order side violations.
- Validation:
  - `/opt/homebrew/bin/micromamba run -n hummingbot python -m pytest test/controllers/market_making/test_gemini_zec_pmm_tiny.py -q` -> `20 passed, 7 warnings`.
  - `/opt/homebrew/bin/micromamba run -n hummingbot python -m pytest test/scripts/maverick/test_gemini_zec_v2_ops.py -q` -> `13 passed, 6 warnings`.
  - `/opt/homebrew/bin/micromamba run -n hummingbot python -m pytest test/scripts/maverick/test_gemini_zec_phase5_status.py -q` -> `7 passed, 6 warnings`.
- No live trading command was run; no cron/unattended mode was enabled; capital/order-size bounds remain unchanged.
