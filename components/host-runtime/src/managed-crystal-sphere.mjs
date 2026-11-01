// Managed presentation only. STS2 owns placement, divinations, reveal and rewards.
// The approved r1.9 public fact is an item's rectangle once any occupied cell is clear.
const text = value => typeof value === "string" && value.trim().length > 0;
const integer = value => Number.isInteger(value);

export function completeCrystalSphere(state) {
  if (!["run_ref", "room_ref", "event_ref", "sphere_ref"].every(key => text(state[key]))
      || state.board_width !== 11 || state.board_height !== 11
      || !integer(state.remaining_divinations) || state.remaining_divinations <= 0
      || !["Small", "Big"].includes(state.selected_tool)
      || !Array.isArray(state.cells) || state.cells.length !== 121
      || !Array.isArray(state.visible_items)) return false;
  const cells = new Map(), refs = new Set();
  for (const cell of state.cells) {
    if (!cell || !text(cell.native_ref) || refs.has(cell.native_ref)
        || !integer(cell.x) || !integer(cell.y) || cell.x < 0 || cell.x >= 11 || cell.y < 0 || cell.y >= 11
        || cells.has(`${cell.x},${cell.y}`) || typeof cell.cleared !== "boolean"
        || (cell.cleared ? typeof cell.empty !== "boolean" : Object.hasOwn(cell, "empty"))) return false;
    cells.set(`${cell.x},${cell.y}`, cell); refs.add(cell.native_ref);
  }
  const occupied = new Set();
  for (const item of state.visible_items) {
    if (!item || !text(item.native_ref) || refs.has(item.native_ref) || !text(item.kind) || !text(item.label)
        || ![item.x, item.y, item.width, item.height].every(integer) || item.x < 0 || item.y < 0
        || item.width <= 0 || item.height <= 0 || item.x + item.width > 11 || item.y + item.height > 11
        || typeof item.fully_revealed !== "boolean" || !Array.isArray(item.cleared_cells)) return false;
    refs.add(item.native_ref);
    const cleared = new Set();
    for (let x = item.x; x < item.x + item.width; x++) for (let y = item.y; y < item.y + item.height; y++) {
      const cell = cells.get(`${x},${y}`);
      if (cell.cleared) {
        const key = `${x},${y}`;
        if (cell.empty || occupied.has(key)) return false;
        cleared.add(key); occupied.add(key);
      }
    }
    if (cleared.size === 0 || item.cleared_cells.length !== cleared.size
        || new Set(item.cleared_cells.map(cell => `${cell?.x},${cell?.y}`)).size !== cleared.size
        || item.cleared_cells.some(cell => !cleared.has(`${cell?.x},${cell?.y}`))
        || item.fully_revealed !== (cleared.size === item.width * item.height)) return false;
  }
  return state.cells.every(cell => !cell.cleared || cell.empty || occupied.has(`${cell.x},${cell.y}`));
}

export function crystalSphereSurface(state, ctx) {
  const complete = completeCrystalSphere(state);
  const surface = { kind: "crystal_sphere", requires_profile_revision: "r1.9",
    width: 11, height: 11, remaining_divinations: state.remaining_divinations,
    selected_tool: state.selected_tool, sphere_entity_id: ctx.entityId("sphere", state.sphere_ref),
    event_entity_id: ctx.entityId("event", state.event_ref), room_entity_id: ctx.entityId("room", state.room_ref),
    cells: [], visible_items: [], tools: [] };
  if (complete) {
    const tools = ["Small", "Big"].map(tool => ctx.referent({ role: "sphere_tool",
      id: ctx.entityId("sphere_tool", `${state.sphere_ref}:${tool}`), label: `${tool} tool`,
      properties: { tool, label: `${tool} tool` } }));
    surface.tools = tools.map(tool => tool.properties);
    surface.visible_items = state.visible_items.map(item => ctx.referent({ role: "sphere_item",
      id: ctx.entityId("sphere_item", item.native_ref), label: item.label,
      properties: { kind: item.kind, label: item.label, x: item.x, y: item.y, width: item.width, height: item.height,
        cleared_cells: item.cleared_cells.map(({ x, y }) => ({ x, y })), fully_revealed: item.fully_revealed } }).properties);
    surface.cells = [...state.cells].sort((a, b) => a.y - b.y || a.x - b.x).map(cell => {
      const subject = ctx.referent({ role: "sphere_cell", id: ctx.entityId("sphere_cell", cell.native_ref),
        label: `Cell ${cell.x},${cell.y}`, properties: { x: cell.x, y: cell.y, cleared: cell.cleared,
          ...(cell.cleared ? { empty: cell.empty } : {}) } });
      for (const target of tools) {
        const tool = target.properties.tool, radius = tool === "Big" ? 1 : 0;
        const masked = state.cells.filter(other => !other.cleared
          && Math.abs(other.x - cell.x) <= radius && Math.abs(other.y - cell.y) <= radius).length;
        ctx.action({ verb: "activate", subject, arguments: [{ role: "target", referent: target }],
          label: `${tool} tool at ${cell.x},${cell.y}: ${masked} covered cells`,
          raw: { cmd: "action", action: "crystal_sphere_click", args: { run_ref: state.run_ref,
            room_ref: state.room_ref, event_ref: state.event_ref, sphere_ref: state.sphere_ref,
            cell_ref: cell.native_ref, tool } } });
      }
      return subject.properties;
    });
  }
  return { kind: "crystal_sphere", stage: "ready", prompt: "Choose a tool and cell", surface,
    context: { kind: "event", event_id: "CRYSTAL_SPHERE" }, complete,
    missing: complete ? [] : ["current_crystal_sphere_public_facts"] };
}
