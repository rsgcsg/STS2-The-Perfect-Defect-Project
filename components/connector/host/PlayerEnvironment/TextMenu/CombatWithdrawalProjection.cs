using System;
using System.Linq;
using STS2Connector.LiveHost;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed record CombatInputView(string Status, string Stage, PlayerEnvironmentCompleteness Completeness);

// Each complete view maps only after its own applicable relation is known.
// It does not discover, invent or suppress a native leaf.
internal static class CombatWithdrawalProjection
{
    internal const string ExpiredReason = "native_combat_input_withdrawal_expired";
    internal static CombatInputView Apply(string status, string stage, PlayerEnvironmentCompleteness completeness,
        bool completeRelation, int leaves, CombatWithdrawalProof? proof)
    {
        if (proof == null || !completeRelation || completeness.Status != "complete" || completeness.Missing.Count != 0
            || status is "visible_unsupported" or "unsupported" or "failed") return new(status, stage, completeness);
        if (leaves > 0) return new("interactive", stage is "settling" or "unsupported" ? "ready" : stage, completeness);
        if (proof.State == CombatWithdrawalState.Pending) return new("settling", "settling", completeness);
        return new("visible_unsupported", "unsupported", completeness with
        { Status = "partial", Missing = completeness.Missing.Append(ExpiredReason).Distinct(StringComparer.Ordinal).ToArray() });
    }
    internal static TextMenuFrame Finalize(TextMenuFrame frame, bool semanticCards = false)
    {
        var view = Apply(frame.Page.Status, frame.Page.Interaction.Stage, frame.Page.Completeness,
            frame.Page.BoundActions.Status != "truncated" && (!semanticCards || frame.CardPlayCatalogComplete),
            frame.Leaves.Count + (semanticCards ? frame.CardPlays.Count : 0), frame.CombatWithdrawal);
        return frame with { Page = frame.Page with { Status = view.Status,
            Interaction = frame.Page.Interaction with { Stage = view.Stage }, Completeness = view.Completeness } };
    }
}
