"""Public ADK append extension: safe metadata commits WITH the actual ADK event.

Class B: retry reading metadata from public get_session().events; destination journal
and outbox are atomic, but NOT atomic with the ADK transaction. No private DB access.
"""
from datetime import datetime,timezone
from functools import wraps
from .journal import event as journal_event
from .runtime import current,references
from backend.observability.hybrid.contracts import decode_event

KEY='slopanoc_execution_audit_v1'

def install_handoff(service):
    if getattr(service,'_slopanoc_audit_handoff',False) or not callable(getattr(service,'append_event',None)):return
    from google.adk.sessions import DatabaseSessionService
    durable=isinstance(service,DatabaseSessionService)
    original=service.append_event
    @wraps(original)
    async def append(session,event):
        adk_event=event
        runtime=current()
        if runtime is None or not durable:return await original(session,adk_event)
        facts=[]
        import re
        actor={'actor_type':'user' if adk_event.author=='user' else 'agent',
            'actor_reference':session.user_id if adk_event.author=='user' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',session.user_id) else adk_event.author if adk_event.author!='user' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',adk_event.author) else None}
        delta=adk_event.actions.state_delta if adk_event.actions else {}
        proposal=delta.get('pending_action_proposal')
        if isinstance(proposal,dict):
            from backend.approval.schemas import ActionProposal
            parsed=ActionProposal.model_validate(proposal)
            prior=await service.get_session(app_name=session.app_name,user_id=session.user_id,session_id=session.id)
            previous=prior.state.get('pending_action_proposal') if prior else None
            changed=not isinstance(previous,dict) or previous.get('proposal_id')!=parsed.proposal_id or previous.get('status')!=parsed.status.value
            if changed:
                kind={'pending':'requested','approved':'approved','rejected':'rejected','expired':'expired','consumed':'consumed'}[parsed.status.value]
                refs=references();refs['session_id']=session.id
                facts.append(journal_event('approval.'+kind,runtime.journal.environment,adk_event.id,
                    at=datetime.fromtimestamp(adk_event.timestamp,timezone.utc),consistency_class='B',outcome=parsed.status.value.upper(),
                    approval_id=parsed.proposal_id,source_revision=adk_event.id,**actor,**refs))
        sources=delta.get('turn_source_references')
        if isinstance(sources,dict):
            prior=await service.get_session(app_name=session.app_name,user_id=session.user_id,session_id=session.id)
            old=prior.state.get('turn_source_references',{}) if prior else {}
            for index,(turn_id,reference) in enumerate(sorted(sources.items()),1):
                if not isinstance(reference,dict) or old.get(turn_id)==reference:continue
                refs=references();refs.update(session_id=session.id,turn_id=turn_id)
                facts.append(journal_event('evidence.selection.recorded',runtime.journal.environment,adk_event.id,index,
                    at=datetime.fromtimestamp(adk_event.timestamp,timezone.utc),consistency_class='B',outcome='RECORDED',
                    provenance_reference=adk_event.id,source_revision=turn_id,
                    selected_count=len(reference.get('knowledge_sources',[])),**actor,**refs))
        if facts:
            # Metadata is not content or state_delta; ADK contents/rewind contract preserved.
            metadata=dict(adk_event.custom_metadata or {})
            metadata[KEY]=[f.model_dump(mode='json') for f in facts]
            adk_event.custom_metadata=metadata
        result=await original(session,adk_event) # actual source commit; never premature audit
        for fact in facts:await runtime.observe(fact)
        return result
    service.append_event=append
    service._slopanoc_audit_handoff=True

async def reconcile(session,journal):
    """Explicit owner-authorized session only; no cross-user enumeration/content export."""
    count=0
    for adk_event in session.events:
        metadata=adk_event.custom_metadata or {}
        items=metadata.get(KEY,())
        if not isinstance(items,list) or len(items)>16:continue
        for raw in items:
            import json
            fact=decode_event(json.dumps(raw).encode(),environment=journal.environment)
            if fact.event_type not in ('approval.requested','approval.approved','approval.rejected','approval.expired','approval.consumed','evidence.selection.recorded') or fact.payload_safe.execution is None or fact.payload_safe.execution.consistency_class!='B':raise ValueError('Invalid ADK handoff')
            if fact.payload_safe.session_id!=session.id or fact.payload_safe.source_record_id!=adk_event.id:raise ValueError('ADK handoff source mismatch')
            count+=int(await journal.append(fact))
    return count
