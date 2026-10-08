import pytest
from pydantic import ValidationError
from backend.observability.redaction import (classify_field, DataClassification, COUNT_FIELDS,
    PROHIBITED_FIELDS, sanitize_metadata, validate_metadata)
from backend.observability.attributes import HIGH_CARDINALITY_FIELDS, validate_metric_labels
from backend.observability.schemas import TraceEvent
from backend.tests.test_observability_contract import event

@pytest.mark.parametrize('field', sorted(PROHIBITED_FIELDS | {'new_unknown_field'}))
def test_prohibited_fields_fail_closed(field):
    fake='Bearer FAKE_SECRET_VALUE'
    assert classify_field(field)==DataClassification.PROHIBITED
    assert fake not in repr(sanitize_metadata({field:fake}))
    with pytest.raises(ValidationError): TraceEvent(**event(metadata={field:fake}))

@pytest.mark.parametrize('field', sorted(HIGH_CARDINALITY_FIELDS))
def test_identifiers_restricted_and_never_metric_labels(field):
    assert classify_field(field)==DataClassification.RESTRICTED
    with pytest.raises(ValueError): validate_metric_labels({field:'id'},value_registry={field:frozenset({'id'})})
    with pytest.raises(ValueError): validate_metadata({field:'id'})

@pytest.mark.parametrize('value', [True,-1,2**63,'FAKE_SECRET', {'api_key':'FAKE'}, ['private'], None, 1.5])
def test_count_fields_reject_wrong_or_nested_values(value):
    assert sanitize_metadata({'message_count':value})=={}
    with pytest.raises(ValueError): validate_metadata({'message_count':value})

def test_sanitizer_preserves_only_safe_counts_without_modifying_input():
    data={'message_count':3,'prompt':{'password':'FAKE_SECRET'}}
    assert sanitize_metadata(data)=={'message_count':3}
    assert 'prompt' in data
    assert all(classify_field(field)==DataClassification.SAFE for field in COUNT_FIELDS)
    assert validate_metadata({key:0 for key in COUNT_FIELDS})=={key:0 for key in COUNT_FIELDS}

def test_metric_values_must_be_bounded_registered_values():
    registry={'agent':frozenset({'team_manager'})}
    assert validate_metric_labels({'agent':'team_manager'},value_registry=registry)=={'agent':'team_manager'}
    for labels in ({'agent':'run-unique-id'},{'agent':'Bearer FAKE'},{'error_message':'raw'},{'config_version':'sha'}):
        with pytest.raises(ValueError): validate_metric_labels(labels,value_registry=registry)
