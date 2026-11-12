/** Deeply frozen inputs only: a frozen object can still hold a mutable child. Checked once per object. */
const DEEP_FROZEN = new WeakMap<object, boolean>();
export function deepFrozen(value: object): boolean {
  if (!Object.isFrozen(value)) return false;
  let known = DEEP_FROZEN.get(value);
  if (known === undefined) {
    known = true;
    for (const key of Object.keys(value)) {
      const child = (value as Record<string, unknown>)[key];
      if (child !== null && typeof child === "object" && !deepFrozen(child)) { known = false; break; }
    }
    DEEP_FROZEN.set(value, known);
  }
  return known;
}
