/**
 * Weight unit conversion — pure helpers (no DOM, no I/O).
 *
 * The estimator, storage, and every API payload are in **kilograms**; `units` is a
 * display preference only. Everything is stored/sent as kg and converted to the
 * chosen unit only at the presentation boundary (and back to kg on input), so the
 * frozen backend never sees pounds.
 */

const KG_PER_LB = 0.45359237;
const LB_PER_KG = 1 / KG_PER_LB;
const CM_PER_IN = 2.54;

/** Short label for the unit ("kg" or "lb"). */
export function unitLabel(units) {
  return units === "lb" ? "lb" : "kg";
}

/**
 * Height display unit paired with the weight unit: metric (kg) → cm, imperial
 * (lb) → in. Height is stored canonically in cm, like weight is in kg.
 */
export function heightUnitLabel(units) {
  return units === "lb" ? "in" : "cm";
}

/** Convert a stored cm value into the display height unit (cm or in). */
export function cmToDisplay(cm, units) {
  if (cm == null) return cm;
  return units === "lb" ? cm / CM_PER_IN : cm;
}

/** Convert a user-entered height (cm or in) back to cm for storage/API. */
export function displayToCm(value, units) {
  if (value == null) return value;
  return units === "lb" ? value * CM_PER_IN : value;
}

/** Format a stored cm value in the display unit. Inches get one decimal; cm none. */
export function formatHeight(cm, units) {
  if (cm == null) return "";
  return units === "lb" ? (cm / CM_PER_IN).toFixed(1) : String(Math.round(cm));
}

/** Convert a stored kg value into the display unit. */
export function fromKg(kg, units) {
  if (kg == null) return kg;
  return units === "lb" ? kg * LB_PER_KG : kg;
}

/** Convert a user-entered display value back to kg for storage/API. */
export function toKg(value, units) {
  if (value == null) return value;
  return units === "lb" ? value * KG_PER_LB : value;
}

/** Format a stored kg value in the display unit, fixed to `digits` decimals. */
export function formatWeight(kg, units, digits = 1) {
  if (kg == null) return "–";
  return fromKg(kg, units).toFixed(digits);
}
