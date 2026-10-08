import pytest
from backend.config.settings import Settings, ConfigurationError
from backend.observability.config import ObservabilityConfig, SETTING_NAMES


def test_settings_reuses_mapping_defaults_and_existing_timeouts():
    values={'SLOPANOC_POWER_AUTOMATE_TIMEOUT_SECONDS':'17','SLOPANOC_KNOWLEDGE_DENSE_TIMEOUT_SECONDS':'23'}
    # Existing gateway setting uses its own established environment name.
    settings=Settings(values)
    config=settings.observability_config
    assert not config.observability_enabled and not config.otel_enabled and not config.finops_enabled
    assert config.trace_sample_rate==1 and config.finops_currency=='EUR'
    assert config.graph_timeout_seconds==settings.request_timeout_seconds==17
    assert config.knowledge_timeout_seconds==15  # M6 whole retrieval; dense cap remains separate
    assert settings.knowledge_dense_timeout_seconds==23
    assert config.model_timeout_seconds==120 and config.heartbeat_seconds==5
    values['SLOPANOC_GRAPH_TIMEOUT_SECONDS']='19'
    assert settings.observability_config.graph_timeout_seconds==19
    assert set(SETTING_NAMES)=={name.upper() for name in ObservabilityConfig.model_fields}

@pytest.mark.parametrize('key,value', [
    ('OTEL_ENABLED','yes'),('TRACE_ERRORS_ALWAYS','false'),('TRACE_GOVERNED_TURNS_ALWAYS','false'),
    ('TRACE_SAMPLE_RATE','0.2'),('TRACE_SAMPLE_RATE','NaN'),('GRAPH_TIMEOUT_SECONDS','inf'),
    ('MODEL_TIMEOUT_SECONDS','-1'),('HEARTBEAT_SECONDS','30'),('FINOPS_CURRENCY','USD'),
    ('OTEL_EXPORTER_OTLP_ENDPOINT','https://user:FAKE_SECRET@host'),
    ('OTEL_EXPORTER_OTLP_ENDPOINT','https://host?token=FAKE_SECRET'),
    ('OTEL_EXPORTER_OTLP_ENDPOINT','file:///tmp/x'),('OTEL_EXPORTER_OTLP_ENDPOINT','http://host:bad'),
    ('FINOPS_PRICE_SOURCE','focus'),('FINOPS_BILLING_PROJECT','!bad'),
    ('FINOPS_BILLING_DATASET','bad dataset'),('STORAGE_TIMEOUT_SECONDS','FAKE_SECRET')])
def test_invalid_settings_fail_without_leaking_values(key,value):
    with pytest.raises(ConfigurationError) as error:
        Settings({'SLOPANOC_'+key:value}).observability_config
    assert value not in str(error.value)
    assert 'FAKE_SECRET' not in str(error.value)

def test_enabled_configuration_is_only_data_not_runtime(monkeypatch):
    def denied(*args,**kwargs): raise AssertionError('Secret resolution forbidden')
    monkeypatch.setattr('backend.config.settings._fetch_secret_from_secret_manager',denied)
    config=Settings({'SLOPANOC_OBSERVABILITY_ENABLED':'true','SLOPANOC_OTEL_ENABLED':'true',
        'SLOPANOC_OTEL_EXPORTER_OTLP_ENDPOINT':'http://collector:4318',
        'SLOPANOC_DATABASE_SECRET_RESOURCE':'never-read'}).observability_config
    assert config.otel_enabled
    assert 'collector' not in repr(config) and 'collector' not in config.model_dump_json()
    with pytest.raises(ConfigurationError): Settings({'SLOPANOC_OTEL_ENABLED':'true'}).observability_config

@pytest.mark.parametrize('key', ['SLOPANOC_POWER_AUTOMATE_TIMEOUT_SECONDS', 'SLOPANOC_KNOWLEDGE_DENSE_TIMEOUT_SECONDS'])
def test_existing_timeout_fallback_errors_are_sanitized(key):
    with pytest.raises(ConfigurationError) as error:
        Settings({key:'FAKE_SECRET'}).observability_config
    assert 'FAKE_SECRET' not in str(error.value)
