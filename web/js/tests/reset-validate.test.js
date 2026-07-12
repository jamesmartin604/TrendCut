import { test } from "node:test";
import assert from "node:assert/strict";

import { getResetToken, validateNewPassword, MIN_PASSWORD_LENGTH } from "../reset-validate.js";

test("getResetToken reads the token param from a query string", () => {
  assert.equal(getResetToken("?token=abc123"), "abc123");
  assert.equal(getResetToken("?foo=1&token=xyz&bar=2"), "xyz");
});

test("getResetToken returns '' when there is no token", () => {
  assert.equal(getResetToken(""), "");
  assert.equal(getResetToken("?other=1"), "");
  assert.equal(getResetToken(undefined), "");
});

test("validateNewPassword passes a valid matching pair", () => {
  assert.equal(validateNewPassword("password123", "password123"), null);
});

test("validateNewPassword enforces the minimum length", () => {
  const short = "x".repeat(MIN_PASSWORD_LENGTH - 1);
  assert.equal(
    validateNewPassword(short, short),
    `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
  );
});

test("validateNewPassword catches a mismatch", () => {
  assert.equal(validateNewPassword("password123", "password124"), "Passwords do not match.");
});
