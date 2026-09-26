using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>The bundle preview's displayed cards have their own native Pressed
/// operation. This adds only those exact visible operations to the text page.</summary>
internal static class NativeTextMenuBundle
{
    internal static TextMenuFrame AppendPreviewInspection(
        TextMenuFrame frame, NativeEntityRegistry entities)
    {
        PlayerEnvironmentSnapshot page = frame.Page;
        if (page.Status != "interactive" || page.Completeness.Status != "complete"
            || page.Interaction.Kind != "card_bundle_selection"
            || page.Interaction.Stage != "preview")
            return frame;
        if (page.Interaction.Content.Surface is not JsonObject surface
            || !ReadString(surface["selected_bundle_entity_id"], out string? bundleId)
            || !ReadExactPublicCards(surface, bundleId!, out string[] cardIds)
            || NOverlayStack.Instance?.Peek() is not NChooseABundleSelectionScreen screen
            || !ActiveInputResolver.IsVisibleActiveOverlay(screen)
            || entities.GetId(screen, "screen") != page.Interaction.InteractionId
            || !entities.TryResolve(bundleId!, out NCardBundle? selected)
            || selected == null
            || !TryCurrentPreview(screen, selected, out Control? preview,
                out NPreviewCardHolder[] holders)
            || holders.Length != cardIds.Length)
            return UnresolvedPreview(frame);

        var referents = page.Referents.ToList();
        var added = new List<TextMenuLeaf>();
        for (int index = 0; index < holders.Length; index++)
        {
            NPreviewCardHolder holder = holders[index];
            CardModel? card = holder.CardNode?.Model;
            MegaLabel? title = holder.CardNode?.GetNodeOrNull<MegaLabel>("%TitleLabel");
            if (card == null || title == null || !ConnectorMod.IsNodeVisible(title)
                || entities.GetId(card, "card") != cardIds[index])
                return UnresolvedPreview(frame);

            string cardId = cardIds[index];
            if (!referents.Any(value => value.ReferentId == cardId))
                referents.Add(new PlayerEnvironmentReferent(cardId, "card", "entity",
                    title.Text, new PlayerEnvironmentReferentState(
                        true, true, false, false, "native_visible_fact"), null, null));
            else if (!referents.Any(value => value.ReferentId == cardId && value.State.Visible))
                return UnresolvedPreview(frame);

            NPreviewCardHolder exactHolder = holder;
            CardModel exactCard = card;
            int exactIndex = index;
            added.Add(new TextMenuLeaf("inspect_bundle_card:" + cardId, "root",
                "inspect_bundle_card", "Inspect " + title.Text, cardId,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                () => Inspect(screen, preview!, selected, exactHolder, exactCard,
                    exactIndex)));
        }
        return frame with
        {
            Page = page with { Referents = referents },
            Leaves = frame.Leaves.Concat(added).ToArray()
        };
    }

    private static TextMenuFrame UnresolvedPreview(TextMenuFrame frame)
    {
        PlayerEnvironmentSnapshot page = frame.Page;
        return frame with
        {
            Page = page with
            {
                Status = "settling",
                Completeness = page.Completeness with
                {
                    Status = "partial",
                    Missing = page.Completeness.Missing
                        .Append("current_native_bundle_preview_inspection_binding")
                        .Distinct(StringComparer.Ordinal).ToArray()
                }
            },
            Leaves = Array.Empty<TextMenuLeaf>()
        };
    }

    private static bool ReadString(JsonNode? node, out string? value)
    {
        value = null;
        return node is JsonValue text && text.TryGetValue(out value)
            && !string.IsNullOrWhiteSpace(value);
    }

    private static bool ReadExactPublicCards(
        JsonObject surface, string selectedId, out string[] cardIds)
    {
        cardIds = Array.Empty<string>();
        if (surface["bundles"] is not JsonArray { Count: 1 } bundles
            || bundles[0] is not JsonObject bundle
            || !ReadString(bundle["entity_id"], out string? id)
            || id != selectedId
            || bundle["cards"] is not JsonArray cards || cards.Count == 0)
            return false;
        var ids = new List<string>();
        foreach (JsonNode? node in cards)
        {
            if (node is not JsonObject card
                || !ReadString(card["entity_id"], out string? cardId)
                || ids.Contains(cardId!, StringComparer.Ordinal))
                return false;
            ids.Add(cardId!);
        }
        cardIds = ids.ToArray();
        return true;
    }

    private static bool TryCurrentPreview(
        NChooseABundleSelectionScreen screen, NCardBundle selected,
        out Control? preview, out NPreviewCardHolder[] holders)
    {
        preview = screen.GetNodeOrNull<Control>("%BundlePreviewContainer");
        Control? cards = screen.GetNodeOrNull<Control>("%Cards");
        holders = Array.Empty<NPreviewCardHolder>();
        if (!ReferenceEquals(NOverlayStack.Instance?.Peek(), screen)
            || !ActiveInputResolver.IsVisibleActiveOverlay(screen)
            || preview == null || !ConnectorMod.IsNodeVisible(preview)
            || cards == null || !ConnectorMod.IsNodeVisible(cards)
            || !ConnectorMod.FindAll<NCardBundle>(screen)
                .Any(bundle => ReferenceEquals(bundle, selected)))
            return false;
        NCardBundle? current;
        try
        {
            current = screen.Get(NChooseABundleSelectionScreen.PropertyName._selectedBundle)
                .As<NCardBundle>();
        }
        catch
        {
            return false;
        }
        if (!ReferenceEquals(current, selected)) return false;

        Node[] children = cards.GetChildren().ToArray();
        if (children.Length == 0 || children.Length != selected.Bundle.Count)
            return false;
        var exact = new List<NPreviewCardHolder>();
        for (int index = 0; index < children.Length; index++)
        {
            if (children[index] is not NPreviewCardHolder holder
                || !ConnectorMod.IsNodeVisible(holder)
                || holder.Hitbox is not { IsEnabled: true } hitbox
                || !ConnectorMod.IsNodeVisible(hitbox)
                || holder.CardNode?.Visibility != ModelVisibility.Visible
                || !ReferenceEquals(holder.CardNode.Model, selected.Bundle[index]))
                return false;
            exact.Add(holder);
        }
        holders = exact.ToArray();
        return true;
    }

    private static NativeInputResult Inspect(
        NChooseABundleSelectionScreen screen, Control preview,
        NCardBundle selected, NPreviewCardHolder holder,
        CardModel card, int index)
    {
        if (!TryCurrentPreview(screen, selected, out Control? currentPreview,
                out NPreviewCardHolder[] holders)
            || !ReferenceEquals(currentPreview, preview)
            || index >= holders.Length || !ReferenceEquals(holders[index], holder)
            || !ReferenceEquals(holder.CardNode?.Model, card))
            return NativeInputResult.Rejected("bundle_card_inspect_changed",
                "The exact displayed bundle card is no longer inspectable.");

        // NChooseABundleSelectionScreen connects this holder's native Pressed
        // signal to OpenPreviewScreen, which opens NInspectCardScreen.
        holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
        return NativeInputResult.Delivered("native_bundle_preview_card_holder_pressed");
    }
}
