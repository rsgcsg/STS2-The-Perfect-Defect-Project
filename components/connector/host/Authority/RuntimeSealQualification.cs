using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace STS2Connector.Authority;

/// <summary>
/// Official-release identity binding for accidental drift. This is not a
/// signature verifier and does not resist a same-account local writer.
/// </summary>
internal static class RuntimeSealQualification
{
    internal const string Repository = "rsgcsg/STS2-The-Perfect-Defect-Project";
    internal const int DocumentLimit = 65536;
    private const string SealSchema = "sts2.platform/runtime-seal-1";
    private const string ProvenanceSchema = "sts2.platform/official-release-provenance-1";
    private const string ModsetScope = "manager_state+ordered_manifest_identity+load_state+source+workshop_id+loaded_assembly_name_version_mvid";
    private static readonly Regex Version = new(
        @"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.(0|[1-9][0-9]*))?$",
        RegexOptions.CultureInvariant);

    internal static string ReadObservedHostKind(Func<string?> readDisplayServerName, string platform)
    {
        try
        {
            string? name = readDisplayServerName();
            if (Same(name, "headless")) return "headless";
            // Explicit DisplayServer names, not renderer names. Missing/new
            // drivers require review rather than inheriting live_ui fallback.
            bool knownUi = platform == "darwin" && Same(name, "macOS")
                || platform == "win32" && Same(name, "Windows")
                || platform == "linux" && (Same(name, "X11") || Same(name, "Wayland"));
            return knownUi ? "live_ui" : "unavailable";
        }
        catch
        {
            return "unavailable";
        }
    }

