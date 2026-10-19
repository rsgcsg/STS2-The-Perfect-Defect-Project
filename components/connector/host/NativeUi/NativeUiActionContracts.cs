using System.Collections.Generic;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.NativeUi;

/// <summary>
/// Host-local description of one native input path. It is neither wire data
/// nor consumer authority; consumers receive a bounded projection of it.
/// </summary>
internal sealed record NativeUiActionDescriptor(
    string Key,
    string Kind,
    string Category,
    string Label,
    string EvidenceCode,
    IReadOnlyList<ActionEntityBinding>? EntityBindings = null);

public sealed record NativeUiOperandDomain(
    string Kind,
    IReadOnlyList<string> EntityIds);

/// <summary>
/// Exact Host-local candidate grammar before a consumer projection binds all
/// operands. Native object references remain in the process-local registry.
/// </summary>
public sealed record NativeUiActionCandidate(
    string CandidateId,
    string Command,
    string Operation,
    string Label,
    IReadOnlyDictionary<string, string> Operands,
    IReadOnlyDictionary<string, NativeUiOperandDomain> OperandDomains,
    IReadOnlyList<ActionEntityBinding> EntityBindings,
    string BindingKind);

internal sealed record NativeUiBoundAction(NativeUiActionCandidate Candidate)
{
    internal NativeLogicalGridInputSpec? GridInput { get; init; }
    internal NonCombatPotionDiscardBinding? NonCombatDiscard { get; init; }
}

internal sealed record NativeUiInput(
    string? Command,
    IReadOnlyDictionary<string, string>? Operands);
