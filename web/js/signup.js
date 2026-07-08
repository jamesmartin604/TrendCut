/**
 * Signup page: validate → POST /auth/register → land authenticated on the dashboard.
 *
 * The server auto-logs-in on success (sets the session cookie), so we just navigate
 * home. Client validation is UX only; the server is authoritative and its messages
 * (email taken → 409, invalid/short → 400) are shown when it rejects.
 */

import { validateSignup } from "./signup-validate.js";

const $ = (id) => document.getElementById(id);

const form = $("signup-form");
const errorEl = $("signup-error");
const submitBtn = $("signup-submit");

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;

  const email = $("email").value;
  const password = $("password").value;
  const confirm = $("confirm").value;

  const clientError = validateSignup(email, password, confirm);
  if (clientError) {
    showError(clientError);
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Creating account…";
  try {
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.ok) {
      // Server set the session cookie; full navigation so the app loads as this user.
      window.location.href = "/";
      return;
    }
    let detail =
      res.status === 409
        ? "That email is already registered."
        : "Could not create your account. Check your details and try again.";
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* keep the status-based message */
    }
    showError(detail);
  } catch {
    showError("Could not reach the server. Check your connection and try again.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Create account";
  }
});