    internal static RuntimeSealSnapshot ReadInstalledPair(string loadedAssemblyPath)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(loadedAssemblyPath))
                return new(false, null, null, "runtime_seal_loaded_path_missing");
            string? directory = Path.GetDirectoryName(loadedAssemblyPath);
            string basename = Path.GetFileNameWithoutExtension(loadedAssemblyPath);
            if (directory is null || !string.Equals(basename, "STS2_PLATFORM", StringComparison.Ordinal))
                return new(false, null, null, "runtime_seal_not_unified_package");
            string sealPath = Path.Combine(directory, basename + ".runtime-seal.json");
            string provenancePath = Path.Combine(directory, basename + ".release-provenance.json");
            bool sealPresent = Present(sealPath);
            bool provenancePresent = Present(provenancePath);
            if (!sealPresent && !provenancePresent)
                return new(false, null, null, "runtime_seal_missing");
            if (!sealPresent || !provenancePresent)
                return new(true, null, null, "runtime_seal_pair_incomplete");
            return new(true, ReadBoundedFile(sealPath), ReadBoundedFile(provenancePath), null);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return new(true, null, null, "runtime_seal_files_unreadable");
        }
    }

    private static bool Present(string path) => File.Exists(path) || Directory.Exists(path)
        || new FileInfo(path).LinkTarget is not null;

    private static byte[] ReadBoundedFile(string path)
    {
        FileAttributes attributes = File.GetAttributes(path);
        if ((attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
            throw new IOException("Runtime seal must be an ordinary adjacent file.");
        using FileStream stream = File.OpenRead(path);
        if (stream.Length is <= 0 or > DocumentLimit)
            throw new IOException("Runtime seal document size is invalid.");
        using var buffer = new MemoryStream();
        byte[] chunk = new byte[4096];
        int count;
        while ((count = stream.Read(chunk, 0, Math.Min(chunk.Length, DocumentLimit + 1 - (int)buffer.Length))) > 0)
        {
            buffer.Write(chunk, 0, count);
            if (buffer.Length > DocumentLimit)
                throw new IOException("Runtime seal exceeds its bound.");
        }
        return buffer.ToArray();
    }

    internal static RuntimeSealPermission Evaluate(RuntimeSealSnapshot snapshot, LoadedRuntimeSealIdentity observed)
    {
        if (snapshot.Error is not null)
            return Closed(snapshot.Error);
        if (!snapshot.Present || snapshot.Seal is null || snapshot.Provenance is null)
            return Closed("runtime_seal_missing");
        try
        {
            using JsonDocument sealDocument = Parse(snapshot.Seal);
            using JsonDocument provenanceDocument = Parse(snapshot.Provenance);
            JsonElement seal = sealDocument.RootElement;
            JsonElement provenance = provenanceDocument.RootElement;
            Shape(seal, "schema", "release", "host", "environment", "evidence");
            Shape(provenance, "schema", "repository", "tag", "version", "release_id",
                "archive_asset", "archive_sha256", "seal_asset", "seal_sha256",
                "evidence_asset", "evidence_sha256");
            Equal(Text(seal, "schema"), SealSchema);
            Equal(Text(provenance, "schema"), ProvenanceSchema);
            JsonElement release = seal.GetProperty("release");
            JsonElement host = seal.GetProperty("host");
            JsonElement environment = seal.GetProperty("environment");
            JsonElement evidence = seal.GetProperty("evidence");
            Shape(release, "repository", "tag", "version", "archive_asset", "archive_sha256");
            Shape(host, "implementation_id", "connector_source_revision", "platform_source_revision",
                "compiled_source_digest_sha256", "artifact_sha256", "artifact_mvid", "protocol");
            Shape(environment, "platform", "architecture", "host_kind", "game_version", "game_commit",
                "main_assembly_hash", "main_assembly_sha256", "main_assembly_mvid", "modset_status",
                "modset_fingerprint_scope", "modset_fingerprint");
            Shape(evidence, "scope", "record_asset", "record_sha256");
            string version = Text(release, "version", 64);
            if (!Version.IsMatch(version))
                return Closed("runtime_seal_version_invalid");
            string tag = "game-mod/v" + version;
            string archive = "STS2-Platform-" + version + "-developer-kit.zip";
            string sealAsset = "STS2-Platform-" + version + "-runtime-seal.json";
            string evidenceAsset = "STS2-Platform-" + version + "-runtime-evidence.json";
            Equal(Text(release, "repository"), Repository);
            Equal(Text(release, "tag"), tag);
            Equal(Text(release, "archive_asset"), archive);
            Equal(Text(evidence, "scope"), "bootstrap_exact_bounded_lifecycle");
            Equal(Text(evidence, "record_asset"), evidenceAsset);
            string archiveSha = Digest(release, "archive_sha256", 64);
            string evidenceSha = Digest(evidence, "record_sha256", 64);
            Equal(Text(provenance, "repository"), Repository);
            Equal(Text(provenance, "tag"), tag);
            Equal(Text(provenance, "version"), version);
            Equal(Text(provenance, "archive_asset"), archive);
            Equal(Digest(provenance, "archive_sha256", 64), archiveSha);
            Equal(Text(provenance, "seal_asset"), sealAsset);
            Equal(Digest(provenance, "seal_sha256", 64), Hash(snapshot.Seal));
            Equal(Text(provenance, "evidence_asset"), evidenceAsset);
            Equal(Digest(provenance, "evidence_sha256", 64), evidenceSha);
            JsonElement releaseId = provenance.GetProperty("release_id");
            if (!releaseId.TryGetInt64(out long id) || id is <= 0 or > 9007199254740991)
                return Closed("runtime_seal_release_id_invalid");
            Equal(Text(host, "implementation_id"), "STS2_PLATFORM");
            Equal(Text(host, "protocol"), "1.0.0");
            Equal(Text(environment, "host_kind"), "live_ui");
            Equal(Text(environment, "modset_status"), "exact_platform_modset");
            Equal(Text(environment, "modset_fingerprint_scope"), ModsetScope);
            Equal(observed.ModsetFingerprintScope, ModsetScope);
            string platform = Text(environment, "platform", 16);
            string architecture = Text(environment, "architecture", 16);
            string gameVersion = Text(environment, "game_version", 64);
            string gameCommit = Text(environment, "game_commit", 40);
            if (platform is not ("darwin" or "win32" or "linux")
                || architecture is not ("arm64" or "x64")
                || !Regex.IsMatch(gameVersion, @"^v[0-9]+\.[0-9]+\.[0-9]+$", RegexOptions.CultureInvariant)
                || !Regex.IsMatch(gameCommit, @"^[0-9a-f]{8,40}$", RegexOptions.CultureInvariant))
                return Closed("runtime_seal_environment_fields_invalid");
            string connectorRevision = Digest(host, "connector_source_revision", 40);
            string platformRevision = Digest(host, "platform_source_revision", 40);
            string compiledDigest = Digest(host, "compiled_source_digest_sha256", 64);
            string artifactSha = Digest(host, "artifact_sha256", 64);
            string artifactMvid = GuidText(host, "artifact_mvid");
            string mainAssemblySha = Digest(environment, "main_assembly_sha256", 64);
            string mainAssemblyMvid = GuidText(environment, "main_assembly_mvid");
            string modsetFingerprint = Digest(environment, "modset_fingerprint", 64);
            JsonElement hashElement = environment.GetProperty("main_assembly_hash");
            if (!hashElement.TryGetInt32(out int mainAssemblyHash))
                return Closed("runtime_seal_game_hash_invalid");
            if (!Same(observed.ImplementationId, "STS2_PLATFORM") || !Same(observed.PackageVersion, version)
                || !Same(observed.Protocol, "1.0.0") || !Same(observed.ConnectorSourceRevision, connectorRevision)
                || !Same(observed.PlatformSourceRevision, platformRevision)
                || !Same(observed.CompiledSourceDigestSha256, compiledDigest)
                || !Same(observed.ArtifactSha256, artifactSha) || !Same(observed.ArtifactMvid, artifactMvid))
                return Closed("runtime_seal_loaded_artifact_mismatch");
            if (!Same(observed.Platform, platform)
                || !Same(observed.Architecture, architecture)
                || !Same(observed.HostKind, "live_ui")
                || !Same(observed.GameVersion, gameVersion)
                || !Same(observed.GameCommit, gameCommit)
                || observed.MainAssemblyHash != mainAssemblyHash
                || !Same(observed.MainAssemblySha256, mainAssemblySha)
                || !Same(observed.MainAssemblyMvid, mainAssemblyMvid)
                || !Same(observed.ModsetStatus, "exact_platform_modset")
                || !Same(observed.ModsetFingerprint, modsetFingerprint))
                return Closed("runtime_seal_loaded_environment_mismatch");
            return new(true, "runtime_seal_exact", "The loaded unified artifact and current environment match the official-release identity pair. Independent game and Modset admission remain required.");
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException or ArgumentException or KeyNotFoundException)
        {
            return Closed("runtime_seal_invalid_document_or_binding");
        }
    }

    private static RuntimeSealPermission Closed(string code) =>
        new(false, code, "Host mutation is disabled: " + code + ". Repair through the official prepared-release installation owner and cold-start the game.");

    private static JsonDocument Parse(byte[] raw)
    {
        if (raw.Length is <= 0 or > DocumentLimit)
            throw new FormatException("Runtime seal document bound.");
        return JsonDocument.Parse(raw, new JsonDocumentOptions { MaxDepth = 16 });
    }

    private static void Shape(JsonElement element, params string[] fields)
    {
        if (element.ValueKind != JsonValueKind.Object)
            throw new FormatException("Expected object.");
        var actual = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonProperty property in element.EnumerateObject())
        {
            // JsonDocument preserves duplicate raw properties. Reject before
            // GetProperty or deserialization can select the final occurrence.
            if (!actual.Add(property.Name))
                throw new FormatException("Duplicate property.");
        }
        if (!actual.SetEquals(fields))
            throw new FormatException("Unexpected document shape.");
    }

    private static string Text(JsonElement element, string field, int limit = 256)
    {
        JsonElement value = element.GetProperty(field);
        if (value.ValueKind != JsonValueKind.String)
            throw new FormatException("Expected string.");
        string text = value.GetString()!;
        if (text.Length == 0 || text.Length > limit || text.Any(character => character < 32 || character > 126))
            throw new FormatException("Invalid bounded string.");
        return text;
    }

    private static string Digest(JsonElement element, string field, int length)
    {
        string value = Text(element, field, length);
        if (value.Length != length || value.Any(character => !(character is >= '0' and <= '9' or >= 'a' and <= 'f')))
            throw new FormatException("Invalid digest.");
        return value;
    }

    private static string GuidText(JsonElement element, string field)
    {
        string value = Text(element, field, 36);
        if (!Guid.TryParseExact(value, "D", out Guid guid) || guid.ToString("D") != value)
            throw new FormatException("Invalid canonical GUID.");
        return value;
    }

    private static string Hash(byte[] raw) => Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant();
    private static bool Same(string? left, string right) => string.Equals(left, right, StringComparison.Ordinal);
    private static void Equal(string left, string right)
    {
        if (!Same(left, right)) throw new FormatException("Runtime seal binding mismatch.");
    }
}

internal sealed record RuntimeSealSnapshot(bool Present, byte[]? Seal, byte[]? Provenance, string? Error);
internal sealed record RuntimeSealPermission(bool ActionExecutionAllowed, string Status, string Detail);
internal sealed record LoadedRuntimeSealIdentity(
    string? ImplementationId, string? PackageVersion, string? ConnectorSourceRevision,
    string? PlatformSourceRevision, string? CompiledSourceDigestSha256,
    string? ArtifactSha256, string? ArtifactMvid, string Protocol,
    string Platform, string Architecture, string HostKind, string? GameVersion,
    string? GameCommit, int? MainAssemblyHash, string? MainAssemblySha256,
    string? MainAssemblyMvid, string? ModsetStatus, string ModsetFingerprintScope,
    string? ModsetFingerprint);
