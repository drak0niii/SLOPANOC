"""Actual configured clone coverage and static bypass gates, not model-count inference."""
import ast
import importlib
from pathlib import Path
import pytest
from google.adk.tools import AgentTool
from backend.observability.adk_adapter import ObservedAgent
from backend.observability.model_context import observation_sink
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.observability.tool_instrumentation import TOOLS
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_model_provider import spans
from backend.tests.test_observability_agent_instrumentation import drive, synthetic

ROOT=Path(__file__).resolve().parents[2]
VARIANTS=[('backend.agents.team_manager.agent','team_manager'),
    ('backend.agents.team_manager.agent','presentation_team_manager'),
    ('backend.agents.team_manager.direct_read_fast_path','_fast_path_incident_manager'),
    ('backend.agents.team_manager.read_continuation_execution','_CONTINUATION_INCIDENT_MANAGER'),
    ('backend.agents.team_manager.read_continuation_execution','_SYNTHESIS_ONLY_INCIDENT_MANAGER'),
    ('backend.agents.incident_manager.agent','incident_manager'),
    ('backend.agents.technical_authority_engineer.agent','technical_authority_engineer'),
    ('backend.agents.problem_manager.agent','problem_manager'),
    ('backend.agents.automated_operations_engineer.agent','automated_operations_engineer')]
FACTORIES=[('backend.agents.team_manager.source_requirements_completion','_declaration_only_agent'),
    ('backend.agents.incident_manager.provenance_compliance','_compliance_retry_incident_manager'),
    ('backend.agents.team_manager.direct_read_fast_path','get_fast_path_team_manager'),
    ('backend.knowledge_ingestion.gemini_image_interpreter','_interpretation_agent')]


def configured_variants():
    values=[getattr(importlib.import_module(mod),name) for mod,name in VARIANTS]
    values += [getattr(importlib.import_module(mod),name)() for mod,name in FACTORIES]
    from backend.agents.team_manager.agent import get_team_manager
    values += [get_team_manager(False,False,False),get_team_manager(True,True,True)]
    from backend.agents.technical_authority_engineer.agent_tool import _finalization_agent
    values.append(_finalization_agent(values[6]))
    return values


def test_all_rosters_and_configured_clones_use_central_agent_and_tool_adapter():
    found=set()
    for agent in configured_variants():
        assert isinstance(agent,ObservedAgent)
        for tool in agent.tools:
            name=tool.name if hasattr(tool,'name') else tool.__name__
            assert name in TOOLS,name
            found.add(name)
            if isinstance(tool,AgentTool):assert isinstance(tool.agent,ObservedAgent)
    assert found == set(TOOLS)-{'teams_get_members','set_model_response'}

@pytest.mark.asyncio
async def test_all_configured_variants_real_execution_boundary():
    r=Runtime(config())
    try:
        for index,actual in enumerate(configured_variants()):
            root=TurnTrace(f'r{index}',f's{index}',r)
            # Same actual class, ownership/model/clone fields; business callback
            # correctness validated independently by governed regression suites.
            variant=actual.model_copy(update={'before_agent_callback':None,'after_agent_callback':None,
                'before_model_callback':synthetic,'after_model_callback':None, 'tools':[],
                'input_schema':None,'output_schema':None,'instruction':''})
            with root.attached(),observation_sink(None,runtime=r):
                await drive(variant);root.finish()
        executions=[s for s in spans(r) if s.name=='slopanoc.agent']
        assert len(executions)==len(configured_variants())
        assert len({s.context.trace_id for s in executions})==len(executions)
        ingestion=next(s for s in executions if s.attributes['slopanoc.agent']=='km_image_interpreter')
        assert 'slopanoc.run_id' not in ingestion.attributes and ingestion.parent is None
    finally:r.close()


def test_no_new_agent_construction_or_direct_tool_call_bypasses_adapter():
    calls={}
    for p in (ROOT/'backend').rglob('*.py'):
        if 'tests' in p.parts or 'observability' in p.parts:continue
        tree=ast.parse(p.read_text())
        imports=[n for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        for node in imports:
            if node.module=='google.adk.agents':
                assert not any(n.name in ('Agent','LlmAgent') for n in node.names),str(p)
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in TOOLS:
                relative=str(p.relative_to(ROOT));calls.setdefault(relative,[]).append(node.func.id)
    assert calls=={
        'backend/agents/technical_authority_engineer/agent_tool.py':['knowledge_search'],
        'backend/agents/team_manager/read_continuation_execution.py':['teams_get_messages','teams_get_messages']}
    direct=(ROOT/'backend/agents/team_manager/read_continuation_execution.py').read_text()
    assert "with tool_scope('teams_get_messages', 'system')" in direct
    assert "with tool_scope('knowledge_search', 'technical_authority_engineer')" in (ROOT/'backend/agents/technical_authority_engineer/agent_tool.py').read_text()
    assert "with tool_scope('teams_get_members', 'system')" in (ROOT/'backend/api/source_reference.py').read_text()

@pytest.mark.asyncio
async def test_actual_direct_bypasses_and_failed_prefetch_has_no_agent(monkeypatch):
    from types import SimpleNamespace
    from backend.observability.agent_instrumentation import active_execution
    import backend.api.source_reference as members
    import backend.agents.team_manager.read_continuation_execution as reads
    import backend.agents.technical_authority_engineer.agent_tool as tae
    import backend.tools.knowledge.tools as knowledge
    calls=[];r=Runtime(config());root=TurnTrace('r','s',r)
    def member_call(chat_id):
        assert active_execution().tool=='teams_get_members'
        calls.append('members');return {'members':[]}
    def read_call(*args,**kwargs):
        assert active_execution().tool=='teams_get_messages'
        calls.append('read');return {'error':{'errorCode':'run_failure','userMessage':'M4_PRIVATE_TEAMS'}}
    async def search_call(**kwargs):
        assert active_execution().tool=='knowledge_search'
        calls.append('search');return {'items':[]}
    monkeypatch.setattr(members,'teams_get_members',member_call)
    monkeypatch.setattr(reads,'teams_get_messages',read_call)
    monkeypatch.setattr(knowledge,'knowledge_search',search_call)
    try:
        with root.attached():
            assert await members.resolve_authoritative_contributors('private')==[]
            await tae._server_governed_search('r',SimpleNamespace(faults={}), 'fault',None,{},
                stage='selection_discovery',reason='test',rule='M4_PRIVATE_RULE',subject='private query')
            continuation=reads.ResolvedReadContinuation(selected_chat_id='chat',selected_chat_topic='private')
            result=await reads._execute_via_deterministic_retrieval(session_service=None,user_id='u',
                parent_session_id='s',internal_session_id='internal',parent_state={},continuation=continuation,
                attachment_service=None,attachment_storage=None)
            assert result['outcome']=='error'
            root.finish()
        samples=spans(r)
        assert calls==['members','search','read']
        assert not any(s.name=='slopanoc.agent' for s in samples)
        assert {s.attributes['slopanoc.tool'] for s in samples if s.name=='slopanoc.tool'}=={'teams_get_members','knowledge_search','teams_get_messages'}
        assert 'M4_PRIVATE' not in repr(samples)
    finally:r.close()
