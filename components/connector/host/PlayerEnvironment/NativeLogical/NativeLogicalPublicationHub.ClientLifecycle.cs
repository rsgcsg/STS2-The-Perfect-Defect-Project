using System;
using System.Linq;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>Only the original Authority client may extend its idle deadline.
/// Implementations take their own short Authority gate and never call back
/// into a publication or request owner while holding it.</summary>
public interface INativeLogicalClientLifetimeDependency
{
    bool TryTouchActiveClient(string clientSessionId);
}

public sealed partial class NativeLogicalPublicationHub
{
    private INativeLogicalClientLifetimeDependency? clientLifetime;

    /// <summary>The host binds its one Authority dependency at composition.
    /// Unbound portable hubs retain their existing standalone semantics.</summary>
    public void BindClientLifetime(INativeLogicalClientLifetimeDependency dependency)
    {
        ArgumentNullException.ThrowIfNull(dependency);
        lock (gate)
        {
            if (disposed) throw new ObjectDisposedException(nameof(NativeLogicalPublicationHub));
            if (clientLifetime is not null && !ReferenceEquals(clientLifetime, dependency))
                throw new InvalidOperationException("The original client Authority cannot be replaced.");
            clientLifetime = dependency;
        }
    }

    // Called only under gate AFTER validating owned subscription/scope/cursor
    // and BEFORE extending its deadline. Hub -> Authority is the declared order.
    private bool TryTouchClient(string clientSessionId) =>
        clientLifetime?.TryTouchActiveClient(clientSessionId) ?? true;

    /// <summary>Authority invokes this only after its gate is released. Closure
    /// never renews/reopens a subscription or modifies another client's scope.</summary>
    public void ExpireClient(string clientSessionId, string reason)
    {
        lock (gate)
        {
            if (disposed) return;
            OnClientClosingLocked(clientSessionId, reason);
            foreach (string id in subscriptions.Where(pair => pair.Value.Client == clientSessionId)
                         .Select(pair => pair.Key).ToArray())
                RemoveSubscription(id, "subscription_expired");
        }
    }

    // The recorder-owned partial marks its accounting failed before its own
    // resource disappears. This is the same Hub owner, not a foreign callback.
    partial void OnClientClosingLocked(string clientSessionId, string reason);
}
