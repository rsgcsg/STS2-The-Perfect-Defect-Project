using System.Reflection;
using System.Text.Json;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceCloseLifecycleTests
{
    [Theory]
    [InlineData(1)]
    [InlineData(2)]
    [InlineData(3)]
    public void ReceiptCannotHashWritableStreamsEvenOnPlatformsWithoutWindowsSharingEnforcement(int version)
    {
        using var f = new Fixture(version);
        f.PrepareClose();
        Assert.All(f.SourceStreams, stream => Assert.True(stream.CanWrite));
        // This calls the actual receipt producer at the unsafe boundary from the Windows incident.
        var error = Assert.Throws<TargetInvocationException>(() => f.Source.GetType()
            .GetMethod("WriteCloseReceipt", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(f.Source, null));
        Assert.Equal("source_streams_not_sealed", Assert.IsType<InvalidOperationException>(error.InnerException).Message);
        Assert.False(File.Exists(f.Receipt));

        f.Store.Dispose();
        Assert.All(f.Streams, stream => Assert.False(stream.CanWrite));
        Assert.True(f.Store.GetSnapshot().Closed);
        using var receipt = JsonDocument.Parse(File.ReadAllBytes(f.Receipt));
        foreach (var hash in receipt.RootElement.GetProperty("stream_sha256").EnumerateObject())
        {
            // FileShare.None rejects any surviving append writer, including on Unix .NET.
            using var file = new FileStream(Path.Combine(f.Store.DirectoryPath, hash.Name),
                FileMode.Open, FileAccess.Read, FileShare.None);
            using var bytes = new MemoryStream(); file.CopyTo(bytes);
            Assert.Equal(hash.Value.GetString(), SourceSessionContract.Sha256(bytes.ToArray()));
        }
        if (version == 1) Assert.Equal("pass", SourceSessionAudit.Audit(f.Store.DirectoryPath).Status);
        else if (version == 2) Assert.Equal("pass", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
        else Assert.Equal("pass", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
        f.AssertOwnerReleased();
        f.Store.Dispose(); // Resource release is idempotent and cannot rewrite the receipt.
    }

    [Theory]
    [InlineData(1, "preparation")]
    [InlineData(2, "preparation")]
    [InlineData(3, "preparation")]
    [InlineData(1, "flush")]
    [InlineData(2, "flush")]
    [InlineData(3, "flush")]
    [InlineData(1, "publication")]
    [InlineData(2, "publication")]
    [InlineData(3, "publication")]
    public void FailedCloseReleasesAllHandlesAndOwnerWithoutClaimingClosedOrAllowingAnotherAppend(int version, string phase)
    {
        using var f = new Fixture(version);
        if (phase != "preparation") f.PrepareClose();
        if (phase == "flush") Field<FileStream>(f.Store, "_journal").Dispose();
        if (phase == "publication") Directory.CreateDirectory(f.Receipt + ".tmp");
        Assert.ThrowsAny<Exception>(() => f.Store.Dispose());
        Assert.False(File.Exists(f.Receipt));
        var status = f.Store.GetSnapshot();
        Assert.False(status.Closed);
        Assert.Equal("failed", status.AppendHealth);
        Assert.False(status.Counters.Decisions!.AccountingComplete);
        Assert.False(version == 1 ? f.Store.GetSourceStatus()!.AccountingComplete
            : f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.All(f.Streams, stream => Assert.False(stream.CanWrite));
        f.AssertOwnerReleased();
        var before = Directory.GetFiles(f.Store.DirectoryPath).ToDictionary(path => path, File.ReadAllBytes);
        if (phase == "publication") Directory.Delete(f.Receipt + ".tmp");
        f.Store.Dispose(); // Removing the obstruction is not permission to retry a failed close.
        Assert.Throws<ObjectDisposedException>(f.AppendObservation);
        Assert.False(File.Exists(f.Receipt));
        Assert.All(before, item => Assert.Equal(item.Value, File.ReadAllBytes(item.Key)));
    }

    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    public void ExplicitWorkerAbortReleasesResourcesWithoutClaimingSuccessfulClose(int version)
    {
        using var f = new Fixture(version);
        f.Store.AbortSourceV2("original_worker_failure");
        var status = f.Store.GetSnapshot();
        Assert.False(status.Closed);
        Assert.Equal("failed", status.AppendHealth);
        Assert.False(status.Counters.Decisions!.AccountingComplete);
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.False(File.Exists(f.Receipt));
        Assert.All(f.Streams, stream => Assert.False(stream.CanWrite));
        f.AssertOwnerReleased();
        var before = Directory.GetFiles(f.Store.DirectoryPath).ToDictionary(path => path, File.ReadAllBytes);
        f.Store.AbortSourceV2("later_error_must_not_replace_original");
        f.Store.Dispose();
        Assert.Throws<ObjectDisposedException>(f.AppendObservation);
        Assert.All(before, item => Assert.Equal(item.Value, File.ReadAllBytes(item.Key)));
    }

    private static T Field<T>(object owner, string name) =>
        (T)owner.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;

    private sealed class Fixture : IDisposable
    {
        private readonly SourceSessionTests.Fixture? source1;
        private readonly SourceSessionV2Tests.Fixture? source2;
        internal RecordingSessionStore Store { get; }
        internal object Source { get; }
        internal FileStream[] SourceStreams { get; }
        internal FileStream[] Streams { get; }
        internal string Receipt => Path.Combine(Store.DirectoryPath, "source-close-receipt.json");
        internal Fixture(int version)
        {
            if (version == 1) { source1 = new(); Store = source1.Store; }
            else { source2 = new(version: version); Store = source2.Store; }
            Source = Field<object>(Store, version == 1 ? "_sourceSession" : "_sourceSessionV2");
            SourceStreams = Field<Dictionary<string, FileStream>>(Source, "streams").Values.ToArray();
            Streams = SourceStreams.Concat(typeof(RecordingSessionStore)
                .GetFields(BindingFlags.Instance | BindingFlags.NonPublic)
                .Where(field => field.FieldType == typeof(FileStream))
                .Select(field => field.GetValue(Store)).OfType<FileStream>()).ToArray();
            AppendObservation();
        }
        internal void AppendObservation()
        {
            if (source1 != null) Store.AppendPublicObservation(source1.Packet(1));
            else Store.AppendPublicObservationV2(source2!.Packet("title", 1, "title-continuity"));
        }
        internal void PrepareClose()
        {
            if (source1 != null)
            {
                Store.RecordSourceBoundary("close", new("stream-fixture", "1"), RecordingLifecycleState.Closing);
                Source.GetType().GetMethod("PrepareClose", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(Source, null);
            }
            else
            {
                var seals = new[] { new SourceNativeSealV2("title", "generation-title", "1", "1") };
                Store.AppendSourceBoundaryV2(Store.AdmitSourceBoundaryV2("close", new("title", "generation-title", "1"), seals));
                Store.PrepareSourceCloseV2(seals);
            }
        }
        internal void AssertOwnerReleased()
        {
            using var owner = new FileStream(Path.Combine(Store.DirectoryPath, "recording-owner.lock"),
                FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        }
        public void Dispose() { source1?.Dispose(); source2?.Dispose(); }
    }
}
