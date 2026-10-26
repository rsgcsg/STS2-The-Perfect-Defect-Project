import { canonicalJson, isObject } from "./canonical.js";

/** Shared with the original C1 capture comparator: selectors only choose a unique
 * candidate; every field and one global bijection still establish equality. */
export interface PublicGraphPolicy {
  alias(value: string, key: string, path: string): { kind: string; id: string } | null;
  unordered?(path: string, parent: Record<string, unknown> | null): boolean;
  /** Keep the prefix ordered; only this suffix is a declared multiset. */
  unorderedSuffix?(path: string, parent: Record<string, unknown> | null): number | null;
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
  const unorderedStart = (path: string, parent: Record<string, unknown> | null): number | null =>
    policy.unordered?.(path, parent) ? 0 : policy.unorderedSuffix?.(path, parent) ?? null;
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
      const start = unorderedStart(normalized(path), null);
      return start === null ? children : [...children.slice(0, start),
        ...children.slice(start).sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b)))];
    }
    if (!isObject(value)) return value;
    return Object.fromEntries(Object.keys(value).sort().map(child => [child,
      selector(value[child], side, child, `${path}.${safeKey(child)}`, depth + 1, mapping)]));
  }
  function unordered(a: readonly unknown[], b: readonly unknown[], path: string, key = "", offset = 0) {
    if (a.length !== b.length) fail(path, "collection_multiplicity_changed");
    const groups = new Map<string, unknown[]>();
    const mapping = { forward: new Map(forward), reverse: new Map(reverse) };
    const signature = (value: unknown, side: "left" | "right") => canonicalJson(selector(
      policy.selectUnorderedMember?.(value, path) ?? value, side, key, `${path}[${offset}]`, 0, mapping));
    for (const value of b) { const sig = signature(value, "right"); const group = groups.get(sig) ?? []; group.push(value); groups.set(sig, group); }
    for (let index = 0; index < a.length; index++) {
      const group = groups.get(signature(a[index], "left"));
      if (!group?.length) fail(`${path}[${index + offset}]`, "collection_member_or_relation_changed");
      if (group!.length !== 1) fail(`${path}[${index + offset}]`, "ambiguous_unordered_alias_mapping", true);
      compare(a[index], group!.pop(), `${path}[${index + offset}]`, key);
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
      const start = unorderedStart(normalized(path), parent);
      if (a.length !== b.length) fail(path, start === null ? "sequence_length_changed" : "collection_multiplicity_changed");
      for (let i = 0; i < (start ?? a.length); i++) compare(a[i], b[i], `${path}[${i}]`, key, null, depth + 1);
      if (start !== null) unordered(a.slice(start), b.slice(start), path, key, start);
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
  /** Compare a complete graph when inventory duplicates need a joint mapping.
   * Each branch retains one global bijection and verifies every remaining field;
   * equal local signatures only select candidates, never establish equality.
   * The existing work bound covers all branches, including failed ones. */
  function compareWhole(a: unknown, b: unknown, path = "$"): void {
    type Task = { kind: "value"; a: unknown; b: unknown; path: string; key: string;
      parent: Record<string, unknown> | null; depth: number }
      | { kind: "members"; a: readonly unknown[]; b: readonly unknown[]; index: number; offset: number; path: string; key: string; depth: number };
    type State = { tasks: Task[]; forward: Map<string, string>; reverse: Map<string, string> };
    const valueTask = (a: unknown, b: unknown, path: string, key: string, parent: Record<string, unknown> | null, depth: number): Task =>
      ({ kind: "value", a, b, path, key, parent, depth });
    const pending: State[] = [{ tasks: [valueTask(a, b, path, "", null, 0)], forward: new Map(forward), reverse: new Map(reverse) }];
    let firstFailure: PublicGraphDifference | null = null;
    while (pending.length) {
      const state = pending.pop()!;
      try {
        while (state.tasks.length) {
          const task = state.tasks.pop()!;
          if (++comparedNodes > 500_000 || task.depth > 64) fail("$", "comparison_work_bound", true);
          if (task.kind === "members") {
            if (task.index === task.a.length) continue;
            const currentPath = `${task.path}[${task.index + task.offset}]`;
            const signature = (value: unknown, side: "left" | "right") => canonicalJson(selector(
              policy.selectUnorderedMember?.(value, task.path) ?? value, side, task.key,
              `${task.path}[${task.offset}]`, task.depth, state));
            const expected = signature(task.a[task.index], "left");
            const candidates = task.b.flatMap((item, index) => signature(item, "right") === expected ? [index] : []);
            if (candidates.length === 0) fail(currentPath, "collection_member_or_relation_changed");
            const tasksFor = (index: number): Task[] => [...state.tasks,
              { ...task, index: task.index + 1, b: task.b.filter((_, i) => i !== index) },
              valueTask(task.a[task.index], task.b[index], currentPath, task.key, null, task.depth + 1)];
            // DFS retains alternatives until the entire graph has been checked.
            for (const index of candidates.slice(1).reverse()) pending.push({ tasks: tasksFor(index),
              forward: new Map(state.forward), reverse: new Map(state.reverse) });
            state.tasks = tasksFor(candidates[0]!);
            continue;
          }
          const { a: leftValue, b: rightValue, path: currentPath, key, parent, depth } = task;
          if (typeof leftValue === "string" && typeof rightValue === "string") {
            const left = policy.alias(leftValue, key, currentPath), right = policy.alias(rightValue, key, currentPath);
            if (left && right && left.kind === right.kind) {
              const ka = `${left.kind}:${left.id}`, kb = `${right.kind}:${right.id}`;
              if (state.forward.has(ka) && state.forward.get(ka) !== right.id
                  || state.reverse.has(kb) && state.reverse.get(kb) !== left.id)
                fail(currentPath, "opaque_alias_equality_or_bijection_changed");
              state.forward.set(ka, right.id); state.reverse.set(kb, left.id); continue;
            }
            if (left || right) fail(currentPath, "alias_domain_changed");
          }
          if (Array.isArray(leftValue) && Array.isArray(rightValue)) {
            const start = unorderedStart(normalized(currentPath), parent);
            if (leftValue.length !== rightValue.length) fail(currentPath,
              start === null ? "sequence_length_changed" : "collection_multiplicity_changed");
            // The fixed prefix and all other ordered public relationships bind
            // before branching on the multiset suffix. Every branch retains them.
            if (start !== null) state.tasks.unshift({ kind: "members",
              a: leftValue.slice(start), b: rightValue.slice(start), index: 0, offset: start, path: currentPath, key, depth });
            for (let i = (start ?? leftValue.length) - 1; i >= 0; i--)
              state.tasks.push(valueTask(leftValue[i], rightValue[i], `${currentPath}[${i}]`, key, null, depth + 1));
            continue;
          }
          if (isObject(leftValue) && isObject(rightValue)) {
            const ak = Object.keys(leftValue).sort(), bk = Object.keys(rightValue).sort();
            if (canonicalJson(ak) !== canonicalJson(bk)) fail(currentPath, "field_presence_changed");
            for (const child of ak.reverse()) state.tasks.push(valueTask(leftValue[child], rightValue[child],
              `${currentPath}.${safeKey(child)}`, child, leftValue, depth + 1));
            continue;
          }
          if (leftValue !== rightValue) fail(currentPath, "semantic_value_changed");
        }
        forward.clear(); reverse.clear();
        for (const [key, value] of state.forward) forward.set(key, value);
        for (const [key, value] of state.reverse) reverse.set(key, value);
        return;
      } catch (error) {
        if (!(error instanceof PublicGraphDifference) || error.incomparable) throw error;
        firstFailure ??= error;
      }
    }
    throw firstFailure!;
  }
  /** Evidence consumers may inspect the successful whole-graph mapping. This
   * neither selects a native action nor creates a second identity relation. */
  const bindings = () => [...forward].map(([key, right]) => {
    const colon = key.indexOf(":");
    return { kind: key.slice(0, colon), left: key.slice(colon + 1), right };
  });
  return { compare, unordered, compareWhole, bindings };
}
