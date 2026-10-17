using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using System.Threading.Tasks;
using System.Runtime.CompilerServices;
using System.Text.Json.Serialization;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Platform.NativeFoundation;
using MegaCrit.Sts2.addons.mega_text;

namespace STS2Connector.NativeUi;

/// <summary>Neutral process-local facts for ONE existing native request. No
/// tracker, P1 prefix, strategy, new legality or executable preview operand.
/// A public projection aliases Owner/Completion/Original, never Display.</summary>
internal sealed record NativeSelectorOccurrenceFacts(
    [property: JsonIgnore] object Owner, [property: JsonIgnore] Task Completion, string Family, string Stage,
    [property: JsonIgnore] IReadOnlyList<CardModel> Cards, [property: JsonIgnore] IReadOnlyList<CardModel> Selected,
    int? MinSelect, int? MaxSelect, bool? RequireManualConfirmation,
    string SelectionModel, string AddTransition, int? DisplayedHolderCount,
    bool CanFinishNow, bool CanDeclineNow, bool MechanicsQualified,
    IReadOnlyList<string> Missing);

internal sealed record NativeSelectorCandidatePreview(
    [property: JsonIgnore] CardModel Original, [property: JsonIgnore] NativeDetachedCardPreview? Detached,
    bool ModelReady, string DisplayContext, IReadOnlyList<string> Missing);

internal sealed record NativeSelectorEnchantmentSummary(string Title, string Description, int Amount);

internal sealed record NativeSelectorPreviewFacts(
    NativeSelectorOccurrenceFacts Request, IReadOnlyList<NativeSelectorCandidatePreview> Candidates,
    [property: JsonIgnore] EnchantmentModel? Enchantment, int? EnchantmentAmount, NativeSelectorEnchantmentSummary? EnchantmentSummary,
    bool RequiresAggregateAfterPresentation,
    IReadOnlyList<string> Missing)
{
    // This is generation/source-fact readiness, not public presentation completeness.
    internal bool ModelFactsReady => Request.MechanicsQualified && Missing.Count == 0
        && Candidates.All(candidate => candidate.Missing.Count == 0);
}

internal static class NativeSelectorPublicFacts
{
    internal const string CapabilityRevision = "sts2.native-selector-public-facts/1";
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_enchantment")]
    private static extern ref EnchantmentModel CurrentEnchantment(NDeckEnchantSelectScreen owner);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_enchantmentAmount")]
    private static extern ref int CurrentEnchantmentAmount(NDeckEnchantSelectScreen owner);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_enchantmentTitle")]
    private static extern ref MegaLabel CurrentEnchantmentTitle(NDeckEnchantSelectScreen owner);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_enchantmentDescription")]
    private static extern ref MegaRichTextLabel CurrentEnchantmentDescription(NDeckEnchantSelectScreen owner);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_cardRows")]
    private static extern ref List<List<NGridCardHolder>> GridRows(NCardGrid grid);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_cardToTransformation")]
    private static extern ref Func<CardModel, CardTransformation> TransformProjection(NDeckTransformSelectScreen owner);

