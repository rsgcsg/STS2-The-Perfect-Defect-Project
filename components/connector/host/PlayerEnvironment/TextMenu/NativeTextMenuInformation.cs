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
        if (_ownedScreen == null && ActiveHoverTipsField?.GetValue(null) is
                Dictionary<Control, NHoverTipSet> tips)
        {
            var shown = tips.Where(pair => ConnectorMod.IsNodeVisible(pair.Key)
                && ConnectorMod.IsNodeVisible(pair.Value)
                && ReferenceEquals(pair.Value.GetParent(),
                    NGame.Instance?.HoverTipsContainer)).ToArray();
            if (shown.Length > 1)
                _unresolvedTipSignal = true;
            else if (shown.Length == 1)
            {
                _ownedScreen = shown[0].Value;
                _ownedKind = "native_tip";
                _nativeTipOwner = shown[0].Key;
                _tipContent = ReadRenderedTips(shown[0].Value);
            }
        }
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
            if (screen is NInspectRelicScreen relic && !ConnectorMod.IsNodeVisible(relic))
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
            PlayerEnvironmentSnapshot mapPage = legacy.Snapshot with
            {
                Status = "interactive",
                Referents = PlayerEnvironmentService.ProjectFactReferents(
                    legacy.Snapshot.Interaction.Content.Surface).Values.ToArray(),
                Completeness = legacy.Snapshot.Completeness with
                {
                    Status = "complete",
                    InteractionDiscovery = "complete_for_current_native_map_return"
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
                new[] { Leaf("return_native_map", "root", "return_native_map",
                    "Close map", () => Return(screen, kind)) });
        }
        if (screen is NInspectRelicScreen relicScreen)
            return CaptureRelic(legacy, relicScreen, key);
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
        return new NativeTextMenuInformationCapture(page, key,
            new[] { Leaf("return_native_information", "root",
                "return_native_information", "Return from information page",
                () => Return(screen, kind)) });
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
            if (tips.Length == 0 || tips.Any(tip => tip is not HoverTip)) continue;
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
        if (tips.Length == 0 || tips.Any(tip => tip is not HoverTip))
            return NativeInputResult.Rejected("native_relic_tip_unavailable",
                "The current relic tips cannot be rendered as a complete text page.");
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
                Kind = _nativeTipGroup ?? "relic_tips",
                Stage = "native_information_page",
                Prompt = "Native tips",
                ContentSchema = $"sts2.player-environment/surface/{_nativeTipGroup ?? "native_tip"}_text_menu-1",
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
                    || holder.CardModel == null
                    || holder.CardModel.HoverTips.Any(tip => tip is not HoverTip))
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
                if (power.Model.HoverTips.Any(tip => tip is not HoverTip)) continue;
                AddSignalTipLeaf(entities, leaves, power, "power_tips",
                    Control.SignalName.MouseEntered, "Power tips");
            }
            foreach (NIntent intent in VisibleNodes<NIntent>(room))
                AddSignalTipLeaf(entities, leaves, intent, "intent_tips",
                    Control.SignalName.MouseEntered, "Intent tips");
            foreach (NOrb orb in VisibleNodes<NOrb>(room))
            {
                if (orb.Model?.HoverTips.Any(tip => tip is not HoverTip) == true)
                    continue;
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
        if (textContainer == null || cardContainer == null
            || cardContainer.GetChildCount() > 0) return null;
        var texts = new JsonArray();
        foreach (Node entry in textContainer.GetChildren())
        {
            var title = entry.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaLabel>("%Title");
            var description = entry.GetNodeOrNull<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%Description");
            if (description == null) return null;
            texts.Add(new JsonObject
            {
                ["title"] = title?.Text,
                ["description"] = description.Text
            });
        }
        return texts.Count > 0 ? texts : null;
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
        string description = screen.GetNode<MegaCrit.Sts2.addons.mega_text.MegaRichTextLabel>("%RelicDescription").Text;
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
                        ["title"] = title, ["description"] = description },
                    new JsonObject { ["kind"] = "relic_inspect" }),
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        return new NativeTextMenuInformationCapture(page, key,
            new[] { Leaf("return_relic_inspect", "root", "return_relic_inspect",
                "Close relic inspection", () => Return(screen, "relic_inspect")) });
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
        if (screen is NMapScreen map)
        {
            map.Close();
            ClearOwner();
            return NativeInputResult.Delivered("NMapScreen.Close; exact page released");
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
