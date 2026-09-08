"""POST-5.1 B6 -- backend/api/multimodal_turn_context.py.

Unit-level tests for the run-id-keyed store of a turn's own trusted image
attachment ids -- register/retrieve/isolation/cleanup, mirroring turn_
context.py's own test discipline exactly (see test_chat_service_turn_
context_lifecycle.py for the precedent this file follows).
"""
from __future__ import annotations

from backend.api.multimodal_turn_context import (
    current_run_image_attachment_ids,
    discard_run_images,
    register_run_images,
)
from backend.api.turn_context import bind_run_id, reset_run_id


def test_register_then_read_under_the_same_run_id() -> None:
    token = bind_run_id("run-1")
    try:
        register_run_images("run-1", ["att-a", "att-b"])
        assert current_run_image_attachment_ids() == ("att-a", "att-b")
    finally:
        discard_run_images("run-1")
        reset_run_id(token)


def test_order_is_preserved_exactly_as_registered() -> None:
    token = bind_run_id("run-order")
    try:
        register_run_images("run-order", ["att-3", "att-1", "att-2"])
        assert current_run_image_attachment_ids() == ("att-3", "att-1", "att-2")
    finally:
        discard_run_images("run-order")
        reset_run_id(token)


def test_empty_registration_is_a_safe_no_op() -> None:
    token = bind_run_id("run-empty")
    try:
        register_run_images("run-empty", [])
        assert current_run_image_attachment_ids() == ()
    finally:
        discard_run_images("run-empty")
        reset_run_id(token)


def test_missing_run_id_registration_is_a_safe_no_op() -> None:
    # Never raises even though run_id is falsy -- mirrors turn_context.py's
    # own record_message_texts contract.
    register_run_images(None, ["att-a"])  # type: ignore[arg-type]
    register_run_images("", ["att-a"])


def test_unbound_run_id_returns_empty_tuple() -> None:
    # No bind_run_id call at all -- current_run_id() is None outside a
    # chat_service.py-driven turn.
    assert current_run_image_attachment_ids() == ()


def test_unknown_run_id_returns_empty_tuple() -> None:
    token = bind_run_id("run-unregistered")
    try:
        assert current_run_image_attachment_ids() == ()
    finally:
        reset_run_id(token)


def test_discard_removes_the_entry() -> None:
    token = bind_run_id("run-discard")
    try:
        register_run_images("run-discard", ["att-a"])
        discard_run_images("run-discard")
        assert current_run_image_attachment_ids() == ()
    finally:
        reset_run_id(token)


def test_double_discard_is_safe() -> None:
    discard_run_images("run-never-registered")
    discard_run_images("run-never-registered")  # never raises


def test_two_interleaved_runs_never_see_each_others_images() -> None:
    """Concurrency/isolation proof (instruction section 11) -- two
    different run_ids, each bound/read/discarded independently, must
    never leak into each other, even though the backing store is a single
    module-level dict shared by the whole process.
    """
    token_a = bind_run_id("run-A")
    try:
        register_run_images("run-A", ["att-A1", "att-A2"])
    finally:
        reset_run_id(token_a)

    token_b = bind_run_id("run-B")
    try:
        register_run_images("run-B", ["att-B1"])
        assert current_run_image_attachment_ids() == ("att-B1",)
    finally:
        reset_run_id(token_b)

    token_a2 = bind_run_id("run-A")
    try:
        assert current_run_image_attachment_ids() == ("att-A1", "att-A2")
    finally:
        discard_run_images("run-A")
        discard_run_images("run-B")
        reset_run_id(token_a2)


def test_registering_a_new_run_id_does_not_disturb_an_existing_one() -> None:
    token_a = bind_run_id("run-X")
    register_run_images("run-X", ["att-x"])
    reset_run_id(token_a)

    token_b = bind_run_id("run-Y")
    register_run_images("run-Y", ["att-y"])
    reset_run_id(token_b)

    token_a2 = bind_run_id("run-X")
    try:
        assert current_run_image_attachment_ids() == ("att-x",)
    finally:
        discard_run_images("run-X")
        discard_run_images("run-Y")
        reset_run_id(token_a2)
