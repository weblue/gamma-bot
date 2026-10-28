"""Price-only opening-range crossing matched to the breakout stop convention.

Replay supplies the same session, account, risk, fills and 2R targets as the GEX
hypotheses. These price-only results cannot establish a GEX edge.
"""
from gamma_bot.features.state import FeatureState
from .models import Signal, SignalStrategy, Variant


BASELINE_RULES = frozenset({"mes_prices", "opening_range"})
OPENING_RANGE_BASELINE = Variant(name="opening_range_baseline", enabled_filters=BASELINE_RULES,
    omitted_rules=("gex", "mapping", "negative_regions", "vix", "cvd", "wall_migration"))


class OpeningRangeBaseline(SignalStrategy):
    family = "baseline"
    full_variant = OPENING_RANGE_BASELINE

    def on_state(self, state: FeatureState) -> Signal | None:
        if not self._ready(state, previous=True):
            return None
        before, close = state.previous_bar.close_ticks, state.bar.close_ticks
        opening = state.opening_range
        if before <= opening.high_ticks and close >= opening.high_ticks + 1:
            return self._propose(state, "long", state.bar.low_ticks - 1, ("opening_range", "high"))
        if before >= opening.low_ticks and close <= opening.low_ticks - 1:
            return self._propose(state, "short", state.bar.high_ticks + 1, ("opening_range", "low"))
        return None
