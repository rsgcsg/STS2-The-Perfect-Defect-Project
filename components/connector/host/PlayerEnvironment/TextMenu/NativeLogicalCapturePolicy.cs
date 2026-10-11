using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal enum NativeLogicalProjectionReplacement { Grid, ChoicePeek, BundlePeek, InformationPage, HeldCard, PotionTarget }

/// <summary>The exact native adapter replaces only its old holder/window facts.
/// Shared/persistent/consistency gaps are not evidence supplied by that adapter.</summary>
internal static class NativeLogicalCapturePolicy
{
    private static readonly IReadOnlySet<string> GridGaps = new HashSet<string>(StringComparer.Ordinal)
    {
        "visible_cards", "current_controls", "selected_cards", "legal_actions",
        "native_logical_public_list_content_incomplete", "native_logical_public_list_action_bindings_incomplete"
    };
    private static readonly IReadOnlySet<string> ChoicePeekGaps = new HashSet<string>(StringComparer.Ordinal)
        { "visible_cards", "current_controls", "current_ui_controls" };
    private static readonly IReadOnlySet<string> BundlePeekGaps = new HashSet<string>(StringComparer.Ordinal)
        { "bundle_membership", "stage", "legal_actions" };

    private static readonly IReadOnlySet<string> InformationGaps = new HashSet<string>(StringComparer.Ordinal)
        { "current_native_information_projection_incomplete" };
    private static readonly IReadOnlySet<string> HeldCardGaps = new HashSet<string>(StringComparer.Ordinal)
        { "current_native_card_display" };
    private static readonly IReadOnlySet<string> PotionTargetGaps = new HashSet<string>(StringComparer.Ordinal)
        { "current_native_potion_target_projection_incomplete" };

    // A qualified binder may supersede only a projection of this same owner,
    // including the old adapter's explicit failure for this exact source type.
    // An unrelated surface (for example the persistent-HUD failure shell) is
    // not proof that its omissions were supplied by the new selector adapter.
    internal static bool DisplacesLegacyProjection(ILiveSurface source, string expectedSourceType,
        string expectedKind, string ownerId)
    {
        if (source is UnsupportedSurface unsupported) return unsupported.SourceType == expectedSourceType;
        if (source.Kind != expectedKind) return false;
        string? sourceOwnerId = source switch
        {
            NativeDeckCardSelectionSurface value => value.ScreenEntityId,
            NativeDeckUpgradeSelectionSurface value => value.ScreenEntityId,
            DeckTransformSelectionSurface value => value.ScreenEntityId,
            DeckEnchantSelectionSurface value => value.ScreenEntityId,
            NativeSimpleCardSelectionSurface value => value.ScreenEntityId,
            NativeCombatPileSelectionSurface value => value.ScreenEntityId,
            NativeGeneratedCardChoiceSurface value => value.ScreenEntityId,
            CardBundleSelectionSurface value => value.ScreenEntityId,
            _ => null
        };
        return sourceOwnerId == ownerId;
    }

    internal static PlayerEnvironmentCompleteness Replace(PlayerEnvironmentCompleteness inherited,
        NativeLogicalProjectionReplacement scope, bool provenReplacement, IEnumerable<string> ownMissing,
        string semantics, string discovery)
    {
        IReadOnlySet<string> displaced = scope switch
        {
            NativeLogicalProjectionReplacement.Grid => GridGaps,
            NativeLogicalProjectionReplacement.ChoicePeek => ChoicePeekGaps,
            NativeLogicalProjectionReplacement.BundlePeek => BundlePeekGaps,
            NativeLogicalProjectionReplacement.InformationPage => InformationGaps,
            NativeLogicalProjectionReplacement.HeldCard => HeldCardGaps,
            NativeLogicalProjectionReplacement.PotionTarget => PotionTargetGaps,
            _ => throw new ArgumentOutOfRangeException(nameof(scope))
        };
        const string catalogGap = "finite_bound_action_projection_incomplete";
        // The aggregate legacy catalog gap can be attributed to the superseded
        // selector only when no unrelated inherited omission remains.
        bool displacedCatalog = provenReplacement && inherited.Missing.All(reason =>
            reason == catalogGap || displaced.Contains(reason));
        string[] missing = inherited.Missing.Where(reason => !provenReplacement || !displaced.Contains(reason))
            .Where(reason => !displacedCatalog || reason != catalogGap)
            .Concat(ownMissing).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
        // A source failure with no concrete omitted-field accounting also cannot
        // be promoted just because a different adapter recognized a local owner.
        bool unresolvedStatus = inherited.Status != "complete" && inherited.Missing.Count == 0;
        return new(missing.Length == 0 && !unresolvedStatus ? "complete" : "partial", semantics,
            discovery, missing, inherited.HiddenByPolicy);
    }

