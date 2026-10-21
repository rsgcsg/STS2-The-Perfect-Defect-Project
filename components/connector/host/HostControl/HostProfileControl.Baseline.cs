using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.DevConsole.ConsoleCommands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Characters;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Ftue;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.TestSupport;
using MegaCrit.Sts2.Core.Timeline;
using MegaCrit.Sts2.Core.Unlocks;
using STS2Connector.Authority;
using STS2Connector.LiveHost;

namespace STS2Connector.HostControl;

internal sealed record HostBaselineUnlockState(IReadOnlyList<string> UnlockedEpochs,
    IReadOnlyList<string> EncountersSeen, int NumberOfRuns);
internal sealed record HostBaselinePools(IReadOnlyList<string> Characters, IReadOnlyList<string> Cards,
    IReadOnlyList<string> Relics, IReadOnlyList<string> Potions, IReadOnlyList<string> Acts);
internal sealed record HostBaselineSnapshot(string BeforeProgressSha256, string ProgressSha256,
    string ProgressJson, HostBaselineUnlockState UnlockState, HostBaselinePools Pools,
    bool TutorialsDisabled, bool AdministrativePriorRunAdded);
internal sealed record HostBaselineFacts(bool IdentityAdmitted, bool IsolationVerified, bool ProfileLoaded,
    bool MainMenuCurrent, bool RunActive, bool Multiplayer, bool TestMode,
    string RuntimeInstanceId, string? GenerationId, int? NativeProfileId, string? NativeProfilePath, string IdentityKey);
internal sealed record HostBaselineProvenance(string Status, bool Ready, string? Recipe,
    string RuntimeInstanceId, string? ProfileGenerationId, int? NativeProfileId,
    string? NativeProfilePath, HostBaselineSnapshot? Prepared, bool? RunUnlockStateMatches = null, string? PreparationError = null);

internal static partial class HostProfileControl
{
    internal const string NormalBaselineRecipe = "normal-sp-all-unlocked-v1";
    private static BaselineSession _baseline = new(null);
    internal static string? BaselineRecipe => _baseline.Recipe;
    internal static string? ResolveBaselineRecipe(string? recipe) => recipe switch
    {
        null => null, NormalBaselineRecipe => recipe,
        _ => throw new InvalidOperationException("Unknown fixed native baseline recipe.")
    };

    // Process-local preparation state only. A failed/unknown write cannot be retried.
    internal sealed class BaselineSession(string? recipe)
    {
        internal string? Recipe { get; } = ResolveBaselineRecipe(recipe);
        private bool attempted;
        private string? preparationError;
        private HostBaselineFacts? binding;
        private HostBaselineSnapshot? prepared;
        internal HostBaselineProvenance Prepare(HostBaselineFacts facts, Func<HostBaselineSnapshot> apply)
        {
            if (Recipe == null) return Result("baseline_not_configured", facts);
            string? refusal = Refusal(facts);
            if (refusal != null) return Result(refusal, facts);
            if (prepared != null) return Observe(facts);
            if (attempted) return Result("baseline_preparation_already_attempted", facts);
            if (facts.RunActive) return Result("baseline_active_run", facts);
            if (!facts.MainMenuCurrent) return Result("baseline_main_menu_required", facts);
            attempted = true;
            try { var result = apply(); binding = facts; prepared = result; return Observe(facts); }
            catch (Exception exception)
            {
                preparationError = exception.GetType().Name + ": " + exception.Message;
                return Result("baseline_application_or_readback_failed", facts);
            }
        }
        internal HostBaselineProvenance Observe(HostBaselineFacts facts)
        {
            if (Recipe == null) return Result("baseline_not_configured", facts);
            string? refusal = Refusal(facts);
            if (refusal != null) return Result(refusal, facts);
            if (prepared == null) return Result("baseline_not_prepared", facts);
            if (binding != facts with { MainMenuCurrent = binding!.MainMenuCurrent, RunActive = binding.RunActive })
                return Result("baseline_binding_changed", facts);
            return Result("baseline_ready", facts, true);
        }
        private static string? Refusal(HostBaselineFacts f) =>
            !f.IdentityAdmitted ? "baseline_identity_unadmitted" : !f.IsolationVerified ? "baseline_isolation_unverified"
            : !f.ProfileLoaded ? "baseline_profile_not_loaded" : f.Multiplayer ? "baseline_multiplayer_refused"
            : f.TestMode ? "baseline_test_mode_refused" : null;
        private HostBaselineProvenance Result(string status, HostBaselineFacts facts, bool ready = false) =>
            new(status, ready, Recipe, facts.RuntimeInstanceId, facts.GenerationId, facts.NativeProfileId, facts.NativeProfilePath, prepared, PreparationError: preparationError);
    }

    internal static HostBaselineProvenance PrepareBaseline(string runtimeInstanceId) => BaselineRecipe == null
        ? NotConfigured(runtimeInstanceId) : _baseline.Prepare(BaselineFacts(runtimeInstanceId), ApplyNormalBaseline);

