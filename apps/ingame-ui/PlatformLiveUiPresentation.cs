using System.Text.Json;
using System.Text.Json.Serialization;
using Godot;

namespace STS2PlatformLiveUi;

/// <summary>
/// Presentation-only layout state. It is deliberately independent from Connector,
/// Policy Runtime and Human evidence; it may be persisted locally and fail-soft.
/// </summary>
public sealed record PlatformLiveLayoutState(
    int Version,
    Vector2 WorkspacePosition,
    Vector2 WorkspaceSize,
    string ActiveSurface,
    bool Compact = false)
{
    public const int CurrentVersion = 5;

    public static PlatformLiveLayoutState Defaults => new(
        CurrentVersion,
        new Vector2(52, 64),
        new Vector2(760, 500),
        "workbench");
}

public static class PlatformLiveLayout
{
    public static readonly Vector2 NormalMinimumSize = new(640, 420);
    public static readonly Vector2 CompactSize = new(440, 174);
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    static PlatformLiveLayout()
    {
        Options.Converters.Add(new Vector2JsonConverter());
    }

    private sealed class Vector2JsonConverter : JsonConverter<Vector2>
    {
        public override Vector2 Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
        {
            using JsonDocument document = JsonDocument.ParseValue(ref reader);
            JsonElement value = document.RootElement;
            return new Vector2(value.GetProperty("x").GetSingle(), value.GetProperty("y").GetSingle());
        }

        public override void Write(Utf8JsonWriter writer, Vector2 value, JsonSerializerOptions options)
        {
            writer.WriteStartObject();
            writer.WriteNumber("x", value.X);
            writer.WriteNumber("y", value.Y);
            writer.WriteEndObject();
        }
    }

    public static string LocalPath()
    {
        string root = System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData);
        return Path.Combine(root, "STS2Platform", "live-ui-layout-v5.json");
    }

    public static PlatformLiveLayoutState Load()
    {
        try
        {
            string path = LocalPath();
            if (!File.Exists(path))
                return PlatformLiveLayoutState.Defaults;
            PlatformLiveLayoutState? state = JsonSerializer.Deserialize<PlatformLiveLayoutState>(
                File.ReadAllText(path), Options);
            return state is { Version: PlatformLiveLayoutState.CurrentVersion }
                ? state
                : PlatformLiveLayoutState.Defaults;
        }
        catch
        {
            return PlatformLiveLayoutState.Defaults;
        }
    }

    public static bool Save(PlatformLiveLayoutState state)
    {
        try
        {
            string path = LocalPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(state, Options));
            File.Move(temporary, path, true);
            return true;
        }
        catch
        {
            return false;
        }
    }

    public static PlatformLiveLayoutState SelectSurface(
        PlatformLiveLayoutState state,
        string selectedSurface)
    {
        if (selectedSurface is not ("agent_run" or "human_recorder" or "workbench"))
            return state;
        return state with { ActiveSurface = selectedSurface };
    }

    public static Rect2 ClampWorkspace(
        Rect2 requested,
        Vector2 viewport,
        bool compact = false)
    {
        Vector2 min = compact ? CompactSize : NormalMinimumSize;
        Vector2 max = compact ? CompactSize : new(Math.Max(min.X, viewport.X - 32), Math.Max(min.Y, viewport.Y - 48));
        Vector2 size = new(
            Math.Clamp(requested.Size.X, min.X, max.X),
            Math.Clamp(requested.Size.Y, min.Y, max.Y));
        Vector2 position = new(
            Math.Clamp(requested.Position.X, 8, Math.Max(8, viewport.X - size.X - 8)),
            Math.Clamp(requested.Position.Y, 8, Math.Max(8, viewport.Y - size.Y - 8)));
        return new Rect2(position, size);
    }

    public static string PolicyUnavailableReason(PlatformLiveStatus status)
    {
        if (status.PolicyRuntime != null)
        {
            if (status.PolicyRuntime.Tainted)
                return $"Policy Runtime tainted: {status.PolicyRuntime.TaintReason ?? "unknown reason"}.";
            if (status.PolicyRuntime.Lifecycle != "running")
                return $"Policy Runtime is {status.PolicyRuntime.Lifecycle}; Human remains in control.";
        }
        if (status.PolicyRuntimeTransportStatus != "connected")
            return status.PolicyRuntimeTransportDetail ?? "Policy Runtime transport is unavailable.";
        return "Policy Runtime has not exposed a usable status.";
    }
}

public sealed record PlatformLiveToast(string Key, string Message, DateTimeOffset ExpiresAt);