    internal static NativeSelectorPreviewFacts Capture(NativeLogicalGridState state, bool originalOnlyTransform = false)
    {
        var missing = new List<string>();
        bool qualified = MechanicsQualified(state.Owner.GetType(), originalOnlyTransform);
        bool customTransform = qualified && originalOnlyTransform && state.Owner is NDeckTransformSelectScreen transform
            && !DefaultTransformProjectionQualified(TransformProjection(transform));
        qualified &= !customTransform;
        // Preferences getters are native methods too. Read their values only
        // after the fixed source/patch preflight, not as part of testing it.
        if (!qualified)
        {
            string gap = customTransform ? "custom_transform_preview_unqualified" : "native_selector_mechanics_call_closure_unqualified";
            var unknown = new NativeSelectorOccurrenceFacts(state.Owner, state.Completion, Family(state.Owner.GetType()), state.Stage,
                state.Cards, state.Selected, null, null, null, "unqualified", "unqualified", null, false, false, false, new[] { gap });
            return new(unknown, state.Cards.Select(card => new NativeSelectorCandidatePreview(card, null, false, "unqualified", new[] { gap })).ToArray(), null, null, null, false, new[] { gap });
        }
        CardSelectorPrefs prefs = state.Preferences;
        int? displayedCount = null;
        if (state.Owner is NCombatPileCardSelectScreen)
        {
            // Exactly CurrentlyDisplayedCards.Count()'s holder multiplicity,
            // obtained from its already-native rows without evaluating the
            // selection filter or patched model/property callbacks.
            if (GridRows(state.Grid) is { } rows && rows.All(row => row != null))
                displayedCount = rows.Sum(row => row.Count);
            else missing.Add("native_selector_current_displayed_holder_count_unavailable");
        }
        bool active = !state.Completion.IsCompleted && state.Stage is "selecting" or "preview";
        bool finish = active && (state.Controls.ContainsKey("confirm") || state.Controls.ContainsKey("confirm_selection")
            || state.Controls.ContainsKey("open_preview"));
        bool decline = active && (state.Controls.ContainsKey("cancel_selection") || state.Selected.Count == 0 && finish);
        var request = new NativeSelectorOccurrenceFacts(state.Owner, state.Completion, Family(state.Owner.GetType()), state.Stage,
            state.Cards, state.Selected, prefs.MinSelect, prefs.MaxSelect, prefs.RequireManualConfirmation,
            "native_selected_original_set", AddTransition(state.Owner.GetType(), prefs.RequireManualConfirmation), displayedCount,
            finish, decline, true, missing.ToArray());
        EnchantmentModel? enchantment = null;
        int? amount = null;
        NativeSelectorEnchantmentSummary? summary = null;
        bool singleEnchant = state.Owner is NDeckEnchantSelectScreen && prefs.MaxSelect == 1;
        if (state.Owner is NDeckEnchantSelectScreen enchantOwner)
        {
            enchantment = CurrentEnchantment(enchantOwner);
            amount = CurrentEnchantmentAmount(enchantOwner);
            if (enchantment == null) missing.Add("native_selector_current_enchantment_owner_facts_unavailable");
            if (amount is int actual && TrySummary(state.Owner, actual, out NativeSelectorEnchantmentSummary? currentSummary)) summary = currentSummary;
            else missing.Add("native_selector_current_enchantment_summary_unavailable");
        }
        var candidates = new List<NativeSelectorCandidatePreview>();
        foreach (CardModel original in state.Cards)
        {
            NativeDetachedCardPreview? preview = null;
            string? gap = null;
            string context = "native_original_only";
            bool requiredAfter = state.Owner is NDeckUpgradeSelectScreen || singleEnchant;
            if (state.Owner is NDeckUpgradeSelectScreen)
            {
                context = "native_deck_upgrade_after";
                NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out preview, out gap);
            }
            else if (singleEnchant && enchantment != null && amount is int exact)
            {
                context = "native_single_enchantment_after";
                NativeDetachedCardPreviewFactory.TrySingleEnchant(original, enchantment, exact, out preview, out gap);
            }
            else if (singleEnchant) gap = "native_selector_current_enchantment_owner_facts_unavailable";
            // Multi-enchant's actual UI shows original cards plus the native
            // enchantment summary. It has no per-candidate MaxSelect=1 widget.
            // Transform delegates/random generators are never invoked here.
            bool modelReady = !requiredAfter || preview != null;
            candidates.Add(new(original, preview, modelReady, context, gap == null ? Array.Empty<string>() : new[] { gap }));
        }
        return new(request, candidates.ToArray(), enchantment, amount, summary,
            state.Owner is NDeckUpgradeSelectScreen || singleEnchant, missing.ToArray());
    }

    private static bool TrySummary(object owner, int amount, out NativeSelectorEnchantmentSummary? summary)
    {
        summary = null;
        // Freeze the already-native _Ready-rendered summary. Never clone an
        // enchantment or invoke its formatter merely to recreate this UI fact.
        if (owner is not NDeckEnchantSelectScreen enchantOwner
            || CurrentEnchantmentTitle(enchantOwner) is not MegaLabel title
            || title.GetType() != typeof(MegaLabel)
            || CurrentEnchantmentDescription(enchantOwner) is not MegaRichTextLabel description
            || description.GetType() != typeof(MegaRichTextLabel)
            || !NativeCardPreviewReadQualification.IsUnpatched(typeof(MegaLabel).GetProperty("Text")?.GetMethod)
            || !NativeCardPreviewReadQualification.IsUnpatched(typeof(MegaRichTextLabel).GetProperty("Text")?.GetMethod)) return false;
        string titleText = title.Text, descriptionText = description.Text;
        if (string.IsNullOrWhiteSpace(titleText) || string.IsNullOrWhiteSpace(descriptionText)) return false;
        summary = new(titleText, descriptionText, amount);
        return true;
    }

    internal static string Family(Type type) => type == typeof(NDeckCardSelectScreen) ? "deck_card"
        : type == typeof(NDeckUpgradeSelectScreen) ? "deck_upgrade"
        : type == typeof(NDeckEnchantSelectScreen) ? "deck_enchant"
        : type == typeof(NSimpleCardSelectScreen) ? "simple"
        : type == typeof(NCombatPileCardSelectScreen) ? "combat_pile"
        : type == typeof(NDeckTransformSelectScreen) ? "deck_transform" : "unqualified";

    internal static string AddTransition(Type type, bool manual) => type == typeof(NDeckCardSelectScreen)
        || type == typeof(NDeckUpgradeSelectScreen) || type == typeof(NDeckEnchantSelectScreen)
        || type == typeof(NDeckTransformSelectScreen) ? "native_preview_at_max"
        : type == typeof(NSimpleCardSelectScreen) ? manual ? "native_manual_completion" : "native_completion_at_max"
        : type == typeof(NCombatPileCardSelectScreen) ? manual ? "native_manual_completion_with_dynamic_roster" : "native_completion_with_display_count_clamp"
        : "unqualified";

    internal static bool MechanicsQualified(Type type, bool originalOnlyTransform = false)
    {
        if (typeof(CardModel).Assembly.ManifestModule.ModuleVersionId != NativeCardPreviewReadQualification.AuditedGameMvid
            || !NativeLogicalGridCallbacks.Supported(type)
            || !NativeCardPreviewReadQualification.MethodsUnpatched(typeof(CardSelectorPrefs), "get_MinSelect", "get_MaxSelect", "get_RequireManualConfirmation")
            || !MechanicsMethodsQualified(type, "OnCardClicked")) return false;
        string[] methods = type == typeof(NDeckCardSelectScreen)
            ? new[] { "PreviewSelection", "RefreshConfirmButtonVisibility", "ConfirmSelection", "CheckIfSelectionComplete", "CloseSelection", "CancelSelection" }
            : type == typeof(NDeckUpgradeSelectScreen)
            ? new[] { "ConfirmSelection", "CheckIfSelectionComplete", "CloseSelection", "CancelSelection" }
            : type == typeof(NDeckEnchantSelectScreen)
            ? new[] { "PreviewSelection", "RefreshConfirmButtonVisibility", "ConfirmSelection", "CheckIfSelectionComplete", "CloseSelection", "CancelSelection" }
            : type == typeof(NSimpleCardSelectScreen) ? new[] { "CheckIfSelectionComplete", "CompleteSelection" }
            : type == typeof(NCombatPileCardSelectScreen) ? new[] { "CheckIfSelectionComplete", "CompleteSelection", "UpdateConfirmButton", "UpdatePileContents" }
            : type == typeof(NDeckTransformSelectScreen) && originalOnlyTransform
                ? new[] { "RefreshConfirmButtonVisibility", "ConfirmSelection", "OpenPreviewScreen", "CompleteSelection", "CloseSelection", "CancelSelection" }
            : Array.Empty<string>();
        // Source/native capture still requires the complete preview. Only the
        // public-basis profile excludes the default random preview animation.
        return methods.Length > 0 && MechanicsMethodsQualified(type, methods);
    }
    internal static MethodInfo? DefaultTransformProjectionMethod => typeof(CardSelectCmd)
        .GetNestedType("<>c", BindingFlags.NonPublic)?.GetMethod("<FromDeckForTransformation>b__22_1",
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);

    internal static bool DefaultTransformProjectionQualified(Func<CardModel, CardTransformation>? projection)
    {
        MethodInfo? expected = DefaultTransformProjectionMethod;
        // Inspect the fixed delegate identity only. Invoking even the native
        // default here would pre-run an input's transformation projection.
        return expected != null && projection != null && projection.GetInvocationList().Length == 1
            && projection.Method == expected && projection.Target?.GetType() == expected.DeclaringType
            && NativeCardPreviewReadQualification.IsUnpatched(expected)
            && NativeCardPreviewReadQualification.IsUnpatched(typeof(CardTransformation).GetConstructor(new[] { typeof(CardModel) }));
    }

    private static bool MechanicsMethodsQualified(Type owner, params string[] names) => names.All(name =>
    {
        MethodInfo[] methods = owner.GetMethods(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            .Where(method => method.Name == name).ToArray();
        return methods.Length > 0 && methods.All(SelectorMethodQualified);
    });

    // The unified Mod's existing recorder observes these callbacks. It never
    // changes their selection result or suppresses the original native body.
    // This permission is local to selector mechanics, never public formatting.
    internal static bool SelectorMethodQualified(MethodBase original)
    {
        if (Harmony.GetPatchInfo(original) is not { } patches) return true;
        bool Allowed(Patch patch, string role) => patch.PatchMethod.Module == typeof(ConnectorMod).Module
            && KnownObserverShape(original, patch.PatchMethod.DeclaringType?.FullName, patch.PatchMethod.Name, role, patch.owner);
        return patches.Transpilers.Count == 0
            && patches.Prefixes.All(patch => Allowed(patch, "Prefix"))
            && patches.Postfixes.All(patch => Allowed(patch, "Postfix"))
            && patches.Finalizers.All(patch => Allowed(patch, "Finalizer"));
    }

    internal static bool KnownObserverShape(MethodBase original, string? patchType, string patchMethod,
        string role, string harmonyOwner)
    {
        if (harmonyOwner != "rsgcsg.sts2-human-annotator" || patchMethod != role
            || role is not ("Prefix" or "Postfix" or "Finalizer")
            || original.DeclaringType is not { } owner || !NativeLogicalGridCallbacks.Supported(owner)) return false;
        Type[] args = original.GetParameters().Select(parameter => parameter.ParameterType).ToArray();
        if (original.Name == "OnCardClicked" && args.SequenceEqual(new[] { typeof(CardModel) }))
            return patchType == "STS2HumanAnnotator.Mod.NativeSelectorCardDecisionPatch";
        bool simpleCompletion = owner == typeof(NSimpleCardSelectScreen) || owner == typeof(NCombatPileCardSelectScreen);
        bool accepted = simpleCompletion ? original.Name == "CompleteSelection" && args.Length == 0
            : args.SequenceEqual(new[] { typeof(NButton) }) && (owner == typeof(NDeckTransformSelectScreen)
                ? original.Name is "CloseSelection" or "CompleteSelection"
                : original.Name is "CloseSelection" or "ConfirmSelection");
        return accepted && patchType == "STS2HumanAnnotator.Mod.NativeNestedSelectorAcceptedPatch";
    }

}
