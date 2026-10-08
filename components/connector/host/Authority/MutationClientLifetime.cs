using System;
using System.Threading;

namespace STS2Connector.Authority;

/// <summary>An original Authority identity, never a second request registry.
/// Request owners may inspect its permanent closed flag; only Authority changes it.</summary>
internal sealed class MutationClientLifetime(string runtimeInstanceId, string clientSessionId)
{
    private int closed;
    internal string RuntimeInstanceId { get; } = runtimeInstanceId;
    internal string ClientSessionId { get; } = clientSessionId;
    internal bool IsClosed => Volatile.Read(ref closed) != 0;
    internal string? CloseReason { get; private set; }
    internal int SpentRequestCount { get; private set; }
    internal void SpendRequest() => SpentRequestCount = checked(SpentRequestCount + 1);
    internal bool Close(string reason)
    {
        if (IsClosed) return false;
        CloseReason = reason;
        Volatile.Write(ref closed, 1);
        return true;
    }
}

internal sealed record MutationClientClosure(string RuntimeInstanceId, string ClientSessionId, string Reason);

internal sealed record MutationRequestAdmission(MutationAdmission Admission, MutationClientLifetime? Client)
{
    internal bool Accepted => Admission.Accepted;
}
