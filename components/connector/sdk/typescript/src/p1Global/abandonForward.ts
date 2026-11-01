import { canonicalJson, isObject } from "./canonical.js";
import { rewardWorldFacts } from "./rewardForward.js";
import type { P1Basis } from "./reducer.js";

const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

/** Public, inspectable warning and exact current Trial option. No default answer. */
export function completeAbandonIntention(value: unknown): value is Record<string, any> {
  return isObject(value) && value.kind === "abandon_run_confirmation"
    && ["event_entity_id", "room_entity_id", "parent_option_entity_id", "title", "body"].every(key => text(value[key]));
}

export function completeAbandonConfirmation(value: unknown): value is Record<string, any> {
  if (!completeAbandonIntention(value) || value.kind !== "abandon_run_confirmation"
      || value.stage !== "awaiting_confirmation" || !text(value.modal_entity_id)
      || !Array.isArray(value.controls) || value.controls.length !== 2) return false;
  const controls = value.controls;
  return new Set(controls.map((control: any) => control?.entity_id)).size === 2
    && controls.every((control: any) => isObject(control) && text(control.entity_id)
      && text(control.label) && typeof control.enabled === "boolean"
      && (control.semantic_id === "yes" && control.operation === "confirm_abandon"
        || control.semantic_id === "no" && control.operation === "cancel_abandon"))
    && controls.filter((control: any) => control.semantic_id === "yes").length === 1
    && controls.filter((control: any) => control.semantic_id === "no").length === 1;
}

/** An advertised parent capability must name its one real current option/input. */
export function boundAbandonIntention(surface: Record<string, any>, actions: readonly any[]): boolean {
  const value = surface.abandon_confirmation;
  if (!completeAbandonIntention(value) || !Array.isArray(surface.options)) return false;
  const options = surface.options.filter((option: any) => option?.entity_id === value.parent_option_entity_id);
  if (options.length !== 1) return false;
  const option = options[0];
  const available = option.is_enabled === true && option.is_locked === false;
  const bound = actions.filter(action => action.subject_referent_id === value.parent_option_entity_id);
  return available ? bound.length === 1 && bound[0].verb === "activate" && bound[0].arguments.length === 0
    : bound.length === 0;
}

export interface AbandonIntention {
  readonly event: string; readonly room: string; readonly option: string;
  readonly warning: string; readonly world: string;
}

export function abandonIntention(value: unknown, subject: string | null, basis: P1Basis): AbandonIntention | null {
  if (!completeAbandonIntention(value) || value.parent_option_entity_id !== subject) return null;
  return { event: value.event_entity_id, room: value.room_entity_id, option: value.parent_option_entity_id,
    warning: canonicalJson({ title: value.title, body: value.body }), world: rewardWorldFacts(basis) };
}

export function matchesAbandonIntention(plan: AbandonIntention, basis: P1Basis): boolean {
  const surface = (basis.snapshot as any)?.interaction?.content?.surface;
  return completeAbandonConfirmation(surface) && surface.event_entity_id === plan.event
    && surface.room_entity_id === plan.room && surface.parent_option_entity_id === plan.option
    && canonicalJson({ title: surface.title, body: surface.body }) === plan.warning
    && rewardWorldFacts(basis) === plan.world;
}
