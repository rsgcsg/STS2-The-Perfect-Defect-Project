using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using STS2Connector.Authority;

static void Require(bool value, string message)
{
    if (!value) throw new Exception(message);
}

using JsonDocument fixture = JsonDocument.Parse(File.ReadAllBytes(args[0]));
Require(fixture.RootElement.GetProperty("synthetic_only").GetBoolean(), "Fixture must be synthetic.");
string seal = fixture.RootElement.GetProperty("seal_raw").GetString()!;
string provenance = fixture.RootElement.GetProperty("provenance_raw").GetString()!;
var snapshot = new RuntimeSealSnapshot(true, Encoding.UTF8.GetBytes(seal), Encoding.UTF8.GetBytes(provenance), null);
var observed = new LoadedRuntimeSealIdentity(
    "STS2_PLATFORM", "0.2.0-rc.23", new string('a', 40), new string('b', 40), new string('2', 64),
    new string('3', 64), "11111111-2222-3333-4444-555555555555", "1.0.0",
    "darwin", "arm64", "live_ui", "v0.111.0", "41cef1ea", 1010476334,
    new string('4', 64), "66666666-7777-8888-9999-aaaaaaaaaaaa", "exact_platform_modset",
    "manager_state+ordered_manifest_identity+load_state+source+workshop_id+loaded_assembly_name_version_mvid", new string('5', 64));
Require(RuntimeSealQualification.Evaluate(snapshot, observed).ActionExecutionAllowed, "Shared golden tuple rejected.");

foreach (string? driver in new string?[] { null, "", "unknown", "Metal", "macOS ", "Windows" })
{
    string kind = RuntimeSealQualification.ReadObservedHostKind(() => driver, "darwin");
    Require(kind == "unavailable", "Missing/unknown/wrong-platform display driver admitted as live UI.");
    Require(!RuntimeSealQualification.Evaluate(snapshot, observed with { HostKind = kind }).ActionExecutionAllowed,
        "Unavailable observed Host kind satisfied a live-UI seal.");
}
string failedKind = RuntimeSealQualification.ReadObservedHostKind(
    () => throw new InvalidOperationException("synthetic display observation failure"), "darwin");
Require(failedKind == "unavailable" && !RuntimeSealQualification.Evaluate(
    snapshot, observed with { HostKind = failedKind }).ActionExecutionAllowed, "Throwing display observation qualified.");
Require(RuntimeSealQualification.ReadObservedHostKind(() => "headless", "darwin") == "headless", "Headless classified as live UI.");
Require(RuntimeSealQualification.ReadObservedHostKind(() => "macOS", "darwin") == "live_ui", "Known macOS UI rejected.");
Require(RuntimeSealQualification.ReadObservedHostKind(() => "Windows", "win32") == "live_ui", "Known Windows UI rejected.");
Require(RuntimeSealQualification.ReadObservedHostKind(() => "X11", "linux") == "live_ui", "Known X11 UI rejected.");
Require(RuntimeSealQualification.ReadObservedHostKind(() => "Wayland", "linux") == "live_ui", "Known Wayland UI rejected.");

