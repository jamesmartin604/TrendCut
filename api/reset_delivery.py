"""Password-reset delivery seam: how a reset link reaches the user.

Two adapters behind one tiny interface, selected by config:

* :class:`SmtpResetDelivery` — the real adapter for a hosted deployment (stdlib
  ``smtplib``; no new dependency). Never logs the token or the reset URL.
* :class:`ConsoleResetDelivery` — prints the link to the server console. The
  self-host / dev path (and what the CLI reuses): a self-hosted instance with no
  email provider can still recover accounts. The link appears in the console/logs by
  design when this adapter is active, so prefer SMTP (or the CLI) for anything
  public-facing.

Neither the reset endpoint nor the token logic knows which adapter is in use.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

from api.reset_email import render_reset_email

__all__ = [
    "ConsoleResetDelivery",
    "ResetDelivery",
    "SmtpResetDelivery",
    "build_reset_delivery_from_env",
]


@runtime_checkable
class ResetDelivery(Protocol):
    def send(self, email: str, reset_url: str) -> None: ...


class ConsoleResetDelivery:
    """Prints the reset link to stdout. Self-host / dev / CLI adapter."""

    def send(self, email: str, reset_url: str) -> None:
        print(f"[password-reset] reset link for {email}: {reset_url}", flush=True)


class SmtpResetDelivery:
    """Sends the reset link by email over SMTP (STARTTLS). Never logs the URL/token."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_starttls = use_starttls

    def _build_message(self, email: str, reset_url: str) -> EmailMessage:
        """Build the multipart/alternative reset message (no I/O — testable).

        text/plain first, then text/html: in multipart/alternative a client renders
        the LAST part it can display, so adding HTML last makes clients prefer it while
        the plain-text part (with the raw URL) remains the universal fallback.
        """
        subject, text_body, html_body = render_reset_email(reset_url)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._sender
        msg["To"] = email
        msg.set_content(text_body)  # text/plain
        msg.add_alternative(html_body, subtype="html")  # text/html — added LAST
        return msg

    def send(self, email: str, reset_url: str) -> None:
        msg = self._build_message(email, reset_url)
        with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
            if self._use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(msg)


def build_reset_delivery_from_env() -> ResetDelivery:
    """Pick the adapter from the environment.

    SMTP if ``TRENDCUT_SMTP_HOST`` is set, otherwise console. When SMTP is configured
    we REQUIRE ``TRENDCUT_BASE_URL``: that's exactly when a Host-header-poisoned link
    would be emailed to a real user, so we refuse to boot rather than fall back to a
    request-derived base URL. A missing SMTP config is fine — the app boots and the
    CLI path handles recovery.
    """
    host = os.environ.get("TRENDCUT_SMTP_HOST")
    if not host:
        return ConsoleResetDelivery()
    if not os.environ.get("TRENDCUT_BASE_URL"):
        raise RuntimeError(
            "TRENDCUT_SMTP_HOST is set but TRENDCUT_BASE_URL is not. Reset links must "
            "be built from a trusted base URL (not the request Host header), so set "
            "TRENDCUT_BASE_URL to your public base URL (e.g. https://trendcut.example)."
        )
    return SmtpResetDelivery(
        host=host,
        port=int(os.environ.get("TRENDCUT_SMTP_PORT", "587")),
        username=os.environ.get("TRENDCUT_SMTP_USER"),
        password=os.environ.get("TRENDCUT_SMTP_PASSWORD"),
        sender=os.environ.get("TRENDCUT_SMTP_FROM", "no-reply@trendcut.local"),
        use_starttls=os.environ.get("TRENDCUT_SMTP_STARTTLS", "1").strip().lower()
        not in {"0", "false", "no"},
    )
