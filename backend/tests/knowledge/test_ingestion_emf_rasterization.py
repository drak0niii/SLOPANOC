"""A5 final corrective pass, Correction B: focused tests for
backend/knowledge/ingestion/image_interpretation.py's EMF/WMF
rasterization step. Synthetic-only (A5 instruction section 48) --
real corpus EMF content is never committed; the "genuinely successful
rasterization" case is proven via a monkeypatched `PIL.Image.open`
(exercising THIS module's own glue logic: convert-to-RGB, encode-to-PNG,
exception handling) rather than a byte-perfect hand-built WMF fixture,
since Pillow's own WMF/EMF decode correctness is a third-party concern
already independently confirmed against the real corpus during this
pass's own audit (see the final corrective-pass report).
"""
from __future__ import annotations

import io
from typing import Optional

import pytest

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.image_interpretation import (
    ImageInterpretationResult,
    apply_image_interpretation,
    rasterize_vector_image,
)


class _FakeSucceedingInterpreter:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
        self.calls.append((image_bytes, media_type))
        return ImageInterpretationResult(succeeded=True, description="A rasterized diagram.", model_identifier="fake-model")


# --- rasterize_vector_image: wrong type / malformed input --------------


def test_non_vector_media_type_returns_none_immediately() -> None:
    assert rasterize_vector_image(b"fake png bytes", "image/png") is None


@pytest.mark.parametrize("media_type", ["image/x-emf", "image/x-wmf"])
def test_malformed_vector_bytes_fail_safely(media_type: str) -> None:
    assert rasterize_vector_image(b"not a real emf or wmf file at all", media_type) is None


def test_empty_bytes_fail_safely() -> None:
    assert rasterize_vector_image(b"", "image/x-emf") is None


# --- rasterize_vector_image: successful path (Pillow decode mocked) -----


def test_successful_rasterization_returns_real_png_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    real_open = Image.open

    class _FakeDecodedImage:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def load(self) -> None:
            pass

        def convert(self, mode: str):
            return Image.new("RGB", (4, 4), color=(10, 20, 30))

    monkeypatch.setattr(Image, "open", lambda buf: _FakeDecodedImage())

    result = rasterize_vector_image(b"pretend-this-is-real-emf-bytes", "image/x-emf")
    assert result is not None

    monkeypatch.undo()  # restore the real Image.open before verifying the output is a genuine PNG
    decoded = real_open(io.BytesIO(result))
    assert decoded.format == "PNG"


def test_pillow_decode_exception_falls_back_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    def _raise(buf):
        raise OSError("simulated Pillow decode failure")

    monkeypatch.setattr(Image, "open", _raise)
    assert rasterize_vector_image(b"anything", "image/x-wmf") is None


# --- apply_image_interpretation wiring ----------------------------------


def _emf_artifact(artifact_id: str = "img-emf-1") -> KnowledgeArtifact:
    return KnowledgeArtifact(
        artifact_id=artifact_id,
        kind="image",
        depth=0,
        media_type="image/x-emf",
        extraction_status=ArtifactExtractionStatus.PARTIAL,
        extraction_error="raw binary preserved; multimodal interpretation not yet run",
    )


@pytest.mark.asyncio
async def test_emf_artifact_is_rasterized_before_reaching_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    class _FakeDecodedImage:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def load(self) -> None:
            pass

        def convert(self, mode: str):
            return Image.new("RGB", (2, 2))

    monkeypatch.setattr(Image, "open", lambda buf: _FakeDecodedImage())

    artifact = _emf_artifact()
    interpreter = _FakeSucceedingInterpreter()
    updated = await apply_image_interpretation([artifact], {"img-emf-1": b"pretend-emf-bytes"}, interpreter)

    assert len(interpreter.calls) == 1
    called_bytes, called_media_type = interpreter.calls[0]
    assert called_media_type == "image/png"  # never the original image/x-emf
    assert called_bytes != b"pretend-emf-bytes"  # the rasterized PNG, not the raw EMF


@pytest.mark.asyncio
async def test_successful_emf_rasterization_records_provenance_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    class _FakeDecodedImage:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def load(self) -> None:
            pass

        def convert(self, mode: str):
            return Image.new("RGB", (2, 2))

    monkeypatch.setattr(Image, "open", lambda buf: _FakeDecodedImage())

    artifact = _emf_artifact()
    updated = await apply_image_interpretation([artifact], {"img-emf-1": b"pretend-emf-bytes"}, _FakeSucceedingInterpreter())

    result_artifact = updated[0]
    # Correction B provenance requirement: same artifact_id/content
    # identity, never a replacement -- only new processing_metadata and
    # the usual derived-interpretation fields are added.
    assert result_artifact.artifact_id == "img-emf-1"
    assert result_artifact.derived is True
    assert result_artifact.extracted_text == "A rasterized diagram."
    assert result_artifact.processing_metadata["rasterized_from"] == "image/x-emf"
    assert result_artifact.processing_metadata["rasterized_to"] == "image/png"


@pytest.mark.asyncio
async def test_unrasterizable_emf_falls_back_to_original_bytes_unchanged_behavior() -> None:
    # When Pillow cannot decode the EMF at all (genuinely malformed),
    # apply_image_interpretation must fall back to sending the ORIGINAL
    # bytes/media_type to the interpreter -- byte-identical to this
    # module's pre-Correction-B behavior, never a new failure mode.
    class _FailingInterpreter:
        def __init__(self) -> None:
            self.calls: list[tuple[bytes, str]] = []

        async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
            self.calls.append((image_bytes, media_type))
            return ImageInterpretationResult(succeeded=False, description=None, model_identifier="fake-model", error="Provided image is not valid.")

    artifact = _emf_artifact()
    interpreter = _FailingInterpreter()
    updated = await apply_image_interpretation([artifact], {"img-emf-1": b"genuinely not a real emf"}, interpreter)

    assert interpreter.calls == [(b"genuinely not a real emf", "image/x-emf")]
    assert updated[0].extraction_status == ArtifactExtractionStatus.PARTIAL
    assert updated[0].extracted_text is None


@pytest.mark.asyncio
async def test_non_vector_image_unaffected_by_rasterization_path() -> None:
    png_artifact = KnowledgeArtifact(artifact_id="img-png-1", kind="image", depth=0, media_type="image/png")
    interpreter = _FakeSucceedingInterpreter()
    await apply_image_interpretation([png_artifact], {"img-png-1": b"real png bytes"}, interpreter)

    assert interpreter.calls == [(b"real png bytes", "image/png")]  # never routed through rasterization
