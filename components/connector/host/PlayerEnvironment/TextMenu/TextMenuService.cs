using System;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Connector.PlayerEnvironment;

internal sealed class TextMenuRunContinuityChangedException : InvalidOperationException
{
    internal TextMenuRunContinuityChangedException()
        : base("run_continuity_changed_during_capture") { }
}

internal static partial class PlayerEnvironmentService
{
    private static readonly System.Lazy<TextMenuExecutor> TextMenus = new(() => new(
        SubmissionGate, RequestFingerprints, CaptureTextMenuFrame, MutationControlRuntime.Authorize,
        () =>
        {
            var lease = MutationControlRuntime.Snapshot().Controller;
            return lease == null ? null : $"{lease.ClientSessionId}:{lease.ControllerGeneration}";
        }));

    private static TextMenuFrame CaptureTextMenuFrame()
    {
        return CaptureWithRunIdentity(
            () => RunManager.Instance.DebugOnlyGetState(),
            () =>
            {
                SnapshotBuildResult native = BuildSnapshot(textMenuCapture: true);
                return NativeTextMenuFrameBuilder.Capture(native, Entities,
                    binding => StartPlayerEnvironmentInput(
                        native, binding.NativeAction, binding.ExactOperands));
            },
            run => Entities.GetId(run, "run"));
    }

    internal static TextMenuFrame CaptureWithRunIdentity(
        System.Func<object?> currentRun, System.Func<TextMenuFrame> capture,
        System.Func<object, string> identity)
    {
        object? before = currentRun();
        TextMenuFrame frame = capture();
        if (!System.Object.ReferenceEquals(before, currentRun()))
            throw new TextMenuRunContinuityChangedException();
        return frame with { GameContinuityId = before == null ? null : identity(before) };
    }

    public static TextMenuSnapshot ObserveTextMenu() => TextMenus.Value.Observe();
    public static TextMenuObservationContext ObserveTextMenuContext() =>
        TextMenus.Value.ObserveContext();
    public static TextMenuActionResult SubmitTextMenu(PlayerEnvironmentActionRequest request) =>
        TextMenus.Value.Submit(request);
    public static TextMenuActionResult? FindTextMenuResult(string requestId) => TextMenus.Value.Find(requestId);
}
