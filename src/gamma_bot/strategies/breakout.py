"""Price crossing into a frozen negative region, rechecked on the newest profile."""
from gamma_bot.features.state import FeatureState, MappedRegion
from .models import Signal, SignalStrategy, Variant, gex_identity, usable_gex


BREAKOUT_RULES = frozenset({"mes_prices", "opening_range", "gex", "mapping", "negative_regions"})
FULL_BREAKOUT = Variant(name="breakout", enabled_filters=BREAKOUT_RULES, omitted_rules=())


def contains(region: MappedRegion, ticks: int) -> bool:
    """Normalized profile convention: lower inclusive, upper exclusive."""
    return ((region.lower_ticks is None or ticks >= region.lower_ticks)
            and (region.upper_ticks is None or ticks < region.upper_ticks))


class BreakoutStrategy(SignalStrategy):
    family = "breakout"
    full_variant = FULL_BREAKOUT
    optional_filters = frozenset({"opening_range"})

    def on_state(self, state: FeatureState) -> Signal | None:
        if not self._ready(state, previous=True):
            return None
        prior, current = state.previous_gex, state.current_gex
        if (not usable_gex(prior, state.previous_bar.available_at, self.config)
                or not usable_gex(current, state.as_of, self.config)
                or gex_identity(prior) != gex_identity(current)
                or not prior.snapshot.profile_available or not current.snapshot.profile_available
                or current.snapshot.total_gex >= 0
                or not any(contains(r, state.bar.close_ticks) for r in current.negative_regions)):
            return None
        before, close = state.previous_bar.close_ticks, state.bar.close_ticks
        for region in sorted(prior.negative_regions, key=lambda r: r.region_id):
            for direction, boundary in (("long", region.lower_ticks), ("short", region.upper_ticks)):
                if boundary is None:
                    continue
                long = direction == "long"
                crossed = (before <= boundary and close >= boundary + 1) if long else (
                    before >= boundary and close <= boundary - 1)
                if not crossed or not contains(region, close):
                    continue
                if "opening_range" in self.required_rules:
                    opening = state.opening_range.high_ticks if long else state.opening_range.low_ticks
                    if abs(boundary - opening) > self.config.level_tolerance_ticks:
                        continue
                stop = state.bar.low_ticks - 1 if long else state.bar.high_ticks + 1
                signal = self._propose(state, direction, stop,
                    (*gex_identity(prior), "region", region.region_id))
                if signal is not None:
                    return signal
        return None
