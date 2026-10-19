using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

// Public source operands and native semantic operands may use different verbs
// (map activate/travel). Keep their exact ingress objects in separate roles.
internal static class P1NativeScopeInputs
{
    internal static ProcessLocalObservedAction? PublicInput(string nativeType,
        ProcessLocalObservedAction? expected, ProcessLocalObservedAction? semantic) => expected
        ?? (nativeType == "EndPlayerTurnAction" && IsEndTurn(semantic) ? semantic : null);

    private static bool IsEndTurn(ProcessLocalObservedAction? action) =>
        action is { Verb: "end_turn", Subject: null, Arguments.Count: 0 };

    internal static bool CanRetainQueuedEndTurn(string nativeType, ProcessLocalObservedAction? expected,
        ProcessLocalNativeWitnessFrame frame, IReadOnlyList<string> semanticBlockers) =>
        nativeType == "EndPlayerTurnAction" && IsEndTurn(expected)
        && frame.Snapshot.Interaction.Kind == "combat_turn" && !frame.ExternalControllerActive
        && !frame.Snapshot.InformationPolicy.IncludesHiddenInformation
        && frame.PublicBasis is { } basis && P1NativeRecordingContract.CompleteBasis(basis.Bytes)
        && semanticBlockers.Count == 0;

    internal static PlayerEnvironmentBoundAction? CurrentPublicAction(ProcessLocalNativeWitnessFrame frame,
        ProcessLocalObservedAction? originalPublicInput)
    {
        if (originalPublicInput == null) return null;
        var current = frame.Resolve(originalPublicInput);
        return current is { Status: "exact_unique", MatchCount: 1 } ? current.BoundAction : null;
    }

    internal static object Failure(string witness, string decision, string detail, string? snapshot,
        string evidenceLevel, HumanActionOccurrenceEvidence? occurrence) =>
        new { action_witness_id = witness, decision_id = decision, detail, snapshot_id = snapshot,
            evidence_level = evidenceLevel, human_occurrence = occurrence };
}
