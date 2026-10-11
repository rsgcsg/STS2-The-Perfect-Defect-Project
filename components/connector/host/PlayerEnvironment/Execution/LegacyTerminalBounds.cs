namespace STS2Connector.PlayerEnvironment;

internal static class LegacyTerminalBounds
{
    // Every closed native legacy producer's reason/detail is within this bound;
    // six bytes per control scalar is covered by the actual encoded preflight.
    internal static readonly string MaximumField = new('\0', 65_536);
    internal static void Preflight(RequestNamespace.Preparation preparation, object worstCore)
    {
        preparation.Encode(worstCore);
        preparation.Reservation.ResetEncoding();
    }
}
