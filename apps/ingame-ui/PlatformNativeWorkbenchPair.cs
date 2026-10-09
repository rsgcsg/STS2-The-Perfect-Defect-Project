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
    [property: JsonIgnore] string Secret, string LauncherSha256, string ConfigSha256)
{
    public override string ToString() => "Native Workbench bootstrap (credential withheld)";
    internal const string Schema = "spireagent/native-workbench-bootstrap-v1";
    [JsonIgnore] internal string CredentialGeneration => Hash(Secret);
    [JsonIgnore] internal string SelectionIdentity => Hash(string.Join('\n', ConfigPath,
        GameModSha256, LauncherSha256, ConfigSha256, Secret));
    private static string Hash(string value) => Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
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
        byte[] configBytes = PrivateBytes(config);
        byte[] launcherBytes = PrivateBytes(Path.Combine(root, "launcher.json"));
        if (Convert.ToHexString(SHA256.HashData(launcherBytes)).ToLowerInvariant() != launcherHash)
            throw new InvalidOperationException("native_launcher_mismatch");
        using JsonDocument launcher = JsonDocument.Parse(launcherBytes);
        if (launcher.RootElement.GetProperty("schema").GetString() != "spireagent/workbench-launcher-v1"
            || launcher.RootElement.GetProperty("config_path").GetString() != config)
            throw new InvalidOperationException("native_launcher_mismatch");
        return new PlatformNativeWorkbenchBootstrap(config, artifact, secret, launcherHash,
            Convert.ToHexString(SHA256.HashData(configBytes)).ToLowerInvariant());
    }
}

