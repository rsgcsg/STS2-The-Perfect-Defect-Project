using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

public sealed record NativeCardRewardAlternativePreviewFact(string OptionId, string Title, string AfterSelected)
{ public bool IsNativeSkip { get; init; } }
public sealed record NativeCardRewardAlternativePreviewResult(bool Complete,
    IReadOnlyList<NativeCardRewardAlternativePreviewFact>? Alternatives,
    bool? CanSkipSelection, bool? CanReroll, bool? SelectionCompletesReward, string? Gap);

/// <summary>
/// Bounded purity qualification for the inspected native alternative factory.
/// Generate remains the choice authority. No selection delegate is invoked,
/// and no unknown mod subscriber is enumerated to discover whether it is safe.
/// The same source is compiled into both exact Host implementations.
/// </summary>
public static class NativeCardRewardAlternativePreview
{
    private static readonly Type[] AlternativeSignature =
        { typeof(Player), typeof(CardReward), typeof(List<CardRewardAlternative>) };
    private static readonly Type[] CompletionSignature = { typeof(Player), typeof(CardReward) };
    private static MethodInfo? Effective(Type type, string name, Type[] signature) =>
        type.GetMethod(name, BindingFlags.Instance | BindingFlags.Public, null, signature, null);
    private static bool Unpatched(MethodBase? method) => NativeRunHookReadQualification.IsUnpatched(method);
    private static bool GetterIsUnpatched(Type owner, string name) =>
        Unpatched(owner.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetMethod);
    private static bool CardsProjectionIsUnpatched()
    {
        MethodInfo[] lambdas = typeof(CardReward).GetNestedType("<>c", BindingFlags.NonPublic)?
            .GetMethods(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            .Where(method => method.Name.StartsWith("<get_Cards>b__", StringComparison.Ordinal)).ToArray()
            ?? Array.Empty<MethodInfo>();
        return lambdas.Length == 1 && Unpatched(lambdas[0]);
    }

    public static string? Outcome(PostAlternateCardRewardAction value) => value switch
    {
        PostAlternateCardRewardAction.DoNothing => "do_nothing",
        PostAlternateCardRewardAction.EndSelectionAndCompleteReward => "end_selection_and_complete_reward",
        PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward => "end_selection_and_do_not_complete_reward",
        _ => null
    };

    private static readonly MethodInfo? EmptySelection = typeof(CardRewardAlternative)
        .GetNestedType("<>c", BindingFlags.NonPublic)?.GetMethod("<.ctor>b__16_0", BindingFlags.Instance | BindingFlags.NonPublic);
    /// <summary>Only the currently attached native handler is observed. Absence
    /// is not history: manually offered rewards may start without this handler.</summary>
    public static string NativeRelicRefreshSubscription(CardReward reward)
    {
        try
        {
            var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
            FieldInfo? field = typeof(Player).GetField("RelicObtained", flags);
            MethodInfo? handler = typeof(CardReward).GetMethod("OnRelicObtained", flags, null, new[] { typeof(RelicModel) }, null);
            PropertyInfo? playerGetter = typeof(Reward).GetProperty("Player", BindingFlags.Instance | BindingFlags.Public);
            if (typeof(CardReward).Module.ModuleVersionId != new Guid("57785517-0b16-42b9-8b36-bad6fb28384b")
                || reward.GetType() != typeof(CardReward) || !Unpatched(playerGetter?.GetMethod)
                || !Unpatched(handler) || field?.FieldType != typeof(Action<RelicModel>)) return "unavailable";
            Player player = reward.Player;
            if (player.GetType() != typeof(Player)) return "unavailable";
            object? current = field.GetValue(player);
            if (current == null) return "absent";
            if (current is not Action<RelicModel> listeners) return "unavailable";
            int count = listeners.GetInvocationList().Count(callback => callback.Method == handler && ReferenceEquals(callback.Target, reward));
            return count == 0 ? "absent" : count == 1 ? "attached" : "unavailable";
        }
        catch { return "unavailable"; }
    }
    public static bool IsNativeSkip(CardRewardAlternative alternative)
    {
        if (typeof(CardRewardAlternative).Module.ModuleVersionId != new Guid("57785517-0b16-42b9-8b36-bad6fb28384b")
            || !FactoryIsAudited() || alternative.GetType() != typeof(CardRewardAlternative)
            || alternative.OptionId != "Skip"
            || alternative.AfterSelected != PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward)
            return false;
        Delegate[] callbacks = alternative.OnSelect.GetInvocationList();
        return callbacks.Length == 1 && EmptySelection != null && callbacks[0].Method == EmptySelection;
    }

    private static bool FactoryIsAudited() => new[] { "Player", "Cards", "IsPopulated", "CanSkip", "CanReroll" }
            .All(name => GetterIsUnpatched(typeof(CardReward), name))
        && GetterIsUnpatched(typeof(Player), "RunState") && CardsProjectionIsUnpatched()
        && new[] { "OptionId", "AfterSelected", "Title", "OnSelect" }.All(name => GetterIsUnpatched(typeof(CardRewardAlternative), name))
        && Unpatched(EmptySelection)
        && Unpatched(typeof(CardRewardAlternative).GetMethod(nameof(CardRewardAlternative.Generate), new[] { typeof(CardReward) }))
        && Unpatched(typeof(CardRewardAlternative).GetConstructor(new[] { typeof(string), typeof(PostAlternateCardRewardAction) }))
        && Unpatched(typeof(CardRewardAlternative).GetConstructor(new[] { typeof(string), typeof(Func<System.Threading.Tasks.Task>), typeof(PostAlternateCardRewardAction) }))
        && Unpatched(typeof(Hook).GetMethod(nameof(Hook.ModifyCardRewardAlternatives)))
        && Unpatched(typeof(Hook).GetMethod(nameof(Hook.ShouldAllowSelectingMoreCardRewards)))
        && Unpatched(typeof(RunState).GetMethod(nameof(RunState.IterateHookListeners)))
        && Unpatched(typeof(ModHelper).GetMethod(nameof(ModHelper.IterateAllRunStateSubscribers)))
        && Unpatched(Effective(typeof(AbstractModel), nameof(AbstractModel.TryModifyCardRewardAlternatives), AlternativeSignature))
        && Unpatched(Effective(typeof(PaelsWing), nameof(AbstractModel.TryModifyCardRewardAlternatives), AlternativeSignature))
        && Unpatched(Effective(typeof(AbstractModel), nameof(AbstractModel.ShouldAllowSelectingMoreCardRewards), CompletionSignature));

    private static bool ListenersAreAudited(IEnumerable<AbstractModel> listeners) => listeners.All(model =>
    {
        Type type = model.GetType();
        if (type.Assembly != typeof(CardReward).Assembly) return false;
        MethodInfo? alternative = Effective(type, nameof(AbstractModel.TryModifyCardRewardAlternatives), AlternativeSignature);
        MethodInfo? completion = Effective(type, nameof(AbstractModel.ShouldAllowSelectingMoreCardRewards), CompletionSignature);
        return alternative?.DeclaringType is { } declaration && (declaration == typeof(AbstractModel) || declaration == typeof(PaelsWing))
            && completion?.DeclaringType == typeof(AbstractModel) && Unpatched(alternative) && Unpatched(completion);
    });

    private static NativeCardRewardAlternativePreviewResult Missing(string gap) => new(false, null, null, null, null, gap);

    public static NativeCardRewardAlternativePreviewResult Capture(CardReward reward)
    {
        try
        {
            // These checks precede IterateHookListeners, whose mod-subscriber
            // branch can invoke arbitrary delegates in an otherwise exact DLL.
            if (!FactoryIsAudited() || !NativeRunHookReadQualification.Preflight(reward.Player.RunState))
                return Missing("native_alternative_factory_not_pure_qualified");
            if (reward.GetType() != typeof(CardReward) || reward.Player.RunState is not RunState run
                || run.GetType() != typeof(RunState) || !reward.IsPopulated
                || !run.Players.Any(player => ReferenceEquals(player, reward.Player)))
                return Missing("native_alternative_reward_owner_not_qualified");
            if (!NativeRunHookReadQualification.TryCaptureListeners(run, out var listeners, out string? gap))
                return Missing(gap!);
            if (!ListenersAreAudited(listeners)) return Missing("native_alternative_listener_not_pure_qualified");
            CardModel[] cards = reward.Cards.ToArray();
            bool canSkip = reward.CanSkip, canReroll = reward.CanReroll;
            IReadOnlyList<CardRewardAlternative> alternatives = CardRewardAlternative.Generate(reward);
            bool selectionCompletes = !Hook.ShouldAllowSelectingMoreCardRewards(run, reward.Player, reward);
            var facts = new List<NativeCardRewardAlternativePreviewFact>();
            foreach (CardRewardAlternative alternative in alternatives)
            {
                string title = alternative.Title.GetFormattedText();
                string? outcome = Outcome(alternative.AfterSelected);
                if (string.IsNullOrWhiteSpace(alternative.OptionId) || string.IsNullOrWhiteSpace(title) || outcome == null)
                    return Missing("native_alternative_public_fact_unavailable");
                facts.Add(new(alternative.OptionId, title, outcome) { IsNativeSkip = IsNativeSkip(alternative) });
            }
            if (facts.Select(value => value.OptionId).Distinct(StringComparer.Ordinal).Count() != facts.Count)
                return Missing("native_alternative_duplicate_option_id");
            if (!FactoryIsAudited() || !ListenersAreAudited(listeners)
                || !NativeRunHookReadQualification.StillCurrent(run, listeners)
                || !cards.SequenceEqual(reward.Cards, ReferenceEqualityComparer.Instance)
                || reward.CanSkip != canSkip || reward.CanReroll != canReroll)
                return Missing("native_alternative_capture_changed");
            return new(true, facts.ToArray(), canSkip, canReroll, selectionCompletes, null);
        }
        catch (Exception) { return Missing("native_alternative_capture_failed"); }
    }
}
