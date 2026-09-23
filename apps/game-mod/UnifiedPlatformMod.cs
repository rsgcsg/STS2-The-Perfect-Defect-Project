using System.Reflection;
using System.Text.Json;
using Godot;
using MegaCrit.Sts2.Core.Modding;
using STS2Connector;
using STS2HumanAnnotator.Mod;
using STS2PlatformLiveUi;

namespace STS2Platform.GameMod;

[ModInitializer("Initialize")]
public static class UnifiedPlatformMod
{
    public const string Version = "0.2.0-rc.10";

    public static void Initialize()
    {
        Assembly assembly = typeof(UnifiedPlatformMod).Assembly;
        Dictionary<string, string> metadata = assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToDictionary(item => item.Key, item => item.Value ?? "", StringComparer.Ordinal);
        GD.Print($"[STS2 Platform] identity {JsonSerializer.Serialize(new
        {
            schema = "sts2.platform/game-mod-loaded-identity-1",
            loaded_at = DateTimeOffset.UtcNow.ToString("O"),
            version = Version,
            artifact_sha256 = PlatformArtifactSha256(assembly),
            module_version_id = assembly.ManifestModule.ModuleVersionId.ToString("D"),
            platform_source_revision = metadata.GetValueOrDefault("PlatformSourceRevision", "unavailable"),
            platform_source_digest_sha256 = metadata.GetValueOrDefault("PlatformSourceDigestSha256", "unavailable"),
            connector_source_revision = metadata.GetValueOrDefault("ConnectorSourceRevision", "unavailable"),
            annotator_source_revision = metadata.GetValueOrDefault("AnnotatorSourceRevision", "unavailable"),
            live_ui_source_revision = metadata.GetValueOrDefault("LiveUiSourceRevision", "unavailable")
        })}");

        // STS2 discovers only this initializer. Explicit order avoids depending
        // on reflection type enumeration and preserves component ownership.
        NativeFoundationOwnerPatches.Initialize();
        ConnectorCardRewardPresentationPatches.Initialize();
        ConnectorMod.Initialize();
        RecorderMod.Initialize();
        PlatformLiveUiMod.Initialize();
        try { PlatformTaskBridge.Start(); }
        catch (Exception exception)
        { GD.PrintErr($"[STS2 Platform] task bridge unavailable; model handoff blocked: {exception.Message}"); }
        GD.Print("[STS2 Platform] unified Mod initialized; components=connector,annotator,live-ui");
    }

    private static string PlatformArtifactSha256(Assembly assembly)
    {
        string location = assembly.Location;
        return File.Exists(location)
            ? Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
                File.ReadAllBytes(location))).ToLowerInvariant()
            : "unavailable";
    }
}
