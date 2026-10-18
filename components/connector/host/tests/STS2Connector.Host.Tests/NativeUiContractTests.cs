using STS2Connector.LiveHost.Contracts;
using STS2Connector.LiveHost;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Ftue;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace STS2Connector.Tests;

public sealed class NativeUiContractTests
{
    [Fact]
    public void PopupPreemptsSpecializedRoomAndSelectorWithoutEvaluatingThem()
    {
        var popup = new LiveObservation("popup", "ready", null!,
            new PotionPopupSurface("potion_popup", "popup", "potion", "BLOCK_POTION", "Block", 0, false, true),
            null!, null!, Array.Empty<string>());
        var result = ActiveInputResolver.ResolvePreferredSurface(false, () => popup,
            () => throw new InvalidOperationException("Underlying room must not be read"));
        Assert.Same(popup, result!.Draft);
        Assert.Null(result.Failure);
        Assert.Contains(NativeUiActionRuntime.DescribePotionPopupCommands((PotionPopupSurface)result.Draft!.Surface),
            action => action.Kind == "discard_potion");
    }

    [Fact]
    public void ModalPreemptsPopupAndSpecializedSurface()
    {
        Assert.Null(ActiveInputResolver.ResolvePreferredSurface(true,
            () => throw new InvalidOperationException("Popup must not be read"),
            () => throw new InvalidOperationException("Room must not be read")));
    }

    [Fact]
    public void AmbiguousPopupCannotFallBackToUnderlyingRoom()
    {
        var result = ActiveInputResolver.ResolvePreferredSurface(false,
            () => throw new InvalidOperationException("Ambiguous popup"),
            () => throw new Exception("Must not evaluate room"));
        Assert.Null(result!.Draft);
        Assert.Equal("potion_popup", result.FailedProvider);
        Assert.IsType<InvalidOperationException>(result.Failure);
    }

    [Fact]
    public void AbsentPopupAllowsSpecializedSurfaceAndPreservesItsFailure()
    {
        bool evaluated = false;
        Assert.Null(ActiveInputResolver.ResolvePreferredSurface(false, () => null,
            () => { evaluated = true; return null; }));
        Assert.True(evaluated);
        var result = ActiveInputResolver.ResolvePreferredSurface(false, () => null,
            () => throw new InvalidOperationException("Missing exact selector owner"));
        Assert.Equal("specialized_surface", result!.FailedProvider);
        Assert.Null(result.Draft);
    }

    [Fact]
    public void PotionPopupPreservesCompleteNativeUseTargetDomain()
    {
        var surface = new PotionPopupSurface("potion_popup", "popup", "potion", "POTION", "Potion", 0, true, true)
            { DirectCombatUse = true, UseTargetEntityIds = new[] { "enemy1", "enemy2" } };
        var actions = NativeUiActionRuntime.DescribePotionPopupCommands(surface);
        var uses = actions.Where(action => action.Kind == "use_potion").ToArray();
        Assert.Equal(2, uses.Length);
        Assert.All(uses, action => Assert.Single(action.EntityBindings!, binding => binding.Role == "target"));
        Assert.DoesNotContain(actions, action => action.Kind == "choose_potion_use");
    }

    [Fact]
    public void NonCombatFruitJuicePopupAdvertisesUseWithPlayerOperand()
    {
        var surface = new PotionPopupSurface("potion_popup", "shop-popup", "juice", "FRUIT_JUICE", "Fruit Juice", 0, true, true)
            { DirectCombatUse = true, UseTargetEntityIds = new[] { "player1" } };
        var actions = NativeUiActionRuntime.DescribePotionPopupCommands(surface);
        var use = Assert.Single(actions, action => action.Kind == "use_potion");
        Assert.Contains(use.EntityBindings!, binding => binding.Role == "target" && binding.EntityId == "player1");
        Assert.DoesNotContain(actions, action => action.Kind == "choose_potion_use");
        Assert.DoesNotContain(NativeUiActionRuntime.DescribePotionPopupCommands(surface with { CanUse = false }),
            action => action.Kind == "use_potion");
    }

    [Theory]
    [InlineData(true, true, 3)]
    [InlineData(false, true, 2)]
    [InlineData(false, false, 1)]
    public void PotionPopupCatalogUsesNativeEnabledControls(bool use, bool discard, int count)
    {
        var surface = new PotionPopupSurface("potion_popup", "popup", "potion", "BLOCK_POTION", "Block potion", 0, use, discard);
        var commands = NativeUiActionRuntime.DescribePotionPopupCommands(surface);
        Assert.Equal(count, commands.Count);
        Assert.Equal(discard, commands.Any(x => x.Kind == "discard_potion"));
        Assert.Equal(use, commands.Any(x => x.Kind == "choose_potion_use"));
        Assert.Contains(commands, x => x.Kind == "cancel_potion_popup");
        Assert.Equal(commands.Count, commands.Select(x => x.Kind).Distinct().Count());
    }

    [Fact]
    public void CurrentGameTutorialModalsHaveExactAuditedBindings()
    {
        const BindingFlags flags = BindingFlags.Instance
                                   | BindingFlags.Public
                                   | BindingFlags.NonPublic;

        Assert.Equal(
            typeof(int),
            typeof(NCombatRulesFtue).GetField("_currentPage", flags)?.FieldType);
        Assert.Equal(
            typeof(NPopupYesNoButton),
            typeof(NVerticalPopup).GetProperty("YesButton", flags)?.PropertyType);
        Assert.Equal(
            typeof(NPopupYesNoButton),
            typeof(NVerticalPopup).GetProperty("NoButton", flags)?.PropertyType);
    }

