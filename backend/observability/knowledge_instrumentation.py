"""Concrete metadata-only adapter injected into generic retrieval composition."""
from contextlib import contextmanager
from .dependency_contract import KNOWLEDGE_STAGES
from .dependency_instrumentation import dependency_scope
from .model_instrumentation import guarded
from .dependency_metrics import outcome
from .errors import ErrorCode


class KnowledgeStage:
    def __init__(self, scope):
        self.scope = scope

    def dense_result(self, status):
        def record():
            if not self.scope:
                return
            if status == 'unavailable:timeout':
                self.scope.fail(ErrorCode.KNOWLEDGE_TIMEOUT, 'timeout', 'knowledge', timeout=True)
            elif type(status) is str and status.startswith('unavailable:'):
                self.scope.fail(ErrorCode.KNOWLEDGE_PROVIDER_ERROR, 'provider', 'knowledge')
            if type(status) is str and status.startswith('unavailable:'):
                outcome(self.scope, 'slopanoc.knowledge.fallback')
        guarded(self.scope.runtime if self.scope else None, record)

    def count(self, count):
        def record():
            if self.scope and type(count) is int and 0 <= count <= 2**63 - 1:
                self.scope.attrs['slopanoc.result_count'] = count
                if count == 0:
                    outcome(self.scope, 'slopanoc.knowledge.no_result')
        guarded(self.scope.runtime if self.scope else None, record)


@contextmanager
def knowledge_stage(operation):
    with dependency_scope('knowledge', operation if operation in KNOWLEDGE_STAGES else 'other',
            'knowledge.retrieval', kind='local') as scope:
        yield KnowledgeStage(scope)