LoadedRuntimeSealIdentity[] changed = [
    observed with { ImplementationId = "STS2_MCP" },
    observed with { PackageVersion = "0.2.0-rc.24" },
    observed with { ConnectorSourceRevision = new string('c', 40) },
    observed with { PlatformSourceRevision = new string('c', 40) },
    observed with { CompiledSourceDigestSha256 = new string('c', 64) },
    observed with { ArtifactSha256 = new string('c', 64) },
    observed with { ArtifactMvid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" },
    observed with { Protocol = "1.0.1" },
    observed with { Platform = "win32" },
    observed with { Architecture = "x64" },
    observed with { HostKind = "headless" },
    observed with { GameVersion = "v0.112.0" },
    observed with { GameCommit = "changed" },
    observed with { MainAssemblyHash = null },
    observed with { MainAssemblySha256 = new string('c', 64) },
    observed with { MainAssemblyMvid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" },
    observed with { ModsetStatus = "canary_exact_observer_modset" },
    observed with { ModsetFingerprintScope = "different_algorithm" },
    observed with { ModsetFingerprint = new string('c', 64) }
];
foreach (LoadedRuntimeSealIdentity value in changed)
    Require(!RuntimeSealQualification.Evaluate(snapshot, value).ActionExecutionAllowed, "Changed loaded tuple admitted.");

// Preserve receipt-to-byte binding while varying raw document validity.
RuntimeSealSnapshot Alter(string raw)
{
    using JsonDocument document = JsonDocument.Parse(provenance);
    var values = document.RootElement.EnumerateObject().ToDictionary(property => property.Name,
        property => property.Value.Clone());
    using JsonDocument digest = JsonDocument.Parse(JsonSerializer.Serialize(
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant()));
    values["seal_sha256"] = digest.RootElement.Clone();
    return snapshot with { Seal = Encoding.UTF8.GetBytes(raw), Provenance = JsonSerializer.SerializeToUtf8Bytes(values) };
}

foreach (JsonElement rawCase in fixture.RootElement.GetProperty("invalid_seal_raw").EnumerateArray())
    Require(!RuntimeSealQualification.Evaluate(Alter(rawCase.GetString()!), observed).ActionExecutionAllowed,
        "Shared malformed raw seal admitted.");

string[] invalid = [
    seal.Replace("\"schema\":", "\"schema\":\"duplicate\",\"schema\":"),
    seal.Replace("\"protocol\":", "\"protocol\":\"duplicate\",\"protocol\":"),
    seal.Replace("\"main_assembly_hash\":1010476334", "\"main_assembly_hash\":1010476334.0"),
    seal.Replace("\"main_assembly_hash\":1010476334", "\"main_assembly_hash\":true"),
    seal.Replace("\"host_kind\":\"live_ui\"", "\"host_kind\":\"headless\""),
    seal.Replace("\"scope\":\"bootstrap_exact_bounded_lifecycle\"", "\"scope\":\"ordinary_accepted\""),
    seal.Replace("\"repository\":", "\"arbitrary_origin\":\"https://example.test\",\"repository\":"),
    seal.Replace("\"artifact_mvid\":\"11111111-2222-3333-4444-555555555555\"", "\"artifact_mvid\":\"not-a-guid\"")
];
foreach (string raw in invalid)
    Require(!RuntimeSealQualification.Evaluate(Alter(raw), observed).ActionExecutionAllowed, "Malformed/foreign raw seal admitted.");
Require(!RuntimeSealQualification.Evaluate(snapshot with { Provenance = Encoding.UTF8.GetBytes(
    provenance.Replace("\"release_id\":1234", "\"release_id\":1234.0")) }, observed).ActionExecutionAllowed,
    "Float release ID admitted.");
Require(!RuntimeSealQualification.Evaluate(snapshot with { Provenance = Encoding.UTF8.GetBytes(
    provenance.Replace("\"schema\":", "\"schema\":\"duplicate\",\"schema\":")) }, observed).ActionExecutionAllowed,
    "Duplicate raw receipt admitted.");
Require(!RuntimeSealQualification.Evaluate(snapshot with { Seal = Encoding.UTF8.GetBytes("{") }, observed).ActionExecutionAllowed,
    "Truncated seal admitted.");
Require(!RuntimeSealQualification.Evaluate(snapshot with { Seal = new byte[65537] }, observed).ActionExecutionAllowed,
    "Oversize seal admitted.");

string temporary = Path.Combine(Path.GetTempPath(), "sts2-seal-test-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(temporary);
try
{
    string dll = Path.Combine(temporary, "STS2_PLATFORM.dll");
    string sealPath = Path.Combine(temporary, "STS2_PLATFORM.runtime-seal.json");
    string receiptPath = Path.Combine(temporary, "STS2_PLATFORM.release-provenance.json");
    Require(!RuntimeSealQualification.ReadInstalledPair(dll).Present, "Missing pair should be explicit.");
    File.WriteAllText(sealPath, seal);
    Require(RuntimeSealQualification.ReadInstalledPair(dll).Error == "runtime_seal_pair_incomplete", "Partial pair was not rejected.");
    File.WriteAllText(receiptPath, provenance);
    Require(RuntimeSealQualification.Evaluate(RuntimeSealQualification.ReadInstalledPair(dll), observed).ActionExecutionAllowed,
        "Bounded adjacent pair rejected.");
    File.WriteAllBytes(sealPath, new byte[65537]);
    Require(RuntimeSealQualification.ReadInstalledPair(dll).Error == "runtime_seal_files_unreadable", "Oversize adjacent pair accepted.");
    Require(!RuntimeSealQualification.ReadInstalledPair(Path.Combine(temporary, "STS2_MCP.dll")).Present,
        "Standalone package accepted the production sidecar.");
}
finally { Directory.Delete(temporary, recursive: true); }
Console.WriteLine("C# shared runtime-seal contract conformance passed; synthetic source tests only.");
