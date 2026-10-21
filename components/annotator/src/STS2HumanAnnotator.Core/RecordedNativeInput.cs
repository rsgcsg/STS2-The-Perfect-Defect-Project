namespace STS2HumanAnnotator.Core;

/// <summary>An exact accepted native input, not a public BoundAction or proof
/// of legality at H. Execution membership is validated separately at S.</summary>
public sealed record RecordedNativeInput(
    string ActionKey,
    string Verb,
    string? SubjectReferentId,
    IReadOnlyDictionary<string, string> Arguments,
    string? Label);

public static class RecordedNativeInputValidator
{
    public static IReadOnlyList<string> Validate(SemanticActionReference action)
    {
        if (action.NativeInput is not { } input) return Array.Empty<string>();
        // These are the exact native-input producers. This validates correlation,
        // not legality: exact execution membership is checked independently.
        bool supportedPair = (action.NativeActionType, input.Verb) is
            ("PlayCardAction", "play") or ("UsePotionAction", "use") or ("EndPlayerTurnAction", "end_turn");
        bool subjectValid = action.NativeActionType == "EndPlayerTurnAction"
            ? input.SubjectReferentId == null && input.Arguments.Count == 0 && action.NativeWitness?.SubjectWitnessId == null
            : !string.IsNullOrWhiteSpace(input.SubjectReferentId)
                && !string.IsNullOrWhiteSpace(action.NativeWitness?.SubjectWitnessId);
        bool valid = action.BoundAction == null && action.NativeMechanism == "game_action"
            && supportedPair
            && !string.IsNullOrWhiteSpace(input.ActionKey)
            && subjectValid
            && input.Arguments.All(pair => !string.IsNullOrWhiteSpace(pair.Key)
                && !string.IsNullOrWhiteSpace(pair.Value))
            && action.NativeWitness is { } witness
            && witness.NativeActionType == action.NativeActionType
            && !string.IsNullOrWhiteSpace(witness.Origin)
            && action.Mapping is { Status: "exact_native_input", MatchCount: 1,
                Basis: "scoped_native_input_reference_equality" };
        return valid ? Array.Empty<string>() : new[] { "native_input_correlation_invalid" };
    }
}
