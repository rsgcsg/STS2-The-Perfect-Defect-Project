using System.Text.Json.Nodes;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector.Tests;

public sealed class TrialAbandonProjectionTests
{
    private static AbandonRunConfirmationSurface Surface() => new("abandon_run_confirmation", "awaiting_confirmation",
        "screen", "modal", "event", "room", "option", "Abandon this run?", "This ends the run.", new[] {
            new VisibleAbandonControl("yes-control", "yes", "confirm_abandon", "Confirm", true),
            new VisibleAbandonControl("no-control", "no", "cancel_abandon", "Cancel", true) });

    [Fact]
    public void ActualDescriptorBindingProjectsBothExactPublicControlSubjects()
    {
        var surface = Surface();
        var facts = PlayerEnvironmentService.ProjectVisibleFacts(surface,
            new EventLiveContext("event", "TRIAL", null, false, false, null));
        Assert.Equal("r1.8", facts.Surface["requires_profile_revision"]!.GetValue<string>());
        Assert.Equal("This ends the run.", facts.Surface["body"]!.GetValue<string>());
        var referents = PlayerEnvironmentService.ProjectFactReferents(facts.Surface);
        var game = new GameBuildIdentity("v", "commit", null, null, new CompatibilityAssessment("test", true, true, false, "test"));
        var observation = new LiveObservation("basis", "ready", new EventLiveContext("event", "TRIAL", null, false, false, null),
            surface, new StateCompleteness("contract_complete_for_supported_surface", "test", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        var bindings = NativeUiActionRuntime.DescribeAbandonCommands(surface)
            .Select(action => NativeUiActionRuntime.BindActionToCurrentObservation(observation, action)!).ToArray();
        var projected = PlayerEnvironmentService.ProjectBoundActions(bindings, "interaction", referents);
        Assert.Equal("complete", projected.Projection.Status);
        Assert.Equal(2, projected.Projection.Actions.Count);
        Assert.Contains(projected.Projection.Actions, action => action.Verb == "confirm" && action.SubjectReferentId == "yes-control" && action.Arguments.Count == 0);
        Assert.Contains(projected.Projection.Actions, action => action.Verb == "cancel" && action.SubjectReferentId == "no-control" && action.Arguments.Count == 0);
        Assert.All(bindings, binding => Assert.Equal("screen", binding.Candidate.Operands["screen_id"]));
    }

    [Fact]
    public void DisabledControlsAreVisibleButGainNoBoundAction()
    {
        var surface = Surface();
        var changed = surface with { Controls = surface.Controls.Select(control => control with { Enabled = false }).ToArray() };
        Assert.Empty(NativeUiActionRuntime.DescribeAbandonCommands(changed));
        Assert.Equal(2, ((JsonArray)PlayerEnvironmentService.ProjectVisibleFacts(changed,
            new EventLiveContext("event", "TRIAL", null, false, false, null)).Surface["controls"]!).Count);
    }

    [Fact]
    public void OrdinaryEventSurfaceDoesNotAcquireNewContractFields()
    {
        var surface = new EventOptionSurface("event_option", "screen", Array.Empty<VisibleEventOption>());
        var plain = PlayerEnvironmentService.ProjectVisibleFacts(surface, new EventLiveContext("event", "OTHER", null, false, false, null)).Surface;
        Assert.False(((JsonObject)plain).ContainsKey("abandon_confirmation"));
        Assert.False(((JsonObject)plain).ContainsKey("requires_profile_revision"));
    }
}
