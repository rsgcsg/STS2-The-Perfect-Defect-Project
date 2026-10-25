using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

[CollectionDefinition("RecordingCapacityEnvironment", DisableParallelization = true)]
public sealed class RecordingCapacityEnvironmentCollection { }

[Collection("RecordingCapacityEnvironment")]
public sealed class RecordingStorageCapacityTests
{
    [Theory]
    [InlineData("-1")]
    [InlineData("1.5")]
    [InlineData("")]
    [InlineData("9223372036854775808")]
    public void InvalidReserveCannotDisableTheCheck(string value) =>
        Assert.Throws<InvalidDataException>(() => RecordingStorageCapacity.ReserveBytes(value));

    [Fact]
    public void NativeRecordingWriterRejectsBeforeCreatingTheSessionDirectory()
    {
        string root = Path.Combine(Path.GetTempPath(), "capacity-" + Guid.NewGuid().ToString("N"));
        string? previous = Environment.GetEnvironmentVariable("SPIREAGENT_MIN_FREE_BYTES");
        var profile = HumanCaptureProfiles.FullRunReadRich;
        var manifest = new CurrentRecordingManifest(2, CurrentRecordingContract.ManifestSchema,
            "session-capacity", "timeline-capacity", DateTimeOffset.UnixEpoch, "test",
            new string('a', 40), "portable", profile.ProfileId,
            EvidenceIdentity.Sha256Json(profile), profile.SupportedActionFamilies, profile.NonClaims);
        try
        {
            Environment.SetEnvironmentVariable("SPIREAGENT_MIN_FREE_BYTES", "1152921504606846976");
            IOException error = Assert.Throws<IOException>(() => RecordingSessionStore.Create(root, manifest, profile));
            Assert.StartsWith("recording_space_insufficient", error.Message);
            Assert.False(Directory.Exists(root));
            Environment.SetEnvironmentVariable("SPIREAGENT_MIN_FREE_BYTES", "0");
            using var store = RecordingSessionStore.Create(root, manifest, profile);
            Assert.True(File.Exists(Path.Combine(store.DirectoryPath, "recording-manifest.json")));
        }
        finally
        {
            Environment.SetEnvironmentVariable("SPIREAGENT_MIN_FREE_BYTES", previous);
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }
}
