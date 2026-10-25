using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static string? _standbyError;

    private static void RestoreRecordingStandby()
    {
        try
        {
            var command = P1RecordingStandby.Load(_configuration!.RecordingRoot)
                .NextSession("restart-standby");
            if (command != null) Continuous.Arm(command.CaptureProfileId, command.P1NativeOrigin);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
        {
            // A broken preference never disables the Recorder or invents an origin.
            // The next explicit operator command can replace it; originals are untouched.
            _standbyError = "recording_standby_restore_failed";
        }
    }

    private static RecordingCommandResult SaveRecordingStandby(RecordingCommand command, RecordingCommandResult result)
    {
        if (!result.Accepted || command.Kind is not (RecordingCommandKind.StartNewSession
                or RecordingCommandKind.Resume or RecordingCommandKind.Pause or RecordingCommandKind.Close))
            return result;
        try
        {
            P1RecordingStandby.From(Continuous.NextSessionCommand("standby-preference"))
                .Save(_configuration!.RecordingRoot);
            _standbyError = null;
            return result;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            _standbyError = "recording_standby_save_failed";
            // Report the actual recording operation as applied, with an explicit restart warning.
            // Do not turn a completed Start/Close into a rejection that invites a second write.
            return result with { Code = _standbyError,
                Detail = result.Detail + " Restart standby settings could not be saved; inspect recorder status before restarting." };
        }
    }
}
