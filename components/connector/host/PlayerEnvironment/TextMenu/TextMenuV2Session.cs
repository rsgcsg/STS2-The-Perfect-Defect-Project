using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed record TextMenuV2Choice(
    TextMenuAction Action, TextMenuLeaf? Leaf, string? Destination,
    string? CardId = null, string? TargetId = null);
internal sealed record TextMenuV2Projection(
    TextMenuV2Snapshot Snapshot, IReadOnlyDictionary<string, TextMenuV2Choice> Choices);

/// <summary>Only a text cursor and a currently bound card/target selection.
/// Native card-play state is neither read nor modified by these selections.</summary>
internal sealed class TextMenuV2Session
{
    private string cursor = "root";
    private string? cardId;
    private string? targetId;
    private string? sourceIdentity;
    private long revision;
    private long sequence;
    private string? lastSnapshotId;

    internal void ResetSelection()
    {
        if (cursor == "root" && cardId == null && targetId == null) return;
        cursor = "root";
        cardId = targetId = null;
        revision++;
    }

    internal TextMenuV2Projection Observe(TextMenuFrame frame)
    {
        string source = StableIdentityHash.Object(new
        {
            frame.OwnerKey, frame.Page.Session, frame.Page.SnapshotId,
            frame.Page.Status, frame.Page.Persistent, frame.Page.Interaction,
            frame.Page.Referents, frame.Page.Completeness,
            leaves = frame.Leaves.Select(leaf => new
            { leaf.Key, leaf.Group, leaf.Verb, leaf.SubjectReferentId, leaf.Arguments }),
            cardPlays = frame.CardPlays.Select(leaf => new
            { leaf.Key, leaf.Verb, leaf.SubjectReferentId, leaf.Arguments }),
            frame.CardPlayCatalogComplete
        });
        if (sourceIdentity != source)
        {
            sourceIdentity = source;
            ResetSelection();
            revision++;
        }

        bool combat = frame.Page.Interaction.Kind == "combat_turn"
            && frame.Page.Interaction.Stage == "ready"
            && frame.Page.Interaction.ContentSchema ==
                "sts2.player-environment/surface/combat_turn-1";
        bool ready = frame.Page.Status == "interactive"
            && frame.Page.Completeness.Status == "complete"
            && (!combat || frame.CardPlayCatalogComplete);
        Validate(frame, combat);
        if (!ready) ResetSelection();

        IReadOnlyList<TextMenuLeaf> leaves = ready ? frame.Leaves : Array.Empty<TextMenuLeaf>();
        bool hasInformation = leaves.Any(leaf => leaf.Group != "root");
        if (cursor is not ("root" or "card_targets" or "card_confirmation")
            && (!ready || !hasInformation
                || cursor != "information" && !leaves.Any(leaf => leaf.Group == cursor)))
            ResetSelection();

        var pairs = ready && combat ? frame.CardPlays : Array.Empty<TextMenuLeaf>();
        if (cardId != null && !pairs.Any(leaf => leaf.SubjectReferentId == cardId
            && (targetId == null || leaf.Arguments.Any(arg => arg.ReferentId == targetId))))
            ResetSelection();

        string snapshotId = "text2-" + StableIdentityHash.Object(new
        {
            profile = TextMenuV2Contract.Profile, source, cursor, cardId, targetId, revision
        });
        if (snapshotId != lastSnapshotId) { sequence++; lastSnapshotId = snapshotId; }
        var choices = new Dictionary<string, TextMenuV2Choice>(StringComparer.Ordinal);
        void Add(string key, string kind, string verb, string label, string? subject,
            IReadOnlyList<PlayerEnvironmentBoundActionArgument> arguments,
            TextMenuLeaf? leaf = null, string? destination = null,
            string? selectedCard = null, string? selectedTarget = null)
        {
            string id = "tm2-" + StableIdentityHash.Object(new { snapshotId, key, kind });
            var action = new TextMenuAction(id, kind, verb, label, subject, arguments,
                kind == "native_input" ? "native_input" : "text_menu");
            choices.Add(id, new TextMenuV2Choice(action, leaf, destination,
                selectedCard, selectedTarget));
        }
        if (ready && cursor == "root")
        {
            foreach (TextMenuLeaf leaf in leaves.Where(leaf => leaf.Group == "root"
                && !(combat && leaf.Verb == "begin_card_play")))
                Add(leaf.Key, "native_input", leaf.Verb, leaf.Label,
                    leaf.SubjectReferentId, leaf.Arguments, leaf);
            if (combat)
                foreach (var group in pairs.GroupBy(leaf => leaf.SubjectReferentId,
                    StringComparer.Ordinal))
                {
                    TextMenuLeaf first = group.First();
                    Add("select_card:" + group.Key, "system_selection", "select_card",
                        "Choose " + first.Label, group.Key,
                        Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                        selectedCard: group.Key);
                }
            if (hasInformation)
                Add("information", "system_navigation", "open_information",
                    "Information", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                    destination: "information");
        }
        else if (ready && cursor == "card_targets" && cardId != null)
        {
            foreach (TextMenuLeaf pair in pairs.Where(leaf => leaf.SubjectReferentId == cardId))
            {
                string target = pair.Arguments.Single().ReferentId;
                Add("select_target:" + target, "system_selection", "select_target",
                    "Choose target " + target, target,
                    Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                    selectedTarget: target);
            }
            Add("cancel_selection", "system_selection", "cancel_selection",
                "Cancel selection", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                destination: "root");
        }
        else if (ready && cursor == "card_confirmation" && cardId != null)
        {
            TextMenuLeaf pair = pairs.Single(leaf => leaf.SubjectReferentId == cardId
                && (targetId == null ? leaf.Arguments.Count == 0
                    : leaf.Arguments.Count == 1 && leaf.Arguments[0].ReferentId == targetId));
            Add(pair.Key, "native_input", "play", pair.Label, cardId, pair.Arguments, pair);
            Add("cancel_selection", "system_selection", "cancel_selection",
                "Cancel selection", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                destination: "root");
        }
        else if (ready && cursor == "information")
        {
            foreach (var category in TextMenuSession.Categories)
                if (leaves.Any(leaf => leaf.Group == category.Key))
                    Add(category.Key, "system_navigation", "open_" + category.Key,
                        category.Value, null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                        destination: category.Key);
            Add("back", "system_navigation", "back", "Back", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), destination: "root");
        }
        else if (ready)
        {
            foreach (TextMenuLeaf leaf in leaves.Where(leaf => leaf.Group == cursor))
                Add(leaf.Key, "native_input", leaf.Verb, leaf.Label,
                    leaf.SubjectReferentId, leaf.Arguments, leaf);
            Add("back", "system_navigation", "back", "Back", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), destination: "information");
        }

        bool interactive = ready && choices.Count > 0;
        var capabilities = choices.Values.GroupBy(choice => new
        {
            choice.Action.Verb,
            HasSubject = choice.Action.SubjectReferentId != null,
            Roles = string.Join("|", choice.Action.Arguments.Select(arg => arg.Role))
        }).Select(group => new PlayerEnvironmentInteractionCapability(
            group.Key.Verb, group.Key.HasSubject ? "subject" : null,
            group.First().Action.Arguments.Select(arg =>
                new PlayerEnvironmentCapabilityArgument(arg.Role, true)).ToArray(),
            "exact_current_text_menu")).ToArray();
        TextMenuV2Selection[] selection = cardId == null
            ? Array.Empty<TextMenuV2Selection>()
            : targetId == null ? new[] { new TextMenuV2Selection("card", cardId) }
            : new[] { new TextMenuV2Selection("card", cardId),
                new TextMenuV2Selection("target", targetId) };
        var page = frame.Page;
        var snapshot = new TextMenuV2Snapshot(PlayerEnvironmentContract.ProtocolVersion,
            TextMenuV2Contract.SnapshotSchema, TextMenuV2Contract.Profile, snapshotId,
            sequence, page.ObservedAt,
            page.Status == "interactive" && !interactive ? "visible_unsupported" : page.Status,
            page.Persistent, page.Interaction with { Capabilities = capabilities },
            page.Referents, page.Completeness, page.Session, page.InformationPolicy,
            new TextMenuV2Cursor(cursor, revision, page.SnapshotId, selection),
            new TextMenuActionCatalog(interactive ? "complete" : "unavailable",
                choices.Count, choices.Count, "native_order_with_text_card_selection",
                choices.Values.Select(choice => choice.Action).ToArray()));
        return new TextMenuV2Projection(snapshot, choices);
    }

    internal TextMenuV2Snapshot Apply(TextMenuFrame frame, string expectedSnapshotId,
        string actionId)
    {
        TextMenuV2Projection current = Observe(frame);
        if (current.Snapshot.SnapshotId != expectedSnapshotId
            || !current.Choices.TryGetValue(actionId, out TextMenuV2Choice? choice)
            || choice.Leaf != null)
            throw new InvalidOperationException("Text selection is no longer current.");
        if (choice.Action.Verb == "select_card")
        {
            cardId = choice.CardId;
            targetId = null;
            bool targeted = frame.CardPlays.Any(leaf => leaf.SubjectReferentId == cardId
                && leaf.Arguments.Count == 1);
            cursor = targeted ? "card_targets" : "card_confirmation";
        }
        else if (choice.Action.Verb == "select_target")
        {
            targetId = choice.TargetId;
            cursor = "card_confirmation";
        }
        else
        {
            cursor = choice.Destination ?? throw new InvalidOperationException(
                "Text choice has no destination.");
            cardId = targetId = null;
        }
        revision++;
        return Observe(frame).Snapshot;
    }

    private static void Validate(TextMenuFrame frame, bool combat)
    {
        if (string.IsNullOrWhiteSpace(frame.OwnerKey))
            throw new InvalidOperationException("Text page has no exact owner.");
        var visible = frame.Page.Referents.Where(item => item.State.Visible)
            .ToDictionary(item => item.ReferentId, StringComparer.Ordinal);
        var keys = new HashSet<string>(StringComparer.Ordinal);
        foreach (TextMenuLeaf leaf in frame.Leaves.Concat(frame.CardPlays))
            if (string.IsNullOrWhiteSpace(leaf.Key) || !keys.Add(leaf.Key)
                || leaf.SubjectReferentId != null && !visible.ContainsKey(leaf.SubjectReferentId)
                || leaf.Arguments.Any(arg => !visible.ContainsKey(arg.ReferentId)))
                throw new InvalidOperationException("Text leaf has no unique current public binding.");
        if (!combat || !frame.CardPlayCatalogComplete) return;
        var pairs = new HashSet<string>(StringComparer.Ordinal);
        var cardModes = new Dictionary<string, bool>(StringComparer.Ordinal);
        foreach (TextMenuLeaf leaf in frame.CardPlays)
        {
            if (leaf.Verb != "play" || leaf.SubjectReferentId is not { } card
                || visible[card].Role is not ("card" or "playable_card")
                || visible[card].State.Enabled == false || leaf.Arguments.Count > 1
                || leaf.Arguments.Any(arg => arg.Role != "target"
                    || visible[arg.ReferentId].Role is not
                        ("target" or "enemy" or "ally" or "creature" or "player" or "companion")
                    || visible[arg.ReferentId].State.Enabled == false))
                throw new InvalidOperationException("Card pair lacks a visible native binding.");
            bool targeted = leaf.Arguments.Count == 1;
            if (cardModes.TryGetValue(card, out bool prior) && prior != targeted)
                throw new InvalidOperationException("Card has mixed target modes.");
            cardModes[card] = targeted;
            if (!pairs.Add(card + "\0" + (targeted ? leaf.Arguments[0].ReferentId : "")))
                throw new InvalidOperationException("Duplicate card/target pair.");
        }
    }
}
