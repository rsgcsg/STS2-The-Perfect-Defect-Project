import { publicFieldPath } from "./publicIdentity.js";

/** Interned public paths for the reducer's tree walks. `path` is exactly the string the
 * walks have always built (publicFieldPath for keys, `[index]` for items); interning only
 * lets policy lookups key on a shared node instead of hashing a fresh string per leaf. */
export class PublicPath {
  private fields: Map<string, PublicPath> | undefined;
  private items: PublicPath[] | undefined;
  /** @internal The policy relation memo whose relationAt / subtreeAt answer is kept here (publicIdentity). */
  relationOwner: object | undefined;
  relation: "declared" | "reference" | null = null;
  subtreeOwner: object | undefined;
  subtree = true;
  constructor(readonly path: string) {}

  field(key: string): PublicPath {
    const fields = this.fields ??= new Map();
    let child = fields.get(key);
    if (child === undefined) { child = new PublicPath(publicFieldPath(this.path, key)); fields.set(key, child); interned++; }
    return child;
  }

  item(index: number): PublicPath {
    const items = this.items ??= [];
    let child = items[index];
    if (child === undefined) { child = items[index] = new PublicPath(`${this.path}[${index}]`); interned++; }
    return child;
  }
}

const INTERN_LIMIT = 1 << 18;
const roots = new Map<string, PublicPath>();
let interned = 0;

export function publicPath(path: string): PublicPath {
  if (interned >= INTERN_LIMIT) { roots.clear(); interned = 0; }
  let root = roots.get(path);
  if (root === undefined) { root = new PublicPath(path); roots.set(path, root); interned++; }
  return root;
}