internal sealed record PlatformNativeWorkbenchConnection(PlatformNativeWorkbenchPair Binding,
    [property: JsonIgnore] string Token, [property: JsonIgnore] string SelectionIdentity)
{
    public override string ToString() => "Native Workbench connection (credential withheld)";
    internal const int MaximumRetiredContexts = 32;
    internal const string CloseSchema = "sts2.platform/native-workbench-close-1";
    private sealed record Context(string Runtime, string Workbench, string Configuration, string Url);
    private static readonly object Gate = new();
    private static readonly HashSet<Context> Retired = [];
    private static PlatformNativeWorkbenchConnection? _current;
    private static string? _credentialGeneration, _selectionIdentity, _runtime;
    private static long _clockHighWater;
    private static bool _selectionRequiresNewGeneration;

    private static Context Key(PlatformNativeWorkbenchPair pair) =>
        new(pair.RuntimeInstanceId, pair.WorkbenchInstanceId, pair.ConfigurationId, pair.WorkbenchUrl);

    // Only a cryptographically new selected credential or confirmed game identity
    // can reset terminal retirement. Same-credential selection drift is not a grant.
    private static void Select(PlatformNativeWorkbenchBootstrap bootstrap, string runtime, long now)
    {
        if (_credentialGeneration != bootstrap.CredentialGeneration || _runtime != runtime)
        {
            _credentialGeneration = bootstrap.CredentialGeneration;
            _selectionIdentity = bootstrap.SelectionIdentity;
            _runtime = runtime;
            _clockHighWater = now;
            _selectionRequiresNewGeneration = false;
            _current = null;
            Retired.Clear();
        }
        else
        {
            if (now < _clockHighWater) throw new InvalidOperationException("native_clock_regressed");
            _clockHighWater = now;
            if (_selectionIdentity != bootstrap.SelectionIdentity)
            {
                _current = null;
                _selectionRequiresNewGeneration = true;
            }
        }
        if (_selectionRequiresNewGeneration)
            throw new InvalidOperationException("native_selection_generation_required");
    }

    private static void Scope(PlatformNativeWorkbenchPair pair, string runtime, long now)
    {
        pair.Validate();
        if (pair.RuntimeInstanceId != runtime || pair.ExpiresAt <= now || pair.ExpiresAt > now + 600)
            throw new InvalidOperationException("native_pair_identity_mismatch");
    }

    internal static object Register(PlatformNativeWorkbenchPair pair,
        PlatformNativeWorkbenchBootstrap bootstrap, string runtime, long now)
    {
        lock (Gate)
        {
            Select(bootstrap, runtime, now);
            Scope(pair, runtime, now);
            Context context = Key(pair);
            if (Retired.Contains(context)) throw new InvalidOperationException("native_context_retired");
            PlatformNativeWorkbenchPair? active = _current?.Binding;
            if (active is not null && active.ExpiresAt > now)
            {
                if (Key(active) != context) throw new InvalidOperationException("native_pair_conflict");
                if (pair != active && pair.ExpiresAt <= active.ExpiresAt)
                    throw new InvalidOperationException("native_pair_renewal_rejected");
            }
            else if (Retired.Count >= MaximumRetiredContexts)
                throw new InvalidOperationException("native_auth_capacity_reached");
            // Retired.Count+the current context's reserved retirement entry <=32.
            _current = new(pair, pair.Sign(bootstrap.Secret, "native-access-v1"), bootstrap.SelectionIdentity);
            return pair.Signed(PlatformNativeWorkbenchPair.AckSchema, "native-register-ack-v1", bootstrap.Secret);
        }
    }

    private static void Headers(System.Collections.Specialized.NameValueCollection headers,
        PlatformNativeWorkbenchPair pair, string secret)
    {
        if (headers["Origin"] is not null || headers["Cookie"] is not null
            || !PlatformNativeWorkbenchPair.EqualSecret(headers["Authorization"],
                "Bearer " + pair.Sign(secret, "native-access-v1"))
            || headers["X-STS2-Game-Instance-ID"] != pair.RuntimeInstanceId
            || headers["X-SpireAgent-Workbench-Instance-ID"] != pair.WorkbenchInstanceId
            || headers["X-SpireAgent-Configuration-ID"] != pair.ConfigurationId
            || headers["X-SpireAgent-Pair-ID"] != pair.PairId)
            throw new InvalidOperationException("native_pair_changed");
    }

    internal static object CurrentProof(System.Collections.Specialized.NameValueCollection headers,
        PlatformNativeWorkbenchBootstrap bootstrap, string runtime, long now)
    {
        lock (Gate)
        {
            Select(bootstrap, runtime, now);
            PlatformNativeWorkbenchPair pair = _current?.Binding
                ?? throw new InvalidOperationException("native_pair_required");
            Scope(pair, runtime, now);
            Headers(headers, pair, bootstrap.Secret);
            // Compare and signature share this authentication linearization point.
            return pair.Signed(PlatformNativeWorkbenchPair.CurrentSchema, "native-current-v1", bootstrap.Secret);
        }
    }

    internal static object Close(PlatformNativeWorkbenchPair pair,
        System.Collections.Specialized.NameValueCollection headers,
        PlatformNativeWorkbenchBootstrap bootstrap, string runtime, long now)
    {
        lock (Gate)
        {
            Select(bootstrap, runtime, now);
            Scope(pair, runtime, now);
            Headers(headers, pair, bootstrap.Secret);
            if (_current is not null && _current.Binding != pair)
                throw new InvalidOperationException("native_pair_conflict");
            Context context = Key(pair);
            if (!Retired.Contains(context) && Retired.Count >= MaximumRetiredContexts)
                throw new InvalidOperationException("native_auth_capacity_reached");
            string status = _current is null ? "already_closed" : "closed";
            Retired.Add(context);
            _current = null;
            return new { schema = CloseSchema, status, binding = pair };
        }
    }

    internal static PlatformNativeWorkbenchConnection? Current
    {
        get
        {
            try
            {
                PlatformNativeWorkbenchBootstrap bootstrap = PlatformNativeWorkbenchBootstrap.Read(
                    PlatformLiveUiMod.CurrentArtifactIdentity().ArtifactSha256 ?? "unavailable");
                lock (Gate)
                {
                    if (_runtime is null) return null;
                    Select(bootstrap, _runtime, DateTimeOffset.UtcNow.ToUnixTimeSeconds());
                    return _current?.Binding.ExpiresAt > _clockHighWater ? _current : null;
                }
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException
                or JsonException or InvalidOperationException or ArgumentException)
            { return null; }
        }
    }

    internal static bool IsCurrent(PlatformNativeWorkbenchConnection connection) => Current == connection;
}