    // A new entered page supplies its own declared slice, not the inherited
    // persistent/shared/consistency obligations. Compatibility text profiles
    // retain their existing projection; only native-logical calls this merge.
    internal static PlayerEnvironmentSnapshot PreserveNativeScope(PlayerEnvironmentSnapshot inherited,
        PlayerEnvironmentSnapshot projected, NativeLogicalProjectionReplacement scope, bool nativeLogical)
    {
        if (!nativeLogical) return projected;
        bool solved = projected.Completeness.Status == "complete"
            && projected.Completeness.Missing.Count == 0
            && projected.Interaction.Kind != "native_information_unresolved";
        IEnumerable<string> ownMissing = projected.Completeness.Missing;
        if (!solved && (!ownMissing.Any() || projected.Interaction.Kind == "native_information_unresolved"))
            ownMissing = ownMissing.Append(scope switch
        {
            NativeLogicalProjectionReplacement.HeldCard => "current_native_card_display",
            NativeLogicalProjectionReplacement.PotionTarget => "current_native_potion_target_projection_incomplete",
            _ => "current_native_information_projection_incomplete"
        });
        PlayerEnvironmentCompleteness completeness = Replace(inherited.Completeness, scope,
            solved, ownMissing, projected.Completeness.VisibleInformation, projected.Completeness.InteractionDiscovery);
        completeness = completeness with
        {
            HiddenByPolicy = inherited.Completeness.HiddenByPolicy.Concat(projected.Completeness.HiddenByPolicy)
                .Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray()
        };
        return projected with
        {
            Completeness = completeness,
            Status = solved ? ProjectionStatus("active", inherited.Status, completeness) : FailureStatus(inherited.Status)
        };
    }

    internal static string FailureStatus(string actualSourceStatus) =>
        actualSourceStatus is "settling" or "observed" ? actualSourceStatus : "visible_unsupported";

    internal static string ProjectionStatus(string nativeStage, string inheritedStatus, PlayerEnvironmentCompleteness completeness) =>
        nativeStage == "settling" ? "settling" : nativeStage == "completed" ? "observed"
            : completeness.Status == "complete" ? "interactive" : FailureStatus(inheritedStatus);

    internal static TextMenuFrame CloseIncompleteRequiredScope(TextMenuFrame frame) =>
        frame.Page.Completeness.Status == "complete" && frame.Page.Completeness.Missing.Count == 0 ? frame
            : frame with { Page = frame.Page with { Status = FailureStatus(frame.Page.Status) }, Leaves = Array.Empty<TextMenuLeaf>() };

    internal static TextMenuFrame Partial(PlayerEnvironmentSnapshot source, string ownerKey, string reason) =>
        new(source with
        {
            Status = FailureStatus(source.Status),
            Completeness = source.Completeness with
            { Status = "partial", Missing = source.Completeness.Missing.Append(reason).Distinct(StringComparer.Ordinal).ToArray() }
        }, ownerKey, Array.Empty<TextMenuLeaf>());
}
