"""Positive-gamma wall reversal using causally confirmed structural pivots."""
from gamma_bot.features.mapping import stable_wall
from gamma_bot.features.state import FeatureState
from .models import Signal, SignalStrategy, Variant, gex_identity, usable_gex


REVERSAL_RULES = frozenset({"mes_prices", "opening_range", "gex", "mapping", "vix", "cvd", "wall_migration"})
FULL_REVERSAL = Variant(name="reversal", enabled_filters=REVERSAL_RULES, omitted_rules=())


class ReversalStrategy(SignalStrategy):
    family = "reversal"
    full_variant = FULL_REVERSAL
    optional_filters = frozenset({"vix", "cvd", "wall_migration", "opening_range"})

    def on_state(self, state: FeatureState) -> Signal | None:
        if not self._ready(state) or not usable_gex(state.current_gex, state.as_of, self.config):
            return None
        gex = state.current_gex
        if gex.snapshot.total_gex <= 0:
            return None
        if "vix" in self.required_rules and (
                state.vix is None or state.vix.available_at > state.as_of
                or not state.vix.mean_reversion_eligible(self.config.vix_ceiling)):
            return None
        tolerance = self.config.level_tolerance_ticks
        setups = []
        if "cvd" in self.required_rules:
            for d in state.divergences:
                if ((d.direction == "long" and d.cvd_second > d.cvd_first)
                        or (d.direction == "short" and d.cvd_second < d.cvd_first)):
                    setups.append((d.direction, d.first_at, d.second_at, d.confirmed_at,
                                   d.second_pivot_ticks))
        else:
            for direction in ("long", "short"):
                pivots = sorted((p for p in state.price_pivots if p.direction == direction
                    and p.confirmed_at <= state.as_of), key=lambda p: p.at)
                for first, second in zip(pivots, pivots[1:]):
                    moved = (second.price_ticks <= first.price_ticks - 1 if direction == "long"
                             else second.price_ticks >= first.price_ticks + 1)
                    if moved:
                        setups.append((direction, first.at, second.at, second.confirmed_at,
                                       second.price_ticks))
        for direction, first_at, second_at, confirmed_at, pivot_ticks in sorted(
                setups, key=lambda s: (s[2], s[0]), reverse=True):
            long = direction == "long"
            if (not first_at < second_at <= confirmed_at <= state.as_of
                    or (second_at - first_at).total_seconds() > self.config.pivot_max_separation_seconds
                    or (state.as_of - second_at).total_seconds() > self.config.pivot_max_separation_seconds):
                continue
            for wall in sorted(gex.walls, key=lambda w: w.wall_id):
                if wall.kind != ("support" if long else "resistance"):
                    continue
                if (abs(pivot_ticks - wall.level_ticks) > tolerance
                        or (long and state.bar.close_ticks <= wall.level_ticks)
                        or (not long and state.bar.close_ticks >= wall.level_ticks)):
                    continue
                if "opening_range" in self.required_rules:
                    opening = state.opening_range.low_ticks if long else state.opening_range.high_ticks
                    if abs(wall.level_ticks - opening) > tolerance:
                        continue
                if "wall_migration" in self.required_rules and not stable_wall(
                        state.wall_history, wall.wall_id, state.as_of, config=self.config):
                    continue
                stop = pivot_ticks + (-1 if long else 1)
                signal = self._propose(state, direction, stop,
                    (*gex_identity(gex), "wall", wall.wall_id))
                if signal is not None:
                    return signal
        return None
