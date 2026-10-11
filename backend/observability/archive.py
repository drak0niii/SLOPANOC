"""Sanitized OTLP archive contract and atomic local object-store stand-in.

No business callbacks, credentials, provider SDK or remote I/O. A root alone is
not completion: a trusted producer closure declares the expected span population.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from typing import Protocol
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from .privacy_policy import ENVIRONMENTS, VERSION as REDACTION_VERSION
from .sampling_policy import EXCEPTIONAL, TraceClass, VERSION as SAMPLING_VERSION, ARCHIVE_VERSION
from .retention_registry import duration

MAX_REQUEST = 4 * 1024 * 1024
MAX_TRACE = 64 * 1024 * 1024
MAX_SPANS = 10000
MAX_EVENTS = 256
SCHEMA = 1
CLOSURE_FIELDS = frozenset({'slopanoc.archive.expected_span_ids', 'slopanoc.archive.population_closed',
    'slopanoc.trace_class', 'slopanoc.redaction_policy', 'slopanoc.sampling_policy', 'slopanoc.archive_policy'})

class ArchiveError(ValueError):
    """Fixed safe errors only; never include an input value."""

def identifier(value, size):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value) or int(value,16)==0:
        raise ArchiveError('Invalid archive identity')
    return value

def decode(value):
    kind=value.WhichOneof('value')
    if kind in ('string_value','bool_value','int_value','double_value'):
        return getattr(value,kind)
    if kind == 'array_value':
        if len(value.array_value.values)>MAX_SPANS:
            raise ArchiveError('Archive array exceeds bound')
        return tuple(decode(v) for v in value.array_value.values)
    return None

def attrs(values):
    if len(values)>256 or len({v.key for v in values})!=len(values):
        raise ArchiveError('Invalid archive attribute population')
    return {v.key:decode(v.value) for v in values}

def encode(value):
    if type(value) is str: return AnyValue(string_value=value)
    if type(value) is bool: return AnyValue(bool_value=value)
    if type(value) is int: return AnyValue(int_value=value)
    if type(value) is float: return AnyValue(double_value=value)
    if type(value) in (list,tuple):
        result=AnyValue()
        result.array_value.values.extend(encode(v) for v in value)
        return result
    raise ArchiveError('Invalid archive value')

def set_attrs(target, values):
    del target[:]
    target.extend(KeyValue(key=k,value=encode(v)) for k,v in sorted(values.items()))

def closure_attributes(values):
    safe={}
    value=values.get('slopanoc.trace_class')
    if value in {v.value for v in TraceClass}: safe['slopanoc.trace_class']=value
    for key,expected in (('slopanoc.redaction_policy',REDACTION_VERSION),
        ('slopanoc.sampling_policy',SAMPLING_VERSION),('slopanoc.archive_policy',ARCHIVE_VERSION)):
        if values.get(key)==expected: safe[key]=expected
    ids=values.get('slopanoc.archive.expected_span_ids')
    if type(ids) is str and len(ids)<=169999: ids=ids.split(',')
    if type(ids) in (tuple,list) and 1<=len(ids)<=MAX_SPANS:
        try:
            valid=tuple(sorted({identifier(v,16) for v in ids}))
            if len(valid)==len(ids): safe['slopanoc.archive.expected_span_ids']=','.join(valid)
        except ArchiveError: pass
    if type(values.get('slopanoc.archive.population_closed')) is bool:
        safe['slopanoc.archive.population_closed']=values['slopanoc.archive.population_closed']
    return safe

def population_root(span):
    """A server population boundary may retain a W3C parent outside that population."""
    values=attrs(span.attributes)
    expected=values.get('slopanoc.archive.expected_span_ids','')
    if type(expected) is str and expected:
        return span.span_id.hex() in expected.split(',') and (not span.parent_span_id or span.parent_span_id.hex() not in expected.split(','))
    return not span.parent_span_id

def sanitize_request(data, environment, *, max_payload=MAX_REQUEST):
    """Bridge revalidates environment, registered span family and all values.

    Unknown payload fields are stripped before durable staging; malformed structure
    fails closed. This function also produces the canonical deterministic OTLP.
    """
    from .tracing import SCOPE, SCHEMA_URL, Operation, safe_events
    from .schemas import TELEMETRY_SCHEMA_VERSION, OperationalEvent
    from .stages import Stage
    from .redaction import safe_span_attributes
    from .attributes import RESOURCE_ATTRIBUTES
    from opentelemetry.sdk.trace import Event
    if environment not in ENVIRONMENTS or not isinstance(data,bytes) or len(data)>max_payload or max_payload>MAX_TRACE:
        raise ArchiveError('Invalid archive request')
    request=ExportTraceServiceRequest()
    try: request.ParseFromString(data)
    except Exception: raise ArchiveError('Invalid OTLP') from None
    output=ExportTraceServiceRequest(); count=0
    names={v.value for v in Operation}|{v.value for v in Stage}|{v.value for v in OperationalEvent}
    resources={v.value for v in RESOURCE_ATTRIBUTES}
    for rs in request.resource_spans:
        resource=attrs(rs.resource.attributes)
        if resource.get('deployment.environment.name')!=environment or resource.get('service.name')!='slopanoc':
            raise ArchiveError('Archive environment mismatch')
        # Resource DTO is the canonical typed validator, not arbitrary syntax.
        from .schemas import ResourceMetadata
        try:
            from .attributes import Attribute
            validated=ResourceMetadata(attributes={Attribute(k):v for k,v in resource.items() if k in resources})
        except Exception: raise ArchiveError('Invalid archive resource') from None
        for scope in rs.scope_spans:
            if scope.scope.name!=SCOPE or scope.scope.version!=str(TELEMETRY_SCHEMA_VERSION):
                continue
            dest=output.resource_spans.add();dest.schema_url=SCHEMA_URL
            set_attrs(dest.resource.attributes,{k.value:v for k,v in validated.attributes.items()})
            target=dest.scope_spans.add();target.scope.name=SCOPE;target.scope.version=str(TELEMETRY_SCHEMA_VERSION);target.schema_url=SCHEMA_URL
            for span in scope.spans:
                if span.name not in names: continue
                count+=1
                if count>MAX_SPANS or len(span.events)>MAX_EVENTS:
                    raise ArchiveError('Archive span population exceeds bound')
                identifier(span.trace_id.hex(),32);identifier(span.span_id.hex(),16)
                if span.parent_span_id: identifier(span.parent_span_id.hex(),16)
                if span.end_time_unix_nano<span.start_time_unix_nano or not span.start_time_unix_nano:
                    raise ArchiveError('Invalid archive timing')
                if span.kind not in range(6) or span.status.code not in range(3):
                    raise ArchiveError('Invalid archive span kind/status')
                clean=target.spans.add();clean.trace_id=span.trace_id;clean.span_id=span.span_id
                clean.parent_span_id=span.parent_span_id;clean.name=span.name;clean.kind=span.kind
                clean.start_time_unix_nano=span.start_time_unix_nano;clean.end_time_unix_nano=span.end_time_unix_nano
                clean.status.code=span.status.code
                values=attrs(span.attributes)
                set_attrs(clean.attributes,safe_span_attributes(values)|closure_attributes(values))
                events=safe_events([Event(e.name,timestamp=e.time_unix_nano,attributes=attrs(e.attributes)) for e in span.events],
                    model=span.name in {Operation.MODEL_REQUEST.value,Operation.MODEL_VALIDATION.value},
                    execution=span.name in {Operation.AGENT.value,Operation.TOOL.value})
                for event in sorted(enumerate(events),key=lambda pair:(pair[1].timestamp,pair[0])):
                    e=event[1];out=clean.events.add();out.name=e.name;out.time_unix_nano=e.timestamp
                    set_attrs(out.attributes,e.attributes)
                # Links, tracestate, flags, status descriptions and dropped-content
                # counters are intentionally not copied.
    return output

@dataclass(frozen=True)
class Commit:
    state: str
    digest: str | None
    duplicate: bool=False

class ExceptionalTraceArchive(Protocol):
    def submit(self, data: bytes) -> tuple[Commit,...]: ...
    def reconstruct(self, principal, trace_id: str, digest: str) -> bytes: ...

class FilesystemArchive:
    """Explicit trusted root; telemetry never chooses paths or object names.

    Atomic hard-link create provides cross-process conditional commit. Chunks are
    safe and content addressed; complete payload/manifest are immutable. Bounds
    include durable staging capacity; excess is explicit failure, never success.
    """
    def __init__(self, root, environment, *, clock=None, max_pending=256, max_spool_bytes=256*1024*1024,runtime=None, staging_seconds=360, read_only=False):
        if environment not in ENVIRONMENTS: raise ArchiveError('Invalid archive environment')
        self.root=Path(root).absolute()
        if self.root.is_symlink(): raise ArchiveError('Archive root may not be a symlink')
        if read_only:
            if not self.root.is_dir(): raise ArchiveError('Archive read root unavailable')
        else:
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.root=self.root.resolve();self.environment=environment
        self.clock=clock or (lambda: datetime.now(timezone.utc))
        self.max_pending=max_pending;self.max_spool_bytes=max_spool_bytes
        if type(max_pending) is not int or not 1<=max_pending<=256 or type(max_spool_bytes) is not int or not 1<=max_spool_bytes<=256*1024*1024:
            raise ArchiveError('Invalid archive bounds')
        if type(staging_seconds) is not int or not 1<=staging_seconds<=600:raise ArchiveError('Invalid staging lifetime')
        self.staging_seconds=staging_seconds
        self.runtime=runtime
        self.lock=RLock();self.failures=0

    def _directory(self, trace_id, *, create=True):
        identifier(trace_id,32)
        path=self.root/self.environment/trace_id
        for p in (self.root/self.environment,path):
            if p.is_symlink(): raise ArchiveError('Unsafe archive directory')
            if create:p.mkdir(exist_ok=True,mode=0o700)
            elif not p.is_dir():raise ArchiveError('Archive unavailable')
        return path

    def _stage(self,trace_id):
        identifier(trace_id,32)
        path=self.root/'.staging'/self.environment/trace_id
        for p in (self.root/'.staging',self.root/'.staging'/self.environment,path):
            if p.is_symlink():raise ArchiveError('Unsafe archive staging directory')
            p.mkdir(exist_ok=True,mode=0o700)
        return path

    def _read(self,path,limit=MAX_TRACE):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size>limit:
                raise ArchiveError('Invalid archive object')
            with os.fdopen(fd,'rb',closefd=False) as stream: return stream.read(limit+1)
        finally: os.close(fd)

    def _create(self,path,data):
        import tempfile
        fd,name=tempfile.mkstemp(dir=path.parent,prefix='.commit-')
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            try: os.link(name,path)
            except FileExistsError:
                if self._read(path)!=data: raise ArchiveError('Archive commit conflict')
                return False
            directory=os.open(path.parent,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
            return True
        finally: os.unlink(name)

    def submit(self,data):
        with self.lock:
            import fcntl
            fd=os.open(self.root/'.archive.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:
                # One non-blocking sink fence; another writer causes safe retry,
                # never unbounded waiting or a refreshed retention anchor.
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return self._submit(data)
            except Exception:
                self.failures+=1
                from .policy_metrics import record
                try:record(self.runtime,'slopanoc.archive.failures')
                except Exception:pass
                raise ArchiveError('Archive ingest failed') from None
            finally:os.close(fd)

    def _submit(self,data):
        self._expire_staging()
        request=sanitize_request(data,self.environment)
        by_trace={}
        for rs in request.resource_spans:
            for scope in rs.scope_spans:
                for span in scope.spans:
                    tid=span.trace_id.hex()
                    record=ExportTraceServiceRequest();r=record.resource_spans.add();r.resource.CopyFrom(rs.resource);r.schema_url=rs.schema_url
                    s=r.scope_spans.add();s.scope.CopyFrom(scope.scope);s.schema_url=scope.schema_url;s.spans.add().CopyFrom(span)
                    by_trace.setdefault(tid,[]).append(record)
        results=[]
        for tid,records in by_trace.items():
            roots=[r.resource_spans[0].scope_spans[0].spans[0] for r in records
                if population_root(r.resource_spans[0].scope_spans[0].spans[0])]
            if any(attrs(root.attributes).get('slopanoc.trace_class')=='HEALTHY' for root in roots):
                results.append(Commit('INELIGIBLE',None));continue
            directory=self.root/self.environment/tid
            if directory.exists():self._directory(tid,create=False)
            # Already committed sub-batch retries acknowledge the existing immutable
            # population, without opening new staging or refreshing its TTL.
            duplicate=self._committed_retry(directory,records)
            if duplicate is not None:
                results.append(duplicate);continue
            staging=self.root/'.staging'/self.environment/tid
            import time
            from itertools import islice
            deadline=time.monotonic()+1
            base=self.root/'.staging'/self.environment
            directories=list(islice(base.iterdir(),self.max_pending+1)) if base.exists() else []
            if len(directories)>self.max_pending:raise ArchiveError('Archive staging capacity exceeded')
            existing=[]
            for stage in directories:
                identifier(stage.name,32)
                if stage.is_symlink():raise ArchiveError('Unsafe archive staging directory')
                paths=list(islice(stage.glob('*.chunk'),MAX_SPANS+1))
                if len(paths)>MAX_SPANS:raise ArchiveError('Archive staging population exceeds bound')
                for path in paths:
                    if time.monotonic()>deadline:raise ArchiveError('Archive quota scan deadline exceeded')
                    existing.append((path,path.stat().st_size))
            incoming=[record.SerializeToString(deterministic=True) for record in records]
            if len(directories)>=self.max_pending and staging not in directories:
                raise ArchiveError('Archive staging capacity exceeded')
            staged={path.name for path,size in existing if path.parent==staging}
            fresh={sha256(payload).hexdigest()+'.chunk':payload for payload in incoming if sha256(payload).hexdigest()+'.chunk' not in staged}
            if len(staged)+len(fresh)>MAX_SPANS:raise ArchiveError('Archive staging population exceeds bound')
            if sum(size for path,size in existing)+sum(len(payload) for payload in fresh.values())>self.max_spool_bytes:
                raise ArchiveError('Archive staging capacity exceeded')
            staging=self._stage(tid)
            anchor=staging/'.opened.json'
            if not anchor.exists():self._create(anchor,json.dumps({'opened_at':self.clock().isoformat()}).encode())
            # Persist safe chunks only. A child without an exceptional root stays
            # staged and can never be promoted as a healthy archive.
            for payload in incoming:
                digest=sha256(payload).hexdigest()
                self._create(staging/(digest+'.chunk'),payload)
            result=self._complete(directory,tid,staging)
            results.append(result)
            from .policy_metrics import record
            try:record(self.runtime,'slopanoc.archive.confirmed' if result.state=='COMPLETE' and not result.duplicate else 'slopanoc.archive.incomplete',int(not result.duplicate))
            except Exception:pass
            if result.state=='COMPLETE':
                for path in staging.glob('*.chunk'):path.unlink()
                (staging/'.opened.json').unlink(missing_ok=True)
                staging.rmdir()
        return tuple(results)

    def _expire_staging(self):
        from datetime import timedelta
        import time
        deadline=time.monotonic()+1
        base=self.root/'.staging'/self.environment
        if not base.exists():return
        from itertools import islice
        for directory in islice(base.iterdir(),self.max_pending):
            if time.monotonic()>deadline:raise ArchiveError('Staging cleanup deadline exceeded')
            identifier(directory.name,32)
            if directory.is_symlink():raise ArchiveError('Unsafe staging directory')
            anchor=directory/'.opened.json'
            if not anchor.exists():continue
            opened=datetime.fromisoformat(json.loads(self._read(anchor,512))['opened_at'])
            if opened.tzinfo is None:raise ArchiveError('Invalid staging clock')
            if self.clock()<opened+timedelta(seconds=self.staging_seconds):continue
            chunks=list(directory.glob('*.chunk'))
            if len(chunks)>MAX_SPANS:raise ArchiveError('Staging population exceeds bound')
            for chunk in chunks:chunk.unlink()
            anchor.unlink();directory.rmdir()
            from .policy_metrics import record
            try:record(self.runtime,'slopanoc.archive.incomplete')
            except Exception:pass

    def _committed_retry(self,directory,records):
        from itertools import islice
        manifests=list(islice(directory.glob('*.manifest.json'),9))
        if len(manifests)>8:raise ArchiveError('Archive versions exceed bound')
        incoming={r.resource_spans[0].scope_spans[0].spans[0].span_id.hex():
            r.SerializeToString(deterministic=True) for r in records}
        for manifest_path in manifests:
            manifest=json.loads(self._read(manifest_path,8192));digest=identifier(manifest['digest'],64)
            payload=self._read(directory/(digest+'.otlp'))
            if sha256(payload).hexdigest()!=digest:raise ArchiveError('Archive integrity failed')
            request=ExportTraceServiceRequest();request.ParseFromString(payload)
            existing={}
            for resource in request.resource_spans:
                single=ExportTraceServiceRequest();single.resource_spans.add().CopyFrom(resource)
                span=resource.scope_spans[0].spans[0]
                existing[span.span_id.hex()]=single.SerializeToString(deterministic=True)
            if all(existing.get(sid)==value for sid,value in incoming.items()):return Commit('COMPLETE',digest,True)
        if len(manifests)==8:raise ArchiveError('Archive versions exceed bound')
        return None

    def _complete(self,directory,tid,staging):
        spans={};encoded={};total=0
        for path in sorted(staging.glob('*.chunk')):
            payload=self._read(path,MAX_REQUEST);total+=len(payload)
            if total>MAX_TRACE: raise ArchiveError('Archive trace exceeds bound')
            item=ExportTraceServiceRequest();item.ParseFromString(payload)
            span=item.resource_spans[0].scope_spans[0].spans[0];sid=span.span_id.hex()
            if sid in encoded and encoded[sid]!=payload: return Commit('CONFLICT',None)
            encoded[sid]=payload;spans[sid]=item
            if len(spans)>MAX_SPANS: raise ArchiveError('Archive trace exceeds bound')
        roots=[v for v in spans.values() if population_root(v.resource_spans[0].scope_spans[0].spans[0])]
        if len(roots)!=1: return Commit('PARTIAL',None)
        root=roots[0].resource_spans[0].scope_spans[0].spans[0]
        values=attrs(root.attributes)
        try: cls=TraceClass(values.get('slopanoc.trace_class'))
        except (ValueError,TypeError): return Commit('UNKNOWN',None)
        if cls not in EXCEPTIONAL: return Commit('INELIGIBLE',None)
        expected=values.get('slopanoc.archive.expected_span_ids')
        if values.get('slopanoc.archive.population_closed') is not True or not expected:
            return Commit('UNKNOWN',None)
        if set(expected.split(','))!=set(spans): return Commit('PARTIAL',None)
        # Memoized parent validation is linear even for a 10,000-span chain.
        root_id=root.span_id.hex();validated={root_id}
        for sid in spans:
            path=set();current=sid
            while current not in validated:
                if current not in spans or current in path: return Commit('CONFLICT',None)
                path.add(current)
                span=spans[current].resource_spans[0].scope_spans[0].spans[0]
                if not span.parent_span_id: return Commit('CONFLICT',None)
                current=span.parent_span_id.hex()
            validated.update(path)
        canonical=ExportTraceServiceRequest()
        for sid in sorted(spans): canonical.resource_spans.extend(spans[sid].resource_spans)
        payload=canonical.SerializeToString(deterministic=True);digest=sha256(payload).hexdigest()
        directory=self._directory(tid)
        target=directory/(digest+'.manifest.json')
        if target.exists():
            return Commit('COMPLETE',digest,True)
        now=self.clock()
        if now.tzinfo is None: raise ArchiveError('Archive clock must be aware')
        manifest={'schema':SCHEMA,'environment':self.environment,'trace_id':tid,'trace_class':cls.value,
            'redaction_policy':REDACTION_VERSION,'sampling_policy':SAMPLING_VERSION,'archive_policy':ARCHIVE_VERSION,
            'terminal_ns':root.end_time_unix_nano,'first_complete_commit':now.isoformat(),
            'eligible_at':(now+duration('exceptional_archive')).isoformat(),'completeness':'COMPLETE',
            'span_count':len(spans),'digest':digest}
        self._create(directory/(digest+'.otlp'),payload)
        try:
            created=self._create(target,json.dumps(manifest,sort_keys=True,separators=(',',':')).encode())
        except ArchiveError:
            existing=json.loads(self._read(target,8192))
            if {k:v for k,v in existing.items() if k not in {'first_complete_commit','eligible_at'}} != {
                k:v for k,v in manifest.items() if k not in {'first_complete_commit','eligible_at'}}:
                raise
            created=False
        return Commit('COMPLETE',digest,not created)

    def reconstruct(self,principal,trace_id,digest):
        from .authorization import Principal, denied
        from .schemas import Permission
        if not isinstance(principal,Principal) or not principal.valid(): raise denied()
        if Permission.TECHNICAL_DIAGNOSTICS not in principal.capabilities or self.environment not in principal.environments:
            raise denied('authorization_error')
        identifier(trace_id,32);identifier(digest,64)
        directory=self._directory(trace_id,create=False)
        manifest=json.loads(self._read(directory/(digest+'.manifest.json'),8192))
        if manifest.get('schema')!=SCHEMA or manifest.get('completeness')!='COMPLETE' or manifest.get('trace_id')!=trace_id or manifest.get('environment')!=self.environment or manifest.get('digest')!=digest:
            raise ArchiveError('Invalid archive manifest')
        if self.clock()>=datetime.fromisoformat(manifest['eligible_at']): raise ArchiveError('Archive retention expired')
        payload=self._read(directory/(digest+'.otlp'))
        if sha256(payload).hexdigest()!=digest: raise ArchiveError('Archive integrity failed')
        # Revalidate on read too; corruption cannot broaden projection.
        safe=sanitize_request(payload,self.environment,max_payload=MAX_TRACE)
        spans=[s for r in safe.resource_spans for scope in r.scope_spans for s in scope.spans]
        roots=[s for s in spans if population_root(s)]
        if any(s.trace_id.hex()!=trace_id for s in spans) or len(roots)!=1 or len(spans)!=manifest.get('span_count'):
            raise ArchiveError('Invalid archive population')
        closure=attrs(roots[0].attributes)
        if closure.get('slopanoc.archive.population_closed') is not True or set(closure.get('slopanoc.archive.expected_span_ids','').split(','))!={s.span_id.hex() for s in spans}:
            raise ArchiveError('Incomplete archive population')
        return safe.SerializeToString(deterministic=True)
