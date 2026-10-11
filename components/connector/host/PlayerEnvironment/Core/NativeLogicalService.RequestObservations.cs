using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed partial class NativeLogicalService
{
    internal void NotifyOriginalInputPrefix(PlayerEnvironmentActionRequest originalRequest)
    {
        try { ObserveOriginalInputPrefix(originalRequest); }
        catch { /* A passive observer cannot prevent or redefine native input. */ }
    }

    internal void NotifyOriginalTerminal(PlayerEnvironmentActionRequest originalRequest,
        NativeLogicalResult sealedResult)
    {
        try { ObserveOriginalInputTerminal(originalRequest, sealedResult); }
        catch { /* Original bytes and outcome were sealed before this observer. */ }
    }

    partial void ObserveOriginalInputPrefix(PlayerEnvironmentActionRequest originalRequest);
    partial void ObserveOriginalInputTerminal(PlayerEnvironmentActionRequest originalRequest,
        NativeLogicalResult sealedResult);
}
