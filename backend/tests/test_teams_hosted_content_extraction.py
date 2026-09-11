"""Teams Rich Content milestone (single-image scope) -- unit tests for
`backend.tools.teams.hosted_content.extract_hosted_content_ids`.

Maps directly onto this milestone's own required test matrix (cases
A-F, plus provenance-safety cases beyond the matrix's own letters).
"""
from __future__ import annotations

from backend.tools.teams.hosted_content import extract_hosted_content_ids

_MESSAGE_ID = "123"


def _img(src: str) -> str:
    return f'<img src="{src}">'


def _hosted_content_url(prefix: str, message_id: str = _MESSAGE_ID, content_id: str = "ABC123") -> str:
    return f"https://graph.microsoft.com/{prefix}/chats/19:abc@thread.v2/messages/{message_id}/hostedContents/{content_id}/$value"


# --- A: beta prefix ---------------------------------------------------------


def test_a_extracts_hosted_content_id_from_beta_url() -> None:
    html = _img(_hosted_content_url("beta"))
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ABC123"]


# --- B: v1.0 prefix ----------------------------------------------------------


def test_b_extracts_hosted_content_id_from_v1_url() -> None:
    html = _img(_hosted_content_url("v1.0"))
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ABC123"]


# --- C: no image --------------------------------------------------------------


def test_c_no_image_returns_empty_list() -> None:
    assert extract_hosted_content_ids("<p>Hello world</p>", _MESSAGE_ID) == []


def test_c_empty_content_returns_empty_list() -> None:
    assert extract_hosted_content_ids("", _MESSAGE_ID) == []
    assert extract_hosted_content_ids("   ", _MESSAGE_ID) == []


# --- D: malformed / non-hosted-content image URL ------------------------------


def test_d_malformed_hosted_content_path_returns_empty_list() -> None:
    # Missing the "/messages/{id}" segment entirely.
    html = _img("https://graph.microsoft.com/beta/chats/x/hostedContents/ABC123/$value")
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == []


def test_d_missing_dollar_value_suffix_returns_empty_list() -> None:
    html = _img(_hosted_content_url("beta").replace("/$value", ""))
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == []


# --- E: duplicate identical hosted image --------------------------------------


def test_e_duplicate_identical_hosted_image_deduplicates() -> None:
    url = _hosted_content_url("beta")
    html = _img(url) + _img(url)
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ABC123"]


def test_e_two_distinct_images_preserve_order_and_both_survive() -> None:
    html = _img(_hosted_content_url("beta", content_id="AAA")) + _img(_hosted_content_url("beta", content_id="BBB"))
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["AAA", "BBB"]


# --- Multiple Teams Hosted Images milestone, section 12.A -------------------


def test_a_three_image_ids_extracted_in_source_order() -> None:
    """Section 3's own worked example: <img ID_A><img ID_B><img ID_C> must
    yield ["ID_A", "ID_B", "ID_C"] -- never reordered by id/hash/mime/
    completion time."""
    html = (
        _img(_hosted_content_url("beta", content_id="ID_A"))
        + _img(_hosted_content_url("beta", content_id="ID_B"))
        + _img(_hosted_content_url("beta", content_id="ID_C"))
    )
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ID_A", "ID_B", "ID_C"]


def test_a_three_image_ids_preserve_order_even_with_text_interleaved() -> None:
    html = (
        f'<p>Look at this:</p>{_img(_hosted_content_url("beta", content_id="ID_A"))}'
        f'<p>and this:</p>{_img(_hosted_content_url("beta", content_id="ID_B"))}'
        f'<p>and this:</p>{_img(_hosted_content_url("beta", content_id="ID_C"))}'
    )
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ID_A", "ID_B", "ID_C"]


# --- F: unrelated image URL ---------------------------------------------------


def test_f_unrelated_external_image_url_returns_empty_list() -> None:
    html = _img("https://example.com/image.png")
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == []


# --- Provenance safety: URL's own embedded messageId must match ---------------


def test_provenance_url_naming_a_different_message_id_is_rejected() -> None:
    """The single most important provenance guarantee this module provides
    (instruction section 4/8): a hosted-content URL embedded in message X's
    own content but naming a DIFFERENT message id must never be attributed
    to X.
    """
    html = _img(_hosted_content_url("beta", message_id="999"))
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == []


def test_provenance_empty_expected_message_id_returns_empty_list() -> None:
    html = _img(_hosted_content_url("beta"))
    assert extract_hosted_content_ids(html, "") == []


# --- Realistic combined message content ---------------------------------------


def test_realistic_message_with_text_before_and_after_image() -> None:
    html = f'<p>Here is the screenshot:</p>{_img(_hosted_content_url("beta"))}<p>Let me know.</p>'
    assert extract_hosted_content_ids(html, _MESSAGE_ID) == ["ABC123"]


def test_never_raises_on_malformed_html() -> None:
    # Unterminated tag / stray characters -- must degrade safely, never raise.
    extract_hosted_content_ids("<img src=", _MESSAGE_ID)
    extract_hosted_content_ids("<<<>>>not html", _MESSAGE_ID)
