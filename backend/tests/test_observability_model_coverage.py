"""Inventory gates: source-level bypass detection + actual configured wrappers."""
import ast
import importlib
from pathlib import Path
import httpx
import pytest
from backend.observability.model_adapter import ObservedModel, instrument_model
from backend.observability.model_context import observation_sink
from backend.tests.test_observability_model_provider import fake_model, request, payload

ROOT=Path(__file__).resolve().parents[2]
AGENTS=[('team_manager','team_manager'),('incident_manager','incident_manager'),
    ('technical_authority_engineer','technical_authority_engineer'),('problem_manager','problem_manager'),
    ('automated_operations_engineer','automated_operations_engineer')]
VARIANTS=[('backend.agents.team_manager.agent','presentation_team_manager','presentation'),
    ('backend.agents.team_manager.direct_read_fast_path','_fast_path_incident_manager','specialist_reasoning'),
    ('backend.agents.team_manager.read_continuation_execution','_CONTINUATION_INCIDENT_MANAGER','specialist_reasoning'),
    ('backend.agents.team_manager.read_continuation_execution','_SYNTHESIS_ONLY_INCIDENT_MANAGER','synthesis')]
BOUNDARIES=[('backend/api/chat_service.py','_retry_trusted_presentation_once','presentation'),
    ('backend/agents/team_manager/direct_read_fast_path.py','_run_trusted_presentation','synthesis'),
    ('backend/agents/team_manager/source_requirements_completion.py','request_source_requirements_declaration','classification'),
    ('backend/agents/team_manager/governed_knowledge_completion.py','enforce_governed_knowledge_at_completion','remediation'),
    ('backend/agents/incident_manager/provenance_compliance.py','_run_compliance_retry','remediation'),
    ('backend/agents/technical_authority_engineer/agent_tool.py','_regenerate_structured_output','structured_output_repair'),
    ('backend/agents/technical_authority_engineer/agent_tool.py','_reselect_procedure_action','action_reselection'),
    ('backend/agents/technical_authority_engineer/agent_tool.py','_run_model_remediation','remediation'),
    ('backend/config/model_warmup.py','_run_warmup_request','warmup')]


@pytest.mark.asyncio
@pytest.mark.parametrize('folder,name',AGENTS)
async def test_each_real_agent_model_uses_shared_adapter(monkeypatch,folder,name):
    agent=getattr(importlib.import_module('backend.agents.'+folder+'.agent'),name)
    assert isinstance(agent.model,ObservedModel)
    assert agent.model._attribution.agent.value==name
    seen=[]
    async with fake_model(lambda req:httpx.Response(200,json=payload())) as (_,client):
        monkeypatch.setitem(agent.model.delegate.__dict__,'api_client',client)
        with observation_sink(seen.append):
            result=[x async for x in agent.canonical_model.generate_content_async(request())]
        assert result and len(seen)==1 and seen[0].agent.value==name


@pytest.mark.parametrize('module,symbol,operation',VARIANTS)
def test_variant_model_ownership_survives_callback_and_tool_overrides(module,symbol,operation):
    agent=getattr(importlib.import_module(module),symbol)
    assert isinstance(agent.model,ObservedModel) and agent.model._attribution.operation.value==operation


@pytest.mark.parametrize('path,function,operation',BOUNDARIES)
def test_real_business_boundaries_have_bounded_attribution(path,function,operation):
    tree=ast.parse((ROOT/path).read_text())
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==function)
    decorators=[n for n in node.decorator_list if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='model_activity']
    assert len(decorators)==1 and decorators[0].args[1].value==operation


def test_no_provider_entrypoint_bypasses_central_layer():
    allowed={'backend/config/model_warmup.py':{'generate_content_async'},
        'backend/observability/model_adapter.py':{'generate_content_async'},
        'backend/observability/model_provider.py':{'embed_content'}}
    found={}
    for path in (ROOT/'backend').rglob('*.py'):
        if 'tests' in path.parts:continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in (
                'generate_content','generate_content_async','generate_content_stream','generate_content_stream_async',
                'embed_content','predict','connect_live'):
                relative=str(path.relative_to(ROOT))
                assert node.func.attr in allowed.get(relative,set()),f'Uninstrumented provider entrypoint {relative}:{node.lineno}'
                found.setdefault(relative,set()).add(node.func.attr)
    assert found==allowed


