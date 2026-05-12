#!/usr/bin/env python3
"""Pure basis policy for Gemini ZEC tiny supervised production mode.

No exchange/network side effects live here. Callers provide sampled mids and the
policy returns a deterministic quote/halt decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Sequence

BUY = "BUY"
SELL = "SELL"
ALL_SIDES = (BUY, SELL)

STATE_NORMAL = "normal_two_sided"
STATE_RICH_SELL_ONLY = "gemini_rich_sell_only"
STATE_CHEAP_BUY_ONLY = "gemini_cheap_buy_only"
STATE_HALT_UNCONFIRMED = "halt_unconfirmed_basis"
STATE_HALT_STALE = "halt_stale_basis"
STATE_HALT_EXTREME = "halt_extreme_basis"
STATE_HALT_SUSTAINED_WIDE = "halt_sustained_wide_basis"

DIRECTIONAL_STATES = {STATE_RICH_SELL_ONLY, STATE_CHEAP_BUY_ONLY}
HALT_STATES = {STATE_HALT_UNCONFIRMED, STATE_HALT_STALE, STATE_HALT_EXTREME, STATE_HALT_SUSTAINED_WIDE}


@dataclass(frozen=True)
class BasisPolicyConfig:
    normal_bp: Decimal = Decimal("25")
    # Legacy hysteresis knob kept for config/backward compatibility. Supervised
    # probe entry no longer waits for this 30 bp threshold; confirmed same-sign
    # samples outside normal_bp and inside sustained_halt_bp are enough.
    entry_bp: Decimal = Decimal("30")
    exit_bp: Decimal = Decimal("20")
    sustained_halt_bp: Decimal = Decimal("75")
    extreme_halt_bp: Decimal = Decimal("100")
    confirmation_samples: int = 3
    confirmation_seconds: Decimal = Decimal("30")
    max_sample_age_seconds: Decimal = Decimal("5")
    sustained_halt_seconds: Decimal = Decimal("60")


@dataclass(frozen=True)
class BasisSample:
    ts: Decimal
    gemini_mid: Decimal
    coinbase_mid: Decimal
    basis_bp: Decimal

    @classmethod
    def from_mids(cls, ts: Decimal | float | int, gemini_mid: Decimal | str | float | int, coinbase_mid: Decimal | str | float | int) -> "BasisSample":
        gemini = _to_decimal(gemini_mid)
        coinbase = _to_decimal(coinbase_mid)
        return cls(
            ts=_to_decimal(ts),
            gemini_mid=gemini,
            coinbase_mid=coinbase,
            basis_bp=signed_basis_bp(gemini, coinbase),
        )


@dataclass(frozen=True)
class BasisDecision:
    state: str
    allowed_sides: list[str]
    suppress_sides: list[str]
    reason: str
    basis_bp: Optional[Decimal]
    confirmed: bool
    halt: bool
    sample_count: int = 0
    sample_span_seconds: Decimal = Decimal("0")
    latest_sample_age_seconds: Optional[Decimal] = None
    samples: list[dict[str, str]] = field(default_factory=list)

    def as_config_fields(self) -> dict[str, object]:
        return {
            "basis_mode_enabled": True,
            "basis_allowed_sides": list(self.allowed_sides),
            "basis_decision_state": self.state,
            "basis_decision_reason": self.reason,
            "basis_signed_bp": str(self.basis_bp) if self.basis_bp is not None else None,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "allowed_sides": list(self.allowed_sides),
            "suppress_sides": list(self.suppress_sides),
            "reason": self.reason,
            "basis_bp": str(self.basis_bp) if self.basis_bp is not None else None,
            "confirmed": self.confirmed,
            "halt": self.halt,
            "sample_count": self.sample_count,
            "sample_span_seconds": str(self.sample_span_seconds),
            "latest_sample_age_seconds": str(self.latest_sample_age_seconds) if self.latest_sample_age_seconds is not None else None,
            "samples": list(self.samples),
        }


def _to_decimal(value: Decimal | str | float | int) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def signed_basis_bp(gemini_mid: Decimal | str | float | int, coinbase_mid: Decimal | str | float | int) -> Decimal:
    gemini = _to_decimal(gemini_mid)
    coinbase = _to_decimal(coinbase_mid)
    if gemini <= 0 or coinbase <= 0:
        raise ValueError(f"invalid mids gemini={gemini} coinbase={coinbase}")
    return (gemini - coinbase) / coinbase * Decimal("10000")


def make_basis_sample(ts: Decimal | float | int, gemini_mid: Decimal | str | float | int, coinbase_mid: Decimal | str | float | int) -> BasisSample:
    return BasisSample.from_mids(ts=ts, gemini_mid=gemini_mid, coinbase_mid=coinbase_mid)


def _sample_debug(samples: Sequence[BasisSample]) -> list[dict[str, str]]:
    return [
        {
            "ts": str(sample.ts),
            "gemini_mid": str(sample.gemini_mid),
            "coinbase_mid": str(sample.coinbase_mid),
            "basis_bp": str(sample.basis_bp),
        }
        for sample in samples
    ]


def _decision(
    *,
    state: str,
    allowed_sides: Iterable[str],
    reason: str,
    basis_bp: Optional[Decimal],
    confirmed: bool,
    samples: Sequence[BasisSample],
    now: Decimal,
) -> BasisDecision:
    allowed = [side for side in ALL_SIDES if side in set(allowed_sides)]
    suppress = [side for side in ALL_SIDES if side not in allowed]
    latest_age = None if not samples else now - samples[-1].ts
    span = Decimal("0") if len(samples) < 2 else samples[-1].ts - samples[0].ts
    return BasisDecision(
        state=state,
        allowed_sides=allowed,
        suppress_sides=suppress,
        reason=reason,
        basis_bp=basis_bp,
        confirmed=confirmed,
        halt=state in HALT_STATES,
        sample_count=len(samples),
        sample_span_seconds=span,
        latest_sample_age_seconds=latest_age,
        samples=_sample_debug(samples),
    )


def _all_valid_and_monotonic(samples: Sequence[BasisSample]) -> bool:
    previous_ts: Optional[Decimal] = None
    for sample in samples:
        if sample.gemini_mid <= 0 or sample.coinbase_mid <= 0:
            return False
        if previous_ts is not None and sample.ts < previous_ts:
            return False
        previous_ts = sample.ts
    return True


def decide_basis_state(
    samples: Sequence[BasisSample],
    now: Decimal | float | int,
    previous_state: Optional[str] = None,
    config: BasisPolicyConfig = BasisPolicyConfig(),
) -> BasisDecision:
    """Return deterministic quote policy for signed Gemini/Coinbase basis.

    Historical confirmation samples may span 30+ seconds; freshness is applied to
    the latest sample at decision time so preflight can confirm over time without
    making its first confirmation sample impossible to use.
    """
    now_dec = _to_decimal(now)
    ordered = list(samples)
    if not ordered:
        return _decision(
            state=STATE_HALT_STALE,
            allowed_sides=[],
            reason="no_basis_samples",
            basis_bp=None,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )
    latest = ordered[-1]
    if not _all_valid_and_monotonic(ordered):
        return _decision(
            state=STATE_HALT_STALE,
            allowed_sides=[],
            reason="invalid_or_non_monotonic_basis_sample",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )
    latest_age = now_dec - latest.ts
    if latest_age < 0 or latest_age > config.max_sample_age_seconds:
        return _decision(
            state=STATE_HALT_STALE,
            allowed_sides=[],
            reason=f"latest_basis_sample_stale:{latest_age}s",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )
    if abs(latest.basis_bp) >= config.extreme_halt_bp:
        return _decision(
            state=STATE_HALT_EXTREME,
            allowed_sides=[],
            reason=f"extreme_basis:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )

    sustained_samples = [sample for sample in ordered if abs(sample.basis_bp) >= config.sustained_halt_bp]
    if sustained_samples and latest.ts - sustained_samples[0].ts >= config.sustained_halt_seconds:
        signs = {1 if sample.basis_bp > 0 else -1 if sample.basis_bp < 0 else 0 for sample in sustained_samples}
        if len(signs) == 1 and 0 not in signs:
            return _decision(
                state=STATE_HALT_SUSTAINED_WIDE,
                allowed_sides=[],
                reason=f"sustained_wide_basis:{latest.basis_bp}bp",
                basis_bp=latest.basis_bp,
                confirmed=True,
                samples=ordered,
                now=now_dec,
            )

    if previous_state in DIRECTIONAL_STATES and abs(latest.basis_bp) <= config.normal_bp:
        return _decision(
            state=STATE_NORMAL,
            allowed_sides=ALL_SIDES,
            reason=f"directional_exit_basis_inside_{config.normal_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )

    if abs(latest.basis_bp) >= config.sustained_halt_bp:
        return _decision(
            state=STATE_HALT_UNCONFIRMED,
            allowed_sides=[],
            reason=f"basis_outside_supervised_probe_band:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )

    if abs(latest.basis_bp) <= config.normal_bp:
        return _decision(
            state=STATE_NORMAL,
            allowed_sides=ALL_SIDES,
            reason=f"normal_basis_inside_{config.normal_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )

    if previous_state == STATE_RICH_SELL_ONLY and latest.basis_bp > config.normal_bp:
        return _decision(
            state=STATE_RICH_SELL_ONLY,
            allowed_sides=[SELL],
            reason=f"previous_rich_probe_still_valid:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )
    if previous_state == STATE_CHEAP_BUY_ONLY and latest.basis_bp < -config.normal_bp:
        return _decision(
            state=STATE_CHEAP_BUY_ONLY,
            allowed_sides=[BUY],
            reason=f"previous_cheap_probe_still_valid:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )

    confirming = ordered[-config.confirmation_samples:]
    if len(confirming) < config.confirmation_samples:
        return _decision(
            state=STATE_HALT_UNCONFIRMED,
            allowed_sides=[],
            reason=f"basis_needs_{config.confirmation_samples}_confirming_samples",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )
    span = confirming[-1].ts - confirming[0].ts
    if span < config.confirmation_seconds:
        return _decision(
            state=STATE_HALT_UNCONFIRMED,
            allowed_sides=[],
            reason=f"basis_confirmation_span_too_short:{span}s",
            basis_bp=latest.basis_bp,
            confirmed=False,
            samples=ordered,
            now=now_dec,
        )
    rich_confirmed = all(config.normal_bp < sample.basis_bp < config.sustained_halt_bp for sample in confirming)
    cheap_confirmed = all(-config.sustained_halt_bp < sample.basis_bp < -config.normal_bp for sample in confirming)
    if rich_confirmed:
        return _decision(
            state=STATE_RICH_SELL_ONLY,
            allowed_sides=[SELL],
            reason=f"gemini_rich_probe_sell_only:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )
    if cheap_confirmed:
        return _decision(
            state=STATE_CHEAP_BUY_ONLY,
            allowed_sides=[BUY],
            reason=f"gemini_cheap_probe_buy_only:{latest.basis_bp}bp",
            basis_bp=latest.basis_bp,
            confirmed=True,
            samples=ordered,
            now=now_dec,
        )
    return _decision(
        state=STATE_HALT_UNCONFIRMED,
        allowed_sides=[],
        reason="basis_unconfirmed_or_mixed_sign",
        basis_bp=latest.basis_bp,
        confirmed=False,
        samples=ordered,
        now=now_dec,
    )


def basis_direction_label(basis_bp: Decimal | str | float | int | None, config: BasisPolicyConfig = BasisPolicyConfig()) -> str:
    if basis_bp is None:
        return "unknown"
    basis = _to_decimal(basis_bp)
    if abs(basis) <= config.normal_bp:
        return "normal"
    if basis > 0:
        return "gemini_rich"
    return "gemini_cheap"