    [Fact]
    public void TutorialDiscoveryPublishesOnlyExplicitCurrentControls()
    {
        var preference = new TutorialSurface(
            "tutorial",
            "preference",
            "tutorial-screen",
            TutorialModalSurfaceReader.AcceptTutorialsId,
            null,
            null,
            "Tutorials",
            "Enable tutorials?",
            null,
            new[]
            {
                new VisibleTutorialOption("enable_tutorials", "Yes", true),
                new VisibleTutorialOption("disable_tutorials", "No", true),
                new VisibleTutorialOption("invented_option", "Unsafe", true)
            });
        NativeUiActionDescriptor[] preferenceCommands =
            NativeUiActionRuntime.DescribeTutorialCommands(preference).ToArray();

        Assert.Equal(2, preferenceCommands.Length);
        Assert.Contains(preferenceCommands, command => command.Kind == "enable_tutorials");
        Assert.Contains(preferenceCommands, command => command.Kind == "disable_tutorials");
        Assert.DoesNotContain(preferenceCommands, command => command.Kind == "invented_option");
        Assert.All(preferenceCommands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen" && binding.EntityId == "tutorial-screen"));

        var page = preference with
        {
            Stage = "page",
            TutorialId = TutorialModalSurfaceReader.CombatRulesId,
            CurrentPage = 2,
            TotalPages = 3,
            Options = new[]
            {
                new VisibleTutorialOption("previous_tutorial_page", "Previous page", true),
                new VisibleTutorialOption("advance_tutorial", "Next page", true)
            }
        };
        NativeUiActionDescriptor[] pageCommands =
            NativeUiActionRuntime.DescribeTutorialCommands(page).ToArray();
        Assert.Contains(pageCommands, command => command.Kind == "previous_tutorial_page");
        Assert.Contains(pageCommands, command => command.Kind == "advance_tutorial");
    }

    [Fact]
    public void CurrentGameDeckUpgradeSelectorHasAuditedNativeBindings()
    {
        const BindingFlags flags = BindingFlags.Instance
                                   | BindingFlags.Public
                                   | BindingFlags.NonPublic;

        FieldInfo? preferences = typeof(NDeckUpgradeSelectScreen).GetField("_prefs", flags);
        FieldInfo? selection = typeof(NDeckUpgradeSelectScreen).GetField("_selectedCards", flags);
        FieldInfo? clickable = typeof(NCardHolder).GetField("_isClickable", flags);
        FieldInfo? previewAfter = typeof(NUpgradePreview).GetField("_after", flags);

        Assert.Equal(typeof(CardSelectorPrefs), preferences?.FieldType);
        Assert.True(selection?.FieldType.IsAssignableTo(typeof(IEnumerable<CardModel>)));
        Assert.Equal(typeof(bool), clickable?.FieldType);
        Assert.True(previewAfter?.FieldType.IsAssignableTo(typeof(Control)));
        Assert.True(typeof(NCardGridSelectionScreen).IsAssignableFrom(
            typeof(NDeckUpgradeSelectScreen)));
    }

    [Fact]
    public void CurrentGameHoverTipKindsAreExhaustivelyProjected()
    {
        string[] implementations = typeof(IHoverTip).Assembly.GetTypes()
            .Where(type => type != typeof(IHoverTip)
                           && !type.IsAbstract
                           && typeof(IHoverTip).IsAssignableFrom(type))
            .Select(type => type.FullName!)
            .OrderBy(name => name, StringComparer.Ordinal)
            .ToArray();

        Assert.Equal(new[]
        {
            typeof(CardHoverTip).FullName,
            typeof(HoverTip).FullName
        }, implementations);
    }

    [Fact]
    public void BoundedTransportBodyDoesNotTrustContentLength()
    {
        Assert.Equal(
            new byte[] { 1, 2, 3, 4 },
            ConnectorMod.ReadBoundedBodyBytes(
                new MemoryStream(new byte[] { 1, 2, 3, 4 }),
                4));
        Assert.Null(ConnectorMod.ReadBoundedBodyBytes(
            new MemoryStream(new byte[] { 1, 2, 3, 4, 5 }),
            4));
    }

    [Fact]
    public void GenericControlsOnOneOwnerHaveDistinctSemanticOperands()
    {
        var owner = new[] { new ActionEntityBinding("room", "room-a") };
        Dictionary<string, string> open = NativeUiActionRuntime.BuildCommandOperands(
            "open_shop_inventory",
            "activate_control",
            owner);
        Dictionary<string, string> proceed = NativeUiActionRuntime.BuildCommandOperands(
            "proceed_shop",
            "activate_control",
            owner);
        Dictionary<string, string> close = NativeUiActionRuntime.BuildCommandOperands(
            "close_shop_inventory",
            "cancel_interaction",
            new[] { new ActionEntityBinding("screen", "screen-a") });

        Assert.Equal("room-a", open["room_id"]);
        Assert.Equal("open_shop_inventory", open["control_id"]);
        Assert.Equal("proceed_shop", proceed["control_id"]);
        Assert.NotEqual(open["control_id"], proceed["control_id"]);
        Assert.Equal("screen-a", close["screen_id"]);
        Assert.Equal("close_shop_inventory", close["control_id"]);
    }

    [Fact]
    public void MapAnnotationExitRequiresTheTypedCurrentControlFact()
    {
        var blocked = new MapNavigationSurface(
            "map_navigation",
            "map-screen",
            false,
            false,
            "drawing",
            Array.Empty<VisibleMapChoice>())
        {
            AnnotationInputEntityId = "annotation-input",
            CanExitAnnotation = false
        };
        Assert.Empty(NativeUiActionRuntime.DescribeMapCommands(blocked));

        var ready = blocked with { CanExitAnnotation = true };
        NativeUiActionDescriptor command = Assert.Single(
            NativeUiActionRuntime.DescribeMapCommands(ready));

        Assert.Equal("exit_map_annotation", command.Kind);
        Assert.Contains(command.EntityBindings!, binding =>
            binding.Role == "map_annotation_input"
            && binding.EntityId == "annotation-input");
    }

    [Fact]
    public void DeckEnchantNativeDiscoveryKeepsPurposeStageAndMembership()
    {
        var card = new VisibleCard(
            "deck-card-a", "STRIKE", "Strike", "Attack", "1", null,
            "Deal 6 damage.", "Basic", false, false, null);
        var selecting = new DeckEnchantSelectionSurface(
            "deck_enchant_selection",
            "selecting",
            "enchant-screen",
            "Choose a card.",
            1,
            1,
            0,
            Array.Empty<string>(),
            true,
            new VisibleEnchantment(
                "FIXTURE_ENCHANTMENT",
                "Fixture",
                "Fixture enchantment.",
                1,
                "current_screen"),
            new[] { card })
        {
            SelectableCardEntityIds = new[] { card.EntityId },
            CanCloseSelection = true
        };

        NativeUiActionDescriptor[] selectingCommands =
            NativeUiActionRuntime.DescribeDeckEnchantCommands(selecting).ToArray();

        NativeUiActionDescriptor toggle = Assert.Single(selectingCommands, command =>
            command.Kind == "toggle_card");
        Assert.Contains(toggle.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "enchant-screen");
        Assert.Contains(toggle.EntityBindings!, binding =>
            binding.Role == "card" && binding.EntityId == card.EntityId);
        Assert.Contains(selectingCommands, command => command.Kind == "close_selection");
        Assert.DoesNotContain(selectingCommands, command =>
            command.Kind is "confirm_selection" or "cancel_preview");

        var preview = selecting with
        {
            Stage = "preview",
            SelectedCount = 1,
            SelectedCardEntityIds = new[] { card.EntityId },
            SelectableCardEntityIds = Array.Empty<string>(),
            CanCloseSelection = false,
            CanConfirm = true,
            CanCancelPreview = true
        };
        NativeUiActionDescriptor[] previewCommands =
            NativeUiActionRuntime.DescribeDeckEnchantCommands(preview).ToArray();

        NativeUiActionDescriptor confirm = Assert.Single(previewCommands, command =>
            command.Kind == "confirm_selection");
        Assert.Contains(confirm.EntityBindings!, binding =>
            binding.Role == "card" && binding.EntityId == card.EntityId);
        Assert.Contains(previewCommands, command => command.Kind == "cancel_preview");
    }

    [Fact]
    public void EventDialogueNativeDiscoveryBindsOnlyTheCurrentRevealedLine()
    {
        var surface = new EventDialogueSurface(
            "event_dialogue",
            "dialogue-screen",
            1,
            new[]
            {
                new VisibleDialogueLine("line-0", 0, "First", "ancient", false),
                new VisibleDialogueLine("line-1", 1, "Current", "character", true)
            },
            "Continue")
        {
            CanAdvance = true
        };

        NativeUiActionDescriptor command = Assert.Single(
            NativeUiActionRuntime.DescribeEventDialogueCommands(surface));

        Assert.Equal("advance_event_dialogue", command.Kind);
        Assert.Contains(command.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "dialogue-screen");
        Assert.Contains(command.EntityBindings!, binding =>
            binding.Role == "dialogue_line" && binding.EntityId == "line-1");
        Assert.DoesNotContain(command.EntityBindings!, binding =>
            binding.EntityId == "line-0");
    }

    [Fact]
    public void AllEnemiesPotionPublishesWithoutInventingCreatureTarget()
    {
        var potion = new ExplosiveAmpoule();

        Assert.Equal(TargetType.AllEnemies, potion.TargetType);
        Assert.True(CombatTurnSurfaceReader.IsAdvertisablePotionTarget(potion, null));
    }

    [Fact]
    public void CombatInputMatchesTheShippedHandDuringQueuedEffects()
    {
        Assert.True(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true,
            playerActionsDisabled: false,
            inPlayPhase: true,
            isPartOfPlayerTurn: true,
            handAcceptsInput: true,
            handIsPeeking: false, nativeActionExecuting: false));
        Assert.False(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true,
            playerActionsDisabled: true,
            inPlayPhase: true,
            isPartOfPlayerTurn: true,
            handAcceptsInput: true,
            handIsPeeking: false, nativeActionExecuting: false));
        Assert.False(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true,
            playerActionsDisabled: false,
            inPlayPhase: true,
            isPartOfPlayerTurn: true,
            handAcceptsInput: false,
            handIsPeeking: false, nativeActionExecuting: false));
        Assert.False(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true,
            playerActionsDisabled: false,
            inPlayPhase: false,
            isPartOfPlayerTurn: true,
            handAcceptsInput: true,
            handIsPeeking: false, nativeActionExecuting: false));
        Assert.False(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true,
            playerActionsDisabled: false,
            inPlayPhase: true,
            isPartOfPlayerTurn: true,
            handAcceptsInput: true,
            handIsPeeking: true, nativeActionExecuting: false));
    }

    [Fact]
    public void ExecutingRebootIsNotAnIdleCombatDecision()
    {
        Assert.False(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true, playerActionsDisabled: false, inPlayPhase: true,
            isPartOfPlayerTurn: true, handAcceptsInput: true, handIsPeeking: false,
            nativeActionExecuting: true));
        Assert.True(CombatTurnSurfaceReader.IsCombatInputReady(
            combatInProgress: true, playerActionsDisabled: false, inPlayPhase: true,
            isPartOfPlayerTurn: true, handAcceptsInput: true, handIsPeeking: false,
            nativeActionExecuting: false));
    }

    [Fact]
    public void RemainingSelectorDiscoveryUsesDirectTypedSurfaceFacts()
    {
        var card = new VisibleCard(
            "card-a", "STRIKE", "Strike", "Attack", "1", null,
            "Deal 6 damage.", "Basic", false, true, null);
        var transform = new DeckTransformSelectionSurface(
            "deck_transform_selection",
            "selecting",
            "transform-screen",
            "Choose a card to transform.",
            1,
            1,
            0,
            Array.Empty<string>(),
            true,
            true,
            false,
            "none",
            false,
            new[] { card })
        {
            SelectableCardEntityIds = new[] { card.EntityId },
            CanCancelSelection = true,
            CanToggleUpgradeView = true
        };
        NativeUiActionDescriptor[] transformCommands =
            NativeUiActionRuntime.DescribeDeckTransformCommands(transform).ToArray();
        Assert.Contains(transformCommands, command => command.Kind == "toggle_deck_transform_card");
        Assert.Contains(transformCommands, command => command.Kind == "cancel_deck_transform_selection");
        Assert.Contains(transformCommands, command => command.Kind == "toggle_deck_transform_upgrade_view");
        Assert.All(transformCommands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen" && binding.EntityId == "transform-screen"));

        var upgrade = new NativeDeckUpgradeSelectionSurface(
            NativeDeckUpgradeSelection.SurfaceKind,
            "selecting",
            "upgrade-screen",
            "Choose a card to upgrade.",
            1,
            1,
            0,
            Array.Empty<string>(),
            new[] { card.EntityId },
            Array.Empty<string>(),
            Cancelable: true,
            ShowingUpgradePreviews: false,
            CanToggleUpgradeView: true,
            CanCancelSelection: true,
            CanCancelPreview: false,
            CanConfirm: false,
            Cards: new[] { card },
            PreviewCards: Array.Empty<VisibleCard>());
        NativeUiActionDescriptor[] upgradeCommands =
            NativeDeckUpgradeSelection.DescribeCommands(upgrade).ToArray();
        Assert.Contains(upgradeCommands, command =>
            command.Kind == NativeDeckUpgradeSelection.SelectOperation);
        Assert.Contains(upgradeCommands, command =>
            command.Kind == NativeDeckUpgradeSelection.CancelSelectionOperation);
        Assert.Contains(upgradeCommands, command =>
            command.Kind == NativeDeckUpgradeSelection.ToggleUpgradeViewOperation);
        Assert.All(upgradeCommands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen" && binding.EntityId == "upgrade-screen"));

        var pile = new NativeCombatPileSelectionSurface(
            NativeCombatPileSelection.SurfaceKind,
            "selecting",
            "pile-screen",
            "Choose a card.",
            "discard",
            0,
            1,
            1,
            new[] { card.EntityId },
            Array.Empty<string>(),
            new[] { card.EntityId },
            Cancelable: false,
            CanCancel: false,
            CanConfirm: true,
            Cards: new[] { card });
        NativeUiActionDescriptor[] pileCommands =
            NativeCombatPileSelection.DescribeCommands(pile).ToArray();
        Assert.Contains(pileCommands, command => command.Kind == NativeCombatPileSelection.DeselectOperation);
        NativeUiActionDescriptor pileConfirm = Assert.Single(
            pileCommands,
            command => command.Kind == NativeCombatPileSelection.ConfirmOperation);
        Assert.Contains(pileConfirm.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "pile-screen");
        Assert.DoesNotContain(pileCommands.SelectMany(command => command.EntityBindings!), binding =>
            binding.Role == "source");
    }

    [Fact]
    public void CardBundleNativeDiscoveryKeepsBundlesAtomicAcrossStages()
    {
        var card = new VisibleCard(
            "bundle-card-a", "STRIKE", "Strike", "Attack", "1", null,
            "Deal 6 damage.", "Basic", false, false, null);
        var bundle = new VisibleCardBundle("bundle-a", new[] { card });
        var choosing = new CardBundleSelectionSurface(
            "card_bundle_selection",
            "choosing",
            "bundle-screen",
            "Choose a bundle.",
            null,
            new[] { "bundle-a" },
            false,
            false,
            new[] { bundle });

        NativeUiActionDescriptor preview = Assert.Single(
            NativeUiActionRuntime.DescribeCardBundleCommands(choosing));
        Assert.Equal("preview_card_bundle", preview.Kind);
        Assert.Contains(preview.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "bundle-screen");
        Assert.Contains(preview.EntityBindings!, binding =>
            binding.Role == "bundle" && binding.EntityId == "bundle-a");

        NativeUiActionDescriptor[] previewStage = NativeUiActionRuntime.DescribeCardBundleCommands(
            choosing with
            {
                Stage = "preview",
                SelectedBundleEntityId = "bundle-a",
                SelectableBundleEntityIds = Array.Empty<string>(),
                CanConfirm = true,
                CanCancelPreview = true
            })
            .ToArray();
        Assert.Contains(previewStage, command => command.Kind == "confirm_card_bundle");
        Assert.Contains(previewStage, command => command.Kind == "cancel_card_bundle_preview");
        Assert.DoesNotContain(previewStage, command => command.Kind == "preview_card_bundle");
    }

    [Fact]
    public void CardBundlePreviewBindingRequiresOneExactGameOwnedReference()
    {
        var first = new object();
        var selected = new object();

        Assert.Same(
            selected,
            CardBundleSelectionSurfaceReader.ResolveUniqueReference(
                new[] { first, selected },
                selected));
        Assert.Null(CardBundleSelectionSurfaceReader.ResolveUniqueReference(
            new[] { first },
            selected));
        Assert.Null(CardBundleSelectionSurfaceReader.ResolveUniqueReference(
            new[] { selected, selected },
            selected));
        Assert.Null(CardBundleSelectionSurfaceReader.ResolveUniqueReference<object>(
            new[] { first },
            null));
    }

    [Fact]
    public void MenuNativeDiscoveryUsesTypedSurfaceFactsAndExactOwners()
    {
        var main = new MainMenuSurface(
            "main_menu",
            "choosing",
            "menu-root",
            new[]
            {
                new VisibleMenuOption(
                    "continue-button",
                    "continue",
                    "Continue",
                    null,
                    true,
                    "actionable",
                    null),
                new VisibleMenuOption(
                    "singleplayer-button",
                    "singleplayer",
                    "Single Player",
                    null,
                    true,
                    "actionable",
                    null),
                new VisibleMenuOption(
                    "settings-button",
                    "settings",
                    "Settings",
                    null,
                    true,
                    "visible_unsupported",
                    "Not in the bounded contract.")
            },
            new VisibleContinueRunSummary(
                "IRONCLAD",
                "Ironclad",
                "ACT_1",
                "Act 1",
                3,
                70,
                80,
                99,
                0));
        var singleplayer = new SingleplayerMenuSurface(
            "singleplayer_menu",
            "choosing",
            "singleplayer-root",
            new[]
            {
                new VisibleMenuOption(
                    "standard-button",
                    "standard",
                    "Standard",
                    null,
                    true,
                    "actionable",
                    null),
                new VisibleMenuOption(
                    "back-button",
                    "back",
                    "Back",
                    null,
                    false,
                    "visible_unsupported",
                    "Disabled.")
            });

        NativeUiActionDescriptor[] mainCommands =
            NativeUiActionRuntime.DescribeMainMenuCommands(main).ToArray();
        NativeUiActionDescriptor singleplayerCommand = Assert.Single(
            NativeUiActionRuntime.DescribeSingleplayerMenuCommands(singleplayer));

        Assert.Equal(2, mainCommands.Length);
        Assert.Contains(mainCommands, command => command.Kind == "continue_run");
        Assert.Contains(mainCommands, command => command.Kind == "open_singleplayer");
        Assert.All(mainCommands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "menu_screen"
                       && binding.EntityId == "menu-root"));
        Assert.Equal("open_standard_run_setup", singleplayerCommand.Kind);
        Assert.Contains(
            singleplayerCommand.EntityBindings!,
            binding => binding.Role == "menu_screen"
                       && binding.EntityId == "singleplayer-root");
    }

    [Fact]
    public void CharacterSelectNativeDiscoveryBindsOwnerAndExactCharacter()
    {
        var surface = new CharacterSelectSurface(
            "character_select",
            "choosing",
            "character-screen",
            new[]
            {
                new VisibleCharacterChoice(
                    "choice-selected",
                    0,
                    "IRONCLAD",
                    "Ironclad",
                    false,
                    true,
                    false,
                    true),
                new VisibleCharacterChoice(
                    "choice-available",
                    1,
                    "SILENT",
                    "Silent",
                    false,
                    false,
                    false,
                    true),
                new VisibleCharacterChoice(
                    "choice-locked",
                    2,
                    "DEFECT",
                    "Defect",
                    true,
                    false,
                    false,
                    true)
            },
            null,
            3,
            "Ascension 3",
            "Harder enemies.",
            true,
            true,
            true,
            true);

        NativeUiActionDescriptor[] commands =
            NativeUiActionRuntime.DescribeCharacterSelectCommands(surface).ToArray();

        Assert.Equal(5, commands.Length);
        Assert.All(commands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen"
                       && binding.EntityId == "character-screen"));
        NativeUiActionDescriptor select = Assert.Single(commands, command =>
            command.Kind == "select_character");
        Assert.Contains(select.EntityBindings!, binding =>
            binding.Role == "character_choice"
            && binding.EntityId == "choice-available");
        Assert.DoesNotContain(commands, command => command.EntityBindings!.Any(binding =>
            binding.EntityId == "choice-locked"));
        NativeUiActionDescriptor embark = Assert.Single(commands, command =>
            command.Kind == "embark_standard_run");
        Assert.Contains(embark.EntityBindings!, binding =>
            binding.Role == "character_choice"
            && binding.EntityId == "choice-selected");

        Dictionary<string, string> operands = NativeUiActionRuntime.BuildCommandOperands(
            select.Kind,
            "select_entity",
            select.EntityBindings!);
        Assert.Equal("character-screen", operands["screen_id"]);
        Assert.Equal("choice-available", operands["character_choice_id"]);
        Assert.DoesNotContain("action_id", operands.Keys);
    }

    [Fact]
    public void GeneratedChoiceNativeOperandsBindOwnerAndExactCard()
    {
        var surface = new NativeGeneratedCardChoiceSurface(
            NativeGeneratedCardChoice.SurfaceKind,
            "choosing",
            "generated-screen",
            "Choose a Card",
            new[] { "generated-card" },
            CanSkip: true,
            IsPeeking: false,
            Cards: new[]
            {
                new VisibleCard(
                    "generated-card", "TRUE_GRIT", "True Grit", "Skill", "1", null,
                    "Gain Block.", "Common", false, false, null),
                new VisibleCard(
                    "blocked-card", "BATTLE_TRANCE", "Battle Trance", "Skill", "0", null,
                    "Draw cards.", "Uncommon", false, false, null)
            });
        NativeUiActionDescriptor[] commands =
            NativeGeneratedCardChoice.DescribeCommands(surface).ToArray();

        Assert.Equal(2, commands.Length);
        Assert.Contains(commands, command => command.Kind == NativeGeneratedCardChoice.SelectOperation
            && command.EntityBindings!.Any(binding => binding.EntityId == "generated-card"));
        Assert.DoesNotContain(commands, command => command.EntityBindings!.Any(binding =>
            binding.EntityId == "blocked-card"));
        Assert.Contains(commands, command => command.Kind == NativeGeneratedCardChoice.SkipOperation);

        Dictionary<string, string> select = NativeUiActionRuntime.BuildCommandOperands(
            NativeGeneratedCardChoice.SelectOperation,
            "select_entity",
            new[]
            {
                new ActionEntityBinding("screen", "generated-screen"),
                new ActionEntityBinding("card", "generated-card")
            });
        Dictionary<string, string> skip = NativeUiActionRuntime.BuildCommandOperands(
            NativeGeneratedCardChoice.SkipOperation,
            "activate_control",
            new[] { new ActionEntityBinding("screen", "generated-screen") });

        Assert.Equal("generated-screen", select["screen_id"]);
        Assert.Equal("generated-card", select["card_id"]);
        Assert.Equal("generated-screen", skip["screen_id"]);
        Assert.Equal(NativeGeneratedCardChoice.SkipOperation, skip["control_id"]);
        Assert.DoesNotContain("action_id", select.Keys);
        Assert.DoesNotContain("action_id", skip.Keys);
    }

    [Fact]
    public void BossRelicSelectionDescriptorsBindExactScreenAndRelicOrSkipControl()
    {
        var relic = new VisibleRelic(
            "relic-choice",
            "RELIC_A",
            "Relic A",
            "A relic.",
            null,
            Array.Empty<VisibleKeyword>(),
            Array.Empty<VisibleCard>());
        var surface = new NativeBossRelicSelectionSurface(
            NativeBossRelicSelection.SurfaceKind,
            "boss-relic-screen",
            new[] { relic },
            new[] { relic.EntityId },
            CanSkip: true);

        NativeUiActionDescriptor[] commands =
            NativeBossRelicSelection.DescribeCommands(surface).ToArray();

        NativeUiActionDescriptor choose = Assert.Single(
            commands,
            command => command.Kind == NativeBossRelicSelection.SelectOperation);
        Assert.Contains(choose.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "boss-relic-screen");
        Assert.Contains(choose.EntityBindings!, binding =>
            binding.Role == "relic" && binding.EntityId == relic.EntityId);

        NativeUiActionDescriptor skip = Assert.Single(
            commands,
            command => command.Kind == NativeBossRelicSelection.SkipOperation);
        Assert.Contains(skip.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "boss-relic-screen");
        Dictionary<string, string> chooseOperands =
            NativeUiActionRuntime.BuildCommandOperands(
                choose.Kind,
                "select_entity",
                choose.EntityBindings!);
        Assert.Equal("boss-relic-screen", chooseOperands["screen_id"]);
        Assert.Equal(relic.EntityId, chooseOperands["choice_id"]);
        Assert.Equal(
            NativeBossRelicSelection.SkipOperation,
            NativeUiActionRuntime.BuildCommandOperands(
                skip.Kind,
                "activate_control",
                skip.EntityBindings!)["control_id"]);
    }

    [Fact]
    public void SimpleCardSelectionUsesCurrentUiFactsWithoutOpeningSourceAuthority()
    {
        var card = new VisibleCard(
            "simple-card", "STRIKE", "Strike", "Attack", "1", null,
            "Deal 6 damage.", "Basic", false, true, null);
        var surface = new NativeSimpleCardSelectionSurface(
            NativeSimpleCardSelection.SurfaceKind,
            "selecting",
            "simple-screen",
            "Choose a card",
            1,
            2,
            1,
            new[] { card.EntityId },
            Array.Empty<string>(),
            new[] { card.EntityId },
            Cancelable: true,
            RequireManualConfirmation: true,
            CanCancel: true,
            CanConfirm: true,
            Cards: new[] { card });

        NativeUiActionDescriptor[] commands =
            NativeSimpleCardSelection.DescribeCommands(surface).ToArray();

        Assert.Contains(commands, command =>
            command.Kind == NativeSimpleCardSelection.DeselectOperation
            && command.EntityBindings!.Any(binding =>
                binding.Role == "card" && binding.EntityId == card.EntityId));
        Assert.Contains(commands, command => command.Kind == NativeSimpleCardSelection.ConfirmOperation);
        Assert.Contains(commands, command => command.Kind == NativeSimpleCardSelection.CancelOperation);
        Assert.All(commands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen" && binding.EntityId == "simple-screen"));
        Assert.DoesNotContain(
            commands.SelectMany(command => command.EntityBindings!),
            binding => binding.Role == "source");
    }

    [Fact]
    public void MapDrawingModeBindingAcceptsOnlyAuditedVersionShapes()
    {
        Assert.True(MapNavigationSurfaceReader.IsCompatibleLocalDrawingModeSignature(
            Array.Empty<Type>()));
        Assert.True(MapNavigationSurfaceReader.IsCompatibleLocalDrawingModeSignature(
            new[] { typeof(bool) }));
        Assert.False(MapNavigationSurfaceReader.IsCompatibleLocalDrawingModeSignature(
            new[] { typeof(string) }));
        Assert.False(MapNavigationSurfaceReader.IsCompatibleLocalDrawingModeSignature(
            new[] { typeof(bool), typeof(bool) }));
        Assert.True(MapNavigationSurfaceReader.HasCompatibleLocalDrawingModeBinding);
        Assert.Contains(
            MapNavigationSurfaceReader.ControllerInputModeBindingName,
            new[] { "IsUsingDirectionalNavigation", "IsUsingController" });
    }

    [Fact]
    public void RestNativeDescriptorsBindOnlyEnabledOptionsAndCurrentProceedControl()
    {
        var surface = new RestSiteSurface(
            "rest_site",
            "rest-screen",
            new[]
            {
                new VisibleRestOption("rest-heal", 0, "heal", "Rest", "Heal", Enabled: true),
                new VisibleRestOption("rest-smith", 1, "smith", "Smith", "Upgrade", Enabled: false)
            },
            CanProceed: true);

        NativeUiActionDescriptor[] commands = NativeRestSite.DescribeCommands(surface).ToArray();

        Assert.Equal(2, commands.Length);
        NativeUiActionDescriptor choose = Assert.Single(commands, command => command.Kind == "choose_rest_option");
        Assert.Contains(choose.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "rest-screen");
        Assert.Contains(choose.EntityBindings!, binding =>
            binding.Role == "rest_option" && binding.EntityId == "rest-heal");
        Assert.DoesNotContain(commands.SelectMany(command => command.EntityBindings ?? Array.Empty<ActionEntityBinding>()),
            binding => binding.EntityId == "rest-smith");
        Assert.Single(commands, command => command.Kind == "proceed_rest_site");
    }

    [Fact]
    public void NativeMapAndRestOperandsBindOwnerAndExactEntity()
    {
        Dictionary<string, string> map = NativeUiActionRuntime.BuildCommandOperands(
            "choose_map_node",
            "navigate",
            new[]
            {
                new ActionEntityBinding("map_screen", "screen-a"),
                new ActionEntityBinding("map_node", "node-b")
            });
        Dictionary<string, string> rest = NativeUiActionRuntime.BuildCommandOperands(
            "choose_rest_option",
            "choose",
            new[]
            {
                new ActionEntityBinding("screen", "screen-c"),
                new ActionEntityBinding("rest_option", "option-d")
            });

        Assert.Equal("screen-a", map["map_screen_id"]);
        Assert.Equal("node-b", map["map_node_id"]);
        Assert.Equal("screen-c", rest["screen_id"]);
        Assert.Equal("option-d", rest["rest_option_id"]);
    }

    [Fact]
    public void NativeDeckEnchantOperandsBindOwnerAndExactCard()
    {
        Dictionary<string, string> operands = NativeUiActionRuntime.BuildCommandOperands(
            "toggle_card",
            "select_entity",
            new[]
            {
                new ActionEntityBinding("screen", "screen-enchant"),
                new ActionEntityBinding("card", "card-target")
            });

        Assert.Equal("screen-enchant", operands["screen_id"]);
        Assert.Equal("card-target", operands["card_id"]);
    }

    [Fact]
    public void EventNativeDiscoveryUsesVisibleControlFactsWithoutDraftActions()
    {
        var surface = new EventOptionSurface(
            "event_option",
            "screen-event",
            new[]
            {
                new VisibleEventOption(
                    "option-enabled",
                    0,
                    "Choose",
                    null,
                    true,
                    false,
                    false,
                    false,
                    false,
                    null,
                    null,
                    Array.Empty<VisibleEventOptionTooltip>()),
                new VisibleEventOption(
                    "option-disabled",
                    1,
                    "Unavailable",
                    null,
                    false,
                    false,
                    false,
                    false,
                    false,
                    null,
                    null,
                    Array.Empty<VisibleEventOptionTooltip>())
            });

        NativeUiActionDescriptor command = Assert.Single(
            NativeUiActionRuntime.DescribeEventOptionCommands(surface));

        Assert.Equal("choose_event_option", command.Kind);
        Assert.Contains(command.EntityBindings!, binding =>
            binding.Role == "screen" && binding.EntityId == "screen-event");
        Assert.Contains(command.EntityBindings!, binding =>
            binding.Role == "option" && binding.EntityId == "option-enabled");
    }

    [Fact]
    public void GameOverNativeDiscoveryKeepsStageAndOwnerExact()
    {
        var intro = new GameOverSurface(
            "game_over",
            "intro",
            "game-over-screen",
            null,
            true,
            false,
            Array.Empty<VisibleMenuOption>());
        var summary = new GameOverSurface(
            "game_over",
            "summary",
            "game-over-screen",
            "main_menu",
            false,
            true,
            new[]
            {
                new VisibleMenuOption(
                    "view-run",
                    "view_run",
                    "View Run",
                    null,
                    true,
                    "visible_unsupported",
                    "outside C1")
            });

        NativeUiActionDescriptor advance = Assert.Single(
            NativeUiActionRuntime.DescribeGameOverCommands(intro));
        NativeUiActionDescriptor exit = Assert.Single(
            NativeUiActionRuntime.DescribeGameOverCommands(summary));

        Assert.Equal("advance_game_over_summary", advance.Kind);
        Assert.Equal("return_game_over", exit.Kind);
        Assert.Contains(advance.EntityBindings!, binding =>
            binding.Role == "game_over_screen"
            && binding.EntityId == "game-over-screen");
        Assert.Contains(exit.EntityBindings!, binding =>
            binding.Role == "game_over_screen"
            && binding.EntityId == "game-over-screen");
        Assert.Equal(
            "advance_game_over_summary",
            NativeUiActionRuntime.BuildCommandOperands(
                advance.Kind,
                "activate_control",
                advance.EntityBindings!)["control_id"]);
        Assert.Equal(
            "return_game_over",
            NativeUiActionRuntime.BuildCommandOperands(
                exit.Kind,
                "activate_control",
                exit.EntityBindings!)["control_id"]);
    }

    [Fact]
    public void TreasureNativeDiscoveryKeepsStageSpecificContractsSeparate()
    {
        var closed = new TreasureRoomSurface(
            "treasure_room",
            "closed",
            "treasure-room",
            false,
            Array.Empty<VisibleTreasureRelic>(),
            false,
            false,
            false);
        var choice = new TreasureRoomSurface(
            "treasure_room",
            "relic_choice",
            "treasure-room",
            true,
            new[]
            {
                new VisibleTreasureRelic(
                    "relic-choice",
                    "RELIC_A",
                    "Relic A",
                    "Visible relic",
                    "Common",
                    Array.Empty<VisibleKeyword>(),
                    Array.Empty<VisibleCard>())
            },
            true,
            true,
            false);

        NativeUiActionDescriptor open = Assert.Single(
            NativeUiActionRuntime.DescribeTreasureRoomCommands(closed));
        NativeUiActionDescriptor[] choices =
            NativeUiActionRuntime.DescribeTreasureRoomCommands(choice).ToArray();

        Assert.Equal("open_treasure_chest", open.Kind);
        Assert.Contains(choices, action => action.Kind == "choose_treasure_relic");
        Assert.Contains(choices, action => action.Kind == "skip_treasure_relic");
        Assert.DoesNotContain(
            NativeUiActionRuntime.DescribeTreasureRoomCommands(
                choice with { CanChoose = false }),
            action => action.Kind == "choose_treasure_relic");
        Assert.All(choices, action => Assert.Contains(
            action.EntityBindings!,
            binding => binding.Role == "treasure_room"
                       && binding.EntityId == "treasure-room"));
    }

    [Fact]
    public void RewardNativeDiscoveryUsesSurfaceFactsWithoutDraftActions()
    {
        var surface = new RewardClaimSurface(
            "reward_claim",
            "screen-reward",
            new[]
            {
                new VisibleReward(
                    "reward-gold",
                    "gold",
                    "25 Gold",
                    "Gain 25 Gold.",
                    true),
                new VisibleReward(
                    "reward-blocked",
                    "potion",
                    "Potion",
                    "Potion slots are full.",
                    false)
            },
            true,
            new[]
            {
                new VisibleCombatPotion(
                    "potion-old",
                    "OLD_POTION",
                    "Old Potion",
                    "Discardable.",
                    1,
                    "AnyPlayer",
                    false,
                    false)
            },
            true,
            false);

        NativeUiActionDescriptor[] commands =
            NativeUiActionRuntime.DescribeRewardClaimCommands(surface).ToArray();

        Assert.Contains(commands, command =>
            command.Kind == "claim_reward"
            && command.EntityBindings!.Any(binding =>
                binding.Role == "reward"
                && binding.EntityId == "reward-gold"));
        Assert.DoesNotContain(commands, command =>
            command.EntityBindings!.Any(binding =>
                binding.EntityId == "reward-blocked"));
        Assert.Contains(commands, command =>
            command.Kind == "discard_potion_for_reward"
            && command.EntityBindings!.Any(binding =>
                binding.Role == "potion"
                && binding.EntityId == "potion-old"));
        Assert.Contains(commands, command =>
            command.Kind == "proceed_rewards"
            && command.EntityBindings!.Any(binding =>
                binding.Role == "screen"
                && binding.EntityId == "screen-reward"));
    }

    [Fact]
    public void CardRewardNativeDiscoveryUsesTypedEligibilityAndExactOwnerOperands()
    {
        static VisibleCard Card(string entityId, string definitionId, string name) =>
            new(
                entityId,
                definitionId,
                name,
                "Attack",
                "1",
                null,
                null,
                "Common",
                false,
                false,
                null);

        var surface = new CardRewardSelectionSurface(
            "card_reward_selection",
            "screen-card-reward",
            new[]
            {
                Card("card-selectable", "STRIKE", "Strike"),
                Card("card-disabled", "DEFEND", "Defend")
            },
            new[]
            {
                new VisibleCardRewardAlternative("alternative-enabled", 0, "Reroll", true),
                new VisibleCardRewardAlternative("alternative-disabled", 1, "Locked", false)
            })
        {
            SelectableCardEntityIds = new[] { "card-selectable", "unknown-card" }
        };

        NativeUiActionDescriptor[] commands =
            NativeUiActionRuntime.DescribeCardRewardCommands(surface).ToArray();

        Assert.Collection(
            commands.OrderBy(command => command.Kind, StringComparer.Ordinal),
            alternative =>
            {
                Assert.Equal("choose_card_reward_alternative", alternative.Kind);
                Assert.Contains(alternative.EntityBindings!, binding =>
                    binding.Role == "screen" && binding.EntityId == "screen-card-reward");
                Assert.Contains(alternative.EntityBindings!, binding =>
                    binding.Role == "alternative" && binding.EntityId == "alternative-enabled");
            },
            card =>
            {
                Assert.Equal("select_card_reward", card.Kind);
                Assert.Contains(card.EntityBindings!, binding =>
                    binding.Role == "screen" && binding.EntityId == "screen-card-reward");
                Assert.Contains(card.EntityBindings!, binding =>
                    binding.Role == "card" && binding.EntityId == "card-selectable");
            });
        Assert.DoesNotContain(commands, command => command.EntityBindings!.Any(binding =>
            binding.EntityId is "card-disabled" or "alternative-disabled" or "unknown-card"));

        NativeUiActionDescriptor enabledAlternative = Assert.Single(commands, command =>
            command.Kind == "choose_card_reward_alternative");
        Dictionary<string, string> alternativeOperands =
            NativeUiActionRuntime.BuildCommandOperands(
                enabledAlternative.Kind,
                "choose",
                enabledAlternative.EntityBindings!);
        Assert.Equal("screen-card-reward", alternativeOperands["screen_id"]);
        Assert.Equal("alternative-enabled", alternativeOperands["choice_id"]);
        Assert.DoesNotContain("alternative_id", alternativeOperands.Keys);
    }

    [Fact]
    public void CardRewardWaitsForVisibleCardsToBecomeClickableBeforeReady()
    {
        Assert.Equal("settling", CardRewardSurfaceReader.ClassifyReadiness(3, 0, 1, 1));
        Assert.Equal("ready", CardRewardSurfaceReader.ClassifyReadiness(3, 3, 1, 1));
        Assert.Equal("ready", CardRewardSurfaceReader.ClassifyReadiness(0, 0, 1, 1));
        Assert.Equal("degraded", CardRewardSurfaceReader.ClassifyReadiness(0, 0, 0, 0));
    }

    [Fact]
    public void ShopInventoryNativeDiscoveryUsesTypedOffersAndExactOwnerOperands()
    {
        static VisibleCard Card(string entityId, string definitionId, string name) =>
            new(
                entityId,
                definitionId,
                name,
                "Attack",
                "1",
                null,
                null,
                "Common",
                false,
                false,
                null);

        var surface = new ShopInventorySurface(
            "shop_inventory",
            "shop-screen",
            new[]
            {
                new VisibleShopCardOffer(
                    "offer-card",
                    "slot-card",
                    0,
                    45,
                    true,
                    true,
                    true,
                    true,
                    null,
                    false,
                    Card("card-offer", "POMMEL_STRIKE", "Pommel Strike")),
                new VisibleShopCardOffer(
                    "offer-blocked",
                    "slot-blocked",
                    1,
                    70,
                    true,
                    true,
                    false,
                    false,
                    "insufficient_gold",
                    false,
                    Card("card-blocked", "SHRUG_IT_OFF", "Shrug It Off"))
            },
            new[]
            {
                new VisibleShopRelicOffer(
                    "offer-relic",
                    "slot-relic",
                    2,
                    100,
                    true,
                    true,
                    true,
                    true,
                    null,
                    new VisibleRelic(
                        "relic-offer",
                        "BAG_OF_PREPARATION",
                        "Bag of Preparation",
                        "Draw more cards.",
                        null,
                        Array.Empty<VisibleKeyword>(),
                        Array.Empty<VisibleCard>()))
            },
            new[]
            {
                new VisibleShopPotionOffer(
                    "offer-potion",
                    "slot-potion",
                    3,
                    50,
                    true,
                    true,
                    true,
                    true,
                    null,
                    "BLOCK_POTION",
                    "Block Potion",
                    "Gain Block.",
                    "Common")
            },
            new VisibleShopCardRemovalOffer(
                "offer-removal",
                "slot-removal",
                4,
                75,
                25,
                true,
                true,
                true,
                true,
                null),
            true);

        NativeUiActionDescriptor[] commands =
            NativeUiActionRuntime.DescribeShopInventoryCommands(surface).ToArray();

        Assert.Equal(5, commands.Length);
        Assert.DoesNotContain(commands, command => command.EntityBindings!.Any(binding =>
            binding.EntityId == "offer-blocked"));
        Assert.All(commands, command => Assert.Contains(
            command.EntityBindings!,
            binding => binding.Role == "screen"
                       && binding.EntityId == "shop-screen"));
        Assert.Contains(commands, command => command.Kind == "purchase_shop_card");
        Assert.Contains(commands, command => command.Kind == "purchase_shop_relic");
        Assert.Contains(commands, command => command.Kind == "purchase_shop_potion");
        Assert.Contains(commands, command => command.Kind == "open_shop_card_removal");
        Assert.Contains(commands, command => command.Kind == "close_shop_inventory");

        NativeUiActionDescriptor purchase = Assert.Single(commands, command =>
            command.Kind == "purchase_shop_card");
        Dictionary<string, string> operands =
            NativeUiActionRuntime.BuildCommandOperands(
                purchase.Kind,
                "purchase",
                purchase.EntityBindings!);
        Assert.Equal("shop-screen", operands["screen_id"]);
        Assert.Equal("offer-card", operands["shop_offer_id"]);
        Assert.DoesNotContain("action_id", operands.Keys);

        NativeUiActionDescriptor close = Assert.Single(commands, command =>
            command.Kind == "close_shop_inventory");
        Dictionary<string, string> closeOperands =
            NativeUiActionRuntime.BuildCommandOperands(
                close.Kind,
                "cancel_interaction",
                close.EntityBindings!);
        Assert.Equal("shop-screen", closeOperands["screen_id"]);
        Assert.Equal("close_shop_inventory", closeOperands["control_id"]);
    }

    [Fact]
    public void CombatHandDescriptorsUseExplicitActionableCardAndControlFacts()
    {
        var surface = new CombatHandCardSelectionSurface(
            "combat_hand_card_selection",
            "hand-current",
            "Choose cards",
            "simple_select",
            1,
            2,
            1,
            new[] { "card-selected" },
            RequireManualConfirmation: true,
            IsPeeking: false,
            SelectableCardEntityIds: new[] { "card-option" },
            DeselectableCardEntityIds: new[] { "card-selected" },
            CanConfirm: true,
            CanClosePeek: false,
            new[]
            {
                new VisibleCard(
                    "card-option", "STRIKE", "Strike", "Attack", "1", null,
                    "Deal 6 damage.", "Basic", false, false, null),
                new VisibleCard(
                    "card-selected", "DEFEND", "Defend", "Skill", "1", null,
                    "Gain 5 Block.", "Basic", false, true, null)
            });

        NativeUiActionDescriptor[] commands =
            NativeUiActionRuntime.DescribeCombatHandCommands(surface).ToArray();

        Assert.Equal(3, commands.Length);
        Assert.Contains(commands, command =>
            command.Kind == "select_combat_hand_card"
            && command.EntityBindings!.Single().EntityId == "card-option");
        Assert.Contains(commands, command =>
            command.Kind == "deselect_combat_hand_card"
            && command.EntityBindings!.Single().EntityId == "card-selected");
        Assert.Contains(commands, command =>
            command.Kind == "confirm_combat_hand_selection"
            && command.EntityBindings!.Count == 0);
        Assert.DoesNotContain(commands, command => command.Kind == "close_combat_hand_peek");
    }

    [Fact]
    public void ParameterizedBindingRequiresEveryExactOperandAndNoExtras()
    {
        var candidate = new NativeUiActionCandidate(
            "candidate-a",
            "play_card",
            "play_card",
            "Play",
            new Dictionary<string, string>
            {
                ["card_id"] = "card-session-1"
            },
            new Dictionary<string, NativeUiOperandDomain>
            {
                ["target_id"] = new(
                    "entity_ids",
                    new[] { "creature-session-2", "creature-session-3" })
            },
            Array.Empty<ActionEntityBinding>(),
            "native_ui_binding");

        Assert.True(NativeUiActionRuntime.OperandsMatch(
            candidate,
            new Dictionary<string, string>
            {
                ["card_id"] = "card-session-1",
                ["target_id"] = "creature-session-3"
            }));
        Assert.False(NativeUiActionRuntime.OperandsMatch(
            candidate,
            new Dictionary<string, string>
            {
                ["card_id"] = "card-session-1",
                ["target_id"] = "creature-replacement-3"
            }));
        Assert.False(NativeUiActionRuntime.OperandsMatch(
            candidate,
            new Dictionary<string, string>
            {
                ["card_id"] = "card-session-1",
                ["target_id"] = "creature-session-3",
                ["unexpected"] = "entity-session-4"
            }));
    }

    [Fact]
    public void TypedActionRegistryHasOneAdapterPerSupportedSurface()
    {
        Assert.Equal(new[]
        {
            "card_bundle_selection",
            "card_reward_selection",
            "character_select",
            "combat_hand_card_selection",
            "combat_turn",
            "deck_enchant_selection",
            "deck_transform_selection",
            "event_dialogue",
            "event_option",
            "game_over",
            "main_menu",
            "map_navigation",
            "potion_popup",
            "reward_claim",
            "shop_inventory",
            "shop_room",
            "singleplayer_menu",
            "treasure_room",
            "tutorial"
        }, NativeUiActionRuntime.DeclaredActionSurfaceKinds);
    }

    [Fact]
    public void RebindingCandidateOperandsRecomputesExactCandidateIdentity()
    {
        var candidate = new NativeUiActionCandidate(
            "stale-candidate",
            "select_entity",
            "select_combat_hand_card",
            "Select",
            new Dictionary<string, string> { ["card_id"] = "card-a" },
            new Dictionary<string, NativeUiOperandDomain>(),
            new[] { new ActionEntityBinding("card", "card-a") },
            "player_environment_native_binding");
        NativeUiActionCandidate first = NativeUiActionRuntime.RebindCandidate(
            candidate,
            new Dictionary<string, string>
            {
                ["card_id"] = "card-a",
                ["hand_id"] = "hand-a"
            },
            new[]
            {
                new ActionEntityBinding("card", "card-a"),
                new ActionEntityBinding("hand", "hand-a")
            });
        NativeUiActionCandidate second = NativeUiActionRuntime.RebindCandidate(
            candidate,
            new Dictionary<string, string>
            {
                ["card_id"] = "card-a",
                ["hand_id"] = "hand-b"
            },
            new[]
            {
                new ActionEntityBinding("card", "card-a"),
                new ActionEntityBinding("hand", "hand-b")
            });

        Assert.NotEqual(candidate.CandidateId, first.CandidateId);
        Assert.NotEqual(first.CandidateId, second.CandidateId);
        Assert.Equal(
            NativeUiActionRuntime.BuildCandidateId(
                first.Command,
                first.Operation,
                first.Operands),
            first.CandidateId);
    }

    [Fact]
    public void IdempotencyIdentityBindsInteractionCommandAndCanonicalOperands()
    {
        var operands = new Dictionary<string, string>
        {
            ["target_id"] = "creature-a",
            ["card_id"] = "card-a"
        };
        var reordered = new Dictionary<string, string>
        {
            ["card_id"] = "card-a",
            ["target_id"] = "creature-a"
        };

        string identity = NativeUiActionRuntime.BuildCommandIdentity(
            "interaction-a",
            "play_card",
            operands);

        Assert.Equal(
            identity,
            NativeUiActionRuntime.BuildCommandIdentity(
                "interaction-a",
                "play_card",
                reordered));
        Assert.NotEqual(
            identity,
            NativeUiActionRuntime.BuildCommandIdentity(
                "interaction-b",
                "play_card",
                operands));
        Assert.NotEqual(
            identity,
            NativeUiActionRuntime.BuildCommandIdentity(
                "interaction-a",
                "use_potion",
                operands));
    }
}
