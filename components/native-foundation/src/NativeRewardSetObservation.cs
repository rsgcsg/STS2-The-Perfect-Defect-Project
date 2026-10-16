using System;
using System.Linq;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>Membership observed at the exact native RewardsSet presentation seam.</summary>
public static class NativeRewardSetObservation
{
    private sealed record Owner(RewardsSet Set, bool Ambiguous);
    private static readonly ConditionalWeakTable<Reward, Owner> Owners = new();
    private static readonly object Gate = new();
    public static void Register(RewardsSet set)
    {
        ArgumentNullException.ThrowIfNull(set);
        lock (Gate)
            foreach (Reward reward in set.Rewards)
            {
                bool ambiguous = Owners.TryGetValue(reward, out Owner? prior)
                    && (prior.Ambiguous || !ReferenceEquals(prior.Set, set));
                Owners.Remove(reward);
                Owners.Add(reward, new Owner(set, ambiguous));
            }
    }
    private static readonly System.Reflection.FieldInfo? LinkedParent = typeof(Reward).GetField("<ParentRewardSet>k__BackingField",
        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
    public static string Relation(Reward reward) => Capture(reward) == null || LinkedParent == null ? "unavailable"
        : LinkedParent.GetValue(reward) == null && reward is not LinkedRewardSet ? "independent" : "linked";
    public static RewardsSet? Capture(Reward reward)
    {
        lock (Gate)
            return Owners.TryGetValue(reward, out Owner? owner) && !owner.Ambiguous
                && owner.Set.Rewards.Count(member => ReferenceEquals(member, reward)) == 1
                ? owner.Set : null;
    }
}
