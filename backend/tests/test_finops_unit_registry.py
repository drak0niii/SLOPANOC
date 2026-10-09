import pytest
from backend.observability.finops.unit_registry import Registry,UNITS,IDS
from backend.observability.finops.unit_economics_contracts import Snapshot
from backend.tests._m13_unit_economics import economics_inputs

def test_canonical_finite_machine_readable_registry_has_scope_versions_and_boundaries():
    registry=Registry();assert len(IDS)==len(UNITS)==20
    assert registry.identity==Registry.model_validate(registry.model_dump()).identity
    for u in UNITS:
        assert u.eligibility and u.required_financial_scope and u.version and u.effective_from<u.effective_to
    with pytest.raises(ValueError):Registry(units=UNITS[:-1])

def test_safe_snapshot_contract_rejects_content_and_nonfinite_financial_values():
    snap=economics_inputs()['snapshots'][0].model_dump()
    with pytest.raises(ValueError):Snapshot.model_validate(dict(snap,prompt='PRIVATE'))
    from backend.observability.finops.unit_economics_contracts import Result
    from backend.observability.finops.unit_economics import calculate
    result=calculate(**economics_inputs())['results'][0]
    for value in ['NaN','Infinity',0.1]:
        with pytest.raises(ValueError):Result.model_validate(dict(result,direct_amount=value))
