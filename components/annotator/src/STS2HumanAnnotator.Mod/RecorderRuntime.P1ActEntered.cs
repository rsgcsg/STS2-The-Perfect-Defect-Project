namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    // Vote commit can finish before ActEntered. Both branches persist their
    // native evidence before consuming the exact long-lived RunManager carrier.
    private static void CompleteP1ActEntered(object owner, string actionWitnessId)
    {
        var evidence = new { seam = "RunManager.ActEntered", action_witness_id = actionWitnessId };
        if (CompleteP1Ui(actionWitnessId, "finished", evidence)
            || ObserveP1Boundary("native_act_entered", evidence))
            NativeUiCompletionRootBindings.TakeIfMatches(owner, actionWitnessId);
    }
}
