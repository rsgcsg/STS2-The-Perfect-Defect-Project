using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using STS2Connector.Authority;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Native-logical-only composition of the complete ordinary reward
/// controls. The legacy clickability-based settling profile is unchanged.</summary>
internal static class NativeLogicalCardRewardCapture
{
    internal static TextMenuFrame? TryCapture(SnapshotBuildResult source,
        PlayerEnvironmentSnapshot page, IReadOnlyList<TextMenuLeaf> information,
        NativeEntityRegistry entities, Func<PlayerEnvironmentNativeBinding, NativeInputResult> executeLegacy)
    {
        if (NOverlayStack.Instance?.Peek() is not NCardRewardSelectionScreen owner) return null;
        string key = "native_card_reward:" + entities.GetId(owner, "screen");
        if (!EnvironmentIdentityRuntime.ExecutionAvailable(source.HostObservation.Game))
            return NativeLogicalCapturePolicy.Partial(page, key, "native_card_reward_execution_identity_unavailable");
        if (source.HostObservation.Surface is not CardRewardSelectionSurface surface
            || surface.ScreenEntityId != entities.GetId(owner, "screen")
            || NativeCardRewardInformation.Capture(owner, entities) is not { } state)
            return NativeLogicalCapturePolicy.Partial(page, key, "native_card_reward_information_binding_missing");
        string[] cardIds = state.Cards.Select(value => entities.GetId(value.Model, "card")).ToArray();
        string[] alternativeIds = state.Alternatives.Select(value => entities.GetId(value.Model, "card_reward_alternative")).ToArray();
        if (!ExactIds(cardIds, surface.Cards.Select(value => value.EntityId))
            || !ExactIds(alternativeIds, surface.Alternatives.Select(value => value.EntityId)))
            return NativeLogicalCapturePolicy.Partial(page, key, "native_card_reward_public_roster_mismatch");

        // Selection/alternatives retain the existing owner descriptors and
        // delivery path. Only source-proved input applicability is refreshed.
        var current = surface with
        {
            SelectableCardEntityIds = state.Cards.Where(value => value.FocusEnabled && value.Clickable)
                .Select(value => entities.GetId(value.Model, "card")).ToArray(),
            Alternatives = surface.Alternatives.Select(value =>
            {
                var control = state.Alternatives.Single(item => entities.GetId(item.Model, "card_reward_alternative") == value.EntityId);
                return value with { Enabled = control.Enabled, Label = control.Label };
            }).ToArray()
        };
        var bindings = new PublicInformationBindings(page);
        var potions = new List<TextMenuLeaf>();
        foreach (TextMenuLeaf opener in NativeTextMenuPotions.Openers(entities))
        {
            if (opener.SubjectReferentId is not { } id || bindings.Potion(id) == null) continue;
            potions.Add(opener);
        }
        TextMenuFrame frame = Compose(source.HostObservation, current, bindings.Page, information.Concat(potions).ToArray(),
            () => state.Current(entities), executeLegacy,
            key + ":" + entities.GetId(state.Completion, "native_reward_request"));
        return NativeTextMenuInformation.AppendCardRewardInformation(frame, state, entities);
    }

    private static bool ExactIds(IEnumerable<string> expected, IEnumerable<string> actual)
    {
        string[] left = expected.ToArray(), right = actual.ToArray();
        return left.Length == right.Length && left.Distinct(StringComparer.Ordinal).Count() == left.Length
            && right.Distinct(StringComparer.Ordinal).Count() == right.Length
            && left.ToHashSet(StringComparer.Ordinal).SetEquals(right);
    }

    // Production composition also has a fixture seam independent of Godot. It
    // consumes the exact owner-proved surface, never a caller-created native input.
    internal static TextMenuFrame Compose(LiveObservation observation, CardRewardSelectionSurface current,
        PlayerEnvironmentSnapshot page, IReadOnlyList<TextMenuLeaf> information,
        Func<bool> exactSource, Func<PlayerEnvironmentNativeBinding, NativeInputResult> executeLegacy, string key)
    {
        IReadOnlyList<NativeUiBoundAction> native = NativeUiActionRuntime.DescribeCardRewardCommands(current)
            .Select(value => NativeUiActionRuntime.BindActionToCurrentObservation(observation, value)!)
            .ToArray();
        BoundActionProjectionResult projection = PlayerEnvironmentService.ProjectBoundActions(native,
            page.Interaction.InteractionId, page.Referents.ToDictionary(value => value.ReferentId, StringComparer.Ordinal),
            NativeLogicalCapture.MaximumActions);
        if (projection.Projection.Status != "complete")
            return NativeLogicalCapturePolicy.Partial(page, key, "native_card_reward_current_catalog_incomplete");
        var leaves = information.ToList();
        foreach (PlayerEnvironmentBoundAction action in NativeTextMenuFrameBuilder.OrderLegacyTextActions(current, projection.Projection.Actions))
        {
            PlayerEnvironmentNativeBinding binding = projection.Bindings[action.BoundActionId];
            leaves.Add(new(action.BoundActionId, "root", action.Verb, action.Label,
                action.SubjectReferentId, action.Arguments, () => executeLegacy(binding)));
        }
        return new TextMenuFrame(page with
        {
            Status = page.Completeness.Status == "complete" && page.Completeness.Missing.Count == 0
                ? leaves.Count > 0 ? "interactive" : "settling" : NativeLogicalCapturePolicy.FailureStatus(page.Status)
        }, key, leaves.Select(leaf => leaf with
        {
            Dispatch = () => exactSource() ? leaf.Dispatch()
                : NativeInputResult.Rejected("native_card_reward_occurrence_changed", "The exact pending reward request changed.")
        }).ToArray());
    }
}
