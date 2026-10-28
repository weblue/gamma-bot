import pytest

from .helpers import point


@pytest.fixture
def pairs():
    return [(point(m, "MESM6", str(600 * ratio)), point(m))
            for m, ratio in zip(range(11, 16), [10, 10, 11, 10, 10])]
