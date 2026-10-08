using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CardLibrary;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.NativeLogical;

namespace STS2Platform.GameMod;

// Exact 9cb4f1ad STS2 composition seams. These hooks observe completed typed
// callbacks only; they never dispatch, wait, persist or infer causal completion.
internal static class ConnectorNativeLogicalPatches
{
    private static bool initialized;
    private static readonly HashSet<string> confirmed = new(StringComparer.Ordinal);
    internal static void Initialize()
    {
        if (initialized) return;
        var game = EnvironmentIdentityRuntime.ReadGame();
        if (game.MainAssemblySha256 != ConnectorNativeLogicalInspectionDeparture.GameAssemblySha256
            || game.MainAssemblyMvid != ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId
            || typeof(NInspectCardScreen).Assembly.ManifestModule.ModuleVersionId.ToString("D")
                != ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId)
            throw new NotSupportedException("The exact native inspection publication source requires the pinned game identity.");
        var harmony = new Harmony("rsgcsg.sts2-platform.connector.native-logical");
        Patch(harmony, typeof(NTargetManager), nameof(NTargetManager.OnNodeHovered), new[] { typeof(Node) }, nameof(TargetReturned));
        Patch(harmony, typeof(NTargetManager), nameof(NTargetManager.OnNodeUnhovered), new[] { typeof(Node) }, nameof(TargetReturned));
        Patch(harmony, typeof(NPlayerHand), "OnHolderPressed", new[] { typeof(NCardHolder) }, nameof(InputReturned));
        Patch(harmony, typeof(NCard), nameof(NCard.SetPreviewTarget), new[] { typeof(Creature) }, nameof(CardPreviewReturned));
        ConnectorNativeCardOperationBindings.Register(harmony);
        Patch(harmony, typeof(NGameOverScreen), "OpenSummaryScreen", new[] { typeof(NButton) }, nameof(TerminalEntryReturned));
        foreach (string seam in new[] { "native_target_focus", "native_card_preview", "native_inspect_preview", "native_input_callback", "native_terminal_entry" })
            confirmed.Add(seam);
        RegisterFamilies(harmony);
        // This confirmation is emitted only after every typed registration above
        // and below returned. Foundation/Service initialization precedes this
        // composition entry point; the single owner verifies intrinsic seams.
        confirmed.Add("connector_initial_observation");
        confirmed.Add("native_owner_ready");
        confirmed.Add("connector_input_start");
        PlayerEnvironmentService.InstallNativeLogicalPublicationProfile(
            NativeLogicalPublicationProfile.ProfileId,
            NativeLogicalPublicationProfile.DefinitionSha256,
            Array.AsReadOnly(confirmed.Select(seam => new NativeLogicalSeamCoverage(seam, "1", "complete_at_seam")).ToArray()));
        initialized = true;
    }
    private static void Patch(Harmony harmony, Type owner, string method, Type[] arguments, string observer)
    {
        var original = AccessTools.Method(owner, method, arguments) ?? throw new MissingMethodException(owner.FullName, method);
        var postfix = AccessTools.Method(typeof(ConnectorNativeLogicalPatches), observer) ?? throw new MissingMethodException(observer);
        harmony.Patch(original, postfix: new HarmonyMethod(postfix));
    }
    private static void FamilyPatch(Harmony harmony, Type owner, string method, Type[] arguments,
        string observer, string? before = null, bool finalizer = false, string[]? after = null)
    {
        var original = AccessTools.Method(owner, method, arguments) ?? throw new MissingMethodException(owner.FullName, method);
        FamilyPatch(harmony, original, observer, before, finalizer, after);
    }
    private static void FamilyPatch(Harmony harmony, MethodInfo original, string observer,
        string? before = null, bool finalizer = false, string[]? after = null)
    {
        var returned = new HarmonyMethod(AccessTools.Method(typeof(ConnectorNativeLogicalFamily), observer)
            ?? throw new MissingMethodException(observer)) { after = after ?? Array.Empty<string>() };
        HarmonyMethod? prefix = before is null ? null : new HarmonyMethod(
            AccessTools.Method(typeof(ConnectorNativeLogicalFamily), before) ?? throw new MissingMethodException(before));
        harmony.Patch(original, prefix: prefix, postfix: finalizer ? null : returned, finalizer: finalizer ? returned : null);
    }
    private static void RegisterFamilies(Harmony harmony)
    {
        var none = Type.EmptyTypes;
        const string foundation = "rsgcsg.sts2-platform.native-foundation";
        const string presentation = "rsgcsg.sts2-platform.connector.card-reward-presentation";
        FamilyPatch(harmony, typeof(NCapstoneContainer), nameof(NCapstoneContainer.Open), new[] { typeof(ICapstoneScreen) },
            nameof(ConnectorNativeLogicalFamily.CapstoneReturned), nameof(ConnectorNativeLogicalFamily.CapstoneOpenBefore));
        FamilyPatch(harmony, typeof(NCapstoneContainer), nameof(NCapstoneContainer.Close), none,
            nameof(ConnectorNativeLogicalFamily.CapstoneReturned), nameof(ConnectorNativeLogicalFamily.CapstoneCloseBefore));
        FamilyPatch(harmony, typeof(NInspectCardScreen), nameof(NInspectCardScreen.Open), new[] { typeof(List<CardModel>), typeof(int), typeof(bool) },
            nameof(ConnectorNativeLogicalFamily.InformationReturned), nameof(ConnectorNativeLogicalFamily.InformationBefore));
        FamilyPatch(harmony, typeof(NInspectCardScreen), nameof(NInspectCardScreen.Close), none,
            nameof(ConnectorNativeLogicalFamily.InformationReturned), nameof(ConnectorNativeLogicalFamily.InformationBefore));
        FamilyPatch(harmony, typeof(ActiveScreenContext), nameof(ActiveScreenContext.Update), none,
            nameof(ConnectorNativeLogicalFamily.ContextReturned), nameof(ConnectorNativeLogicalFamily.ContextBefore));
        // Exact native Close TweenCallback; no inherited/compiler-name search or fallback.
        var departure = typeof(NInspectCardScreen).GetMethod(ConnectorNativeLogicalInspectionDeparture.NativeCallbackName,
            BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly, none)
            ?? throw new MissingMethodException(typeof(NInspectCardScreen).FullName, ConnectorNativeLogicalInspectionDeparture.NativeCallbackName);
        if (departure.DeclaringType != typeof(NInspectCardScreen) || departure.IsStatic || departure.ReturnType != typeof(void))
            throw new MissingMethodException("The exact native inspection departure signature is unsupported.");
        FamilyPatch(harmony, departure, nameof(ConnectorNativeLogicalFamily.InspectDepartureReturned),
            nameof(ConnectorNativeLogicalFamily.InspectDepartureBefore), finalizer: true);
        FamilyPatch(harmony, typeof(NDeckViewScreen), "DisplayCards", none, nameof(ConnectorNativeLogicalFamily.InformationContentReturned));
        FamilyPatch(harmony, AccessTools.PropertySetter(typeof(NCardGrid), nameof(NCardGrid.IsShowingUpgrades))
            ?? throw new MissingMethodException(nameof(NCardGrid.IsShowingUpgrades)), nameof(ConnectorNativeLogicalFamily.InformationContentReturned));
        FamilyPatch(harmony, typeof(NCardGrid), nameof(NCardGrid.SetCards),
            new[] { typeof(IReadOnlyList<CardModel>), typeof(PileType), typeof(List<SortingOrders>), typeof(Task) },
            nameof(ConnectorNativeLogicalFamily.InformationContentReturned));
        FamilyPatch(harmony, typeof(NRewardsScreen), nameof(NRewardsScreen.ShowScreen),
            new[] { typeof(RewardsSet), typeof(bool), typeof(IRunState) }, nameof(ConnectorNativeLogicalFamily.RewardShown), after: new[] { foundation });
        FamilyPatch(harmony, typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.ShowScreen),
            new[] { typeof(IReadOnlyList<CardCreationResult>), typeof(IReadOnlyList<CardRewardAlternative>) },
            nameof(ConnectorNativeLogicalFamily.CardRewardShown), after: new[] { foundation });
        FamilyPatch(harmony, typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.RefreshOptions),
            new[] { typeof(IReadOnlyList<CardCreationResult>), typeof(IReadOnlyList<CardRewardAlternative>) },
            nameof(ConnectorNativeLogicalFamily.CardRewardRefreshReturned), nameof(ConnectorNativeLogicalFamily.RewardBefore),
            finalizer: true, after: new[] { foundation, presentation });
        foreach (var owner in new[] { typeof(NRewardsScreen), typeof(NCardRewardSelectionScreen) })
            foreach (var phase in new[] { "AfterOverlayOpened", "AfterOverlayShown", "AfterOverlayHidden", "AfterOverlayClosed" })
                FamilyPatch(harmony, owner, phase, none, nameof(ConnectorNativeLogicalFamily.RewardOwnerReturned), nameof(ConnectorNativeLogicalFamily.RewardBefore));
        foreach (var phase in new[] { "UpdateScreenState", "BeforeRoomExit" })
            FamilyPatch(harmony, typeof(NRewardsScreen), phase, none,
                nameof(ConnectorNativeLogicalFamily.RewardCatalogReturned), nameof(ConnectorNativeLogicalFamily.RewardBefore));
        foreach (var phase in new[] { nameof(NRewardsScreen.RewardCollectedFrom), nameof(NRewardsScreen.RewardSkippedFrom) })
            FamilyPatch(harmony, typeof(NRewardsScreen), phase, new[] { typeof(Control) },
                nameof(ConnectorNativeLogicalFamily.RewardCatalogReturned), nameof(ConnectorNativeLogicalFamily.RewardBefore));
        FamilyPatch(harmony, typeof(NRewardsScreen), "OnProceedButtonPressed", new[] { typeof(NButton) },
            nameof(ConnectorNativeLogicalFamily.RewardCatalogReturned), nameof(ConnectorNativeLogicalFamily.RewardBefore));
        FamilyPatch(harmony, typeof(NOverlayStack), nameof(NOverlayStack.Push), new[] { typeof(IOverlayScreen) },
            nameof(ConnectorNativeLogicalFamily.OverlayReturned), nameof(ConnectorNativeLogicalFamily.OverlayBefore));
        FamilyPatch(harmony, typeof(NOverlayStack), nameof(NOverlayStack.Remove), new[] { typeof(IOverlayScreen) },
            nameof(ConnectorNativeLogicalFamily.OverlayReturned), nameof(ConnectorNativeLogicalFamily.OverlayRemoveBefore));
        FamilyPatch(harmony, typeof(NCardHolder), nameof(NCardHolder.SetClickable), new[] { typeof(bool) }, nameof(ConnectorNativeLogicalFamily.HolderReturned));
        FamilyPatch(harmony, typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.OptionSelected), none, nameof(ConnectorNativeLogicalFamily.ChoiceReturned));
        foreach (var phase in new[] { nameof(NClickableControl.Enable), nameof(NClickableControl.Disable) })
            FamilyPatch(harmony, typeof(NClickableControl), phase, none,
                nameof(ConnectorNativeLogicalFamily.ControlReturned), nameof(ConnectorNativeLogicalFamily.ControlBefore));
        // No declaration is returned when any exact registration throws.
        foreach (string seam in new[] { "native_information_owner", "native_information_content", "native_reward_owner", "native_reward_catalog", "native_reward_input_availability" })
            confirmed.Add(seam);
    }
    private static void TargetReturned(NTargetManager __instance)
    {
        if (ReferenceEquals(NTargetManager.Instance, __instance) && ConnectorMod.IsLiveNode(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_target_focus", "target_focus_callback_returned");
    }
    private static void InputReturned(NPlayerHand __instance)
    {
        if (ReferenceEquals(NPlayerHand.Instance, __instance) && ConnectorMod.IsLiveNode(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_input_callback", "hand_holder_pressed_returned");
    }
    private static void CardPreviewReturned(NCard __instance)
    {
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_card_preview", "preview_target_callback_returned");
    }
    private static void TerminalEntryReturned(NGameOverScreen __instance)
    {
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_terminal_entry", "summary_animation_started", "terminal");
    }
}
