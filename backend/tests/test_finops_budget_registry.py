from datetime import date,timedelta
import pytest
from backend.tests._m14_controls import *
from pydantic import ValidationError

def test_empty_source_controlled_and_revision_asof():
    from backend.observability.finops.budget_registry import load
    assert load('infra/observability/finops/budgets.json').budgets==()
    first=budget();later=budget(version='b2',supersedes='b1',recorded_at=ISSUE+timedelta(days=5))
    reg=Registry(version='r2',budgets=(first,later))
    assert reg.active('development','2026-10',ISSUE)==(first,)
    assert reg.active('development','2026-10',ISSUE+timedelta(days=6))==(later,)
    with pytest.raises(ValidationError):Registry(budgets=(first,budget(version='b3')))

@pytest.mark.parametrize('changes',[{'amount':'-1'},{'amount':1.2},{'amount':'NaN'},{'effective_from':date(2026,10,2)},{'currency':'eur'},{'unknown':'SECRET'}])
def test_invalid_definitions(changes):
    with pytest.raises((ValidationError,ValueError)):budget(**changes)

@pytest.mark.parametrize('values',[{'noise_absolute_eur':'0'},{'watch_score':'-1'},{'min_folds':0},{'min_months':1},{'min_unit_population':0},{'scenario_fraction':'2'}])
def test_invalid_policy_cannot_break_mad_or_qualification(values):
    with pytest.raises((ValidationError,ValueError)):Policy(**values)
