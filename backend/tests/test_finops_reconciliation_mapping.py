from datetime import timedelta
import pytest
from pydantic import ValidationError
from backend.tests._m12_reconciliation import *
from backend.observability.finops.reconciliation_contracts import ratio,Policy

@pytest.mark.parametrize('change',[dict(sku=digest('other')),dict(field='total_tokens',sku=digest('other')),dict(field='cached_tokens',sku=digest('other'))])
def test_mapping_and_token_population_overlap_rejected(change):
    m=registry().mappings[0];other=m.model_copy(update=change)
    with pytest.raises(ValidationError):Registry(mappings=(m,other))


def test_configuration_overlap_and_effective_dates():
    m=registry().mappings[0]
    with pytest.raises(ValidationError):Registry(mappings=(m,m))
    later=m.model_copy(update={'effective_from':m.effective_to,'effective_to':m.effective_to+timedelta(days=1)})
    assert len(Registry(mappings=(m,later)).mappings)==2
    r,b,p=inputs();assert registry().lookup(b['buckets'][0],JAN,JAN+timedelta(hours=1))==m
    assert registry().lookup(b['buckets'][0],m.effective_to,m.effective_to+timedelta(hours=1)) is None
    assert registry().identity==registry().identity

@pytest.mark.parametrize('value',[1.0,True,'NaN','Infinity'])
def test_financial_float_rejection(value):
    from backend.observability.finops.billing_contracts import decimal,SourceError
    with pytest.raises(SourceError):decimal(value)


def test_zero_denominator_and_policy_bounds():
    assert ratio('0','0') is None and ratio('1','0') is None
    assert ratio('0.1','0.3')=='0.333333333333'
    with pytest.raises(ValidationError):Policy(max_rows=100001)
    with pytest.raises(ValidationError):Policy(retention_days=1,overlap_days=2)
