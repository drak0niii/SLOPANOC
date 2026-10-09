"""Unsampled archive expectations and independent durable confirmation/health."""
import asyncio
import json
from datetime import datetime
from hashlib import sha256
from sqlalchemy import select,func,update
from .policy_models import ArchiveReceipt
from .archive import identifier,attrs,sanitize_request,MAX_TRACE,population_root
from .sampling_policy import ARCHIVE_VERSION
from .privacy_policy import ENVIRONMENTS

class ArchiveMetadata:
    def __init__(self,sessions,environment):
        if environment not in ENVIRONMENTS:raise ValueError('Invalid archive metadata scope')
        self.sessions=sessions;self.environment=environment

    async def confirm(self,archive,trace_id,digest):
        """Confirmation reads the committed object; an exporter ACK isn't enough."""
        if archive.environment!=self.environment:raise ValueError('Archive scope mismatch')
        identifier(trace_id,32);identifier(digest,64)
        async with asyncio.timeout(2):
            directory=archive._directory(trace_id,create=False)
            manifest=json.loads(await asyncio.to_thread(archive._read,directory/(digest+'.manifest.json'),8192))
            payload=await asyncio.to_thread(archive._read,directory/(digest+'.otlp'))
            if manifest.get('completeness')!='COMPLETE' or manifest.get('trace_id')!=trace_id or manifest.get('environment')!=self.environment or sha256(payload).hexdigest()!=digest:
                raise ValueError('Invalid archive confirmation')
            request=sanitize_request(payload,self.environment,max_payload=MAX_TRACE)
            spans=[s for r in request.resource_spans for scope in r.scope_spans for s in scope.spans]
            roots=[s for s in spans if population_root(s)]
            if any(s.trace_id.hex()!=trace_id for s in spans) or len(roots)!=1:raise ValueError('Invalid archive confirmation')
            root=attrs(roots[0].attributes)
            if root.get('slopanoc.archive.population_closed') is not True:raise ValueError('Incomplete archive confirmation')
            if set(root.get('slopanoc.archive.expected_span_ids','').split(','))!={s.span_id.hex() for s in spans}:
                raise ValueError('Incomplete archive confirmation')
            async with self.sessions() as session,session.begin():
                result=await session.execute(update(ArchiveReceipt).where(ArchiveReceipt.environment==self.environment,
                    ArchiveReceipt.trace_id==trace_id,ArchiveReceipt.expected_spans==len(spans),
                    ArchiveReceipt.population_digest==sha256(','.join(sorted(s.span_id.hex() for s in spans)).encode()).hexdigest(),
                    ArchiveReceipt.population_closed==True,ArchiveReceipt.trace_class==manifest['trace_class'],
                    ArchiveReceipt.policy_version==ARCHIVE_VERSION).values(confirmed_digest=digest,
                        confirmed_at=datetime.fromisoformat(manifest['first_complete_commit'])))
                return result.rowcount==1

    async def health(self,*,since,until,coverage_confirmed=False):
        if since.tzinfo is None or until.tzinfo is None or not 0<=(until-since).total_seconds()<=90*86400:
            raise ValueError('Invalid archive health range')
        async with asyncio.timeout(2),self.sessions() as session:
            row=(await session.execute(select(func.count(),func.count(ArchiveReceipt.confirmed_digest)).where(
                ArchiveReceipt.environment==self.environment,ArchiveReceipt.terminal_at>=since,ArchiveReceipt.terminal_at<until))).one()
            return {'eligible':row[0],'confirmed':row[1],
                'completeness':row[1]/row[0] if coverage_confirmed is True and row[0] else None,
                'coverage':'CONFIRMED' if coverage_confirmed is True else 'UNKNOWN'}

    async def reconcile(self,archive,*,batch=32):
        """Bounded local confirmation; scheduling and production binding are external.

        Iterate unsampled pending receipts, never treat a bridge ACK as a durable
        population confirmation. Missing/partial/corrupt commits remain pending.
        """
        if archive.environment!=self.environment or type(batch) is not int or not 1<=batch<=100:
            raise ValueError('Invalid archive reconciliation scope')
        confirmed=unknown=0
        async with asyncio.timeout(2):
            async with self.sessions() as session:
                rows=(await session.execute(select(ArchiveReceipt.trace_id).where(
                    ArchiveReceipt.environment==self.environment,
                    ArchiveReceipt.confirmed_digest.is_(None)).order_by(ArchiveReceipt.terminal_at,ArchiveReceipt.trace_id).limit(batch))).scalars().all()
            for trace_id in rows:
                directory=archive.root/self.environment/identifier(trace_id,32)
                manifests=list(directory.glob('*.manifest.json'))[:9]
                if len(manifests)>8:
                    unknown+=1;continue
                success=False
                for path in manifests:
                    try:
                        digest=identifier(path.name.removesuffix('.manifest.json'),64)
                        success=await self.confirm(archive,trace_id,digest) or success
                    except (ValueError,OSError):pass
                confirmed+=int(success);unknown+=int(not success)
        return {'scanned':len(rows),'confirmed':confirmed,'pending_or_unknown':unknown}
