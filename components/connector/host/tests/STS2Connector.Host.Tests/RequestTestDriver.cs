using System.Text.Json;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

/// <summary>Tests use the production original byte owner and decode only their
/// caller-owned reply. No production typed-result map is recreated.</summary>
internal static class RequestTestDriver
{
    internal static RequestNamespace Namespace(Func<MutationAuthorizationRequest, MutationAdmission>? begin = null,
        int nativeEnvelope = RequestNamespace.NativeEnvelopeBytes,
        int legacyEnvelope = RequestNamespace.LegacyEnvelopeBytes)
    {
        MutationAdmission Allowed(MutationAuthorizationRequest request) => MutationAdmission.Allow(new(
            "runtime", request.ClientSessionId!, "instance", "test", "Test", "1", request.ControllerLeaseId!, request.ControllerGeneration!.Value));
        var client = new MutationClientLifetime("runtime", "client");
        return new("runtime", Allowed, request => new(Allowed(request), client), begin ?? Allowed,
            enableTimer: false, nativeEnvelope: nativeEnvelope, legacyEnvelope: legacyEnvelope);
    }
    internal static void AssertWireEqual(object? expected, object? actual) => Assert.Equal(
        JsonSerializer.Serialize(expected, ConnectorMod._jsonOptions), JsonSerializer.Serialize(actual, ConnectorMod._jsonOptions));
    internal static T Decode<T>(RequestNamespace.TerminalReply terminal)
    {
        using (terminal) { using var stream = new MemoryStream(); terminal.WriteTo(stream);
            return JsonSerializer.Deserialize<T>(stream.ToArray(), ConnectorMod._jsonOptions)!; }
    }
    internal static T Submit<T>(RequestNamespace requests, PlayerEnvironmentActionRequest request,
        Func<PlayerEnvironmentActionRequest, bool> run)
    {
        var admitted = requests.Admit(request);
        if (admitted.Reply is { } replay) return Decode<T>(replay);
        if (admitted.Status != "admitted")
            return (T)RequestResultFactory.BeforeInput(request, admitted.Reason ?? admitted.Status);
        run(request); return Decode<T>(admitted.OriginalCompletion!.GetAwaiter().GetResult());
    }
    internal static TextMenuActionResult Submit(this TextMenuExecutor executor, PlayerEnvironmentActionRequest request) =>
        Submit<TextMenuActionResult>(executor.Requests, request, executor.RunAdmitted);
    internal static TextMenuV2ActionResult Submit(this TextMenuV2Executor executor, PlayerEnvironmentActionRequest request) =>
        Submit<TextMenuV2ActionResult>(executor.Requests, request, executor.RunAdmitted);
    internal static TextMenuActionResult? Find(this TextMenuExecutor executor, string requestId)
    {
        var lookup = executor.Requests.Find(requestId, TextMenuContract.Profile);
        return lookup.Reply is { } bytes ? Decode<TextMenuActionResult>(bytes) : null;
    }
}
