/** M060, user approved 2026-10-28: the upgrade-view toggle is a UI
 * preference. Only its Boolean value is omitted in recorded Managed replay.
 * Original observations, upgrade previews and the general P1 comparator stay
 * unchanged. Missing/invalid values and every other field still compare. */
import { matchReferenceChoice } from '../components/policy-runtime/dist/p1-global/trajectory.js';

export function recordedPreferencePair(left, actual) {
  const a = left.scene.content?.surface, b = actual.scene.content?.surface;
  if (left.scene.native_kind !== 'deck_upgrade_selection'
      || actual.scene.native_kind !== left.scene.native_kind
      || typeof a?.showing_upgrade_previews !== 'boolean'
      || typeof b?.showing_upgrade_previews !== 'boolean'
      || a.showing_upgrade_previews === b.showing_upgrade_previews)
    return { left, right: actual, exception: null };
  const normalizedLeft = structuredClone(left), normalizedRight = structuredClone(actual);
  normalizedLeft.scene.content.surface.showing_upgrade_previews = false;
  normalizedRight.scene.content.surface.showing_upgrade_previews = false;
  return { left: normalizedLeft, right: normalizedRight, exception: { id: 'M060',
      path: '$.observation.scene.content.surface.showing_upgrade_previews',
      reference: a.showing_upgrade_previews, actual: b.showing_upgrade_previews,
      scope: 'recorded_managed_replay_only' } };
}

export function matchRecordedChoice(reference, actual) {
  const pair = recordedPreferencePair(reference.observation, actual);
  const match = matchReferenceChoice({ ...reference, observation: pair.left }, pair.right);
  return pair.exception ? { ...match, comparison_exception: { ...pair.exception,
    original_comparison: matchReferenceChoice(reference, actual) } } : match;
}
