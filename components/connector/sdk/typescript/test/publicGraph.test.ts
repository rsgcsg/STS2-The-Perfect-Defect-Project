import { expect, it } from "vitest";
import { createPublicGraphComparator, PublicGraphDifference } from "../src/p1Global/publicGraph.js";
const compare = (left: unknown, right: unknown) => createPublicGraphComparator({
  alias: (value, key) => key === "entity_id" ? { kind: "entity", id: value } : null,
  unordered: path => path === "$.a_inventory"
}).compareWhole(left, right);
it("binds ordered instance relationships before searching a symmetric inventory", () => {
  const left = { a_inventory: Array.from({ length: 12 }, (_, index) => ({ entity_id: `left${index}`, text: "same card" })),
    z_ordered: Array.from({ length: 12 }, (_, index) => ({ entity_id: `left${index}` })) };
  const right = { a_inventory: Array.from({ length: 12 }, (_, index) => ({ entity_id: `right${11 - index}`, text: "same card" })),
    z_ordered: Array.from({ length: 12 }, (_, index) => ({ entity_id: `right${index}` })) };
  expect(() => compare(left, right)).not.toThrow();
  right.a_inventory[0]!.text = "changed public text";
  expect(() => compare(left, right)).toThrow(PublicGraphDifference);
  right.a_inventory[0]!.text = "same card";
  right.z_ordered[1]!.entity_id = right.z_ordered[0]!.entity_id;
  expect(() => compare(left, right)).toThrow(PublicGraphDifference);
});
