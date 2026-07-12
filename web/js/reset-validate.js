/**
 * Pure helpers for the password-reset page — no DOM, so they're unit-testable under
 * `node --test`. Client validation is UX only; the server (api/auth.py
 * validate_password + token check) is authoritative.
 */

export const MIN_PASSWORD_LENGTH = 8;

/** Read the reset token from a URL query string (e.g. location.search). Returns the
 *  token, or "" if absent. */
export function getResetToken(search) {
  const params = new URLSearchParams(search || "");
  return params.get("token") || "";
}

/**
 * Validate a new password + its confirmation. Returns an error message string, or
 * null when valid. (Only a minimum here; the server also enforces a max-length cap.)
 */
export function validateNewPassword(password, confirm) {
  if (String(password).length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (password !== confirm) return "Passwords do not match.";
  return null;
}
