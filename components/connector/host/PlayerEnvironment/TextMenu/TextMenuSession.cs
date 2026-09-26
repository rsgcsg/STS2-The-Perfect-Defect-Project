using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Presentation state, not strategy, memory or another legality engine.
/// Its only synthetic edges are the approved information index and seven lists.
/// The host supplies every native leaf and keeps execute-time native validation.</summary>
internal sealed class TextMenuSession
{
    internal static readonly IReadOnlyDictionary<string, string> Categories =
        new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["relic_inspect"] = "Inspect relics", ["relic_tips"] = "Relic tips",
            ["card_tips"] = "Card tips", ["power_tips"] = "Status tips",
            ["intent_tips"] = "Intent tips", ["orb_tips"] = "Orb tips",
            ["topbar_tips"] = "Top bar tips"
        };
    private string cursor = "root";
    private string? owner;
    private long revision;
    private long sequence;
    private string? lastSnapshotId;

    internal void ResetCursor()
    {
        if (cursor == "root") return;
        cursor = "root";
        revision++;
    }

    internal TextMenuProjection Observe(TextMenuFrame frame)
    {
        string currentOwner = StableIdentityHash.Object(new { frame.OwnerKey, frame.Page.Session });
        if (owner != currentOwner)
        {
            owner = currentOwner;
            cursor = "root";
            revision++;
        }
        ValidateFrame(frame);
        bool ready = frame.Page.Status == "interactive"
            && frame.Page.Completeness.Status == "complete";
        var leaves = ready ? frame.Leaves : Array.Empty<TextMenuLeaf>();
        bool hasInformation = leaves.Any(leaf => leaf.Group != "root");
        if (cursor != "root" && (!ready || !hasInformation
            || (cursor != "information" && !leaves.Any(leaf => leaf.Group == cursor))))
        {
            cursor = "root";
            revision++;
        }
        // The hash includes the source menu and current public content so a host
        // cannot accidentally retain authority by reusing a page token.
        string snapshotId = "text-" + StableIdentityHash.Object(new
        {
            profile = TextMenuContract.Profile, owner, revision, cursor,
            native = frame.Page.SnapshotId,
            frame.Page.Status, frame.Page.Persistent, frame.Page.Interaction,
            frame.Page.Referents, frame.Page.Completeness,
            catalog = leaves.Select(leaf => new
            { leaf.Key, leaf.Group, leaf.Verb, leaf.Label, leaf.SubjectReferentId, leaf.Arguments })
        });
        if (snapshotId != lastSnapshotId)
        {
            sequence++;
            lastSnapshotId = snapshotId;
        }
        var choices = new Dictionary<string, TextMenuChoice>(StringComparer.Ordinal);
        void Add(string key, string kind, string verb, string label,
            TextMenuLeaf? leaf, string? destination)
        {
            string id = "tm-" + StableIdentityHash.Object(new { snapshotId, key, kind });
            var action = new TextMenuAction(id, kind, verb, label,
                leaf?.SubjectReferentId,
                leaf?.Arguments ?? Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                kind == "system_navigation" ? "text_menu" : "native_input");
            choices.Add(id, new TextMenuChoice(action, leaf, destination));
        }
        foreach (TextMenuLeaf leaf in leaves.Where(leaf => leaf.Group == cursor))
            Add(leaf.Key, "native_input", leaf.Verb, leaf.Label, leaf, null);
        if (ready && cursor == "root" && hasInformation)
            Add("information", "system_navigation", "open_information", "Information", null, "information");
        if (ready && cursor == "information")
            foreach (var category in Categories)
                if (leaves.Any(leaf => leaf.Group == category.Key))
                    Add(category.Key, "system_navigation", "open_" + category.Key, category.Value, null, category.Key);
        if (ready && cursor != "root")
            Add("back", "system_navigation", "back", "Back", null,
                cursor == "information" ? "root" : "information");

        var page = frame.Page;
        bool interactive = ready && choices.Count > 0;
        var capabilities = choices.Values.GroupBy(choice => new
        {
            choice.Action.Verb,
            HasSubject = choice.Action.SubjectReferentId != null,
            Roles = string.Join("|", choice.Action.Arguments.Select(argument => argument.Role))
        }).Select(group => new PlayerEnvironmentInteractionCapability(group.Key.Verb,
            group.Key.HasSubject ? "subject" : null,
            group.First().Action.Arguments.Select(argument =>
                new PlayerEnvironmentCapabilityArgument(argument.Role, true)).ToArray(),
            "exact_current_text_menu")).ToArray();
        var snapshot = new TextMenuSnapshot(PlayerEnvironmentContract.ProtocolVersion,
            TextMenuContract.SnapshotSchema, TextMenuContract.Profile, snapshotId,
            sequence, page.ObservedAt,
            page.Status == "interactive" && !interactive ? "visible_unsupported" : page.Status,
            page.Persistent, page.Interaction with { Capabilities = capabilities },
            page.Referents, page.Completeness, page.Session, page.InformationPolicy,
            new TextMenuCursor(cursor, revision, page.SnapshotId),
            new TextMenuActionCatalog(interactive ? "complete" : "unavailable",
                choices.Count, choices.Count, "native_order_with_fixed_information_groups",
                choices.Values.Select(choice => choice.Action).ToArray()));
        return new TextMenuProjection(snapshot, choices);
    }

    internal TextMenuSnapshot Navigate(TextMenuFrame currentFrame, string expectedSnapshotId, string actionId)
    {
        TextMenuProjection current = Observe(currentFrame);
        if (current.Snapshot.SnapshotId != expectedSnapshotId
            || !current.Choices.TryGetValue(actionId, out TextMenuChoice? choice)
            || choice.TargetCursor == null || choice.Leaf != null)
            throw new InvalidOperationException("Text navigation is no longer current.");
        cursor = choice.TargetCursor;
        revision++;
        return Observe(currentFrame).Snapshot;
    }

    private static void ValidateFrame(TextMenuFrame frame)
    {
        if (string.IsNullOrWhiteSpace(frame.OwnerKey))
            throw new InvalidOperationException("Text page has no exact owner.");
        var keys = new HashSet<string>(StringComparer.Ordinal);
        var visible = frame.Page.Referents.Where(r => r.State.Visible)
            .Select(r => r.ReferentId).ToHashSet(StringComparer.Ordinal);
        foreach (TextMenuLeaf leaf in frame.Leaves)
        {
            if (string.IsNullOrWhiteSpace(leaf.Key) || !keys.Add(leaf.Key)
                || (leaf.Group != "root" && leaf.Group != "information" && !Categories.ContainsKey(leaf.Group))
                || (leaf.SubjectReferentId != null && !visible.Contains(leaf.SubjectReferentId))
                || leaf.Arguments.Any(argument => !visible.Contains(argument.ReferentId)))
                throw new InvalidOperationException("Text native leaf lacks a unique current public binding.");
        }
    }
}
