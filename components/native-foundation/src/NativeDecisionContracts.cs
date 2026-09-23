using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Supplies process-local opaque identities without making Native Foundation
/// own a public entity registry or transport format.
/// </summary>
public interface INativeReferentIdentity
{
    string GetId(object value, string kind);
}

public sealed record NativeSemanticOperand(
    string Role,
    string ReferentId,
    object NativeValue);

/// <summary>
/// A game-owned action that is legal at a native semantic decision boundary.
/// Native operands stay process-local; consumers project only opaque referents.
/// </summary>
public sealed record NativeSemanticAction(
    string Key,
    string Verb,
    string? SubjectReferentId,
    object? NativeSubject,
    IReadOnlyList<NativeSemanticOperand> Operands,
    string NativeLegalityBasis);

public sealed record NativeObservedSemanticAction(
    string NativeActionType,
    string? Key,
    string Status,
    int MatchCount,
    string Membership,
    string? Detail);

public sealed record NativeCombatDecision(
    string Status,
    string Scope,
    bool IsDecisionOpen,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail)
{
    public NativeObservedSemanticAction Describe(
        GameAction action,
        INativeReferentIdentity identities) =>
        NativeCombatDecisionProvider.Describe(action, this, identities);
}

public sealed record NativeMapDecision(
    string Status,
    string Scope,
    bool IsDecisionOpen,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail);

public sealed record NativeRewardDecision(
    string Status,
    string Scope,
    bool IsDecisionOpen,
    bool IsTerminal,
    IReadOnlyList<Reward> Rewards,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail);

public sealed record NativeRewardDecisionOwner(
    RewardsSet RewardsSet,
    bool IsTerminal);

public sealed record NativeCardRewardDecision(
    string Status,
    string Scope,
    bool IsDecisionOpen,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail);

/// <summary>
/// Exact process-local parent and alternative outcome facts for the native
/// card-reward screen. This neither publishes a public action nor proves
/// membership in an outer RewardsSet.
/// </summary>
public sealed record NativeCardRewardAlternativeFact(
    CardRewardAlternative Alternative,
    string OptionId,
    PostAlternateCardRewardAction AfterSelected);

public sealed record NativeCardRewardParentFacts(
    string Status,
    CardReward? ParentReward,
    IReadOnlyList<NativeCardRewardAlternativeFact> Alternatives,
    string? Detail);

public sealed record NativeTreasureDecision(
    string Status,
    string Scope,
    string Stage,
    bool ChestOpened,
    bool IsDecisionOpen,
    IReadOnlyList<RelicModel> Relics,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail);

/// <summary>
/// Read-only semantic catalog for ordinary non-combat room decisions.  The
/// provider projects STS2-owned option/entry state; it never delivers an
/// action or replaces the Connector's public binding validator.
/// </summary>
public sealed record NativeRoomDecision(
    string Status,
    string Scope,
    string InteractionKind,
    bool IsDecisionOpen,
    IReadOnlyList<NativeSemanticAction> Actions,
    IReadOnlyList<string> Evidence,
    string? Detail);

public static class NativeDecisionProjection
{
    public static IReadOnlyList<NativeSemanticAction> VisibleSubjects(
        NativeCombatDecision decision,
        string verb,
        IEnumerable<object> visibleSubjects) =>
        VisibleSubjects(decision.Actions, verb, visibleSubjects);

    public static IReadOnlyList<NativeSemanticAction> VisibleSubjects(
        IEnumerable<NativeSemanticAction> actions,
        string verb,
        IEnumerable<object> visibleSubjects)
    {
        var visible = new HashSet<object>(
            visibleSubjects,
            ReferenceEqualityComparer.Instance);
        return actions
            .Where(action => action.Verb == verb
                && action.NativeSubject != null
                && visible.Contains(action.NativeSubject))
            .OrderBy(action => action.Key, StringComparer.Ordinal)
            .ToArray();
    }

    public static bool HasExactReferenceBijection<T>(
        IEnumerable<T> semanticSubjects,
        IEnumerable<T> presentationSubjects)
        where T : class
    {
        var counts = new Dictionary<T, int>(ReferenceEqualityComparer.Instance);
        foreach (T subject in semanticSubjects)
        {
            if (!counts.TryAdd(subject, 1))
                return false;
        }
        foreach (T subject in presentationSubjects)
        {
            if (!counts.TryGetValue(subject, out int count) || count != 1)
                return false;
            counts[subject] = 0;
        }
        return counts.Values.All(count => count == 0);
    }
}

/// <summary>
/// Mechanical operations over a native semantic action catalog. This helper
/// neither discovers legality nor binds presentation controls.
/// </summary>
public static class NativeSemanticActionCatalog
{
    public static string BuildKey(
        string verb,
        string? subjectReferentId,
        IReadOnlyDictionary<string, string>? arguments = null)
    {
        string operands = arguments == null
            ? string.Empty
            : string.Join(",", arguments
                .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                .Select(pair => $"{pair.Key}={pair.Value}"));
        return $"{verb}|{subjectReferentId ?? "-"}|{operands}";
    }

