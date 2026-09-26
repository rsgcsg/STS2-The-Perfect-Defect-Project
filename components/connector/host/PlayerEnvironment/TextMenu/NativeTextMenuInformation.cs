using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Orbs;
using MegaCrit.Sts2.Core.Nodes.HoverTips;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.InspectScreens;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.TopBar;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed record NativeTextMenuInformationLeaf(
    string Key,
    string Group,
    string Verb,
    string Label,
    string? SubjectReferentId,
    IReadOnlyList<PlayerEnvironmentBoundActionArgument> Arguments,
    Func<NativeInputResult> Dispatch);

internal sealed record NativeTextMenuInformationCapture(
    PlayerEnvironmentSnapshot Page,
    string OwnerKey,
    IReadOnlyList<NativeTextMenuInformationLeaf> Leaves);

/// <summary>
/// A narrow adapter for native information pages. The only retained operand is
/// the exact screen opened through a visible player control. All reads and
/// returns check that the same screen still owns native input.
/// </summary>
internal static class NativeTextMenuInformation
{
    private static object? _ownedScreen;
    private static string? _ownedKind;
    private static ILiveContext? _openedContext;
    private static bool _returnPending;
    private static NRelicInventoryHolder? _tipOwner;
    private static JsonNode? _tipContent;
    private static Control? _nativeTipOwner;
    private static Control? _nativeTipSource;
    private static string? _nativeTipGroup;
    private static bool _unresolvedTipSignal;
    private static readonly FieldInfo? ActiveHoverTipsField = typeof(NHoverTipSet)
        .GetField("_activeHoverTips", BindingFlags.Static | BindingFlags.NonPublic);

    internal static NativeTextMenuInformationCapture Capture(
        SnapshotBuildResult legacy,
        NativeEntityRegistry entities)
    {
        legacy = legacy with { Snapshot = SanitizePage(legacy.Snapshot) };
        if (_unresolvedTipSignal)
        {
            if (ActiveHoverTipsField?.GetValue(null) is
                    Dictionary<Control, NHoverTipSet> unresolved
                && unresolved.Values.Any(ConnectorMod.IsNodeVisible))
                return FailClosedPage(legacy, RootOwnerKey(legacy),
                    "native_tip_owner_unresolved");
            _unresolvedTipSignal = false;
        }
        if (_ownedScreen != null && !IsExactOwner(_ownedScreen, _ownedKind!))
            ClearOwner();
        // A tooltip merely discovered in the native hover registry is not an
        // input-owning page. Older captures may still hold that passive owner.
        if (_ownedKind == "native_tip")
            ClearOwner();
        if (_ownedScreen == null)
            RecognizeCurrentNativePage();
        if (_unresolvedTipSignal)
            return FailClosedPage(legacy, RootOwnerKey(legacy),
                "native_tip_owner_unresolved");
        if (_ownedScreen != null)
            return CaptureOwned(legacy, entities);

        var leaves = new List<NativeTextMenuInformationLeaf>();
        AddDeckOpen(legacy, leaves);
        AddPileOpen(legacy, PileType.Draw, leaves);
        AddPileOpen(legacy, PileType.Discard, leaves);
        AddPileOpen(legacy, PileType.Exhaust, leaves);
        AddMapOpen(leaves);
        AddRelicInspectOpen(entities, leaves);
        AddRelicTipsOpen(entities, leaves);
        AddOtherTipLeaves(entities, leaves);
        return new NativeTextMenuInformationCapture(
            legacy.Snapshot,
            RootOwnerKey(legacy),
            leaves);
    }

    internal static PlayerEnvironmentSnapshot SanitizePage(
        PlayerEnvironmentSnapshot snapshot)
    {
        snapshot = snapshot with
        {
            Referents = snapshot.Referents.Select(SanitizeReferent).ToArray()
        };
        if (snapshot.Persistent == null) return snapshot;
        PersistentVisibleState? state;
        try
        {
            state = snapshot.Persistent.Content.Deserialize<PersistentVisibleState>(
                ConnectorMod._jsonOptions);
        }
        catch (JsonException)
        {
            state = null;
        }
        if (state == null)
            return snapshot with { Persistent = null };
        PersistentVisibleState hud = state with
        {
            Run = state.Run with
            {
                Bosses = Array.Empty<VisibleBoss>(),
                Modifiers = state.Run.Modifiers.Select(modifier => modifier with
                {
                    Description = null,
                    Keywords = Array.Empty<VisibleKeyword>(),
                    CardPreviews = Array.Empty<VisibleCard>()
                }).ToArray()
            },
            Player = state.Player with
            {
                Relics = state.Player.Relics.Select(relic => relic with
                {
                    Description = null,
                    Keywords = Array.Empty<VisibleKeyword>(),
                    CardPreviews = Array.Empty<VisibleCard>()
                }).ToArray(),
                Potions = state.Player.Potions.Select(potion => potion with
                {
                    Description = null,
                    Keywords = Array.Empty<VisibleKeyword>(),
                    CardPreviews = Array.Empty<VisibleCard>()
                }).ToArray()
            }
        };
        JsonNode content = JsonSerializer.SerializeToNode(hud, ConnectorMod._jsonOptions)
            ?? new JsonObject();
        if (content is JsonObject hudObject
            && hudObject["run"] is JsonObject runObject)
            runObject["boss_icons"] = CaptureShownBossIcons();
        return snapshot with
        {
            Persistent = snapshot.Persistent with
            {
                Content = content
            }
        };
    }

    private static PlayerEnvironmentReferent SanitizeReferent(
        PlayerEnvironmentReferent referent)
    {
        if (referent.Properties is not JsonObject properties
            || !(referent.Role.Contains("relic", StringComparison.OrdinalIgnoreCase)
                 || referent.Role.Contains("potion", StringComparison.OrdinalIgnoreCase)
                 || referent.Role.Contains("modifier", StringComparison.OrdinalIgnoreCase)))
            return referent;
        JsonObject visible = (JsonObject)properties.DeepClone();
        StripUnfocusedTips(visible);
        return referent with { Properties = visible };
    }

    private static void StripUnfocusedTips(JsonNode node)
    {
        if (node is JsonObject obj)
        {
            obj.Remove("description");
            obj.Remove("keywords");
            obj.Remove("card_previews");
            obj.Remove("hover_tips");
            foreach (JsonNode? child in obj.Select(pair => pair.Value).ToArray())
                if (child != null) StripUnfocusedTips(child);
        }
        else if (node is JsonArray array)
        {
            foreach (JsonNode? child in array)
                if (child != null) StripUnfocusedTips(child);
        }
    }

