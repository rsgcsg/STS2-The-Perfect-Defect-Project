using Godot;
using MegaCrit.Sts2.Core.Modding;
using HttpClient = System.Net.Http.HttpClient;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using static Godot.Control;

namespace STS2PlatformLiveUi;

#if !STS2_PLATFORM_UNIFIED
[ModInitializer("Initialize")]
#endif
public static class PlatformLiveUiMod
{
    private static bool _initialized;
    private static PlatformLivePanel? _panel;

    public static void Initialize()
    {
        if (_initialized)
            return;

        _initialized = true;
        try
        {
            var layer = new CanvasLayer
            {
                Layer = 100
            };
            var tree = (SceneTree)Engine.GetMainLoop();
            var root = tree.Root;
            var panel = new PlatformLivePanel();
            GD.Print($"[STS2 Platform Live UI] identity {JsonSerializer.Serialize(RuntimeIdentity())}");
            layer.AddChild(panel.Root);
            PlatformLiveUiMount? mount = null;
            mount = new PlatformLiveUiMount(
                callback => Callable.From(callback).CallDeferred(),
                () => GodotObject.IsInstanceValid(tree) && GodotObject.IsInstanceValid(root)
                    && root.IsInsideTree(),
                () =>
                {
                    GD.Print("[STS2 Platform Live UI] adding layer to SceneTree root");
                    root.AddChild(layer);
                },
                () => layer.IsInsideTree() && panel.Root.IsInsideTree()
                    && layer.GetParent() == root && panel.Root.GetTree() == tree,
                () =>
                {
                    if (!panel.Mount(tree))
                        throw new InvalidOperationException("Live UI panel preparation failed.");
                },
                () =>
                {
                    _panel = panel;
                    GD.Print("[STS2 Platform Live UI] panel ready; input=mouse; visible=true; HUD=game-desk");
                    GD.Print("[STS2 Platform Live UI] layer added; game desk ready. Commands map to the paired application.");
                },
                exception => GD.PrintErr($"[STS2 Platform Live UI] deferred mount failed: {exception}"),
                () =>
                {
                    if (GodotObject.IsInstanceValid(root) && mount is not null)
                        root.TreeExiting -= mount.Cancel;
                    panel.Dispose();
                    if (ReferenceEquals(_panel, panel))
                        _panel = null;
                    if (GodotObject.IsInstanceValid(layer))
                    {
                        if (layer.IsInsideTree())
                            layer.QueueFree();
                        else
                            layer.Free();
                    }
                });
            root.TreeExiting += mount.Cancel;
            mount.Begin();
        }
        catch (Exception exception)
        {
            GD.PrintErr($"[STS2 Platform Live UI] initialization failed: {exception}");
        }
    }

    private static object RuntimeIdentity()
    {
        PlatformArtifactIdentity identity = CurrentArtifactIdentity();
        return new
        {
            schema = "sts2.platform/live-ui-loaded-identity-1",
            loaded_at = DateTimeOffset.UtcNow.ToString("O"),
            artifact_sha256 = identity.ArtifactSha256,
            module_version_id = identity.ModuleVersionId,
            source_revision = identity.SourceRevision,
            source_digest_sha256 = ReadAssemblyMetadata("LiveUiSourceDigestSha256", "SourceDigestSha256"),
            version = identity.Version
        };
    }

    private static readonly Lazy<PlatformArtifactIdentity> ArtifactIdentity = new(ReadArtifactIdentity);

    internal static PlatformArtifactIdentity CurrentArtifactIdentity() => ArtifactIdentity.Value;

    private static PlatformArtifactIdentity ReadArtifactIdentity()
    {
        Assembly assembly = typeof(PlatformLiveUiMod).Assembly;
        Dictionary<string, string> metadata = assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToDictionary(item => item.Key, item => item.Value ?? "", StringComparer.Ordinal);
        string location = assembly.Location;
        string sha256 = File.Exists(location)
            ? Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(location))).ToLowerInvariant()
            : "unavailable";
        return new PlatformArtifactIdentity(
            "STS2 Platform Live UI",
            assembly.GetName().Version?.ToString() ?? "unavailable",
            metadata.GetValueOrDefault(
                "LiveUiSourceRevision",
                metadata.GetValueOrDefault("SourceRevision", "unavailable")),
            assembly.ManifestModule.ModuleVersionId.ToString(),
            sha256);
    }

    private static string ReadAssemblyMetadata(params string[] keys)
    {
        AssemblyMetadataAttribute[] metadata = typeof(PlatformLiveUiMod).Assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToArray();
        foreach (string key in keys)
        {
            string? value = metadata.FirstOrDefault(item =>
                    string.Equals(item.Key, key, StringComparison.Ordinal))
                ?.Value;
            if (!string.IsNullOrWhiteSpace(value))
                return value;
        }
        return "unavailable";
    }
}
