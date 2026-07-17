"""Tests for the HTML+text password-reset email body.

The plain-text part is mandatory (the always-works fallback with the raw URL); the
HTML is added last so clients prefer it; and the stated expiry is derived from the
real token TTL, so it can't drift.
"""

from __future__ import annotations

from api.auth import RESET_TOKEN_TTL_MINUTES
from api.reset_delivery import SmtpResetDelivery
from api.reset_email import SUBJECT, render_reset_email

_URL = "https://trendcut.net/reset.html?token=Tok3n_-abc"


def _message():
    d = SmtpResetDelivery(host="smtp.example", port=587, username=None, password=None, sender="x@y")
    return d._build_message("user@example.com", _URL)  # noqa: SLF001 - testing the builder


def test_message_is_multipart_alternative_text_then_html() -> None:
    msg = _message()
    assert msg.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in msg.iter_parts()]
    assert parts == ["text/plain", "text/html"]  # HTML last → clients prefer it


def test_plain_text_part_contains_the_raw_url() -> None:
    plain = _message().get_body(("plain",)).get_content()
    assert _URL in plain  # the universal fallback


def test_html_part_contains_the_url_in_href_and_as_text() -> None:
    html = _message().get_body(("html",)).get_content()
    assert f'href="{_URL}"' in html  # the button
    assert _URL in html  # and the copy-paste fallback line


def test_expiry_copy_is_derived_from_the_ttl_constant() -> None:
    # Assert against the CONSTANT, not a hardcoded "45", so the test can't drift either.
    msg = _message()
    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert str(RESET_TOKEN_TTL_MINUTES) in plain
    assert str(RESET_TOKEN_TTL_MINUTES) in html


def test_subject_and_no_unfilled_placeholders() -> None:
    msg = _message()
    assert msg["Subject"] == SUBJECT == "Reset your TrendCut password"
    for part in ("plain", "html"):
        body = msg.get_body((part,)).get_content()
        assert "{{" not in body and "}}" not in body  # every placeholder substituted


def test_render_returns_subject_text_html_with_url() -> None:
    subject, text, html = render_reset_email(_URL)
    assert subject == SUBJECT
    assert _URL in text and _URL in html
