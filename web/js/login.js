/**
 * Login page: POST /auth/login, then redirect to the dashboard.
 *
 * On success the server sets the session cookie; we just navigate home. On failure
 * the API returns a single generic message (it never reveals whether the email
 * exists) which we show inline. No framework — same hand-rolled idiom as the rest.
 */

const $ = (id) => document.getElementById(id);

const form = $("login-form");
const errorEl = $("login-error");
const submitBtn = $("login-submit");

// After a password reset the user is routed here with ?reset=1 — confirm it worked.
if (new URLSearchParams(window.location.search).get("reset") === "1") {
  const notice = $("login-notice");
  if (notice) {
    notice.textContent = "Your password was updated. Please sign in with your new password.";
    notice.hidden = false;
  }
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;

  const email = $("email").value.trim();
  const password = $("password").value;
  if (!email || !password) {
    showError("Enter your email and password.");
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Signing in…";
  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.ok) {
      // Full navigation so every page re-fetches under the new session.
      window.location.href = "/";
      return;
    }
    let detail = "Invalid email or password.";
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
    submitBtn.textContent = "Sign in";
  }
});