    public static bool ContainsExactlyOnce(
        IEnumerable<NativeSemanticAction> actions,
        string verb) =>
        actions.Count(action => action.Verb == verb) == 1;

    public static bool ContainsExactlyOnce(
        IEnumerable<NativeSemanticAction> actions,
        string verb,
        object subject) =>
        actions.Count(action =>
            action.Verb == verb && ReferenceEquals(action.NativeSubject, subject)) == 1;

    public static IReadOnlyList<T> Subjects<T>(
        IEnumerable<NativeSemanticAction> actions,
        string verb)
        where T : class =>
        actions
            .Where(action => action.Verb == verb)
            .Select(action => action.NativeSubject)
            .OfType<T>()
            .ToArray();

    /// <summary>
    /// Describes one exact native selection against an already captured
    /// STS2-owned catalog. This is a mechanical identity join, not legality
    /// discovery: the provider that produced <paramref name="actions"/>
    /// remains the sole owner of action availability.
    /// </summary>
    public static NativeObservedSemanticAction Describe(
        IEnumerable<NativeSemanticAction> actions,
        string nativeActionType,
        string verb,
        object? subject,
        IReadOnlyDictionary<string, object>? operands = null)
    {
        IReadOnlyDictionary<string, object> expectedOperands = operands
            ?? new Dictionary<string, object>(StringComparer.Ordinal);
        NativeSemanticAction[] matches = actions.Where(action =>
                action.Verb == verb
                && (subject == null
                    ? action.NativeSubject == null
                    : ReferenceEquals(action.NativeSubject, subject))
                && HasExactOperands(action, expectedOperands))
            .ToArray();
        return DescribeMatches(nativeActionType, matches);
    }

    /// <summary>
    /// Describes a native action whose public/native selection intentionally
    /// has no subject. The catalog may retain an opaque native owner for that
    /// action (for example, the local player on End Turn); exact-once verb and
    /// operand membership remain required.
    /// </summary>
    public static NativeObservedSemanticAction DescribeWithoutSubject(
        IEnumerable<NativeSemanticAction> actions,
        string nativeActionType,
        string verb,
        IReadOnlyDictionary<string, object>? operands = null)
    {
        IReadOnlyDictionary<string, object> expectedOperands = operands
            ?? new Dictionary<string, object>(StringComparer.Ordinal);
        NativeSemanticAction[] matches = actions.Where(action =>
                action.Verb == verb
                && HasExactOperands(action, expectedOperands))
            .ToArray();
        return DescribeMatches(nativeActionType, matches);
    }

    /// <summary>
    /// Describes an exact selection when a delivery adapter intentionally uses
    /// a different public verb for the same native subject. The subject and all
    /// operands must still identify exactly one action in the captured catalog.
    /// </summary>
    public static NativeObservedSemanticAction DescribeByIdentity(
        IEnumerable<NativeSemanticAction> actions,
        string nativeActionType,
        object subject,
        IReadOnlyDictionary<string, object>? operands = null)
    {
        IReadOnlyDictionary<string, object> expectedOperands = operands
            ?? new Dictionary<string, object>(StringComparer.Ordinal);
        NativeSemanticAction[] matches = actions.Where(action =>
                ReferenceEquals(action.NativeSubject, subject)
                && HasExactOperands(action, expectedOperands))
            .ToArray();
        return DescribeMatches(nativeActionType, matches);
    }

    private static bool HasExactOperands(
        NativeSemanticAction action,
        IReadOnlyDictionary<string, object> expectedOperands) =>
        action.Operands.Count == expectedOperands.Count
        && action.Operands.All(operand =>
            expectedOperands.TryGetValue(operand.Role, out object? value)
            && ReferenceEquals(operand.NativeValue, value));

    private static NativeObservedSemanticAction DescribeMatches(
        string nativeActionType,
        IReadOnlyList<NativeSemanticAction> matches) =>
        new(
            nativeActionType,
            matches.Count == 1 ? matches[0].Key : null,
            matches.Count == 1 ? "described" : "not_described",
            matches.Count,
            matches.Count == 1 ? "exact_once" : matches.Count == 0 ? "absent" : "ambiguous",
            matches.Count == 0
                ? "The exact native subject and operands are absent from the captured semantic catalog."
                : matches.Count > 1
                    ? "The exact native subject and operands occur more than once in the captured semantic catalog."
                    : null);
}

public sealed record NativePlayerChoiceLineage(
    string Status,
    GameAction? ParentAction,
    string? ParentActionType,
    string? ParentState)
{
    public static NativePlayerChoiceLineage Capture()
    {
        try
        {
            GameAction? parent = MegaCrit.Sts2.Core.Runs.RunManager.Instance
                .ActionExecutor.CurrentlyRunningAction;
            return parent == null
                ? new NativePlayerChoiceLineage("no_parent", null, null, null)
                : new NativePlayerChoiceLineage(
                    "parent_observed",
                    parent,
                    parent.GetType().Name,
                    parent.State.ToString().ToLowerInvariant());
        }
        catch (Exception exception)
        {
            return new NativePlayerChoiceLineage(
                "unavailable",
                null,
                null,
                exception.GetType().Name);
        }
    }
}
