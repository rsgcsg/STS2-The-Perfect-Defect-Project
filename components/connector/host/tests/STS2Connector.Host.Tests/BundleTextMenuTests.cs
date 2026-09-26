using System;
using System.Text.Json.Nodes;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class BundleTextMenuTests
{
    [Theory]
    [InlineData("combat_turn", "preview", "interactive", "complete")]
    [InlineData("card_bundle_selection", "choosing", "interactive", "complete")]
    [InlineData("card_bundle_selection", "preview", "settling", "complete")]
    [InlineData("card_bundle_selection", "preview", "interactive", "partial")]
    public void PreviewInspectionDoesNotInheritIntoAnotherCurrentOwnerOrIncompletePage(
        string kind, string stage, string status, string completeness)
    {
        TextMenuFrame frame = Frame(kind, stage, status, completeness);
        var entities = new NativeEntityRegistry();

        TextMenuFrame result = NativeTextMenuBundle.AppendPreviewInspection(frame, entities);

        Assert.Same(frame, result);
        Assert.Single(result.Leaves);
        Assert.Equal(0, entities.TrackedReferenceCount);
    }

    [Fact]
    public void RecognizedPreviewFailsClosedWhenPublicCardsAreUnbound()
    {
        TextMenuFrame frame = Frame("card_bundle_selection", "preview", "interactive", "complete");
        var entities = new NativeEntityRegistry();

        TextMenuFrame result = NativeTextMenuBundle.AppendPreviewInspection(frame, entities);

        AssertPartialWithoutActions(result);
        Assert.Equal(0, entities.TrackedReferenceCount);
    }

    [Fact]
    public void PublicPreviewFactsAloneNeverAuthorizeAHolderPress()
    {
        TextMenuFrame frame = Frame("card_bundle_selection", "preview", "interactive", "complete");
        var surface = new JsonObject
        {
            ["kind"] = "card_bundle_selection", ["stage"] = "preview",
            ["selected_bundle_entity_id"] = "bundle-public",
            ["bundles"] = new JsonArray(new JsonObject
            {
                ["entity_id"] = "bundle-public",
                ["cards"] = new JsonArray(new JsonObject
                {
                    ["entity_id"] = "card-public", ["name"] = "Visible card"
                })
            })
        };
        frame = frame with { Page = frame.Page with
        {
            Interaction = frame.Page.Interaction with
            {
                Content = frame.Page.Interaction.Content with { Surface = surface }
            }
        } };
        var entities = new NativeEntityRegistry();

        TextMenuFrame result = NativeTextMenuBundle.AppendPreviewInspection(frame, entities);

        AssertPartialWithoutActions(result);
        Assert.Equal(0, entities.TrackedReferenceCount);
    }

    private static void AssertPartialWithoutActions(TextMenuFrame result)
    {
        Assert.Equal("settling", result.Page.Status);
        Assert.Equal("partial", result.Page.Completeness.Status);
        Assert.Contains("current_native_bundle_preview_inspection_binding",
            result.Page.Completeness.Missing);
        Assert.Empty(result.Leaves);
        TextMenuSnapshot observed = new TextMenuSession().Observe(result).Snapshot;
        Assert.Equal("unavailable", observed.MenuActions.Status);
        Assert.Empty(observed.MenuActions.Actions);
    }

    private static TextMenuFrame Frame(
        string kind, string stage, string status, string completeness)
    {
        var leaf = new TextMenuLeaf("existing-native", "root", "confirm_card_bundle",
            "Confirm bundle", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
            () => NativeInputResult.Delivered("synthetic"));
        var page = new PlayerEnvironmentSnapshot(
            "1.0.0", PlayerEnvironmentContract.SnapshotSchema, "native-page", 1,
            DateTimeOffset.UnixEpoch, status, null,
            new PlayerEnvironmentInteraction("screen-current", kind, stage, null,
                "synthetic", new PlayerEnvironmentInteractionContent(
                    new JsonObject { ["kind"] = kind, ["stage"] = stage },
                    new JsonObject { ["kind"] = kind }),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(),
            new PlayerEnvironmentBoundActionProjection("bound-actions-1", "complete",
                0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new PlayerEnvironmentCompleteness(completeness, "visible", "exact",
                Array.Empty<string>(), Array.Empty<string>()),
            new PlayerEnvironmentSessionReference("runtime", "environment"),
            new PlayerEnvironmentInformationPolicy("player_visible_v1", "current_page",
                false, "omit"));
        return new TextMenuFrame(page, "current-owner", new[] { leaf });
    }
}
