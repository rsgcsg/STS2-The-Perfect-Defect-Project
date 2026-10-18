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

it("keeps a fixed catalog prefix while matching only its detail suffix", () => {
  const comparator = () => createPublicGraphComparator({
    alias: (value, key) => key === "entity_id" || key === "owner" ? { kind: "entity", id: value } : null,
    unorderedSuffix: path => path === "$.reads" ? 1 : null
  });
  const left = { reads: [{ kind: "catalog", entries: [{ entity_id: "a" }, { entity_id: "b" }] },
    { kind: "detail", entity_id: "a", owner: "a", text: "same" },
    { kind: "detail", entity_id: "b", owner: "b", text: "same" }] };
  const right = { reads: [{ kind: "catalog", entries: [{ entity_id: "x" }, { entity_id: "y" }] },
    { kind: "detail", entity_id: "y", owner: "y", text: "same" },
    { kind: "detail", entity_id: "x", owner: "x", text: "same" }] };
  expect(() => comparator().compareWhole(left, right)).not.toThrow();
  expect(() => comparator().compare(left, right)).not.toThrow();
  const wrongOwner = structuredClone(right); wrongOwner.reads[1]!.owner = "x";
  expect(() => comparator().compareWhole(left, wrongOwner)).toThrow(PublicGraphDifference);
  const movedCatalog = structuredClone(right); movedCatalog.reads.reverse();
  expect(() => comparator().compareWhole(left, movedCatalog)).toThrow(PublicGraphDifference);
  const duplicate = structuredClone(right); duplicate.reads[2] = structuredClone(duplicate.reads[1]!);
  expect(() => comparator().compareWhole(left, duplicate)).toThrow(PublicGraphDifference);
});
