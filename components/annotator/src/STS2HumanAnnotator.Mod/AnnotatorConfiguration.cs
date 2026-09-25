using System.Text.Json;

namespace STS2HumanAnnotator.Mod;

internal sealed record AnnotatorConfiguration(
    string RecordingRoot,
    string RuntimeStatusPath)
{
    internal const string FileName = "STS2_HUMAN_ANNOTATOR.conf";
    internal const string RecordingRootEnvironmentVariable =
        "STS2_HUMAN_ANNOTATOR_RECORDING_ROOT";
    internal const string RuntimeStatusEnvironmentVariable =
        "STS2_HUMAN_ANNOTATOR_STATUS_PATH";

    internal static AnnotatorConfiguration Load(string modDirectory)
    {
        // Only Steam-managed Workshop installs change the default destination.
        // Manual development installs retain their existing paths and config.
        bool workshop = Path.GetFullPath(modDirectory).Replace('\\', '/').Contains(
            "/steamapps/workshop/content/2868840/", StringComparison.OrdinalIgnoreCase);
        string stateDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "spireagent", "annotator");
        string defaultDirectory = workshop ? stateDirectory : modDirectory;
        string defaultRoot = Path.Combine(defaultDirectory, "recordings");
        string defaultStatus = Path.Combine(defaultDirectory, "STS2_HUMAN_ANNOTATOR.runtime.json");
        string root = defaultRoot;
        string status = defaultStatus;
        string operatorConfig = Path.Combine(stateDirectory, FileName);
        string legacyConfig = Path.Combine(modDirectory, FileName);
        string configPath = workshop && File.Exists(operatorConfig) ? operatorConfig : legacyConfig;
        if (File.Exists(configPath))
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(configPath));
            JsonElement value;
            if (document.RootElement.TryGetProperty("recording_root", out value)
                && value.ValueKind == JsonValueKind.String)
                root = value.GetString() ?? root;
            if (document.RootElement.TryGetProperty("runtime_status_path", out value)
                && value.ValueKind == JsonValueKind.String)
                status = value.GetString() ?? status;
        }

        root = Environment.GetEnvironmentVariable(RecordingRootEnvironmentVariable) ?? root;
        status = Environment.GetEnvironmentVariable(RuntimeStatusEnvironmentVariable) ?? status;
        if (workshop && (InsideModDirectory(modDirectory, root) || InsideModDirectory(modDirectory, status)))
            throw new InvalidOperationException("Workshop directory cannot own mutable recording state");
        return new AnnotatorConfiguration(
            Path.GetFullPath(root),
            Path.GetFullPath(status));
    }

    private static bool InsideModDirectory(string modDirectory, string candidate)
    {
        string relative = Path.GetRelativePath(modDirectory, Path.GetFullPath(candidate));
        return relative == "." || (!relative.StartsWith("..", StringComparison.Ordinal)
            && !Path.IsPathRooted(relative));
    }
}
