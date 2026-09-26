using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

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
        SnapshotBuildResult native = BuildSnapshot();
        return NativeTextMenuFrameBuilder.Capture(native, Entities,
            binding => StartPlayerEnvironmentInput(native, binding.NativeAction, binding.ExactOperands));
    }

    public static TextMenuSnapshot ObserveTextMenu() => TextMenus.Value.Observe();
    public static TextMenuActionResult SubmitTextMenu(PlayerEnvironmentActionRequest request) =>
        TextMenus.Value.Submit(request);
    public static TextMenuActionResult? FindTextMenuResult(string requestId) => TextMenus.Value.Find(requestId);
}