    private static JsonArray CaptureShownBossIcons()
    {
        var icons = new JsonArray();
        Control? boss = NRun.Instance?.GlobalUi.TopBar.BossIcon;
        if (boss == null || !ConnectorMod.IsNodeVisible(boss)) return icons;
        TextureRect? primary = boss.GetNodeOrNull<TextureRect>("Icon");
        if (primary == null || !ConnectorMod.IsNodeVisible(primary)) return icons;
        if (primary.Texture != null)
            icons.Add(new JsonObject
            {
                ["slot"] = "primary",
                ["rendered_texture_path"] = primary.Texture.ResourcePath
            });
        foreach (TextureRect child in primary.GetChildren().OfType<TextureRect>())
        {
            if (child.Name == "Outline" || !ConnectorMod.IsNodeVisible(child)
                || child.Texture == null) continue;
            icons.Add(new JsonObject
            {
                ["slot"] = "secondary",
                ["rendered_texture_path"] = child.Texture.ResourcePath
            });
        }
        return icons;
    }

    private static void RecognizeCurrentNativePage()
    {
        NInspectCardScreen? inspectCard = CurrentInspectCard();
        if (inspectCard != null)
        {
            _ownedScreen = inspectCard;
            _ownedKind = "inspect_card";
            return;
        }
        NInspectRelicScreen? inspect = NGame.Instance?.InspectRelicScreen;
        if (inspect != null && ConnectorMod.IsNodeVisible(inspect)
            && ActiveScreenContext.Instance.IsCurrent(inspect))
        {
            _ownedScreen = inspect;
            _ownedKind = "relic_inspect";
            return;
        }
        NMapScreen? map = NMapScreen.Instance;
        if (map?.IsOpen == true && ActiveScreenContext.Instance.IsCurrent(map))
        {
            _ownedScreen = map;
            _ownedKind = "native_map";
            return;
        }
        object? capstone = NCapstoneContainer.Instance?.CurrentCapstoneScreen;
        if (capstone is NDeckViewScreen deck
            && ActiveScreenContext.Instance.IsCurrent(deck))
        {
            _ownedScreen = deck;
            _ownedKind = "run_deck";
        }
        else if (capstone is NCardPileScreen pile
                 && ActiveScreenContext.Instance.IsCurrent(pile))
        {
            _ownedScreen = pile;
            _ownedKind = pile.Pile.Type switch
            {
                PileType.Draw => "combat_draw_pile",
                PileType.Discard => "combat_discard_pile",
                PileType.Exhaust => "combat_exhaust_pile",
                _ => null
            };
            if (_ownedKind == null) ClearOwner();
        }
    }

    private static NInspectCardScreen? CurrentInspectCard()
    {
        Node? root = NGame.Instance?.GetTree()?.Root;
        if (root == null) return null;
        NInspectCardScreen[] screens = ConnectorMod.FindAll<NInspectCardScreen>(root)
            .Where(screen => ConnectorMod.IsLiveNode(screen)
                && ConnectorMod.IsNodeVisible(screen)
                && ActiveScreenContext.Instance.IsCurrent(screen)).ToArray();
        if (screens.Length > 1)
            throw new InvalidOperationException("Ambiguous native card inspection owner.");
        return screens.SingleOrDefault();
    }

    private static NativeTextMenuInformationCapture CaptureOwned(
        SnapshotBuildResult legacy,
        NativeEntityRegistry entities)
    {
        object screen = _ownedScreen!;
        string kind = _ownedKind!;
        string key = $"native_information:{legacy.Snapshot.Session.RuntimeInstanceId}:{kind}:{entities.GetId(screen, "native_page")}";
        if (_returnPending)
        {
            if (screen is NInspectRelicScreen relic && !ConnectorMod.IsNodeVisible(relic)
                || screen is NInspectCardScreen card && !ConnectorMod.IsNodeVisible(card))
            {
                ClearOwner();
                return Capture(legacy, entities);
            }
            return FailClosedPage(legacy, key, "native_information_return_pending");
        }
        if (!IsExactOwner(screen, kind))
            return FailClosedPage(legacy, key, "native_information_owner_changed");

        if (screen is NMapScreen)
        {
            if (legacy.HostObservation.Surface is not MapNavigationSurface)
                return FailClosedPage(legacy, key, "native_map_content_unresolved");
            NBackButton? back = ((NMapScreen)screen).GetNodeOrNull<NBackButton>("Back");
            bool backAvailable = back is { IsEnabled: true }
                && ConnectorMod.IsNodeVisible(back);
            bool mapCatalogComplete = CanPublishMapPage(
                legacy.Snapshot.Status,
                legacy.Snapshot.BoundActions.Status,
                legacy.Snapshot.BoundActions.MaterializedCount,
                legacy.Snapshot.BoundActions.TotalCount,
                legacy.HostObservation.Readiness,
                legacy.HostObservation.Completeness,
                (MapNavigationSurface)legacy.HostObservation.Surface,
                backAvailable);
            PlayerEnvironmentSnapshot mapPage = legacy.Snapshot with
            {
                Status = mapCatalogComplete ? "interactive" : "settling",
                Referents = PlayerEnvironmentService.ProjectFactReferents(
                    legacy.Snapshot.Interaction.Content.Surface).Values.ToArray(),
                Completeness = legacy.Snapshot.Completeness with
                {
                    Status = mapCatalogComplete ? "complete" : "partial",
                    InteractionDiscovery = "current_native_map_travel_and_back"
                },
                Interaction = legacy.Snapshot.Interaction with
                {
                    Kind = "native_map",
                    Stage = "native_information_page",
                    Content = legacy.Snapshot.Interaction.Content with
                    { Context = new JsonObject { ["kind"] = "native_map" } }
                }
            };
            return new NativeTextMenuInformationCapture(mapPage, key,
                backAvailable
                    ? new[] { Leaf("return_native_map", "root", "return_native_map",
                        "Close map", () => ReturnMap((NMapScreen)screen, back)) }
                    : Array.Empty<NativeTextMenuInformationLeaf>());
        }
        if (screen is NInspectRelicScreen relicScreen)
            return CaptureRelic(legacy, relicScreen, key);
        if (screen is NInspectCardScreen cardScreen)
            return CaptureCardInspect(legacy, cardScreen, key);
        if (screen is NHoverTipSet)
            return CaptureTip(legacy, key);

        PlayerReadBuildResult read = PlayerVisibleReadBuilder.Build(
            kind == "run_deck"
                ? PlayerVisibleReadBuilder.RunDeckKind
                : PlayerVisibleReadBuilder.CombatPilesKind,
            _openedContext ?? legacy.HostObservation.Context,
            entities);
        if (read.Draft == null)
            return FailClosedPage(legacy, key,
                read.ErrorCode ?? "native_information_read_unavailable");

        object selectedContent = read.Draft.Content;
        if (kind != "run_deck")
        {
            // The combat Read is a three-pile aggregate. An entered native
            // pile page publishes only its own semantic contents.
            string zone = kind switch
            {
                "combat_draw_pile" => "draw",
                "combat_discard_pile" => "discard",
                _ => "exhaust"
            };
            CombatPileReadZone? selected =
                read.Draft.Content is CombatPilesReadContent pileRead
                    ? pileRead.Zones.FirstOrDefault(entry => entry.Zone == zone)
                    : null;
            if (selected == null)
                return FailClosedPage(legacy, key, "native_information_zone_missing");
            selectedContent = selected;
        }
        JsonNode details = JsonSerializer.SerializeToNode(
            selectedContent, selectedContent.GetType(), ConnectorMod._jsonOptions)
            ?? new JsonObject();
        var content = new JsonObject
        {
            ["kind"] = kind,
            ["details"] = details
        };

        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Status = "interactive",
            Referents = PlayerEnvironmentService.ProjectFactReferents(content)
                .Values.ToArray(),
            Completeness = new PlayerEnvironmentCompleteness(
                "complete",
                "complete_for_entered_native_information_page",
                "complete_for_current_native_information_return",
                Array.Empty<string>(),
                PlayerVisibleReadBuilder.HiddenByPolicyFor(
                    kind == "run_deck"
                        ? PlayerVisibleReadBuilder.RunDeckKind
                        : PlayerVisibleReadBuilder.CombatPilesKind)),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = kind,
                Stage = "native_information_page",
                Prompt = kind.Replace('_', ' '),
                ContentSchema = $"sts2.player-environment/surface/{kind}_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(
                    content,
                    new JsonObject { ["kind"] = kind }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        var pageLeaves = new List<NativeTextMenuInformationLeaf>();
        NButton? capstoneBack = screen is Node capstone
            ? capstone.GetNodeOrNull<NButton>("BackButton") : null;
        if (capstoneBack is { IsEnabled: true }
            && ConnectorMod.IsNodeVisible(capstoneBack))
            pageLeaves.Add(Leaf("return_native_information", "root",
                "return_native_information", "Return from information page",
                () => ReturnCapstone(screen, kind, capstoneBack)));
        if (screen is NDeckViewScreen deck)
        {
            var visible = page.Referents.ToList();
            AddDeckCardInspectLeaves(deck, entities, pageLeaves, visible);
            page = page with { Referents = visible };
        }
        return new NativeTextMenuInformationCapture(page, key, pageLeaves);
    }

