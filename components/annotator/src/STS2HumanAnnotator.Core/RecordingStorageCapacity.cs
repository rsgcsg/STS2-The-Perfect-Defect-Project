using System.Globalization;

namespace STS2HumanAnnotator.Core;

/// <summary>Admission before a new recording writes anything; never an in-run disk quota.</summary>
public static class RecordingStorageCapacity
{
    public const long OperatingReserveBytes = 2L * 1024 * 1024 * 1024;
    // The observed 563 MiB prefix plus simultaneous archive/extraction fits in
    // this planning allowance. It is not a measured upper bound for all games.
    public const long NextSessionPeakBytes = 2L * 1024 * 1024 * 1024;

    public static long ReserveBytes(string? value)
    {
        if (value == null) return OperatingReserveBytes;
        if (!long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out long parsed))
            throw new InvalidDataException("recording_workspace_reserve_invalid");
        return parsed;
    }

    public static long AvailableBytes(string directory)
    {
        string path = Path.GetFullPath(directory);
        while (!Directory.Exists(path))
            path = Path.GetDirectoryName(path)
                ?? throw new IOException("recording_filesystem_unavailable");
        // On Unix DriveInfo preserves this path and statfs observes its actual
        // filesystem, including an external mount, rather than assuming '/'.
        return new DriveInfo(path).AvailableFreeSpace;
    }

    public static void Require(string directory)
    {
        long reserve = ReserveBytes(Environment.GetEnvironmentVariable("SPIREAGENT_MIN_FREE_BYTES"));
        long required;
        try { required = checked(reserve + NextSessionPeakBytes); }
        catch (OverflowException) { throw new InvalidDataException("recording_workspace_reserve_invalid"); }
        long available = AvailableBytes(directory);
        if (available < required)
            throw new IOException($"recording_space_insufficient: available={available}; required={required}");
    }
}
