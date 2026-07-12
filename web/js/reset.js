/**
 * Reset page: read the token from the URL, take a new password (+ confirm), submit
 * /auth/reset, and on success route to login (a reset invalidates all sessions, so
 * the user signs in fresh with the new password). Matches the login/signup idiom.
 */

import { getResetToken, validateNewPassword } from "./reset-validate.js";

const $ = (id) => document.getElementById(id);

const form = $("reset-form");
const errorEl = $("reset-error");
const submitBtn = $("reset-submit");
const token = getResetToken(window.location.search);

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

// A missing token means the link was mangled — say so up front rather than on submit.
if (!token) {
  showError("This reset link is missing its token. Request a new link from the sign-in page.");
  submitBtn.disabled = true;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;

  const password = $("password").value;
  const confirm = $("confirm").value;
  const clientError = validateNewPassword(password, confirm);
  if (clientError) {
    showError(clientError);
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Saving…";
  try {
    const res = await fetch("/auth/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: password }),
    });
    if (res.ok) {
      window.location.href = "/login.html?reset=1";
      return;
    }
    let detail = "This reset link is invalid or has expired. Request a new one.";
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* keep the generic message */
    }
    showError(detail);
  } catch {
    showError("Could not reach the server. Check your connection and try again.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Set new password";
  }
});
