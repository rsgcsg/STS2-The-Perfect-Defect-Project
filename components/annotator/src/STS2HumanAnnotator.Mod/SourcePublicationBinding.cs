using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

/// <summary>Exact process-local callback binding, read only under the recording owner's gate.</summary>
internal sealed class SourcePublicationBinding(RecordingSessionStore store,
    ISourceRecordingAttachment attachment, string sessionId, string timelineId)
{
    internal bool Matches(RecordingSessionStore? currentStore, ISourceRecordingAttachment? currentAttachment,
        string? currentSessionId, string? currentTimelineId, RecordingLifecycleSnapshot lifecycle) =>
        ReferenceEquals(store, currentStore) && ReferenceEquals(attachment, currentAttachment)
        && sessionId == currentSessionId && timelineId == currentTimelineId && sessionId == lifecycle.SessionId
        && lifecycle.State is RecordingLifecycleState.Recording or RecordingLifecycleState.Paused or RecordingLifecycleState.Closing;
}
