using System.Text.Json;
using STS2Connector.HostControl;
using Xunit;

namespace STS2Connector.NativeLogical.Tests;

public sealed class HostProfileIsolationTests
{
    [Fact]
    public void ActualUserDataPathAndGenerationMustMatchTheHostMarker()
    {
        string root = Path.Combine(Path.GetTempPath(), "sts2-profile-binding-" + Guid.NewGuid().ToString("N"), "worker");
        string home = Path.Combine(root, "home");
        string userData = Path.Combine(home, "UserData");
        string nativeProfile = Path.Combine(userData, "default", "1", "modded", "profile1");
        Directory.CreateDirectory(nativeProfile);
        string marker = Path.Combine(root, "headless-profile.json");
        void WriteMarker(object value) => File.WriteAllText(marker, JsonSerializer.Serialize(value));
        bool Verify(string? generation = "generation-1", string? actual = null, string? native = null,
            string? actualHome = null, string? actualUserProfile = null) => HostProfileIsolation.Verify(root,
                userData, generation, actual ?? userData, native ?? nativeProfile,
                actualHome ?? home, actualUserProfile ?? home);
        try
        {
            WriteMarker(new { schema_version = 1, profile_id = "worker", generation_id = "generation-1" });
            Assert.True(Verify());
            Assert.False(Verify(generation: "old-generation"));
            Assert.False(Verify(actual: Path.Combine(Path.GetTempPath(), "personal")));
            Assert.False(Verify(native: Path.Combine(userData + "-sibling", "profile1")));
            Assert.False(Verify(actualHome: Path.Combine(Path.GetTempPath(), "personal")));
            Assert.False(Verify(actualUserProfile: Path.Combine(Path.GetTempPath(), "personal")));
            Assert.False(HostProfileIsolation.Verify(null, userData, "generation-1", userData, nativeProfile, home, home));
            WriteMarker(new { schema_version = 1, profile_id = "other", generation_id = "generation-1" });
            Assert.False(Verify());
            WriteMarker(new { schema_version = 2, profile_id = "worker", generation_id = "generation-1" });
            Assert.False(Verify());
            File.WriteAllText(marker, new string(' ', 4097));
            Assert.False(Verify());
            File.Delete(marker);
            Assert.False(Verify());
        }
        finally { Directory.Delete(Path.GetDirectoryName(root)!, recursive: true); }
    }
}
