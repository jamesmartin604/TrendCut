/**
 * Shared auth helpers for the app pages (not the login page itself).
 *
 * - `redirectToLogin()` — bounce to the login page (used by api.js on a 401).
 * - Auto-wires any `#logout-btn` present in the top bar: POST /auth/logout, then
 *   go to login. Importing this module on a page is enough; it's a no-op if the
 *   page has no logout control.
 */

const LOGIN_URL = "/login.html";

export function redirectToLogin() {
  // Guard against loops if login.html itself ever imports this.
  if (window.location.pathname !== LOGIN_URL) {
    window.location.href = LOGIN_URL;
  }
}

export async function logout() {
  try {
    await fetch("/auth/logout", { method: "POST" });
  } catch {
    /* even if the request fails, drop the user at the login page */
  }
  window.location.href = LOGIN_URL;
}

function wireLogout() {
  const btn = document.getElementById("logout-btn");
  if (!btn) return;
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    logout();
  });
}

// Browser-only auto-wiring. Guarded so importing this module in a non-DOM context
// (e.g. a Node unit test that pulls in api.js) is a harmless no-op.
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireLogout);
  } else {
    wireLogout();
  }
}
