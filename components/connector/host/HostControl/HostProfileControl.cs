using System;
using System.IO;
using Godot;
using MegaCrit.Sts2.Core.Platform.Steam;
using MegaCrit.Sts2.Core.Saves;

namespace STS2Connector.HostControl;

internal sealed record HostNativeProfileBinding(bool Verified, string UserDataRoot,
    string? GenerationId, int? NativeProfileId, string? NativeProfilePath);

// The same existing Host-control process configuration binds speed and isolated
// native-UI seed preparation. Neither permission depends on the speed value.
internal static class HostProfileControl
{
    internal static string? ProfileRoot { get; private set; }
    internal static string? GenerationId { get; private set; }
    private static string? _expectedUserDataRoot;
    private static bool _configured;
    internal static bool Enabled => ProfileRoot != null;

    internal static void Configure(string? profileRoot, string? generation, string? userDataRoot)
    {
        if (_configured)
        {
            if (profileRoot != ProfileRoot || generation != GenerationId || userDataRoot != _expectedUserDataRoot)
                throw new InvalidOperationException("Conflicting Host profile process configuration.");
            return;
        }
        if (profileRoot != null || generation != null || userDataRoot != null)
        {
            if (!HostLifecycleControl.Enabled || string.IsNullOrEmpty(profileRoot)
                || !Path.IsPathFullyQualified(profileRoot) || string.IsNullOrEmpty(userDataRoot)
                || !Path.IsPathFullyQualified(userDataRoot) || string.IsNullOrEmpty(generation)
                || generation.Length > 128)
                throw new InvalidOperationException("Host profile requires process-local control and an exact isolated binding.");
            ProfileRoot = profileRoot;
            GenerationId = generation;
            _expectedUserDataRoot = userDataRoot;
        }
        _configured = true;
    }

    internal static HostNativeProfileBinding Observe(SaveManager save)
    {
        int? profileId = save.IsProfileInitialized ? save.CurrentProfileId : null;
        string? profilePath = save.IsProfileInitialized
            ? ProjectSettings.GlobalizePath(save.GetProfileScopedPath("")) : null;
        string userDataRoot = OS.GetUserDataDir();
        bool verified = HostLifecycleControl.Enabled && !SteamInitializer.Initialized
            && HostProfileIsolation.Verify(ProfileRoot, _expectedUserDataRoot, GenerationId,
                userDataRoot, profilePath, System.Environment.GetEnvironmentVariable("HOME"),
                System.Environment.GetEnvironmentVariable("USERPROFILE"));
        return new(verified, userDataRoot, verified ? GenerationId : null, profileId, profilePath);
    }
}
