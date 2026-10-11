using System;
using System.IO;
using System.Text.Json;

namespace STS2Connector.HostControl;

internal static class HostProfileIsolation
{
    internal static bool Verify(string? profileRoot, string? expectedUserDataRoot, string? generationId,
        string actualUserDataRoot, string? nativeProfilePath, string? home, string? userProfile)
    {
        try
        {
            if (profileRoot == null || expectedUserDataRoot == null || nativeProfilePath == null
                || !SamePath(actualUserDataRoot, expectedUserDataRoot)
                || !Contained(profileRoot, actualUserDataRoot)
                || !Contained(actualUserDataRoot, nativeProfilePath)
                || !SamePath(home, Path.Combine(profileRoot, "home"))
                || !SamePath(userProfile, Path.Combine(profileRoot, "home"))) return false;
            foreach (string target in new[] { actualUserDataRoot, nativeProfilePath })
            {
                string? current = Path.GetFullPath(target);
                while (current != null && (SamePath(current, profileRoot) || Contained(profileRoot, current)))
                {
                    if (Directory.Exists(current) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return false;
                    if (SamePath(current, profileRoot)) break;
                    current = Path.GetDirectoryName(current);
                }
            }
            string marker = Path.Combine(profileRoot, "headless-profile.json");
            var info = new FileInfo(marker);
            if (!info.Exists || info.Length > 4096 || info.LinkTarget != null) return false;
            using FileStream stream = File.OpenRead(marker);
            var bytes = new byte[4097];
            int length = stream.Read(bytes, 0, bytes.Length);
            if (length > 4096) return false;
            using JsonDocument generation = JsonDocument.Parse(bytes.AsMemory(0, length));
            return generation.RootElement.GetProperty("schema_version").GetInt32() == 1
                && generation.RootElement.GetProperty("generation_id").GetString() == generationId
                && generation.RootElement.GetProperty("profile_id").GetString() == Path.GetFileName(profileRoot);
        }
        catch { return false; }
    }

    private static bool SamePath(string? first, string second) => first != null
        && string.Equals(Path.TrimEndingDirectorySeparator(Path.GetFullPath(first)),
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(second)),
            OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal);

    private static bool Contained(string root, string target)
    {
        string relative = Path.GetRelativePath(root, target);
        return relative != "." && relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal)
            && !Path.IsPathFullyQualified(relative);
    }
}
