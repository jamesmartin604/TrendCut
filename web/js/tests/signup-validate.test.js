import { test } from "node:test";
import assert from "node:assert/strict";

import { emailLooksValid, validateSignup, MIN_PASSWORD_LENGTH } from "../signup-validate.js";

test("emailLooksValid accepts a normal address", () => {
  assert.equal(emailLooksValid("a@b.com"), true);
  assert.equal(emailLooksValid("  Foo@X.com "), true); // trimmed/lowered internally
});

test("emailLooksValid rejects malformed addresses", () => {
  assert.equal(emailLooksValid("nope"), false); // no @
  assert.equal(emailLooksValid("@b.com"), false); // empty local
  assert.equal(emailLooksValid("a@"), false); // empty domain
  assert.equal(emailLooksValid("a b@c.com"), false); // whitespace
  assert.equal(emailLooksValid("a@b@c.com"), false); // two @
});

test("validateSignup passes a valid trio and returns null", () => {
  assert.equal(validateSignup("a@b.com", "password123", "password123"), null);
});

test("validateSignup flags an invalid email first", () => {
  assert.equal(validateSignup("nope", "short", "short"), "Enter a valid email address.");
});

test("validateSignup enforces the minimum password length", () => {
  const short = "x".repeat(MIN_PASSWORD_LENGTH - 1);
  assert.equal(
    validateSignup("a@b.com", short, short),
    `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
  );
});

test("validateSignup catches a confirm-password mismatch", () => {
  assert.equal(
    validateSignup("a@b.com", "password123", "password124"),
    "Passwords do not match.",
  );
});
