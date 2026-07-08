/**
 * Pure client-side signup validation — no DOM, so it's unit-testable under
 * `node --test`. This is a UX convenience only; the server (api/auth.py
 * validate_registration) is authoritative and mirrors these rules.
 */

export const MIN_PASSWORD_LENGTH = 8;

/** A light "looks like an email" check: one `@` with a non-empty local part and
 *  domain, and no whitespace. Deliberately loose — emails are unverified. */
export function emailLooksValid(email) {
  const e = String(email).trim().toLowerCase();
  if (/\s/.test(e)) return false;
  const at = e.indexOf("@");
  return at > 0 && at < e.length - 1 && e.indexOf("@", at + 1) === -1;
}

/**
 * Returns an error message string if the signup input is invalid, or null if it
 * passes. Order matters: email, then password length, then confirm match.
 */
export function validateSignup(email, password, confirm) {
  if (!emailLooksValid(email)) return "Enter a valid email address.";
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (password !== confirm) return "Passwords do not match.";
  return null;
}
