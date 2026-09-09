"""A5 Layer G: focused tests for
backend/knowledge/ingestion/image_interpretation.py's generic
`ImageInterpreter` Protocol and `apply_image_interpretation` orchestration
-- using fake, in-repo test doubles only (no real Gemini call; the
concrete `GeminiImageInterpreter` lives in
backend/knowledge_ingestion/gemini_image_interpreter.py and is exercised
separately, structurally, without a live model call).
"""
from __future__ import annotations

from typing import Optional

import pytest

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.image_interpretation import ImageInterpretationResult, apply_image_interpretation


class _FakeSucceedingInterpreter:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, Optional[str]]] = []

    async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
        self.calls.append((image_bytes, media_type, context))
        return ImageInterpretationResult(succeeded=True, description="A status panel showing GREEN.", model_identifier="fake-model-1", error=None)


class _FakeFailingInterpreter:
    async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
        return ImageInterpretationResult(succeeded=False, description=None, model_identifier="fake-model-1", error="simulated model timeout")


def _image_artifact(artifact_id: str = "img-1") -> KnowledgeArtifact:
    return KnowledgeArtifact(
        artifact_id=artifact_id,
        kind="image",
        depth=0,
        media_type="image/png",
        extraction_status=ArtifactExtractionStatus.PARTIAL,
        extraction_error="raw binary preserved; multimodal interpretation not yet run",
    )


@pytest.mark.asyncio
async def test_successful_interpretation_updates_artifact() -> None:
    artifact = _image_artifact()
    interpreter = _FakeSucceedingInterpreter()
    updated = await apply_image_interpretation([artifact], {"img-1": b"fake-png-bytes"}, interpreter)

    assert updated[0].extracted_text == "A status panel showing GREEN."
    assert updated[0].derived is True
    assert updated[0].extraction_status == ArtifactExtractionStatus.COMPLETE
    assert updated[0].extraction_error is None
    assert updated[0].processing_metadata["multimodal_model_identifier"] == "fake-model-1"


@pytest.mark.asyncio
async def test_interpreter_receives_raw_bytes_and_media_type() -> None:
    artifact = _image_artifact()
    interpreter = _FakeSucceedingInterpreter()
    await apply_image_interpretation([artifact], {"img-1": b"fake-png-bytes"}, interpreter)
    assert interpreter.calls == [(b"fake-png-bytes", "image/png", None)]


@pytest.mark.asyncio
async def test_bounded_context_forwarded_to_interpreter() -> None:
    artifact = _image_artifact()
    interpreter = _FakeSucceedingInterpreter()
    await apply_image_interpretation(
        [artifact], {"img-1": b"fake-png-bytes"}, interpreter, context_by_artifact_id={"img-1": "Section: Alarm Validation"}
    )
    assert interpreter.calls[0][2] == "Section: Alarm Validation"


@pytest.mark.asyncio
async def test_failed_interpretation_does_not_fabricate_text_or_raise() -> None:
    artifact = _image_artifact()
    updated = await apply_image_interpretation([artifact], {"img-1": b"fake-png-bytes"}, _FakeFailingInterpreter())

    assert updated[0].extracted_text is None
    assert updated[0].derived is False
    assert updated[0].extraction_status == ArtifactExtractionStatus.PARTIAL
    assert updated[0].extraction_error == "simulated model timeout"


@pytest.mark.asyncio
async def test_non_image_artifact_unaffected() -> None:
    artifact = KnowledgeArtifact(artifact_id="sheet-1", kind="xlsx_sheet", depth=0, extracted_text="already has text")
    updated = await apply_image_interpretation([artifact], {"sheet-1": b"irrelevant"}, _FakeSucceedingInterpreter())
    assert updated[0] == artifact


@pytest.mark.asyncio
async def test_image_artifact_with_no_binary_available_unaffected() -> None:
    artifact = _image_artifact()
    updated = await apply_image_interpretation([artifact], {}, _FakeSucceedingInterpreter())
    assert updated[0] == artifact


@pytest.mark.asyncio
async def test_one_failure_does_not_affect_other_artifacts() -> None:
    class _MixedInterpreter:
        async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
            if image_bytes == b"good":
                return ImageInterpretationResult(succeeded=True, description="Readable status.", model_identifier="fake-model-1")
            return ImageInterpretationResult(succeeded=False, description=None, model_identifier="fake-model-1", error="corrupt image")

    artifacts = [_image_artifact("img-good"), _image_artifact("img-bad")]
    updated = await apply_image_interpretation(artifacts, {"img-good": b"good", "img-bad": b"bad"}, _MixedInterpreter())

    good = next(a for a in updated if a.artifact_id == "img-good")
    bad = next(a for a in updated if a.artifact_id == "img-bad")
    assert good.extracted_text == "Readable status."
    assert bad.extracted_text is None and bad.extraction_error == "corrupt image"
