"""Static sink coverage plus concrete integration checks; no business imports."""
import ast
from pathlib import Path
from backend.observability.dependency_instrumentation import CLIENT_OWNERS
ROOT=Path(__file__).resolve().parents[2]
NETWORK_IMPORTS=('requests','httpx','aiohttp','google.cloud','msal','azure','boto3','redis','pinecone','qdrant_client','weaviate','chromadb','elasticsearch','google.genai')
# Exception classifier imports/types are not transport sinks.
CLASSIFIERS={'backend/observability/model_instrumentation.py','backend/observability/dependency_instrumentation.py'}
TYPE_ONLY={'backend/agents/incident_manager/evidence.py','backend/agents/incident_manager/provenance_compliance.py',
 'backend/agents/team_manager/direct_read_fast_path.py','backend/agents/team_manager/governed_knowledge_completion.py',
 'backend/agents/team_manager/multimodal_agent_tool.py','backend/agents/team_manager/operational_routing.py',
 'backend/agents/team_manager/read_continuation_execution.py','backend/agents/team_manager/source_requirements_completion.py',
 'backend/agents/technical_authority_engineer/agent.py','backend/agents/technical_authority_engineer/agent_tool.py','backend/agents/technical_authority_engineer/validation.py',
 'backend/api/chat_service.py','backend/api/hosted_content_vision_context.py','backend/api/multimodal_turn_context.py',
 'backend/api/turn_final_answers.py','backend/config/model_warmup.py','backend/knowledge_ingestion/gemini_image_interpreter.py'}


def test_no_unassigned_network_import_or_concrete_sink():
    hits=set()
    for path in (ROOT/'backend').rglob('*.py'):
        if 'tests' in path.parts:continue
        relative=str(path.relative_to(ROOT));tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            if any(m==prefix or m.startswith(prefix+'.') for m in modules for prefix in NETWORK_IMPORTS):hits.add(relative)
            if isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                if any(name.endswith(suffix) for suffix in ['.post','.get','.send','.request','.upload_from_string','.download_as_bytes','.access_secret_version','create_async_engine','storage.Client','SecretManagerServiceClient']):
                    # AST import guard owns aliases; ordinary mapping.get isn't a network sink.
                    if name in {'requests.post','storage.Client','secretmanager.SecretManagerServiceClient','create_async_engine'}:
                        assert relative in CLIENT_OWNERS, relative
    assert hits <= set(CLIENT_OWNERS)|CLASSIFIERS|TYPE_ONLY, sorted(hits-set(CLIENT_OWNERS)-CLASSIFIERS-TYPE_ONLY)
    assert {'backend/attachments/storage.py','backend/config/settings.py','backend/gateway/power_automate_client.py'} <= hits


def test_all_db_owners_register_engine_and_pool_observation():
    for path in ['backend/cases/db.py','backend/attachments/repository.py','backend/knowledge/repository/sqlalchemy.py','backend/api/session_service.py']:
        source=(ROOT/path).read_text()
        assert ('observe_engine(' in source or 'observe_session_service(' in source) and 'pool_kwargs(' in source
    assert 'observe_call' not in (ROOT/'backend/observability/model_provider.py').read_text()
    assert 'dependency_scope' not in (ROOT/'backend/observability/exporters/otlp.py').read_text()
