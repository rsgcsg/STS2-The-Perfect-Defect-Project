import { canonicalJson, isObject } from "./canonical.js";

/** Shared with the original C1 capture comparator: selectors only choose a unique
 * candidate; every field and one global bijection still establish equality. */
export interface PublicGraphPolicy {
  alias(value: string, key: string, path: string): { kind: string; id: string } | null;
  unordered?(path: string, parent: Record<string, unknown> | null): boolean;
  selectUnorderedMember?(value: unknown, path: string): unknown;
}
export class PublicGraphDifference extends Error {
  constructor(readonly path: string, readonly reason: string, readonly incomparable = false) { super(reason); }
}
export function createPublicGraphComparator(policy: PublicGraphPolicy) {
  const forward = new Map<string, string>(); const reverse = new Map<string, string>();
  const fail = (path: string, reason: string, incomparable = false): never => {
    throw new PublicGraphDifference(path.slice(0, 240), reason, incomparable);
  };
  const safeKey = (key: string) => /^[A-Za-z0-9_:-]{1,80}$/u.test(key) ? key : "<field>";
  const normalized = (path: string) => path.replace(/\[\d+\]/gu, "[]");
  function bind(kind: string, a: string, b: string, path: string) {
    const ka = `${kind}:${a}`; const kb = `${kind}:${b}`;
    if ((forward.has(ka) && forward.get(ka) !== b) || (reverse.has(kb) && reverse.get(kb) !== a))
      fail(path, "opaque_alias_equality_or_bijection_changed");
    forward.set(ka, b); reverse.set(kb, a);
  }
  function selector(value: unknown, side: "left" | "right", key: string, path: string, depth: number,
    mapping: { forward: Map<string, string>; reverse: Map<string, string> }): unknown {
    if (++comparedNodes > 500_000 || depth > 64) fail("$", "comparison_work_bound", true);
    if (typeof value === "string") {
      const alias = policy.alias(value, key, path);
      if (alias) return ["alias", alias.kind, side === "left"
        ? (mapping.forward.has(`${alias.kind}:${alias.id}`) ? alias.id : "<unbound>")
        : mapping.reverse.get(`${alias.kind}:${alias.id}`) ?? "<unbound>"];
    }
    if (Array.isArray(value)) {
      const children = value.map((child, index) => selector(child, side, key, `${path}[${index}]`, depth + 1, mapping));
      return policy.unordered?.(normalized(path), null)
        ? children.sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b))) : children;
    }
    if (!isObject(value)) return value;
    return Object.fromEntries(Object.keys(value).sort().map(child => [child,
      selector(value[child], side, child, `${path}.${safeKey(child)}`, depth + 1, mapping)]));
  }
  function unordered(a: readonly unknown[], b: readonly unknown[], path: string, key = "") {
    if (a.length !== b.length) fail(path, "collection_multiplicity_changed");
    const groups = new Map<string, unknown[]>();
    const mapping = { forward: new Map(forward), reverse: new Map(reverse) };
    const signature = (value: unknown, side: "left" | "right") => canonicalJson(selector(
      policy.selectUnorderedMember?.(value, path) ?? value, side, key, `${path}[0]`, 0, mapping));
    for (const value of b) { const sig = signature(value, "right"); const group = groups.get(sig) ?? []; group.push(value); groups.set(sig, group); }
    for (let index = 0; index < a.length; index++) {
      const group = groups.get(signature(a[index], "left"));
      if (!group?.length) fail(`${path}[${index}]`, "collection_member_or_relation_changed");
      if (group!.length !== 1) fail(`${path}[${index}]`, "ambiguous_unordered_alias_mapping", true);
      compare(a[index], group!.pop(), `${path}[${index}]`, key);
    }
  }
  let comparedNodes = 0;
  function compare(a: unknown, b: unknown, path = "$", key = "", parent: Record<string, unknown> | null = null, depth = 0): void {
    if (++comparedNodes > 500_000 || depth > 64) fail("$", "comparison_work_bound", true);
    if (typeof a === "string" && typeof b === "string") {
      const left = policy.alias(a, key, path); const right = policy.alias(b, key, path);
      if (left && right && left.kind === right.kind) return bind(left.kind, left.id, right.id, path);
      if (left || right) fail(path, "alias_domain_changed");
    }
    if (Array.isArray(a) && Array.isArray(b)) {
      if (policy.unordered?.(normalized(path), parent)) return unordered(a, b, path, key);
      if (a.length !== b.length) fail(path, "sequence_length_changed");
      for (let i = 0; i < a.length; i++) compare(a[i], b[i], `${path}[${i}]`, key, null, depth + 1);
      return;
    }
    if (isObject(a) && isObject(b)) {
      const ak = Object.keys(a).sort(); const bk = Object.keys(b).sort();
      if (canonicalJson(ak) !== canonicalJson(bk)) fail(path, "field_presence_changed");
      for (const child of ak) compare(a[child], b[child], `${path}.${safeKey(child)}`, child, a, depth + 1);
      return;
    }
    if (a !== b) fail(path, "semantic_value_changed");
  }
  return { compare, unordered };
}
