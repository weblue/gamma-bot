import pytest

from .helpers import breakout_state, reversal_state


@pytest.fixture(params=["long", "short"])
def reversal(request):
    return reversal_state(request.param)


@pytest.fixture(params=["long", "short"])
def breakout(request):
    return breakout_state(request.param)
