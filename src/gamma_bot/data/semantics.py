"""Normalized GEX semantics shared by admission, features and experiment reports."""
from .models import Dataset, GexSnapshot, InvalidDatasetError

PROFILE_BOUNDARY_CONVENTION = 'lower inclusive, upper exclusive'


def canonical_profile(metadata) -> bool:
    """Only this explicit convention certifies normalized negative membership."""
    return metadata.get('profile_boundary_convention') == PROFILE_BOUNDARY_CONVENTION


def snapshot_identity(snapshot: GexSnapshot) -> dict:
    return dict(source_id=snapshot.source_id, model_id=snapshot.model_id,
                underlying=snapshot.underlying, expiry_days=tuple(sorted(snapshot.expiry_days)),
                provenance=snapshot.provenance)


def experiment_gex_identity(dataset: Dataset) -> dict:
    """Validate one actual identity over all supplied experiment inputs.

    This is input validation/provenance, never feature availability. Warm-up,
    excluded dates and future study partitions cannot elect a second identity.
    Optional source labels must agree with snapshots; metadata alone never
    selects an identity. Indicative snapshots retain their actual provenance.
    """
    selected = None
    for event in dataset.events:
        if not isinstance(event, GexSnapshot):
            continue
        identity = snapshot_identity(event)
        metadata = dataset.source_metadata.get(event.source_id, {})
        for key, actual in identity.items():
            if key not in metadata:
                continue
            declared = metadata[key]
            if key == 'expiry_days':
                if (not isinstance(declared, (list, tuple)) or not declared
                        or any(type(d) is not int or d < 0 for d in declared)
                        or len(set(declared)) != len(declared)):
                    raise InvalidDatasetError('GEX metadata has invalid expiry universe')
                declared = tuple(sorted(declared))
            if declared != actual:
                raise InvalidDatasetError('GEX metadata contradicts actual snapshot identity')
        if selected is not None and selected != identity:
            raise InvalidDatasetError('GEX identity switch requires a separate experiment')
        selected = identity
    return {'status': 'unselected'} if selected is None else dict(status='selected', **selected)
