using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalRevalidationDiagnosticsTests
{
    private static NativeLogicalPublicFrame Frame()
    {
        var f = NativeLogicalCoreTests.Frame();
        return f with { Interaction = f.Interaction with { Capabilities = new[] {
            new PlayerEnvironmentInteractionCapability("pick", "card", new[] { new PlayerEnvironmentCapabilityArgument("target", true) }, "native"),
            new PlayerEnvironmentInteractionCapability("inspect", null, Array.Empty<PlayerEnvironmentCapabilityArgument>(), "visible") } },
            Referents = new[] { f.Referents[0], f.Referents[0] with { ReferentId = "second" } },
            Leaves = new[] { f.Leaves[0], f.Leaves[0] with { BindingKey = "second-private-key" } } };
    }
    [Fact]
    public void ComparatorPreservesFrozenStrictEqualityForEveryPreviouslyComparedField()
    {
        var f = Frame();
        var cases = new List<(NativeLogicalFactChange Group, NativeLogicalPublicFrame Frame)>();
        void Add(NativeLogicalFactChange group, NativeLogicalPublicFrame value) => cases.Add((group, value));
        Add(NativeLogicalFactChange.StreamGeneration, f with { StreamGeneration = "new" });
        Add(NativeLogicalFactChange.Session, f with { Session = f.Session with { RuntimeInstanceId = "new" } });
        Add(NativeLogicalFactChange.Session, f with { Session = f.Session with { EnvironmentFingerprint = "new" } });
        Add(NativeLogicalFactChange.OwnerOccurrence, f with { OwnerOccurrence = f.OwnerOccurrence with { OwnerId = "new" } });
        Add(NativeLogicalFactChange.OwnerOccurrence, f with { OwnerOccurrence = f.OwnerOccurrence with { OccurrenceId = "new" } });
        Add(NativeLogicalFactChange.BindingRevision, f with { OwnerOccurrence = f.OwnerOccurrence with { BindingRevision = "new" } });
        Add(NativeLogicalFactChange.FocusOccurrence, f with { OwnerOccurrence = f.OwnerOccurrence with { FocusReferentId = "new" } });
        Add(NativeLogicalFactChange.FocusOccurrence, f with { OwnerOccurrence = f.OwnerOccurrence with { FocusOccurrence = "new" } });
        Add(NativeLogicalFactChange.Status, f with { Status = "settling" });
        foreach (var policy in new[] { f.InformationPolicy with { Id = "new" }, f.InformationPolicy with { Scope = "new" },
            f.InformationPolicy with { IncludesHiddenInformation = true }, f.InformationPolicy with { UnknownFieldBehavior = "new" } })
            Add(NativeLogicalFactChange.InformationPolicy, f with { InformationPolicy = policy });
        Add(NativeLogicalFactChange.SourceCompleteness, f with { SourceCompleteness = new("partial", Array.Empty<string>()) });
        Add(NativeLogicalFactChange.SourceCompleteness, f with { SourceCompleteness = new("complete", new[] { "missing" }) });
        Add(NativeLogicalFactChange.Persistent, f with { Persistent = null });
        Add(NativeLogicalFactChange.Persistent, f with { Persistent = f.Persistent! with { ContentSchema = "new" } });
        Add(NativeLogicalFactChange.Persistent, f with { Persistent = f.Persistent! with { Content = new JsonObject { ["value"] = 2 } } });
        foreach (var i in new[] {
            f.Interaction with { InteractionId = "new" }, f.Interaction with { Kind = "new" }, f.Interaction with { Stage = "new" },
            f.Interaction with { Prompt = "new" }, f.Interaction with { ContentSchema = "new" },
            f.Interaction with { Content = f.Interaction.Content with { Surface = new JsonObject { ["new"] = 1 } } },
            f.Interaction with { Content = f.Interaction.Content with { Context = new JsonObject { ["new"] = 1 } } } })
            Add(NativeLogicalFactChange.Interaction, f with { Interaction = i });
        var r = f.Referents[0];
        foreach (var value in new[] {
            r with { ReferentId = "new" }, r with { Role = "new" }, r with { Kind = "new" }, r with { Label = "new" },
            r with { State = r.State with { Enabled = false } }, r with { PropertiesSchema = "new" },
            r with { Properties = new JsonObject { ["new"] = 1 } } })
            Add(NativeLogicalFactChange.Referents, f with { Referents = new[] { value, f.Referents[1] } });
        foreach (var state in new[] { r.State with { Visible = false }, r.State with { Selected = true },
            r.State with { Focused = true }, r.State with { ObservationBasis = "new" } })
            Add(NativeLogicalFactChange.Referents, f with { Referents = new[] { r with { State = state }, f.Referents[1] } });
        Add(NativeLogicalFactChange.Referents, f with { Referents = Array.Empty<PlayerEnvironmentReferent>() });
        Add(NativeLogicalFactChange.Referents, f with { Referents = f.Referents.Reverse().ToArray() });
        var l = f.Leaves[0];
        foreach (var value in new[] {
            l with { BindingKey = "new" }, l with { Verb = "new" }, l with { Label = "new" }, l with { SubjectReferentId = "new" },
            l with { EffectDomain = "new" }, l with { Arguments = new[] { new NativeLogicalArgument("target", "new") } } })
            Add(NativeLogicalFactChange.Leaves, f with { Leaves = new[] { value, f.Leaves[1] } });
        Add(NativeLogicalFactChange.Leaves, f with { Leaves = Array.Empty<NativeLogicalLeaf>() });
        Add(NativeLogicalFactChange.Leaves, f with { Leaves = f.Leaves.Reverse().ToArray() });
        var c = f.Interaction.Capabilities[0];
        foreach (var value in new[] {
            c with { Verb = "new" }, c with { SubjectRole = "new" }, c with { AvailabilityBasis = "new" },
            c with { Arguments = new[] { new PlayerEnvironmentCapabilityArgument("target", false) } } })
            Add(NativeLogicalFactChange.Capabilities, f with { Interaction = f.Interaction with { Capabilities = new[] { value, f.Interaction.Capabilities[1] } } });
        Add(NativeLogicalFactChange.Capabilities, f with { Interaction = f.Interaction with { Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>() } });
        Add(NativeLogicalFactChange.Capabilities, f with { Interaction = f.Interaction with { Capabilities = f.Interaction.Capabilities.Reverse().ToArray() } });
        foreach (var (group, value) in cases)
        {
            Assert.False(FrozenPreDiagnosticEquality(f, value));
            Assert.Equal(group, NativeLogicalFactComparison.Compare(f, value, collectAll: true));
            Assert.Equal(group, NativeLogicalFactComparison.Compare(value, f, collectAll: true));
        }
        var all = cases.Select(x => x.Frame).Prepend(f).ToArray();
        foreach (var x in all)
        foreach (var y in all)
            Assert.Equal(FrozenPreDiagnosticEquality(x, y), NativeLogicalFactComparison.Compare(x, y) == NativeLogicalFactChange.None);
        var clone = Frame(); Assert.True(FrozenPreDiagnosticEquality(f, clone));
        Assert.Equal(NativeLogicalFactChange.None, NativeLogicalFactComparison.Compare(f, clone, collectAll: true));
    }
    [Fact]
    public void DerivativeBindingRevisionDoesNotHideActualPublicFactChangeOrLeakValues()
    {
        var lines = new List<string>(); var diagnostics = new NativeLogicalRevalidationDiagnostics(true, lines.Add);
        var f = Frame(); var changed = f with { OwnerOccurrence = f.OwnerOccurrence with { BindingRevision = "private-revision" },
            Persistent = f.Persistent! with { Content = new JsonObject { ["private-content-canary"] = 2 } } };
        diagnostics.Reject("original-request", "original-snapshot", "original-action", "projected-snapshot",
            NativeLogicalRejectionGate.PublicFactsChanged, f, changed);
        using var doc = JsonDocument.Parse(Assert.Single(lines)); var row = doc.RootElement;
        Assert.Equal("original-request", row.GetProperty("request_id").GetString());
        Assert.Equal("original-snapshot", row.GetProperty("expected_snapshot_id").GetString());
        Assert.Equal("original-action", row.GetProperty("action_id").GetString());
        Assert.Equal("public_facts_changed", row.GetProperty("rejection_gate").GetString());
        Assert.Equal(new[] { "binding_revision", "persistent" }, row.GetProperty("changed_fact_groups").EnumerateArray().Select(x => x.GetString()));
        Assert.DoesNotContain("private", lines[0]);
    }
    [Fact]
    public void LoggingIsOptInBoundedAndPermanentlyDisabledAfterSinkFailure()
    {
        int calls = 0;
        var disabled = new NativeLogicalRevalidationDiagnostics(false, _ => calls++);
        disabled.Reject("r", "s", "a", null, NativeLogicalRejectionGate.BasisMissing); Assert.Equal(0, calls);
        var failed = new NativeLogicalRevalidationDiagnostics(true, _ => { calls++; throw new IOException("private failure"); });
        failed.Reject("r", "s", "a", null, NativeLogicalRejectionGate.BasisMissing);
        failed.Reject("r", "s", "a", null, NativeLogicalRejectionGate.BasisMissing); Assert.Equal(1, calls); Assert.False(failed.Enabled);
        var lines = new List<string>(); var bounded = new NativeLogicalRevalidationDiagnostics(true, lines.Add);
        for (int i = 0; i < 250; i++) bounded.Reject("r", "s", "a", null, NativeLogicalRejectionGate.ActionUnknown);
        Assert.Equal(201, lines.Count); Assert.False(bounded.Enabled);
        using var last = JsonDocument.Parse(lines[^1]); Assert.Equal("budget_exhausted", last.RootElement.GetProperty("event_kind").GetString());
        Assert.All(lines, line => Assert.True(System.Text.Encoding.UTF8.GetByteCount(line) < 8192));
        var oversized = new List<string>(); var attribution = new NativeLogicalRevalidationDiagnostics(true, oversized.Add);
        attribution.Reject(new string('界', 86), "s\"\n", "a", null, NativeLogicalRejectionGate.ActionUnknown);
        using var row = JsonDocument.Parse(Assert.Single(oversized)); Assert.True(row.RootElement.GetProperty("attribution_omitted").GetBoolean());
        Assert.Equal(JsonValueKind.Null, row.RootElement.GetProperty("request_id").ValueKind);
        Assert.Equal("s\"\n", row.RootElement.GetProperty("expected_snapshot_id").GetString());
    }
    private static bool FrozenPreDiagnosticEquality(NativeLogicalPublicFrame a, NativeLogicalPublicFrame b)
    {
        if (a.StreamGeneration != b.StreamGeneration || a.Session != b.Session || a.OwnerOccurrence != b.OwnerOccurrence
            || a.Status != b.Status || a.InformationPolicy != b.InformationPolicy || a.SourceCompleteness.Status != b.SourceCompleteness.Status
            || !a.SourceCompleteness.Missing.SequenceEqual(b.SourceCompleteness.Missing)
            || a.Persistent?.ContentSchema != b.Persistent?.ContentSchema || !JsonNode.DeepEquals(a.Persistent?.Content, b.Persistent?.Content)
            || a.Interaction.InteractionId != b.Interaction.InteractionId || a.Interaction.Kind != b.Interaction.Kind
            || a.Interaction.Stage != b.Interaction.Stage || a.Interaction.Prompt != b.Interaction.Prompt || a.Interaction.ContentSchema != b.Interaction.ContentSchema
            || !JsonNode.DeepEquals(a.Interaction.Content.Surface, b.Interaction.Content.Surface)
            || !JsonNode.DeepEquals(a.Interaction.Content.Context, b.Interaction.Content.Context)
            || a.Referents.Count != b.Referents.Count || a.Leaves.Count != b.Leaves.Count || a.Interaction.Capabilities.Count != b.Interaction.Capabilities.Count) return false;
        for (int i = 0; i < a.Referents.Count; i++)
        {
            var x = a.Referents[i]; var y = b.Referents[i];
            if (x.ReferentId != y.ReferentId || x.Role != y.Role || x.Kind != y.Kind || x.Label != y.Label || x.State != y.State
                || x.PropertiesSchema != y.PropertiesSchema || !JsonNode.DeepEquals(x.Properties, y.Properties)) return false;
        }
        for (int i = 0; i < a.Leaves.Count; i++)
        {
            var x = a.Leaves[i]; var y = b.Leaves[i];
            if (x.BindingKey != y.BindingKey || x.Verb != y.Verb || x.Label != y.Label || x.SubjectReferentId != y.SubjectReferentId
                || x.EffectDomain != y.EffectDomain || !x.Arguments.SequenceEqual(y.Arguments)) return false;
        }
        for (int i = 0; i < a.Interaction.Capabilities.Count; i++)
        {
            var x = a.Interaction.Capabilities[i]; var y = b.Interaction.Capabilities[i];
            if (x.Verb != y.Verb || x.SubjectRole != y.SubjectRole || x.AvailabilityBasis != y.AvailabilityBasis || !x.Arguments.SequenceEqual(y.Arguments)) return false;
        }
        return true;
    }
}