    internal static bool CanPublishMapPage(
        string snapshotStatus,
        string projectionStatus,
        int materializedCount,
        long totalCount,
        string nativeReadiness,
        StateCompleteness nativeCompleteness,
        MapNavigationSurface map,
        bool nativeBackAvailable)
    {
        if (projectionStatus != "complete")
            return false;
        if (snapshotStatus == "interactive")
            return true;
        // The map reader deliberately settles when it has no route or
        // annotation command. Its separately enabled native Back control is
        // still a complete current-page action in that exact empty state.
        return nativeBackAvailable
            && snapshotStatus == "settling"
            && nativeReadiness == "settling"
            && nativeCompleteness.PlayerVisibleSemantics
                == "contract_complete_for_visible_singleplayer_map_navigation"
            && nativeCompleteness.InteractionDiscovery
                == "temporarily_empty_while_map_input_is_not_route_ready"
            && nativeCompleteness.Missing.Count == 0
            && map.NextOptions.Count == 0
            && !map.CanExitAnnotation
            && materializedCount == 0
            && totalCount == 0;
    }

    private static NativeTextMenuInformationCapture FailClosedPage(
        SnapshotBuildResult legacy, string key, string reason)
    {
        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = "native_information_unresolved",
                Stage = "unresolved",
                Prompt = reason,
                ContentSchema = "sts2.player-environment/surface/native_information_unresolved-1",
                Content = new PlayerEnvironmentInteractionContent(
                    new JsonObject { ["kind"] = "native_information_unresolved",
                        ["reason"] = reason },
                    new JsonObject { ["kind"] = "native_information_unresolved" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        return new NativeTextMenuInformationCapture(page, key,
            Array.Empty<NativeTextMenuInformationLeaf>());
    }

    private static void AddDeckOpen(
        SnapshotBuildResult legacy,
        List<NativeTextMenuInformationLeaf> leaves)
    {
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        Player? player = run == null ? null : LocalContext.GetMe(run);
        NTopBarDeckButton? button = NRun.Instance?.GlobalUi.TopBar.Deck;
        if (player == null || !CanOpen(button)) return;
        leaves.Add(Leaf("open_run_deck", "information", "open_run_deck",
            "Open deck", () => OpenDeck(button!, legacy.HostObservation.Context)));
    }

    private static void AddPileOpen(
        SnapshotBuildResult legacy,
        PileType type,
        List<NativeTextMenuInformationLeaf> leaves)
    {
        if (!CombatManager.Instance.IsInProgress) return;
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        Player? player = run == null ? null : LocalContext.GetMe(run);
        PlayerCombatState? combat = player?.PlayerCombatState;
        NCombatRoom? room = NCombatRoom.Instance;
        NCombatCardPile? button = type switch
        {
            PileType.Draw => room?.Ui.DrawPile,
            PileType.Discard => room?.Ui.DiscardPile,
            PileType.Exhaust => room?.Ui.ExhaustPile,
            _ => null
        };
        CardPile? pile = type switch
        {
            PileType.Draw => combat?.DrawPile,
            PileType.Discard => combat?.DiscardPile,
            PileType.Exhaust => combat?.ExhaustPile,
            _ => null
        };
        if (pile == null || !CanOpen(button)) return;
        string zone = type switch
        {
            PileType.Draw => "draw",
            PileType.Discard => "discard",
            _ => "exhaust"
        };
        leaves.Add(Leaf($"open_combat_{zone}_pile", "root",
            $"open_combat_{zone}_pile", $"Open {zone} pile",
            () => OpenPile(button!, pile, type, legacy.HostObservation.Context)));
    }

    private static void AddMapOpen(List<NativeTextMenuInformationLeaf> leaves)
    {
        NTopBarMapButton? button = NRun.Instance?.GlobalUi.TopBar.Map;
        NMapScreen? screen = NMapScreen.Instance;
        if (button == null || screen == null || screen.IsOpen
            || !button.IsEnabled || !ConnectorMod.IsNodeVisible(button)
            || NOverlayStack.Instance?.Peek() != null
            || NCapstoneContainer.Instance is { InUse: true }) return;
        leaves.Add(Leaf("open_native_map", "information", "open_native_map",
            "Open map", () => OpenMap(button, screen)));
    }

    private static NativeInputResult OpenMap(NTopBarMapButton button, NMapScreen screen)
    {
        if (!ReferenceEquals(NRun.Instance?.GlobalUi.TopBar.Map, button)
            || !ReferenceEquals(NMapScreen.Instance, screen)
            || screen.IsOpen || !button.IsEnabled
            || !ConnectorMod.IsNodeVisible(button)
            || NOverlayStack.Instance?.Peek() != null
            || NCapstoneContainer.Instance is { InUse: true })
            return NativeInputResult.Rejected("native_map_control_changed",
                "The exact Map control is no longer openable.");
        button.ForceClick();
        _ownedScreen = screen;
        _ownedKind = "native_map";
        if (!screen.IsOpen || !ActiveScreenContext.Instance.IsCurrent(screen))
            return NativeInputResult.Delivered("NTopBarMapButton.ForceClick; map owner unresolved");
        return NativeInputResult.Delivered("NTopBarMapButton.ForceClick; exact Map owner");
    }

    private static void AddRelicInspectOpen(
        NativeEntityRegistry entities,
        List<NativeTextMenuInformationLeaf> leaves)
    {
        NRelicInventory? inventory = NRun.Instance?.GlobalUi.RelicInventory;
        if (inventory == null || !ConnectorMod.IsNodeVisible(inventory)
            || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true
            || NCapstoneContainer.Instance is { InUse: true }) return;
        foreach (NRelicInventoryHolder holder in inventory.RelicNodes)
        {
            if (!ReferenceEquals(holder.Inventory, inventory)
                || holder.Relic?.Model == null
                || !holder.IsEnabled || !ConnectorMod.IsNodeVisible(holder)) continue;
            string id = entities.GetId(holder, "relic_holder");
            string label = holder.Relic.Model.Title.GetFormattedText();
            leaves.Add(Leaf($"inspect_relic:{id}", "relic_inspect",
                "inspect_relic", $"Inspect {label}",
                () => OpenRelic(holder, inventory)));
        }
    }

    private static void AddRelicTipsOpen(
        NativeEntityRegistry entities,
        List<NativeTextMenuInformationLeaf> leaves)
    {
        NRelicInventory? inventory = NRun.Instance?.GlobalUi.RelicInventory;
        if (inventory == null || !ConnectorMod.IsNodeVisible(inventory)
            || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true
            || NCapstoneContainer.Instance is { InUse: true }) return;
        foreach (NRelicInventoryHolder holder in inventory.RelicNodes)
        {
            if (!ReferenceEquals(holder.Inventory, inventory)
                || holder.Relic?.Model == null
                || !holder.IsEnabled || !ConnectorMod.IsNodeVisible(holder)) continue;
            IHoverTip[] tips = holder.Relic.Model.HoverTips.ToArray();
            if (tips.Length == 0) continue;
            string id = entities.GetId(holder, "relic_holder");
            leaves.Add(Leaf($"show_relic_tips:{id}", "relic_tips",
                "show_relic_tips", $"Show {holder.Relic.Model.Title.GetFormattedText()} tips",
                () => OpenRelicTips(holder, inventory)));
        }
    }

    private static NativeInputResult OpenRelicTips(
        NRelicInventoryHolder holder, NRelicInventory inventory)
    {
        if (!ReferenceEquals(NRun.Instance?.GlobalUi.RelicInventory, inventory)
            || !inventory.RelicNodes.Contains(holder)
            || !ReferenceEquals(holder.Inventory, inventory)
            || holder.Relic?.Model == null
            || !holder.IsEnabled || !ConnectorMod.IsNodeVisible(holder)
            || !ConnectorMod.IsNodeVisible(inventory)
            || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true
            || NCapstoneContainer.Instance is { InUse: true })
            return NativeInputResult.Rejected("native_relic_tip_owner_changed",
                "The exact relic holder is no longer current and enabled.");
        IHoverTip[] tips = holder.Relic.Model.HoverTips.ToArray();
        if (tips.Length == 0)
            return NativeInputResult.Rejected("native_relic_tip_unavailable",
                "The current relic has no native hover tips.");
        NHoverTipSet.Remove(holder);
        NHoverTipSet? set = NHoverTipSet.CreateAndShow(holder, tips);
        if (set == null || !ConnectorMod.IsNodeVisible(set))
            return NativeInputResult.Delivered(
                "NHoverTipSet.CreateAndShow; visible tip owner unresolved");
        _ownedScreen = set;
        _ownedKind = "relic_tips";
        _tipOwner = holder;
        _tipContent = ReadRenderedTips(set);
        return NativeInputResult.Delivered("NHoverTipSet.CreateAndShow; exact relic tip set");
    }

    private static NativeTextMenuInformationCapture CaptureTip(
        SnapshotBuildResult legacy, string key)
    {
        if (_tipContent == null)
            return FailClosedPage(legacy, key, "native_tip_content_unresolved");
        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Status = "interactive",
            Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Completeness = new PlayerEnvironmentCompleteness(
                "complete", "complete_for_current_native_hover_tip",
                "complete_for_current_native_tip_return",
                Array.Empty<string>(), Array.Empty<string>()),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = _nativeTipGroup ?? _ownedKind ?? "native_tip",
                Stage = "native_information_page",
                Prompt = "Native tips",
                ContentSchema = $"sts2.player-environment/surface/{_nativeTipGroup ?? _ownedKind ?? "native_tip"}_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(
                    new JsonObject { ["kind"] = "native_tips",
                        ["tips"] = _tipContent.DeepClone() },
                    new JsonObject { ["kind"] = "native_tips" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        return new NativeTextMenuInformationCapture(page, key,
            new[] { Leaf("return_native_tips", "root", "return_native_tips",
                "Close tips", () => Return(_ownedScreen!, _ownedKind!)) });
    }

    private static void AddOtherTipLeaves(
        NativeEntityRegistry entities, List<NativeTextMenuInformationLeaf> leaves)
    {
        if (ActiveHoverTipsField == null || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true) return;
        NCombatRoom? room = NCombatRoom.Instance;
        Node? cardRoot = NCapstoneContainer.Instance?.CurrentCapstoneScreen as Node
            ?? room;
        if (cardRoot != null)
        {
            foreach (NCardHolder holder in VisibleNodes<NCardHolder>(cardRoot))
            {
                if (holder.CardNode?.Visibility != ModelVisibility.Visible
                    || holder.CardModel == null)
                    continue;
                AddSignalTipLeaf(entities, leaves, holder, "card_tips",
                    Control.SignalName.FocusEntered, "Card tips");
            }
        }
        if (room != null && ConnectorMod.IsNodeVisible(room)
            && NCapstoneContainer.Instance is not { InUse: true })
        {
            foreach (NPower power in VisibleNodes<NPower>(room))
            {
                AddSignalTipLeaf(entities, leaves, power, "power_tips",
                    Control.SignalName.MouseEntered, "Power tips");
            }
            foreach (NIntent intent in VisibleNodes<NIntent>(room))
                AddSignalTipLeaf(entities, leaves, intent, "intent_tips",
                    Control.SignalName.MouseEntered, "Intent tips");
            foreach (NOrb orb in VisibleNodes<NOrb>(room))
            {
                AddSignalTipLeaf(entities, leaves, orb, "orb_tips",
                    Control.SignalName.FocusEntered, "Orb tips");
            }
        }
        NTopBar? topbar = NRun.Instance?.GlobalUi.TopBar;
        if (topbar != null && ConnectorMod.IsNodeVisible(topbar)
            && NCapstoneContainer.Instance is not { InUse: true })
        {
            foreach (Control control in new Control[]
                     { topbar.Deck, topbar.Map, topbar.FloorIcon,
                         topbar.BossIcon, topbar.Gold, topbar.Hp })
                AddSignalTipLeaf(entities, leaves, control, "topbar_tips",
                    control is NClickableControl
                        ? Control.SignalName.FocusEntered
                        : Control.SignalName.MouseEntered,
                    "Top bar tips");
        }
    }

    private static IEnumerable<T> VisibleNodes<T>(Node root) where T : Control
    {
        var queue = new Queue<Node>();
        queue.Enqueue(root);
        int visited = 0;
        while (queue.Count != 0 && visited++ < 2048)
        {
            Node node = queue.Dequeue();
            if (node is T typed && ConnectorMod.IsNodeVisible(typed))
                yield return typed;
            foreach (Node child in node.GetChildren()) queue.Enqueue(child);
        }
        if (queue.Count != 0)
            throw new InvalidOperationException(
                "Native tip source tree exceeded the bounded complete scan.");
    }

    private static void AddSignalTipLeaf(
        NativeEntityRegistry entities, List<NativeTextMenuInformationLeaf> leaves,
        Control source, string group, StringName signal, string label)
    {
        if (!ConnectorMod.IsNodeVisible(source)) return;
        if (source is NClickableControl clickable && !clickable.IsEnabled) return;
        string id = entities.GetId(source, "tip_source");
        leaves.Add(Leaf($"show_{group}:{id}", group, $"show_{group}", label,
            () => OpenSignalTip(source, group, signal)));
    }

    private static NativeInputResult OpenSignalTip(
        Control source, string group, StringName signal)
    {
        if (!ConnectorMod.IsNodeVisible(source)
            || source is NClickableControl { IsEnabled: false }
            || ActiveHoverTipsField?.GetValue(null) is not
                Dictionary<Control, NHoverTipSet> active
            || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true)
            return NativeInputResult.Rejected("native_tip_owner_changed",
                "The exact visible tip source or native tip registry is unavailable.");
        var before = active.ToDictionary(pair => pair.Key, pair => pair.Value);
        source.EmitSignal(signal);
        var changed = active.Where(pair =>
            !before.TryGetValue(pair.Key, out NHoverTipSet? previous)
            || !ReferenceEquals(previous, pair.Value)).ToArray();
        if (changed.Length != 1)
        {
            _unresolvedTipSignal = true;
            return NativeInputResult.Delivered(
                "native focus/hover signal; exact tip owner unresolved");
        }
        (Control owner, NHoverTipSet set) = (changed[0].Key, changed[0].Value);
        if (!ConnectorMod.IsNodeVisible(set)
            || !ReferenceEquals(set.GetParent(), NGame.Instance?.HoverTipsContainer))
        {
            _unresolvedTipSignal = true;
            return NativeInputResult.Delivered(
                "native focus/hover signal; visible tip unresolved");
        }
        _ownedScreen = set;
        _ownedKind = group;
        _nativeTipOwner = owner;
        _nativeTipSource = source;
        _nativeTipGroup = group;
        _tipContent = ReadRenderedTips(set);
        return NativeInputResult.Delivered("native focus/hover signal; exact rendered tip set");
    }

    private static JsonNode? ReadRenderedTips(NHoverTipSet set)
    {
        Node? textContainer = set.GetNodeOrNull<Node>("textHoverTipContainer");
        Node? cardContainer = set.GetNodeOrNull<Node>("cardHoverTipContainer");
        if (textContainer == null || cardContainer == null) return null;
        var texts = new JsonArray();
        foreach (Node entry in textContainer.GetChildren())
        {
            if (entry is not Control visibleText
                || !ConnectorMod.IsNodeVisible(visibleText))
                return null;
            var title = entry.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%Title");
            var description = entry.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%Description");
            if (description == null) return null;
            texts.Add(new JsonObject
            {
                ["title"] = title != null && ConnectorMod.IsNodeVisible(title)
                    ? title.Text : null,
                ["description"] = description.Text
            });
        }
        var cards = new JsonArray();
        foreach (Node entry in cardContainer.GetChildren())
        {
            if (entry is not Control control || !ConnectorMod.IsNodeVisible(control))
                return null;
            NCard? card = control.GetNodeOrNull<NCard>("%Card");
            if (card == null || !ConnectorMod.IsNodeVisible(card)
                || card.Visibility != ModelVisibility.Visible)
                return null;
            var title = card.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%TitleLabel");
            var cost = card.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%EnergyLabel");
            var description = card.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%DescriptionLabel");
            if (title == null || cost == null || description == null)
                return null;
            cards.Add(new JsonObject
            {
                ["title"] = title.Text,
                ["cost"] = cost.Text,
                ["description"] = description.Text
            });
        }
        return texts.Count + cards.Count > 0
            ? new JsonObject { ["text_tips"] = texts, ["card_previews"] = cards }
            : null;
    }

    private static (IReadOnlyList<JsonNode> Rendered, int Unresolved) ReadPassiveHoverFacts()
    {
        if (ActiveHoverTipsField?.GetValue(null) is not
                Dictionary<Control, NHoverTipSet> active
            || NGame.Instance?.HoverTipsContainer is not { } container)
            return (Array.Empty<JsonNode>(), 0);
        var rendered = new List<JsonNode>();
        int unresolved = 0;
        foreach ((Control owner, NHoverTipSet set) in active)
        {
            if (!ConnectorMod.IsNodeVisible(owner)
                || !ConnectorMod.IsNodeVisible(set)
                || !ReferenceEquals(set.GetParent(), container))
                continue;
            JsonNode? facts = ReadRenderedTips(set);
            if (facts == null) unresolved++;
            else rendered.Add(facts);
        }
        return (rendered, unresolved);
    }

    internal static PlayerEnvironmentSnapshot AttachCurrentPassiveHoverFacts(
        PlayerEnvironmentSnapshot page)
    {
        if (_ownedScreen is NHoverTipSet explicitTip)
        {
            if (_ownedKind != null && _ownedKind != "native_tip"
                && IsExactOwner(explicitTip, _ownedKind))
                return page;
            ClearOwner();
        }
        return AttachPassiveHoverFacts(page, ReadPassiveHoverFacts());
    }

    internal static PlayerEnvironmentSnapshot AttachPassiveHoverFacts(
        PlayerEnvironmentSnapshot page,
        (IReadOnlyList<JsonNode> Rendered, int Unresolved) facts)
    {
        if (facts.Rendered.Count == 0 && facts.Unresolved == 0
            || page.Interaction.Content.Surface is not JsonObject source)
            return page;
        var surface = (JsonObject)source.DeepClone();
        // Tooltip nodes are nonmodal: retain the underlying native interaction
        // and its exact action catalog while publishing only rendered text.
        surface["visible_hover_tips"] = new JsonArray(facts.Rendered
            .OrderBy(tip => tip.ToJsonString(), StringComparer.Ordinal)
            .Select(tip => tip.DeepClone()).ToArray());
        if (facts.Unresolved > 0)
            surface["unresolved_visible_hover_tip_count"] = facts.Unresolved;
        return page with
        {
            Interaction = page.Interaction with
            {
                Content = page.Interaction.Content with { Surface = surface }
            }
        };
    }

    private static NativeInputResult OpenRelic(
        NRelicInventoryHolder holder, NRelicInventory inventory)
    {
        if (!ReferenceEquals(NRun.Instance?.GlobalUi.RelicInventory, inventory)
            || !inventory.RelicNodes.Contains(holder)
            || !ReferenceEquals(holder.Inventory, inventory)
            || holder.Relic?.Model == null
            || !holder.IsEnabled || !ConnectorMod.IsNodeVisible(holder)
            || !ConnectorMod.IsNodeVisible(inventory)
            || NOverlayStack.Instance?.Peek() != null
            || NMapScreen.Instance?.IsOpen == true
            || NCapstoneContainer.Instance is { InUse: true })
            return NativeInputResult.Rejected("native_relic_control_changed",
                "The exact relic holder is no longer current and enabled.");
        holder.ForceClick();
        NInspectRelicScreen? screen = NGame.Instance?.InspectRelicScreen;
        if (screen != null)
        {
            _ownedScreen = screen;
            _ownedKind = "relic_inspect";
        }
        if (screen == null || !ConnectorMod.IsNodeVisible(screen)
            || !ActiveScreenContext.Instance.IsCurrent(screen))
            return NativeInputResult.Delivered("NRelicInventoryHolder.ForceClick; inspect owner unresolved");
        return NativeInputResult.Delivered("NRelicInventoryHolder.ForceClick; exact InspectRelic owner");
    }

    private static NativeTextMenuInformationCapture CaptureRelic(
        SnapshotBuildResult legacy, NInspectRelicScreen screen, string key)
    {
        // Read the text actually rendered by native's unlocked/seen logic.
        // The underlying RelicModel may contain facts the screen hides.
        string title = screen.GetNode<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%RelicName").Text;
        string rarity = screen.GetNode<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%Rarity").Text;
        string description = screen.GetNode<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%RelicDescription").Text;
        string flavor = screen.GetNode<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%FlavorText").Text;
        NGoldArrowButton? left = screen.GetNodeOrNull<NGoldArrowButton>("LeftArrow");
        NGoldArrowButton? right = screen.GetNodeOrNull<NGoldArrowButton>("RightArrow");
        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Status = "interactive",
            Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Completeness = new PlayerEnvironmentCompleteness(
                "complete", "complete_for_current_native_relic_inspect_text",
                "complete_for_current_native_relic_inspect_return",
                Array.Empty<string>(), Array.Empty<string>()),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = "relic_inspect", Stage = "native_information_page",
                Prompt = title,
                ContentSchema = "sts2.player-environment/surface/relic_inspect_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(
                    new JsonObject { ["kind"] = "relic_inspect",
                        ["title"] = title, ["rarity"] = rarity,
                        ["description"] = description, ["flavor"] = flavor },
                    new JsonObject { ["kind"] = "relic_inspect" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        var leaves = new List<NativeTextMenuInformationLeaf>
        {
            Leaf("return_relic_inspect", "root", "return_relic_inspect",
                "Close relic inspection", () => Return(screen, "relic_inspect"))
        };
        if (left is { IsEnabled: true } && ConnectorMod.IsNodeVisible(left))
            leaves.Add(Leaf("previous_relic", "root", "previous_relic",
                "Previous relic", () => ClickRelicArrow(screen, left, "LeftArrow")));
        if (right is { IsEnabled: true } && ConnectorMod.IsNodeVisible(right))
            leaves.Add(Leaf("next_relic", "root", "next_relic",
                "Next relic", () => ClickRelicArrow(screen, right, "RightArrow")));
        return new NativeTextMenuInformationCapture(page, key, leaves);
    }

    private static NativeInputResult ClickRelicArrow(
        NInspectRelicScreen screen, NGoldArrowButton arrow, string path)
    {
        if (!ReferenceEquals(_ownedScreen, screen)
            || !IsExactOwner(screen, "relic_inspect")
            || !ReferenceEquals(screen.GetNodeOrNull<NGoldArrowButton>(path), arrow)
            || !arrow.IsEnabled || !ConnectorMod.IsNodeVisible(arrow))
            return NativeInputResult.Rejected("relic_inspect_arrow_changed",
                "The native relic inspection arrow is no longer enabled.");
        arrow.ForceClick();
        return NativeInputResult.Delivered("native_relic_inspect_arrow_clicked");
    }

    private static void AddDeckCardInspectLeaves(
        NDeckViewScreen deck, NativeEntityRegistry entities,
        List<NativeTextMenuInformationLeaf> leaves,
        List<PlayerEnvironmentReferent> referents)
    {
        NCardGrid? grid = deck.GetNodeOrNull<NCardGrid>("CardGrid");
        if (grid == null || !IsExactOwner(deck, "run_deck")) return;
        foreach (NGridCardHolder holder in grid.CurrentlyDisplayedCardHolders)
        {
            CardModel? card = holder.CardModel;
            if (card == null || !ConnectorMod.IsLiveNode(holder)
                || !ConnectorMod.IsNodeVisible(holder)
                || holder.CardNode?.Visibility != ModelVisibility.Visible)
                continue;
            string id = entities.GetId(card, "card");
            if (!referents.Any(value => value.ReferentId == id))
                referents.Add(new PlayerEnvironmentReferent(id, "card", "entity",
                    holder.CardNode.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%TitleLabel")?.Text,
                    new PlayerEnvironmentReferentState(true, true, false, false,
                        "native_visible_fact"), null, null));
            NGridCardHolder exactHolder = holder;
            CardModel exactCard = card;
            leaves.Add(new NativeTextMenuInformationLeaf(
                "inspect_deck_card:" + id, "root", "inspect_deck_card",
                "Inspect " + (referents.First(value => value.ReferentId == id).Label
                    ?? "card"), id,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                () => OpenDeckCardInspect(deck, grid, exactHolder, exactCard)));
        }
    }

    private static NativeInputResult OpenDeckCardInspect(
        NDeckViewScreen deck, NCardGrid grid,
        NGridCardHolder holder, CardModel card)
    {
        if (!IsExactOwner(deck, "run_deck")
            || !ReferenceEquals(deck.GetNodeOrNull<NCardGrid>("CardGrid"), grid)
            || !grid.CurrentlyDisplayedCardHolders.Contains(holder)
            || !ReferenceEquals(holder.CardModel, card)
            || !ConnectorMod.IsLiveNode(holder)
            || !ConnectorMod.IsNodeVisible(holder)
            || holder.CardNode?.Visibility != ModelVisibility.Visible)
            return NativeInputResult.Rejected("deck_card_holder_changed",
                "The exact native deck card holder is no longer displayed.");
        // Native NCardGrid receives the holder's Pressed signal, then
        // NDeckViewScreen receives HolderPressed and opens NInspectCardScreen.
        holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
        return NativeInputResult.Delivered("native_deck_card_holder_pressed");
    }

    private static NativeTextMenuInformationCapture CaptureCardInspect(
        SnapshotBuildResult legacy, NInspectCardScreen screen, string key)
    {
        NCard? card = screen.GetNodeOrNull<NCard>("Card");
        var title = card?.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%TitleLabel");
        var cost = card?.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%EnergyLabel");
        var description = card?.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%DescriptionLabel");
        NButton? left = screen.GetNodeOrNull<NButton>("LeftArrow");
        NButton? right = screen.GetNodeOrNull<NButton>("RightArrow");
        NTickbox? upgrade = screen.GetNodeOrNull<NTickbox>("%Upgrade");
        if (card == null || card.Visibility != ModelVisibility.Visible
            || title == null || cost == null || description == null
            || upgrade == null)
            return FailClosedPage(legacy, key, "native_card_inspect_display_unresolved");
        var surface = new JsonObject
        {
            ["kind"] = "inspect_card",
            ["title"] = title.Text,
            ["cost"] = cost.Text,
            ["description"] = description.Text,
            ["upgrade_preview_checked"] = upgrade.IsTicked
        };
        PlayerEnvironmentSnapshot page = legacy.Snapshot with
        {
            Status = "interactive",
            Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Completeness = new PlayerEnvironmentCompleteness("complete",
                "current_native_card_inspection_display",
                "exact_native_card_inspection_controls",
                Array.Empty<string>(), Array.Empty<string>()),
            Interaction = legacy.Snapshot.Interaction with
            {
                Kind = "inspect_card", Stage = "native_information_page",
                Prompt = title.Text,
                ContentSchema = "sts2.player-environment/surface/inspect_card_text_menu-1",
                Content = new PlayerEnvironmentInteractionContent(surface,
                    new JsonObject { ["kind"] = "inspect_card" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        var leaves = new List<NativeTextMenuInformationLeaf>
        {
            Leaf("return_card_inspect", "root", "return_card_inspect",
                "Close card inspection", () => Return(screen, "inspect_card"))
        };
        if (left is { IsEnabled: true } && ConnectorMod.IsNodeVisible(left))
            leaves.Add(Leaf("previous_inspect_card", "root", "previous_inspect_card",
                "Previous card", () => ClickCardInspectControl(screen, left, "LeftArrow")));
        if (right is { IsEnabled: true } && ConnectorMod.IsNodeVisible(right))
            leaves.Add(Leaf("next_inspect_card", "root", "next_inspect_card",
                "Next card", () => ClickCardInspectControl(screen, right, "RightArrow")));
        if (upgrade.IsEnabled && ConnectorMod.IsNodeVisible(upgrade))
            leaves.Add(Leaf("toggle_card_upgrade_preview", "root",
                "toggle_card_upgrade_preview", "Toggle upgrade preview",
                () => ClickCardInspectControl(screen, upgrade, "%Upgrade")));
        return new NativeTextMenuInformationCapture(page, key, leaves);
    }

    private static NativeInputResult ClickCardInspectControl(
        NInspectCardScreen screen, NButton button, string path)
    {
        if (!ReferenceEquals(_ownedScreen, screen)
            || !IsExactOwner(screen, "inspect_card")
            || !ReferenceEquals(screen.GetNodeOrNull<NButton>(path), button)
            || !button.IsEnabled || !ConnectorMod.IsNodeVisible(button))
            return NativeInputResult.Rejected("card_inspect_control_changed",
                "The exact native card inspection control is no longer enabled.");
        button.ForceClick();
        return NativeInputResult.Delivered("native_card_inspect_control_clicked");
    }

    private static bool CanOpen(NTopBarDeckButton? button) =>
        button != null && button.IsEnabled
        && ConnectorMod.IsNodeVisible(button)
        && NCapstoneContainer.Instance is { InUse: false }
        && NOverlayStack.Instance?.Peek() == null;

    private static bool CanOpen(NCombatCardPile? button) =>
        button != null && button.IsEnabled
        && ConnectorMod.IsNodeVisible(button)
        && NCapstoneContainer.Instance is { InUse: false }
        && NOverlayStack.Instance?.Peek() == null;

    private static NativeInputResult OpenDeck(
        NTopBarDeckButton button, ILiveContext context)
    {
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        if (run == null || LocalContext.GetMe(run) == null
            || !ReferenceEquals(NRun.Instance?.GlobalUi.TopBar.Deck, button)
            || !CanOpen(button))
            return NativeInputResult.Rejected("native_information_control_changed",
                "The exact visible Deck control is no longer available.");
        button.ForceClick();
        NDeckViewScreen? screen = NCapstoneContainer.Instance?.CurrentCapstoneScreen as NDeckViewScreen;
        if (screen != null)
        {
            _ownedScreen = screen;
            _ownedKind = "run_deck";
            _openedContext = context;
        }
        if (screen == null || !ActiveScreenContext.Instance.IsCurrent(screen))
            return NativeInputResult.Delivered(
                "NTopBarDeckButton.ForceClick; Deck owner unresolved");
        return NativeInputResult.Delivered("NTopBarDeckButton.ForceClick; exact Deck owner");
    }

    private static NativeInputResult OpenPile(
        NCombatCardPile button, CardPile pile, PileType type,
        ILiveContext context)
    {
        NCombatRoom? room = NCombatRoom.Instance;
        NCombatCardPile? currentButton = type switch
        {
            PileType.Draw => room?.Ui.DrawPile,
            PileType.Discard => room?.Ui.DiscardPile,
            PileType.Exhaust => room?.Ui.ExhaustPile,
            _ => null
        };
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        Player? player = run == null ? null : LocalContext.GetMe(run);
        CardPile? currentPile = type switch
        {
            PileType.Draw => player?.PlayerCombatState?.DrawPile,
            PileType.Discard => player?.PlayerCombatState?.DiscardPile,
            PileType.Exhaust => player?.PlayerCombatState?.ExhaustPile,
            _ => null
        };
        if (!CombatManager.Instance.IsInProgress
            || !ReferenceEquals(button, currentButton)
            || !ReferenceEquals(pile, currentPile)
            || !CanOpen(button))
            return NativeInputResult.Rejected("native_information_control_changed",
                "The exact visible combat pile control is no longer available.");
        button.ForceClick();
        NCardPileScreen? screen = NCapstoneContainer.Instance?.CurrentCapstoneScreen as NCardPileScreen;
        if (screen != null)
        {
            _ownedScreen = screen;
            _ownedKind = type switch
            {
                PileType.Draw => "combat_draw_pile",
                PileType.Discard => "combat_discard_pile",
                _ => "combat_exhaust_pile"
            };
            _openedContext = context;
        }
        if (screen == null || !ReferenceEquals(screen.Pile, pile)
            || !ActiveScreenContext.Instance.IsCurrent(screen))
            return NativeInputResult.Delivered(
                "NCombatCardPile.ForceClick; pile owner unresolved");
        return NativeInputResult.Delivered("NCombatCardPile.ForceClick; exact pile owner");
    }

    private static NativeInputResult Return(object screen, string kind)
    {
        if (!ReferenceEquals(_ownedScreen, screen) || !IsExactOwner(screen, kind))
            return NativeInputResult.Rejected("native_information_owner_changed",
                "Return requires the exact current native information page.");
        if (screen is NInspectRelicScreen relic)
        {
            relic.Close();
            _returnPending = true;
            return NativeInputResult.Delivered("NInspectRelicScreen.Close; native close animation pending");
        }
        if (screen is NInspectCardScreen inspectCard)
        {
            inspectCard.Close();
            _returnPending = true;
            return NativeInputResult.Delivered("NInspectCardScreen.Close; native close animation pending");
        }
        if (screen is NHoverTipSet && (_nativeTipOwner ?? _tipOwner) is { } tipOwner)
        {
            NHoverTipSet.Remove(tipOwner);
            ClearOwner();
            return NativeInputResult.Delivered("NHoverTipSet.Remove; exact relic tip closed");
        }
        NCapstoneContainer.Instance!.Close();
        if (ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, screen))
            return NativeInputResult.Delivered("NCapstoneContainer.Close; release pending");
        ClearOwner();
        return NativeInputResult.Delivered("NCapstoneContainer.Close; exact page released");
    }

    private static NativeInputResult ReturnMap(NMapScreen map, NBackButton back)
    {
        if (!ReferenceEquals(_ownedScreen, map)
            || !IsExactOwner(map, "native_map")
            || !ReferenceEquals(map.GetNodeOrNull<NBackButton>("Back"), back)
            || !back.IsEnabled || !ConnectorMod.IsNodeVisible(back))
            return NativeInputResult.Rejected("native_map_back_changed",
                "The native map Back control is not currently enabled.");
        back.ForceClick();
        if (!map.IsOpen) ClearOwner();
        return NativeInputResult.Delivered("native_map_back_button_clicked");
    }

    private static NativeInputResult ReturnCapstone(
        object screen, string kind, NButton back)
    {
        if (!ReferenceEquals(_ownedScreen, screen) || !IsExactOwner(screen, kind)
            || screen is not Node node
            || !ReferenceEquals(node.GetNodeOrNull<NButton>("BackButton"), back)
            || !back.IsEnabled || !ConnectorMod.IsNodeVisible(back))
            return NativeInputResult.Rejected("native_information_back_changed",
                "The exact native information Back control is unavailable.");
        back.ForceClick();
        if (!ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, screen))
            ClearOwner();
        return NativeInputResult.Delivered("native_information_back_button_clicked");
    }

    private static void ClearOwner()
    {
        _ownedScreen = null;
        _ownedKind = null;
        _openedContext = null;
        _returnPending = false;
        _tipOwner = null;
        _tipContent = null;
        _nativeTipOwner = null;
        _nativeTipSource = null;
        _nativeTipGroup = null;
        _unresolvedTipSignal = false;
    }

    private static bool IsExactOwner(object screen, string kind) =>
        screen switch
        {
            NDeckViewScreen deck => kind == "run_deck"
                && ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, deck)
                && ActiveScreenContext.Instance.IsCurrent(deck),
            NCardPileScreen pile => kind switch
            {
                "combat_draw_pile" => pile.Pile.Type == PileType.Draw,
                "combat_discard_pile" => pile.Pile.Type == PileType.Discard,
                "combat_exhaust_pile" => pile.Pile.Type == PileType.Exhaust,
                _ => false
            } && ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, pile)
              && ActiveScreenContext.Instance.IsCurrent(pile),
            NMapScreen map => kind == "native_map" && map.IsOpen
                && ActiveScreenContext.Instance.IsCurrent(map),
            NInspectRelicScreen relic => kind == "relic_inspect"
                && ConnectorMod.IsNodeVisible(relic)
                && ActiveScreenContext.Instance.IsCurrent(relic),
            NInspectCardScreen card => kind == "inspect_card"
                && ConnectorMod.IsNodeVisible(card)
                && ActiveScreenContext.Instance.IsCurrent(card),
            NHoverTipSet tip => kind is "native_tip" or "relic_tips" or "card_tips"
                    or "power_tips" or "intent_tips" or "orb_tips"
                    or "topbar_tips"
                && (_nativeTipOwner ?? _tipOwner) is { } owner
                && ConnectorMod.IsNodeVisible(owner)
                && ConnectorMod.IsNodeVisible(tip)
                && ReferenceEquals(tip.GetParent(), NGame.Instance?.HoverTipsContainer)
                && ActiveHoverTipsField?.GetValue(null) is
                    Dictionary<Control, NHoverTipSet> active
                && active.TryGetValue(owner, out NHoverTipSet? current)
                && ReferenceEquals(current, tip),
            _ => false
        };

    private static string RootOwnerKey(SnapshotBuildResult legacy)
    {
        object? nativeOwner = NOverlayStack.Instance?.Peek()
            ?? NCapstoneContainer.Instance?.CurrentCapstoneScreen
            ?? NCombatRoom.Instance
            ?? (object?)RunManager.Instance.DebugOnlyGetState()?.CurrentRoom
            ?? NRun.Instance;
        return $"native_information_root:{legacy.Snapshot.Session.RuntimeInstanceId}:"
            + (nativeOwner == null ? "unresolved"
                : $"{nativeOwner.GetType().Name}:{RuntimeHelpers.GetHashCode(nativeOwner)}");
    }

    private static NativeTextMenuInformationLeaf Leaf(
        string key, string group, string verb, string label,
        Func<NativeInputResult> dispatch) =>
        new(key, group, verb, label, null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch);
}
