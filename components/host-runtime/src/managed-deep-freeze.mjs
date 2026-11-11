/** Freezes every object and array reachable from value (already frozen subtrees are skipped).
 * Dependency-free, so projection-only modules can use it without loading the SDK. */
export function deepFreeze(value) {
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (item === null || typeof item !== "object" || Object.isFrozen(item)) continue;
    Object.freeze(item);
    for (const key of Object.keys(item)) pending.push(item[key]);
  }
  return value;
}