    internal static HostBaselineProvenance ObserveBaseline(string runtimeInstanceId)
    {
        if (BaselineRecipe == null) return NotConfigured(runtimeInstanceId);
        var observed = _baseline.Observe(BaselineFacts(runtimeInstanceId));
        if (observed.Ready && RunManager.Instance.DebugOnlyGetState() is { } run)
        {
            bool matches = JsonSerializer.Serialize(DescribeUnlockState(run.UnlockState))
                == JsonSerializer.Serialize(observed.Prepared!.UnlockState);
            return observed with { Ready = matches, Status = matches ? observed.Status : "baseline_run_unlock_state_mismatch",
                RunUnlockStateMatches = matches };
        }
        return observed;
    }
    private static HostBaselineProvenance NotConfigured(string runtimeInstanceId) =>
        new("baseline_not_configured", false, null, runtimeInstanceId, null, null, null, null);

    private static HostBaselineFacts BaselineFacts(string runtimeInstanceId)
    {
        var game = EnvironmentIdentityRuntime.ReadGame(); var host = EnvironmentIdentityRuntime.HostIdentity();
        var save = SaveManager.Instance; var profile = Observe(save); var surface = ActiveInputResolver.Capture();
        bool mainMenu = surface.MenuRoot != null && ReferenceEquals(surface.MenuRoot, NGame.Instance?.MainMenu)
            && surface.MenuSubmenu == null && surface.TopOverlay == null && !surface.MapIsOpen && surface.OpenModal == null;
        return new(EnvironmentIdentityRuntime.ExecutionAvailable(game), profile.Verified,
            save.IsProfileInitialized && save.IsPrefsLoaded, mainMenu,
            RunManager.Instance.DebugOnlyGetState() != null || NGame.Instance?.CurrentRunNode != null || CombatManager.Instance.IsInProgress,
            RunManager.Instance.NetService?.Type.IsMultiplayer() == true, TestMode.IsOn,
            runtimeInstanceId, profile.GenerationId, profile.NativeProfileId, profile.NativeProfilePath,
            string.Join("|", game.Version, game.Commit, game.MainAssemblySha256, game.MainAssemblyMvid,
                host.SourceRevision, host.ArtifactSha256, host.ModuleVersionId, game.Modset?.Fingerprint));
    }

    private static string Sha(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant();
    internal static HostBaselineUnlockState DescribeUnlockState(UnlockState unlocks)
    {
        var value = unlocks.ToSerializable();
        return new(value.UnlockedEpochs.Order(StringComparer.Ordinal).ToArray(),
            value.EncountersSeen.Select(id => id.ToString()).Order(StringComparer.Ordinal).ToArray(), value.NumberOfRuns);
    }
    private static string[] Ids<T>(IEnumerable<T> models) where T : AbstractModel =>
        models.Select(model => model.Id.ToString()).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
    internal static HostBaselinePools DescribePools(UnlockState unlocks) => new(
        Ids(unlocks.Characters), Ids(unlocks.CardPools.SelectMany(pool => pool.GetUnlockedCards(unlocks, CardMultiplayerConstraint.SingleplayerOnly))),
        Ids(unlocks.Relics), Ids(unlocks.Potions), Ids(ModelDb.Acts.Where(act => act.IsUnlocked(unlocks))));

    internal static void PrepareNormalBaselineTutorials(ProgressState progress)
    {
        progress.EnableFtues = false;
        // The native Standard lobby uses SeenPopup for this unlock notification;
        // unlike SeenFtue, that owner deliberately ignores EnableFtues.
        progress.MarkFtueAsComplete(NAscensionSingleplayerFtue.id);
    }

    // Fixed administrative preparation, never an arbitrary console or in-run edit.
    private static HostBaselineSnapshot ApplyNormalBaseline()
    {
        var save = SaveManager.Instance;
        string before = Sha(SaveManager.ToJson(save.Progress.ToSerializable()));
        var result = new UnlockConsoleCmd().Process(null, new[] { "all" });
        if (!result.success || result.task != null) throw new InvalidOperationException("native_unlock_all_failed");
        foreach (var encounter in ModelDb.AllEncounters) save.Progress.GetOrCreateEncounterStats(encounter.Id);
        bool added = save.Progress.NumberOfRuns == 0;
        if (added) save.Progress.GetOrCreateCharacterStats(ModelDb.GetId<Defect>()).TotalLosses = 1;
        PrepareNormalBaselineTutorials(save.Progress);
        string expected = SaveManager.ToJson(save.Progress.ToSerializable());
        save.SaveProgressFile();
        var loaded = save.InitProgressData();
        if (!loaded.Success || loaded.SaveData == null || expected != SaveManager.ToJson(save.Progress.ToSerializable()))
            throw new InvalidOperationException("native_baseline_save_reload_mismatch");
        var unlocks = save.GenerateUnlockStateFromProgress(); var native = unlocks.ToSerializable();
        if (!native.UnlockedEpochs.ToHashSet(StringComparer.Ordinal).SetEquals(EpochModel.AllEpochIds)
            || ModelDb.AllEncounters.Any(encounter => !unlocks.HasSeenEncounter(encounter)) || unlocks.NumberOfRuns <= 0
            || save.Progress.EnableFtues || !save.Progress.FtueCompleted.Contains(NAscensionSingleplayerFtue.id)
            || ModelDb.AllCharacters.Any(character => save.Progress.GetStatsForCharacter(character.Id)?.MaxAscension != 10))
            throw new InvalidOperationException("native_baseline_readback_incomplete");
        string progress = SaveManager.ToJson(loaded.SaveData);
        return new(before, Sha(progress), progress, DescribeUnlockState(unlocks), DescribePools(unlocks), true, added);
    }
}
