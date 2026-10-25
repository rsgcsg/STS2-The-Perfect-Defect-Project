using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>Operator preference only: no session, game identity or evidence survives a restart.</summary>
public sealed record P1RecordingStandby(string Schema, bool Enabled, string? Origin)
{
    public const string SettingsSchema = "sts2.human-annotator/p1-recording-standby-1";
    public const string FileName = "recording-standby.json";
    public static P1RecordingStandby Disabled { get; } = new(SettingsSchema, false, null);

    public static P1RecordingStandby From(RecordingCommand? next) =>
        next?.CaptureProfileId == P1NativeRecordingContract.ProfileId
            ? new(SettingsSchema, true, next.P1NativeOrigin) : Disabled;

    public RecordingCommand? NextSession(string commandId)
    {
        Validate();
        return Enabled ? new(commandId, RecordingCommandKind.StartNewSession,
            P1NativeRecordingContract.ProfileId) { P1NativeOrigin = Origin } : null;
    }

    private void Validate()
    {
        if (Schema != SettingsSchema || (Enabled
                ? Origin is not ("declared_human" or "agent_native_ui" or "unknown")
                : Origin != null))
            throw new InvalidDataException("recording_standby_settings_invalid");
    }

    public static P1RecordingStandby Load(string recordingRoot)
    {
        string path = Path.Combine(recordingRoot, FileName);
        if (!File.Exists(path)) return Disabled;
        var settings = JsonSerializer.Deserialize<P1RecordingStandby>(File.ReadAllBytes(path), EvidenceJson.Options)
            ?? throw new InvalidDataException("recording_standby_settings_invalid");
        settings.Validate();
        return settings;
    }

    public void Save(string recordingRoot)
    {
        Validate();
        Directory.CreateDirectory(recordingRoot);
        string path = Path.Combine(recordingRoot, FileName);
        string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(stream, this, EvidenceJson.Options);
                stream.Flush(true);
            }
            File.Move(temporary, path, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