def test_embeddings_and_image_real_clients_use_central_paths():
    for filename in ('backend/tools/knowledge/dense_similarity.py','backend/knowledge/embeddings/service.py'):
        tree=ast.parse((ROOT/filename).read_text())
        assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='embedding_request' for n in ast.walk(tree))
    from backend.knowledge_ingestion.gemini_image_interpreter import _interpretation_agent
    model=_interpretation_agent().model
    assert isinstance(model,ObservedModel) and model._attribution.workload=='ingestion'
    assert model._attribution.agent.value=='km_image_interpreter'


def test_fast_path_callback_order_and_shared_provider_preserved():
    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager, _fast_path_before_model_callback
    from backend.agents.team_manager.agent import get_team_manager, presentation_team_manager
    from backend.config.settings import get_shared_llm
    assert _fast_path_incident_manager.before_model_callback[0] is _fast_path_before_model_callback
    for agent in (get_team_manager(),presentation_team_manager,_fast_path_incident_manager):
        assert agent.model.delegate is get_shared_llm(agent.model.model)


@pytest.mark.asyncio
async def test_real_image_model_provider_path_is_independent_ingestion(monkeypatch):
    from backend.knowledge_ingestion.gemini_image_interpreter import _interpretation_agent
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import TurnTrace
    from backend.tests.test_observability_runtime import config
    from google.genai import types
    seen=[];r=Runtime(config())
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (_,client):
            agent=_interpretation_agent();monkeypatch.setitem(agent.model.delegate.__dict__,'api_client',client)
            llm_request=request();llm_request.contents[0].parts.append(types.Part.from_bytes(data=b'PRIVATE_IMAGE_BYTES',mime_type='image/png'))
            root=TurnTrace('image-user-run','image-user-session',r)
            with root.attached(),observation_sink(seen.append,runtime=r):
                output=[x async for x in agent.model.generate_content_async(llm_request)]
                root.finish()
            assert output and len(seen)==1 and seen[0].workload=='ingestion' and seen[0].run_id is None
            assert seen[0].agent.value=='km_image_interpreter' and seen[0].operation.value=='image_interpretation'
            assert 'PRIVATE_IMAGE_BYTES' not in seen[0].model_dump_json()
    finally:r.close()


@pytest.mark.asyncio
async def test_both_actual_embedding_callsites_and_local_fallback(monkeypatch):
    from types import SimpleNamespace
    import google.genai
    import backend.config.settings as settings_module
    from backend.tools.knowledge.dense_similarity import VertexEmbeddingSimilarityProvider
    from backend.knowledge.embeddings.service import VectorEmbeddingService
    seen=[];code=[200]
    def handler(req):
        return httpx.Response(code[0],json={'embeddings':[{'values':[.1,.2]}]} if code[0]==200 else {'error':{'code':500,'message':'private'}})
    async with fake_model(handler) as (_,client):
        dense=VertexEmbeddingSimilarityProvider(dimension=2,client_factory=lambda:client)
        vector=VectorEmbeddingService(dimension=2)
        from backend.observability.config import ObservabilityConfig
        monkeypatch.setattr(settings_module,'get_settings',lambda:SimpleNamespace(google_genai_use_vertexai=True,observability_config=ObservabilityConfig()))
        monkeypatch.setattr(google.genai,'Client',lambda:client)
        with observation_sink(seen.append):
            assert await vector.embed_text('')==[0.,0.]
            vector.embed_text_sync('private document')
            assert seen==[]
            assert await dense._embed(['private query'],'RETRIEVAL_QUERY')==[[.1,.2]]
            assert await dense._embed(['private document'],'RETRIEVAL_DOCUMENT')==[[.1,.2]]
            assert await vector.embed_text('private document')==[.1,.2]
            code[0]=500
            assert len(await vector.embed_text('private fallback'))==2
        assert len(seen)==4
        assert [o.operation.value for o in seen]==['embedding_query','embedding_document','embedding','embedding']
        assert [o.agent.value for o in seen]==['knowledge_retrieval','knowledge_retrieval','knowledge_embedding','knowledge_embedding']
        assert seen[-1].status.value=='FAILED' and seen[-1].usage.availability=='UNKNOWN'
        assert all('private' not in o.model_dump_json() for o in seen)
