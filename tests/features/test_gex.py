import pytest
from gamma_bot.data.models import CapabilityError, OptionSnapshot
from gamma_bot.features.gex import gex_from_options
from .helpers import at
from decimal import Decimal


def test_unverified_reconstruction_fails_instead_of_inventing_gamma_or_rates():
    source = OptionSnapshot(source_id="synthetic-options", sequence=1, event_at=at(),
        available_at=at(), underlying="SPY", spot=Decimal("600"), rows=(),
        annual_rate=None, dividend_yield=None, rate_provenance="unavailable",
        dividend_provenance="unavailable")
    with pytest.raises(CapabilityError, match="no_gex_history.*rate.*dividend.*OI.*IV"):
        gex_from_options(source)
