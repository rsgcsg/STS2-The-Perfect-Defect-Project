using System.Runtime.CompilerServices;
using System.Text.Json;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;

namespace STS2Connector.Host.Tests;

public sealed class CardRewardCanaryDiagnosticsTests
{
    [Fact]
    public void DisabledProbeDoesNotWriteOrAlterTheNativeScope()
    {
        var lines = new List<string>();
        var probe = new CardRewardCanaryDiagnostics(false, lines.Add);
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Bare<CardRewardAlternative>();
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(
            screen, new[] { alternative });
        try
        {
            probe.Begin(screen, scope, 1);
            CardRewardAlternativePresentationBindings.ObserveCreated(new object());
            probe.Created(CardRewardAlternativePresentationBindings.CurrentScope, true);
            scope.Finish(true);
            probe.Finish(scope, true, true);
            probe.Page(screen, "captured", null, true, 1, 1, true, true);
            Assert.Empty(lines);
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void ActualHookEventsAndFirstCompletePageShareOneGeneration()
    {
        var lines = new List<string>();
        var probe = new CardRewardCanaryDiagnostics(true, lines.Add);
        var screen = Bare<NCardRewardSelectionScreen>();
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(
            screen, new[] { Bare<CardRewardAlternative>() });
        try
        {
            probe.Begin(screen, scope, 1);
            CardRewardAlternativePresentationBindings.ObserveCreated(new object());
            probe.Created(CardRewardAlternativePresentationBindings.CurrentScope, true);
            scope.Finish(true);
            probe.Finish(scope, true, true);
            probe.Page(screen, "parent_not_bound", null, false, 1, 1, true, true);
            probe.Page(screen, "captured", "reward_existing_registry_id", true, 1, 1, true, true);
            probe.Page(screen, "captured", "reward_existing_registry_id", true, 1, 1, true, true);

            Assert.Equal(4, lines.Count);
            Assert.Equal(new[] { "refresh_begin", "refresh_finish", "page_observation", "page_observation" },
                lines.Select(line => Detail(line).GetProperty("event_kind").GetString()));
            Assert.All(lines, line => Assert.Equal(1, Detail(line).GetProperty("generation").GetInt32()));
            Assert.Equal(1, Detail(lines[1]).GetProperty("create_postfix_count").GetInt32());
            Assert.Equal("incomplete", Detail(lines[2]).GetProperty("status").GetString());
            Assert.Equal("parent_fact_unavailable",
                Detail(lines[2]).GetProperty("failure_category").GetString());
            Assert.Equal("binding_facts_captured", Detail(lines[3]).GetProperty("status").GetString());
            Assert.Equal("reward_existing_registry_id", Detail(lines[3]).GetProperty("parent_id").GetString());
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void MissingCreateAndMissingParentRegistryIdRemainExplicitlyIncomplete()
    {
        var lines = new List<string>();
        var probe = new CardRewardCanaryDiagnostics(true, lines.Add);
        var screen = Bare<NCardRewardSelectionScreen>();
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(
            screen, new[] { Bare<CardRewardAlternative>(), Bare<CardRewardAlternative>() });
        try
        {
            probe.Begin(screen, scope, 2);
            probe.Created(CardRewardAlternativePresentationBindings.CurrentScope, true);
            scope.Finish(true);
            probe.Finish(scope, true, true);
            probe.Page(screen, "captured", null, true, 2, 1, false, false);
            JsonElement detail = Detail(lines[^1]);
            Assert.Equal("incomplete", detail.GetProperty("status").GetString());
            Assert.Equal("create_postfix_mismatch",
                detail.GetProperty("failure_category").GetString());
            Assert.Equal("registry_id_unavailable", detail.GetProperty("parent_id_status").GetString());
            Assert.Equal(1, detail.GetProperty("create_postfix_count").GetInt32());
            Assert.False(detail.GetProperty("exact_button_references").GetBoolean());
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void MissingRefreshHookAndExhaustedBudgetAreNotSilentSuccesses()
    {
        var lines = new List<string>();
        var probe = new CardRewardCanaryDiagnostics(true, lines.Add, generationLimit: 1);
        var noHookScreen = Bare<NCardRewardSelectionScreen>();
        probe.Page(noHookScreen, "captured", null, true, 1, 1, true, true);
        Assert.Equal("incomplete", Detail(lines[0]).GetProperty("status").GetString());
        Assert.False(Detail(lines[0]).GetProperty("refresh_hook_observed").GetBoolean());
        Assert.Equal("refresh_hook_unobserved",
            Detail(lines[0]).GetProperty("failure_category").GetString());

        var secondScreen = Bare<NCardRewardSelectionScreen>();
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(
            secondScreen, new[] { Bare<CardRewardAlternative>() });
        try
        {
            probe.Begin(secondScreen, scope, 1);
            probe.Begin(secondScreen, scope, 1);
            probe.Page(secondScreen, "captured", null, true, 1, 1, true, true);
            Assert.Equal(2, lines.Count);
            Assert.Equal("budget_exhausted", Detail(lines[1]).GetProperty("event_kind").GetString());
            Assert.False(probe.Enabled);
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void BrokenLogSinkStopsOnlyDiagnostics()
    {
        int calls = 0;
        var probe = new CardRewardCanaryDiagnostics(true, _ =>
        {
            calls++;
            throw new InvalidOperationException("synthetic sink failure");
        });
        var screen = Bare<NCardRewardSelectionScreen>();
        object button = new();
        var alternative = Bare<CardRewardAlternative>();
        var alternatives = new[] { alternative };
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        try
        {
            probe.Begin(screen, scope, 1);
            CardRewardAlternativePresentationBindings.ObserveCreated(button);
            probe.Created(CardRewardAlternativePresentationBindings.CurrentScope, true);
            scope.Finish(true);
            probe.Finish(scope, true, true);
            probe.Page(screen, "captured", null, true, 1, 1, true, true);
            Assert.Equal(1, calls);
            Assert.False(probe.Enabled);
            Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
                screen, alternative, alternatives, new[] { button }, out var resolved));
            Assert.Same(button, resolved);
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void CrossThreadFinishCannotPublishACompleteCanary()
    {
        var lines = new List<string>();
        var probe = new CardRewardCanaryDiagnostics(true, lines.Add);
        var screen = Bare<NCardRewardSelectionScreen>();
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(
            screen, new[] { Bare<CardRewardAlternative>() });
        try
        {
            probe.Begin(screen, scope, 1);
            var worker = new Thread(() =>
            {
                scope.Finish(true);
                probe.Finish(scope, true, true);
            });
            worker.Start();
            Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
            probe.Page(screen, "captured", null, true, 1, 1, true, true);
            Assert.Equal(2, lines.Count); // No Godot log write from the worker.
            JsonElement page = Detail(lines[^1]);
            Assert.Equal("incomplete", page.GetProperty("status").GetString());
            Assert.Equal("cross_thread", page.GetProperty("failure_category").GetString());
            Assert.False(page.GetProperty("same_thread").GetBoolean());
        }
        finally { scope.Finish(false); }
    }

    [Fact]
    public void ExistingIdLookupNeverAllocatesOrAdvancesTheReferentCounter()
    {
        var registry = new NativeEntityRegistry();
        object observed = new();
        object unobserved = new();
        Assert.False(registry.TryGetExistingId(unobserved, out _));
        Assert.Equal(0, registry.TrackedReferenceCount);
        string first = registry.GetId(observed, "reward");
        Assert.EndsWith("_1", first);
        Assert.True(registry.TryGetExistingId(observed, out string? same));
        Assert.Equal(first, same);
        Assert.False(registry.TryGetExistingId(unobserved, out _));
        Assert.Equal(1, registry.TrackedReferenceCount);
        Assert.EndsWith("_2", registry.GetId(unobserved, "reward"));
    }

    private static JsonElement Detail(string line)
    {
        using JsonDocument doc = JsonDocument.Parse(line);
        Assert.Equal("sts2.platform/card-reward-canary-diagnostic-1",
            doc.RootElement.GetProperty("schema").GetString());
        return doc.RootElement.GetProperty("detail").Clone();
    }

    private static T Bare<T>() where T : class =>
        (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
}
