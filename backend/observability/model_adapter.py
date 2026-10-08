"""Public ADK BaseLlm adapter; immutable ownership, shared lazy provider."""
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from contextlib import nullcontext
from .model_context import Attribution, ModelAgent, ModelPurpose
from .model_instrumentation import ModelOperation, guarded, protect_content
from .model_provider import instrument_client, safe_model
from .turn_trace import degraded
from .deadlines import child_budget, enforce, close_iterator
from .reliability_contract import Category
from .blocking_work import run_blocking


class ObservedModel(BaseLlm):
    _delegate: BaseLlm = PrivateAttr()
    _attribution: Attribution = PrivateAttr()

    def __init__(self, delegate, attribution):
        super().__init__(model=getattr(delegate, 'model', 'other'))
        self._delegate, self._attribution = delegate, attribution

    @property
    def delegate(self):
        return self._delegate

    async def generate_content_async(self, llm_request, stream=False):
        protect_content()
        op = None
        try:
            op = ModelOperation(safe_model(self.model), self._attribution, streaming=stream)
        except Exception:
            degraded(None)
        iterator = self._delegate.generate_content_async(llm_request, stream=stream)
        error = None
        budget = child_budget(Category.MODEL)
        client_ready = False
        try:
            while True:
                try:
                    with op.attached() if op is not None else nullcontext():
                        if not client_ready:
                            # Client initialization is bounded even if observation fails.
                            try:
                                client = await run_blocking(lambda: self._delegate.api_client,
                                    category=Category.MODEL, budget=budget)
                                if op is not None:
                                    op.provider = 'gcp.vertex_ai' if client.vertexai else 'gcp.gemini'
                                instrument_client(client)
                            except AttributeError:
                                pass  # injected/non-Gemini BaseLlm still executes
                            client_ready = True
                        async with enforce(budget):
                            response = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                yield response  # restored consumer context; original object
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                if op is not None:
                    with op.attached():
                        await close_iterator(iterator)
                else:
                    await close_iterator(iterator)
            finally:
                if op is not None:
                    for response in op.responses:
                        if not response._attempt.closed:
                            try:
                                await close_iterator(response)
                            except BaseException:
                                degraded(op.runtime)
                    op.responses.clear()
                    guarded(op.runtime, op.finish, error)

    def connect(self, llm_request):
        return self._delegate.connect(llm_request)


def instrument_model(model, agent, operation='specialist_reasoning', *, workload=None):
    """Clone attribution only, never the provider/client or business config."""
    delegate = model._delegate if isinstance(model, ObservedModel) else model
    return ObservedModel(delegate, Attribution(ModelAgent(agent), ModelPurpose(operation), workload))
