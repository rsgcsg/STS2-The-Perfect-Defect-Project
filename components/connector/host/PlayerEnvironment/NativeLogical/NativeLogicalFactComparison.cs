using System;
using System.Linq;
using System.Text.Json.Nodes;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

[Flags]
internal enum NativeLogicalFactChange
{
    None = 0, StreamGeneration = 1, Session = 2, OwnerOccurrence = 4,
    BindingRevision = 8, FocusOccurrence = 16, Status = 32, InformationPolicy = 64,
    SourceCompleteness = 128, Persistent = 256, Interaction = 512,
    Referents = 1024, Leaves = 2048, Capabilities = 4096
}

// The same strict comparisons serve equality and optional diagnostic grouping.
// No values, native operands, new identity or action permission are produced.
internal static class NativeLogicalFactComparison
{
    internal static NativeLogicalFactChange Compare(NativeLogicalPublicFrame a,
        NativeLogicalPublicFrame b, bool collectAll = false)
    {
        NativeLogicalFactChange changed = NativeLogicalFactChange.None;
        bool Different(bool condition, NativeLogicalFactChange group)
        { if (condition) changed |= group; return condition && !collectAll; }
        if (Different(a.StreamGeneration != b.StreamGeneration, NativeLogicalFactChange.StreamGeneration)) return changed;
        if (Different(a.Session != b.Session, NativeLogicalFactChange.Session)) return changed;
        if (Different(a.OwnerOccurrence.OwnerId != b.OwnerOccurrence.OwnerId || a.OwnerOccurrence.OccurrenceId != b.OwnerOccurrence.OccurrenceId,
            NativeLogicalFactChange.OwnerOccurrence)) return changed;
        if (Different(a.OwnerOccurrence.BindingRevision != b.OwnerOccurrence.BindingRevision, NativeLogicalFactChange.BindingRevision)) return changed;
        if (Different(a.OwnerOccurrence.FocusReferentId != b.OwnerOccurrence.FocusReferentId || a.OwnerOccurrence.FocusOccurrence != b.OwnerOccurrence.FocusOccurrence,
            NativeLogicalFactChange.FocusOccurrence)) return changed;
        if (Different(a.Status != b.Status, NativeLogicalFactChange.Status)) return changed;
        if (Different(a.InformationPolicy != b.InformationPolicy, NativeLogicalFactChange.InformationPolicy)) return changed;
        if (Different(a.SourceCompleteness.Status != b.SourceCompleteness.Status || !a.SourceCompleteness.Missing.SequenceEqual(b.SourceCompleteness.Missing),
            NativeLogicalFactChange.SourceCompleteness)) return changed;
        if (Different(a.Persistent?.ContentSchema != b.Persistent?.ContentSchema || !JsonNode.DeepEquals(a.Persistent?.Content, b.Persistent?.Content),
            NativeLogicalFactChange.Persistent)) return changed;
        if (Different(a.Interaction.InteractionId != b.Interaction.InteractionId || a.Interaction.Kind != b.Interaction.Kind
            || a.Interaction.Stage != b.Interaction.Stage || a.Interaction.Prompt != b.Interaction.Prompt || a.Interaction.ContentSchema != b.Interaction.ContentSchema
            || !JsonNode.DeepEquals(a.Interaction.Content.Surface, b.Interaction.Content.Surface)
            || !JsonNode.DeepEquals(a.Interaction.Content.Context, b.Interaction.Content.Context), NativeLogicalFactChange.Interaction)) return changed;
        bool referentsChanged = a.Referents.Count != b.Referents.Count;
        for (int i = 0; !referentsChanged && i < a.Referents.Count; i++)
        {
            var x = a.Referents[i]; var y = b.Referents[i];
            referentsChanged = x.ReferentId != y.ReferentId || x.Role != y.Role || x.Kind != y.Kind || x.Label != y.Label || x.State != y.State
                || x.PropertiesSchema != y.PropertiesSchema || !JsonNode.DeepEquals(x.Properties, y.Properties);
        }
        if (Different(referentsChanged, NativeLogicalFactChange.Referents)) return changed;
        bool leavesChanged = a.Leaves.Count != b.Leaves.Count;
        for (int i = 0; !leavesChanged && i < a.Leaves.Count; i++)
        {
            var x = a.Leaves[i]; var y = b.Leaves[i];
            leavesChanged = x.BindingKey != y.BindingKey || x.Verb != y.Verb || x.Label != y.Label || x.SubjectReferentId != y.SubjectReferentId
                || x.EffectDomain != y.EffectDomain || !x.Arguments.SequenceEqual(y.Arguments);
        }
        if (Different(leavesChanged, NativeLogicalFactChange.Leaves)) return changed;
        bool capabilitiesChanged = a.Interaction.Capabilities.Count != b.Interaction.Capabilities.Count;
        for (int i = 0; !capabilitiesChanged && i < a.Interaction.Capabilities.Count; i++)
        {
            var x = a.Interaction.Capabilities[i]; var y = b.Interaction.Capabilities[i];
            capabilitiesChanged = x.Verb != y.Verb || x.SubjectRole != y.SubjectRole || x.AvailabilityBasis != y.AvailabilityBasis || !x.Arguments.SequenceEqual(y.Arguments);
        }
        Different(capabilitiesChanged, NativeLogicalFactChange.Capabilities);
        return changed;
    }
}
