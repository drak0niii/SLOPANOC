import pytest
from backend.observability.model_usage import extract_usage, count

@pytest.mark.parametrize('value',[None,True,-1,1.5,float('inf'),float('nan'),'1',2**64])
def test_invalid_usage_stays_unknown(value):
    assert count(value) is None
    result=extract_usage({'usageMetadata':{'promptTokenCount':value}})
    assert result.input_tokens is None and result.availability=='UNKNOWN'

@pytest.mark.parametrize('value',[0,1,50,1.0])
def test_authoritative_counts(value):
    u=extract_usage({'usageMetadata':{'promptTokenCount':value,'candidatesTokenCount':3,
        'thoughtsTokenCount':2,'cachedContentTokenCount':value,'totalTokenCount':int(value)+5}})
    assert u.input_tokens==int(value) and u.output_tokens==5 and u.candidate_tokens==3
    assert u.cached_tokens==int(value) and u.availability=='KNOWN'


def test_missing_components_not_zero_or_reconstructed():
    u=extract_usage({'usageMetadata':{'promptTokenCount':5,'candidatesTokenCount':3}})
    assert u.candidate_tokens==3 and u.output_tokens is None and u.total_tokens is None
    assert u.availability=='PARTIAL'
    assert extract_usage({}).availability=='UNKNOWN'


def test_embeddings_token_and_billable_characters_only():
    u=extract_usage({'predictions':[{'embeddings':{'values':['SECRET'],
        'statistics':{'token_count':3.0}}},{'embeddings':{'statistics':{'token_count':7}}}],
        'metadata':{'billableCharacterCount':25}})
    assert u.input_tokens==10 and u.billable_characters==25 and u.output_tokens is None
    assert 'SECRET' not in u.model_dump_json()


def test_partial_embedding_batch_not_summed_as_exact():
    u=extract_usage({'embeddings':[{'statistics':{'token_count':3}},{}]})
    assert u.input_tokens is None


@pytest.mark.parametrize('update', [{'cachedContentTokenCount':11}, {'totalTokenCount':9}, {'candidatesTokenCount':30}])
def test_conflicting_provider_components_are_preserved_as_partial(update):
    raw={'promptTokenCount':10,'candidatesTokenCount':4,'thoughtsTokenCount':2,'totalTokenCount':16}
    raw.update(update)
    usage=extract_usage({'usageMetadata':raw})
    assert usage.availability=='PARTIAL' and usage.input_tokens==10


def test_nonnumeric_provider_metadata_is_not_a_token_parse_error():
    usage=extract_usage({'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':4,
        'thoughtsTokenCount':2,'totalTokenCount':16,'trafficType':'ON_DEMAND'}})
    assert usage.availability=='KNOWN'
    partial=extract_usage({'embeddings':[{'statistics':{'token_count':3}}]},complete=False)
    assert partial.availability=='PARTIAL' and partial.input_tokens==3
