"""Unit-explicit TOML research configuration; no environment or credential reads."""
from pathlib import Path
import tomllib

from .data.io import decode_record
from .data.models import InvalidDatasetError, RunConfig


def load_config(path: Path) -> RunConfig:
    try:
        with Path(path).open("rb") as handle:
            return decode_record(tomllib.load(handle), RunConfig)
    except InvalidDatasetError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise InvalidDatasetError("invalid research configuration: " + type(exc).__name__) from exc
