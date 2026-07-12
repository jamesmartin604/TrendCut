/**
 * Request-reset page: POST /auth/request-reset, then show the same generic
 * confirmation regardless of whether the email exists (the server never discloses
 * account existence). No framework — matches the login/signup idiom.
 */

const $ = (id) => document.getElementById(id);

const form = $("forgot-form");
const doneEl = $("forgot-done");
const submitBtn = $("forgot-submit");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = $("email").value.trim();
  if (!email) return;

  submitBtn.disabled = true;
  submitBtn.textContent = "Sending…";
  try {
    const res = await fetch("/auth/request-reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    let message = "If that email is registered, a password reset link has been sent.";
    try {
      const body = await res.json();
      if (body.message) message = String(body.message);
    } catch {
      /* keep the default generic message */
    }
    doneEl.textContent = `${message} Check your inbox (and spam), then follow the link.`;
    doneEl.hidden = false;
    form.querySelector(".field-box").classList.add("is-disabled");
  } catch {
    doneEl.textContent = "Could not reach the server. Check your connection and try again.";
    doneEl.hidden = false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Send reset link";
  }
});
