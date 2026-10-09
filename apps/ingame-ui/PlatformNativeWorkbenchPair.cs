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
    internal const string CloseSchema = "sts2.platform/native-workbench-close-1";
    private static Authority? _production;

    // TaskBridge captures the process-immutable runtime before starting its listener.
    // No controller Snapshot/expiry callback is invoked under an authentication lock.
    internal static void Initialize(string runtime)
    {
        if (!PlatformNativeWorkbenchPair.Bounded(runtime, 128))
            throw new InvalidOperationException("invalid_native_runtime");
        var created = new Authority(runtime, () => PlatformNativeWorkbenchBootstrap.Read(
            PlatformLiveUiMod.CurrentArtifactIdentity().ArtifactSha256 ?? "unavailable"));
        Authority? prior = Interlocked.CompareExchange(ref _production, created, null);
        if (prior is not null && prior.Runtime != runtime)
            throw new InvalidOperationException("native_runtime_owner_changed");
    }

    internal static Authority Production => _production
        ?? throw new InvalidOperationException("native_workbench_not_initialized");
    internal static PlatformNativeWorkbenchConnection? Current => _production?.Current;
    internal static bool IsCurrent(PlatformNativeWorkbenchConnection connection) => Current == connection;

    // One production instance; tests instantiate this same owner with a fresh private
    // bootstrap fixture. No caller-supplied pre-read authority can reset retirement.
    internal sealed class Authority(string runtime, Func<PlatformNativeWorkbenchBootstrap> bootstrapReader,
        Func<long>? clock = null)
    {
        internal const int MaximumRetiredContexts = 32;
        private sealed record Context(string Workbench, string Configuration, string Url);
        private readonly object _gate = new();
        private readonly HashSet<Context> _retired = [];
        private readonly Func<long> _clock = clock ?? (() => DateTimeOffset.UtcNow.ToUnixTimeSeconds());
        private PlatformNativeWorkbenchConnection? _current;
        private string? _credentialGeneration, _selectionIdentity;
        private long _clockHighWater;
        private bool _selectionRequiresNewGeneration;
        internal string Runtime { get; } = runtime;

        private static Context Key(PlatformNativeWorkbenchPair pair) =>
            new(pair.WorkbenchInstanceId, pair.ConfigurationId, pair.WorkbenchUrl);

        private void Select(PlatformNativeWorkbenchBootstrap bootstrap, long now)
        {
            if (_credentialGeneration != bootstrap.CredentialGeneration)
            {
                _credentialGeneration = bootstrap.CredentialGeneration;
                _selectionIdentity = bootstrap.SelectionIdentity;
                _clockHighWater = now;
                _selectionRequiresNewGeneration = false;
                _current = null;
                _retired.Clear();
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

        private void Scope(PlatformNativeWorkbenchPair pair, long now)
        {
            pair.Validate();
            if (pair.RuntimeInstanceId != Runtime || pair.ExpiresAt <= now || pair.ExpiresAt > now + 600)
                throw new InvalidOperationException("native_pair_identity_mismatch");
        }

        internal object Register(JsonElement body)
        {
            lock (_gate)
            {
                PlatformNativeWorkbenchBootstrap bootstrap = bootstrapReader();
                PlatformNativeWorkbenchPair pair = PlatformNativeWorkbenchPair.ReadSigned(body,
                    PlatformNativeWorkbenchPair.Schema, "native-register-v1", bootstrap.Secret);
                long now = _clock();
                Scope(pair, now);
                // Signature/scope are checked against this fresh authoritative read
                // before any generation transition or terminal-state reset.
                Select(bootstrap, now);
                Context context = Key(pair);
                if (_retired.Contains(context)) throw new InvalidOperationException("native_context_retired");
                PlatformNativeWorkbenchPair? active = _current?.Binding;
                if (active is not null && active.ExpiresAt > now)
                {
                    if (Key(active) != context) throw new InvalidOperationException("native_pair_conflict");
                    if (pair != active && pair.ExpiresAt <= active.ExpiresAt)
                        throw new InvalidOperationException("native_pair_renewal_rejected");
                }
                else if (_retired.Count >= MaximumRetiredContexts)
                    throw new InvalidOperationException("native_auth_capacity_reached");
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

        internal object CurrentProof(System.Collections.Specialized.NameValueCollection headers)
        {
            lock (_gate)
            {
                PlatformNativeWorkbenchBootstrap bootstrap = bootstrapReader();
                PlatformNativeWorkbenchConnection current = _current
                    ?? throw new InvalidOperationException("native_pair_required");
                long now = _clock();
                Scope(current.Binding, now);
                Headers(headers, current.Binding, bootstrap.Secret);
                if (current.SelectionIdentity != bootstrap.SelectionIdentity)
                    throw new InvalidOperationException("native_pair_changed");
                Select(bootstrap, now);
                return current.Binding.Signed(PlatformNativeWorkbenchPair.CurrentSchema,
                    "native-current-v1", bootstrap.Secret);
            }
        }

        internal object Close(JsonElement body, System.Collections.Specialized.NameValueCollection headers)
        {
            lock (_gate)
            {
                PlatformNativeWorkbenchBootstrap bootstrap = bootstrapReader();
                PlatformNativeWorkbenchPair pair = PlatformNativeWorkbenchPair.Read(body);
                long now = _clock();
                Scope(pair, now);
                Headers(headers, pair, bootstrap.Secret);
                Select(bootstrap, now);
                if (_current is not null && _current.Binding != pair)
                    throw new InvalidOperationException("native_pair_conflict");
                Context context = Key(pair);
                if (!_retired.Contains(context) && _retired.Count >= MaximumRetiredContexts)
                    throw new InvalidOperationException("native_auth_capacity_reached");
                string status = _current is null ? "already_closed" : "closed";
                _retired.Add(context);
                _current = null;
                return new { schema = CloseSchema, status, binding = pair };
            }
        }

        internal PlatformNativeWorkbenchConnection? Current
        {
            get
            {
                lock (_gate)
                {
                    try
                    {
                        // The read itself shares the auth linearization point. An old
                        // read delayed before I/O can never rewind a newer selection.
                        PlatformNativeWorkbenchBootstrap bootstrap = bootstrapReader();
                        Select(bootstrap, _clock());
                        return _current?.Binding.ExpiresAt > _clockHighWater ? _current : null;
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException
                        or JsonException or InvalidOperationException or ArgumentException)
                    { return null; }
                }
            }
        }
    }
}
