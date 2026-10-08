using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class NativeCardOperationBindingTests
{
    private static T Bare<T>() => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static CardModel Card() => (CardModel)RuntimeHelpers.GetUninitializedObject(
        typeof(CardModel).Assembly.GetTypes().First(type => !type.IsAbstract && type.IsSubclassOf(typeof(CardModel))));

    [Fact]
    public void MouseOriginRequiresSameHandHolderCardAndCancellationSource()
    {
        using var cancellation = new CancellationTokenSource();
        using var replacement = new CancellationTokenSource();
        var original = new NativeMouseCardConfirmation.Origin(Bare<NPlayerHand>(), Bare<NHandCardHolder>(), Card(), cancellation);
        Assert.True(NativeMouseCardConfirmation.SameLiveOrigin(original, original));
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original with { Hand = Bare<NPlayerHand>() }));
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original with { Holder = Bare<NHandCardHolder>() }));
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original with { Card = Card() }));
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original with { Cancellation = replacement }));
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original with { Cancellation = null }));
        // Start entry may precede the body's CTS allocation; its completed fact
        // binds that actual source and all later captures require exact identity.
        Assert.True(NativeMouseCardConfirmation.SameLiveOrigin(original with { Cancellation = null }, original));
        cancellation.Cancel();
        Assert.False(NativeMouseCardConfirmation.SameLiveOrigin(original, original));
    }

    [Theory]
    [InlineData(TargetMode.ReleaseMouseToTarget, true, true)]
    [InlineData(TargetMode.ReleaseMouseToTarget, false, false)]
    [InlineData(TargetMode.ClickMouseToTarget, false, true)]
    [InlineData(TargetMode.ClickMouseToTarget, true, false)]
    [InlineData((TargetMode)(-1), true, false)]
    public void OnlyActualModePendingTransitionCanOfferConfirmation(TargetMode mode, bool down, bool expected) =>
        Assert.Equal(expected, NativeMouseCardConfirmation.TransitionPending(mode, down));

    [Fact]
    public void InspectorRequiresCurrentOriginalListIndexNodeUpgradeAndRenderedModel()
    {
        CardModel original = Card(), other = Card(), display = Card();
        IReadOnlyList<CardModel> list = new[] { original, other };
        var fact = new NativeCardInspectionBinding.Fact(list, 0, original, Bare<NCard>(), display, Bare<NTickbox>(), false);
        Assert.True(NativeCardInspectionBinding.SameDisplay(fact, fact));
        foreach (var changed in new[]
        {
            fact with { List = new[] { original, other } }, fact with { Index = 1 },
            fact with { Original = other }, fact with { DisplayNode = Bare<NCard>() },
            fact with { Upgrade = Bare<NTickbox>() }, fact with { DisplayModel = Card() },
            fact with { Checked = true }
        }) Assert.False(NativeCardInspectionBinding.SameDisplay(fact, changed));
        // Equal card definition/text is never used to prove this identity relation.
        Assert.False(ReferenceEquals(original, display));
    }

    [Fact]
    public void InspectorCompletionRejectsTornOriginalAndPreviewTupleAndLateAttachCannotGuessRelation()
    {
        CardModel first = Card(), second = Card(), oldDisplay = Card(), newDisplay = Card();
        IReadOnlyList<CardModel> list = new[] { first, second };
        var entering = new NativeCardInspectionBinding.Fact(list, 1, second, Bare<NCard>(), oldDisplay, Bare<NTickbox>(), false);
        var returned = entering with { DisplayModel = newDisplay };
        Assert.True(NativeCardInspectionBinding.CanComplete(entering, returned));
        foreach (var invalid in new[]
        {
            returned with { Original = first }, returned with { Index = 0 },
            returned with { List = new[] { first, second } }, returned with { DisplayNode = Bare<NCard>() },
            returned with { Upgrade = Bare<NTickbox>() }, returned with { Checked = true },
            returned with { DisplayModel = null }, returned with { DisplayModel = second }
        }) Assert.False(NativeCardInspectionBinding.CanComplete(entering, invalid));
        Assert.Null(NativeCardInspectionBinding.Capture(Bare<NInspectCardScreen>(), entering.DisplayNode));
        Assert.False(NativeMouseCardConfirmation.KnownOperation(Bare<NMouseCardPlay>(), Bare<NPlayerHand>(), first));
    }

    [Fact]
    public void InspectorPublicRelationAddsExactlyCurrentTwoInformationalReferentsWithoutSourceRoster()
    {
        PlayerEnvironmentSnapshot source = new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
            DateTimeOffset.UnixEpoch, "observed", null,
            new("interaction", "game_over", "summary", null, "sts2.player-environment/surface/game_over-1", new(new JsonObject(), new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { new PlayerEnvironmentReferent("unopened-original", "card", "entity", "Same native title",
                new(true, false, false, false, "fixture_unopened_source"), null, null) },
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 65536, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new("complete", "fixture", "fixture", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        var legacy = NativeTextMenuInformation.ProjectCardInspectPage(source, "Native display", "1", "Rendered upgraded text", true);
        var native = NativeTextMenuInformation.ProjectCardInspectionRelation(legacy, "original-current", "display-current");
        Assert.Equal(2, native.Referents.Count);
        Assert.All(native.Referents, referent => Assert.False(referent.State.Enabled));
        Assert.Equal(new[] { "original-current", "display-current" }, native.Referents.Select(referent => referent.ReferentId));
        JsonObject surface = Assert.IsType<JsonObject>(native.Interaction.Content.Surface);
        Assert.Equal("original-current", surface["source_card_referent_id"]!.GetValue<string>());
        Assert.Equal("display-current", surface["display_card_referent_id"]!.GetValue<string>());
        Assert.Equal("Rendered upgraded text", surface["description"]!.GetValue<string>());
        Assert.Null(surface["cards"]); Assert.Null(surface["index"]); Assert.Null(surface["source_list"]);
        Assert.Null(legacy.Interaction.Content.Surface["source_card_referent_id"]); // Legacy and source remain detached.
        Assert.Equal("unopened-original", Assert.Single(source.Referents).ReferentId);
        string json = JsonSerializer.Serialize(native);
        Assert.DoesNotContain("unopened-original", json);
        // Use the actual frozen native catalog/Resolve path: informational
        // source/display referents do not become action subjects or operands.
        var frame = new NativeLogicalPublicFrame("generation", source.Session,
            new("inspect-owner", "1", "1", null, null), native.Status, native.Persistent,
            native.Interaction, native.Referents, native.InformationPolicy,
            new[] { new NativeLogicalLeaf("return_card_inspect", "Close", null, Array.Empty<NativeLogicalArgument>(), "native")
                { BindingKey = "current-inspect-control" } }, new("complete", Array.Empty<string>()));
        var frozen = new NativeLogicalProjector().Freeze(frame, NativeLogicalProjector.ScopeFields,
            "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
        Assert.Null(Assert.Single(frozen.Catalog.Actions).SubjectReferentId);
        Assert.Empty(Assert.Single(frozen.Catalog.Actions).Arguments);
        foreach (string id in new[] { "original-current", "display-current" })
        {
            var expression = JsonSerializer.SerializeToElement(new { verb = "play", subject_referent_id = id,
                arguments = Array.Empty<NativeLogicalArgument>() }, NativeLogicalWire.Options);
            Assert.Equal("no_match", frozen.Catalog.Resolve("generation", expression).Status);
        }
    }
}
