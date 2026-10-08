using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace STS2PlatformLiveUi;

/// <summary>One exact process/config pair. It carries no task or gameplay authority.</summary>
public sealed record PlatformNativeWorkbenchPair(
    [property: JsonPropertyName("runtime_instance_id")] string RuntimeInstanceId,
    [property: JsonPropertyName("workbench_instance_id")] string WorkbenchInstanceId,
    [property: JsonPropertyName("configuration_id")] string ConfigurationId,
    [property: JsonPropertyName("workbench_url")] string WorkbenchUrl,
    [property: JsonPropertyName("pair_id")] string PairId,
    [property: JsonPropertyName("expires_at")] long ExpiresAt)
{
    public const string Schema = "sts2.platform/native-workbench-pair-1";
    public const string AckSchema = "sts2.platform/native-workbench-pair-ack-1";
    public const string CurrentSchema = "sts2.platform/native-workbench-current-1";
    internal static readonly string[] Fields = ["runtime_instance_id", "workbench_instance_id",
        "configuration_id", "workbench_url", "pair_id", "expires_at"];

    public void Validate()
    {
        if (!Bounded(RuntimeInstanceId, 128) || !Hex(WorkbenchInstanceId, 32)
            || !Hex(ConfigurationId, 64) || !Hex(PairId, 32)
            || !PlatformWorkbenchOpenClient.IsSafeWorkbenchRoot(WorkbenchUrl)
            || ExpiresAt is <= 0 or > 9007199254740991)
            throw new InvalidOperationException("invalid_native_binding");
    }

    internal static bool Bounded(string? value, int maximum) => value is not null
        && value.Length > 0 && Encoding.UTF8.GetByteCount(value) <= maximum
        && !value.Any(char.IsControl);

    internal static bool Hex(string? value, int length) => value is not null && value.Length == length
        && value.All(character => character is >= '0' and <= '9' or >= 'a' and <= 'f');

    public string Sign(string secret, string role)
    {
        Validate();
        if (!Hex(secret, 64) || role is not ("native-register-v1" or "native-register-ack-v1"
            or "native-access-v1" or "native-current-v1"))
            throw new InvalidOperationException("invalid_native_role");
        string bytes = string.Join('\n', role, RuntimeInstanceId, WorkbenchInstanceId,
            ConfigurationId, WorkbenchUrl, PairId, ExpiresAt.ToString(CultureInfo.InvariantCulture)) + "\n";
        return Convert.ToHexString(HMACSHA256.HashData(Convert.FromHexString(secret),
            Encoding.UTF8.GetBytes(bytes))).ToLowerInvariant();
    }

    internal static bool EqualSecret(string? received, string expected) => received is not null
        && CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(received), Encoding.UTF8.GetBytes(expected));

    internal static bool Names(JsonElement body, params string[] expected) => body.ValueKind == JsonValueKind.Object
        && body.EnumerateObject().Select(item => item.Name).Order(StringComparer.Ordinal)
            .SequenceEqual(expected.Order(StringComparer.Ordinal), StringComparer.Ordinal);

    public static PlatformNativeWorkbenchPair Read(JsonElement body)
    {
        if (!Names(body, Fields)) throw new InvalidOperationException("invalid_native_binding");
        var result = new PlatformNativeWorkbenchPair(
            body.GetProperty("runtime_instance_id").GetString()!, body.GetProperty("workbench_instance_id").GetString()!,
            body.GetProperty("configuration_id").GetString()!, body.GetProperty("workbench_url").GetString()!,
            body.GetProperty("pair_id").GetString()!, body.GetProperty("expires_at").GetInt64());
        result.Validate();
        return result;
    }

    public static PlatformNativeWorkbenchPair ReadSigned(JsonElement body, string schema, string role, string secret)
    {
        if (!Names(body, [.. Fields, "schema", "signature"])
            || body.GetProperty("schema").GetString() != schema)
            throw new InvalidOperationException("invalid_native_pair_schema");
        var binding = new Dictionary<string, JsonElement>();
        foreach (string field in Fields) binding[field] = body.GetProperty(field);
        using JsonDocument document = JsonDocument.Parse(JsonSerializer.Serialize(binding));
        PlatformNativeWorkbenchPair pair = Read(document.RootElement);
        if (!EqualSecret(body.GetProperty("signature").GetString(), pair.Sign(secret, role)))
            throw new InvalidOperationException("invalid_native_signature");
        return pair;
    }

    public object Signed(string schema, string role, string secret) => new
    {
        schema, runtime_instance_id = RuntimeInstanceId, workbench_instance_id = WorkbenchInstanceId,
        configuration_id = ConfigurationId, workbench_url = WorkbenchUrl, pair_id = PairId,
        expires_at = ExpiresAt, signature = Sign(secret, role)
    };
}

