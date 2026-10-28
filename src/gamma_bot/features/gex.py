"""Import boundary: normalized GexSnapshot retains its supplier/model provenance.

No source/model was verified by the free-data audit. Imported vendor or explicitly
modeled test-only snapshots can exercise mechanics; neither enables local option
reconstruction nor establishes historical market coverage.
"""
from gamma_bot.data.models import CapabilityError, GexSnapshot, OptionSnapshot


def gex_from_options(snapshot: OptionSnapshot) -> GexSnapshot:
    raise CapabilityError(
        "no_gex_history: local option reconstruction is disabled; no verified "
        "source/model, annual rate, dividend yield, published OI, fixed-contract IV "
        "or price-dependent profile semantics selected by the data audit"
    )
