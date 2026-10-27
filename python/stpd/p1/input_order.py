"""Research projection of the SDK's public ordering domains, without changing P1.

No aliases, seeds or private game state participate in ordering. A color-refined
public reference graph breaks content ties when public references distinguish them.
Unresolved automorphisms remain ties; exact Frame equality is measured, not assumed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .data import digest, json_bytes
from .input import Frame


def unordered_domains(observation: dict[str, Any]) -> set[str]:
    # Mirrors p1Global/publicOrdering.ts. Selected/effect/display arrays are ordered.
    paths = {"$.catalog", "$.deck.content.cards", "$.map.content.nodes"}
    if observation.get("scene", {}).get("native_kind") == "combat_turn":
        paths.update({"$.scene.content.surface.playable_cards[].target_entity_ids",
                      "$.scene.content.surface.usable_potions[].target_entity_ids"})
    elif observation.get("scene", {}).get("native_kind") == "potion_popup":
        paths.add("$.scene.content.surface.use_target_entity_ids")
    # These are qualification domains (SDK r1.6 publicMembershipFields), not
    # selected/effect order. The new InputSpec deliberately forgets this order;
    # it does not relabel the source r1.5 observation or its comparison contract.
    native = observation.get("scene", {}).get("native_kind")
    selectors = {"card_selection", "combat_hand_card_selection", "native_combat_pile_selection",
                 "native_deck_card_selection", "native_generated_card_choice",
                 "native_simple_card_selection", "deck_transform_selection",
                 "deck_card_selection", "deck_upgrade_selection", "deck_enchant_selection"}
    fields = []
    if native in selectors:
        fields = ["selectable_card_entity_ids", "deselectable_card_entity_ids"]
    elif native == "card_reward_selection":
        fields = ["selectable_card_entity_ids"]
    elif native == "card_bundle_selection":
        fields = ["selectable_bundle_entity_ids"]
    elif native in {"treasure_room", "treasure_chest", "treasure_relic_selection",
                    "treasure_completion", "relic_choice", "native_boss_relic_selection"}:
        fields = ["selectable_relic_entity_ids"]
    paths.update("$.scene.content.surface." + field for field in fields)
    reads = observation.get("extra_reads", [])
    if reads and all(read.get("kind") == "surface_card" for read in reads):
        paths.add("$.extra_reads")
    return paths


def order_frame(
    frame: Frame, observation: dict[str, Any], *, sorted_positions: bool,
    alias_views: dict[str, list[int]] | None = None,
    reference_links: list[tuple[int, str]] | None = None,
    tree_refs: list[list[int]] | None = None,
    sorting_bindings: dict[int, set[str]] | None = None,
) -> Frame:
    paths = unordered_domains(observation)
    zones = observation.get("piles", {}).get("content") or {}
    unordered_zones = {i for i, zone in enumerate(zones.get("zones", []))
                       if zone.get("ordering") == "unordered_multiset"}
    nodes = [replace(e, refs=list(e.refs)) for e in frame.entities]
    sets: list[list[int]] = []
    for index, node in enumerate(nodes):
        members = [r for r in node.refs if nodes[r].kind == node.kind + "[]"]
        is_set = node.kind in paths
        reads = observation.get("extra_reads", [])
        if (node.kind == "$.extra_reads" and reads and reads[0].get("kind") == "shop_catalog"
                and all(read.get("kind") == "surface_card" for read in reads[1:])):
            members = members[1:]
            is_set = True
        if node.kind == "$.piles.content.zones[].cards":
            # The parent zone carries the original zone position. Its order is
            # public (hand/discard/exhaust are not silently treated as draw).
            owners = [p for p in nodes if p.kind == "$.piles.content.zones[]"
                      and index in p.refs]
            is_set = bool(owners and owners[0].position in unordered_zones)
        if is_set and members:
            sets.append(members)
            for member in members:
                nodes[member] = replace(nodes[member], position=0)

    if not sorted_positions:
        # M-std has no global scene position encoding. Shared member positions,
        # symmetric public references and pooled roles already give permutation
        # equivariance; sorting/color-refining every v2 node would add CPU work
        # without changing that guarantee.
        return Frame(nodes, frame.choices, frame.roles)

    def colors() -> list[str]:
        bindings = sorting_bindings or {}
        base = [(e.kind, e.position,
                 [(key, "<BINDING>" if key in bindings.get(i, ()) else value)
                  for key, value in e.identities],
                 [(key, "<BINDING>" if key in bindings.get(i, ()) else value)
                  for key, value in e.text], e.numeric) for i, e in enumerate(nodes)]
        labels = [digest(json_bytes(b)) for b in base]
        # Partition refinement stops when no class splits. Both outgoing and
        # incoming references distinguish public roles in repeated bodies.
        incoming: list[list[int]] = [[] for _ in nodes]
        for i, e in enumerate(nodes):
            for r in e.refs:
                incoming[r].append(i)
        for _ in range(len(nodes)):
            refined = [digest(json_bytes((labels[i], sorted(labels[r] for r in e.refs),
                                         sorted(labels[r] for r in incoming[i]))))
                       for i, e in enumerate(nodes)]
            if len(set(refined)) == len(set(labels)):
                return refined
            labels = refined
        return labels

    labels = colors()
    def selected_view(role: int | tuple[int, ...]) -> int:
        if isinstance(role, int):
            return role
        return min(role, key=lambda row: (nodes[row].kind, labels[row]))
    selected_roles = [(selected_view(s), selected_view(t)) for s, t in frame.roles]
    if sorted_positions:
        for members in sets:
            # Equal public content receives an equal position. Instance spelling
            # cannot break a public-content tie.
            ranks = {label: rank for rank, label in enumerate(sorted({labels[r] for r in members}))}
            for r in members:
                nodes[r] = replace(nodes[r], position=ranks[labels[r]])
        if alias_views is not None:
            assert tree_refs is not None and reference_links is not None
            anchors = {alias: selected_view(tuple(members))
                       for alias, members in alias_views.items()}
            # 438 retains its trained star/reference graph and single-view role
            # readout. Only the public-content choice of its old anchor changes;
            # the symmetric graph above is a temporary sorting aid, not input.
            for i, refs in enumerate(tree_refs):
                nodes[i].refs = list(refs)
            for alias, members in alias_views.items():
                anchor = anchors[alias]
                for member in members:
                    if member != anchor:
                        nodes[member].refs.append(anchor)
                        nodes[anchor].refs.append(member)
            for row, alias in reference_links:
                nodes[row].refs.append(anchors[alias])
        labels = colors()
    order = sorted(range(len(nodes)), key=lambda i: (nodes[i].kind, labels[i]))
    inverse = {old: new for new, old in enumerate(order)}
    return Frame([replace(nodes[i], refs=sorted(inverse[r] for r in nodes[i].refs)) for i in order],
                 frame.choices, [(inverse[s] if s >= 0 else -1, inverse[t] if t >= 0 else -1)
                                 for s, t in selected_roles])