internal sealed record PlatformNativeWorkbenchBootstrap(string ConfigPath, string GameModSha256,
    [property: JsonIgnore] string Secret)
{
    public override string ToString() => "Native Workbench bootstrap (credential withheld)";
    internal const string Schema = "spireagent/native-workbench-bootstrap-v1";
    internal static string SelectedDirectory() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Application Support",
        "spireagent", "workbench");

    internal static byte[] PrivateBytes(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.Split(Path.DirectorySeparatorChar).Contains(".."))
            throw new InvalidOperationException("private_native_path_required");
        string? ancestor = path;
        while (ancestor is not null)
        {
            if ((File.GetAttributes(ancestor) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("private_native_path_required");
            ancestor = Path.GetDirectoryName(ancestor);
        }
        if ((File.GetAttributes(path) & FileAttributes.Directory) != 0)
            throw new InvalidOperationException("private_native_file_required");
        if (!OperatingSystem.IsWindows() && (File.GetUnixFileMode(path) &
            (UnixFileMode.GroupRead | UnixFileMode.GroupWrite | UnixFileMode.GroupExecute |
             UnixFileMode.OtherRead | UnixFileMode.OtherWrite | UnixFileMode.OtherExecute)) != 0)
            throw new InvalidOperationException("private_native_file_required");
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (input.Length > 4096) throw new InvalidOperationException("private_native_file_required");
        var bytes = new byte[input.Length];
        input.ReadExactly(bytes);
        if (input.Length != bytes.Length) throw new InvalidOperationException("private_native_file_changed");
        return bytes;
    }

    internal static PlatformNativeWorkbenchBootstrap Read(string loadedArtifact, string? directory = null)
    {
        if (directory is null && !OperatingSystem.IsMacOS())
            throw new InvalidOperationException("native_access_platform_unsupported");
        string root = directory ?? SelectedDirectory();
        using JsonDocument document = JsonDocument.Parse(PrivateBytes(Path.Combine(root, "native-access.json")));
        JsonElement body = document.RootElement;
        if (!PlatformNativeWorkbenchPair.Names(body, "schema", "enabled", "config_path",
                "launcher_sha256", "game_mod_sha256", "secret")
            || body.GetProperty("schema").GetString() != Schema || !body.GetProperty("enabled").GetBoolean())
            throw new InvalidOperationException("native_access_not_configured");
        string config = body.GetProperty("config_path").GetString()!;
        string artifact = body.GetProperty("game_mod_sha256").GetString()!;
        string secret = body.GetProperty("secret").GetString()!;
        string launcherHash = body.GetProperty("launcher_sha256").GetString()!;
        if (!PlatformNativeWorkbenchPair.Bounded(config, 1024) || !PlatformNativeWorkbenchPair.Hex(secret, 64)
            || !PlatformNativeWorkbenchPair.Hex(artifact, 64) || artifact != loadedArtifact
            || !PlatformNativeWorkbenchPair.Hex(launcherHash, 64))
            throw new InvalidOperationException("native_bootstrap_identity_mismatch");
        _ = PrivateBytes(config);
        byte[] launcherBytes = PrivateBytes(Path.Combine(root, "launcher.json"));
        if (Convert.ToHexString(SHA256.HashData(launcherBytes)).ToLowerInvariant() != launcherHash)
            throw new InvalidOperationException("native_launcher_mismatch");
        using JsonDocument launcher = JsonDocument.Parse(launcherBytes);
        if (launcher.RootElement.GetProperty("schema").GetString() != "spireagent/workbench-launcher-v1"
            || launcher.RootElement.GetProperty("config_path").GetString() != config)
            throw new InvalidOperationException("native_launcher_mismatch");
        return new PlatformNativeWorkbenchBootstrap(config, artifact, secret);
    }
}

internal sealed record PlatformNativeWorkbenchConnection(PlatformNativeWorkbenchPair Binding,
    [property: JsonIgnore] string Token)
{
    public override string ToString() => "Native Workbench connection (credential withheld)";
    private static readonly object Gate = new();
    private static PlatformNativeWorkbenchConnection? _current;
    internal static PlatformNativeWorkbenchConnection? Current { get { lock (Gate) return _current; } }
    internal static void Install(PlatformNativeWorkbenchPair pair, string secret)
    { lock (Gate) _current = new(pair, pair.Sign(secret, "native-access-v1")); }
    internal static void Clear(string workbenchInstanceId)
    { lock (Gate) if (_current?.Binding.WorkbenchInstanceId == workbenchInstanceId) _current = null; }
}
