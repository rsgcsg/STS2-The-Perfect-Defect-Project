using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class MixedRunManifestCompatibilityTests
{
    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    [InlineData(4)]
    public void MixedIdentityDoesNotExtendHistoricalSourceManifests(int version)
    {
        using var fixture = new SourceSessionV2Tests.Fixture(version: version);
        fixture.Store.AppendPublicObservationV2(fixture.Packet("title", 1, "title-continuity"));
        fixture.Close("title", 1, new[] { new SourceNativeSealV2("title", "generation-title", "1", "1") });
        string manifestPath = Path.Combine(fixture.Store.DirectoryPath, "recording-manifest.json");
        var manifest = JsonNode.Parse(File.ReadAllBytes(manifestPath))!.AsObject();
        Assert.False(manifest.ContainsKey("mixed_run_id"));
        Assert.Equal("pass", Audit().Status);

        manifest["mixed_run_id"] = new string('a', 32);
        File.WriteAllText(manifestPath, manifest.ToJsonString(EvidenceJson.Options));
        var changed = Audit();
        Assert.Equal("fail", changed.Status);
        Assert.Contains("source_json_unknown_field", changed.Errors);

        SourceSessionAuditResult Audit() => version == 4 ? SourceSessionAuditV4.Audit(fixture.Store.DirectoryPath)
            : version == 3 ? SourceSessionAuditV3.Audit(fixture.Store.DirectoryPath)
            : SourceSessionAuditV2.Audit(fixture.Store.DirectoryPath);
    }
}
