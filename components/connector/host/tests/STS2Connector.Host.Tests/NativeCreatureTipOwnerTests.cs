using STS2Connector.PlayerEnvironment;
using Xunit;

namespace STS2Connector;

public sealed class NativeCreatureTipOwnerTests
{
    private sealed record SameActorNode(string Actor);
    [Fact]
    public void VisibleDyingIntentHasRetiredInputOwnerBeforeItsAnimationDisappears()
    {
        object enemy = new(), player = new();
        // Native death moves the exact enemy node from CreatureNodes to
        // RemovingCreatureNodes while its old intent subtree remains visible.
        var result = NativeCreatureTipOwner.Resolve(new[] { enemy }, new[] { player }, new[] { enemy });
        Assert.Equal(NativeCreatureTipOwnerScope.Retired, result.Scope);
        Assert.Same(enemy, result.Owner);
        Assert.Equal(NativeCreatureTipOwnerScope.Current,
            NativeCreatureTipOwner.Resolve(new[] { player }, new[] { player }, new[] { enemy }).Scope);
    }

    [Fact]
    public void MissingFrozenFactsForRegisteredLiveOwnerAreNotRetirementEvidence()
    {
        object enemy = new();
        var result = NativeCreatureTipOwner.Resolve(new[] { enemy }, new[] { enemy }, Array.Empty<object>());
        Assert.Equal(NativeCreatureTipOwnerScope.Current, result.Scope);
        // The caller must still validate all required public owner/intent facts.
        // No public context or HP predicate is involved in this native-roster fact.
        Assert.Same(enemy, result.Owner);
    }

    [Fact]
    public void UnregisteredOrAmbiguousTreeOwnerFailsClosedWithoutPretendingItRetired()
    {
        object enemy = new(), other = new();
        foreach (var candidates in new[] { Array.Empty<object>(), new[] { enemy }, new[] { enemy, other } })
            Assert.Equal(NativeCreatureTipOwnerScope.Unresolved,
                NativeCreatureTipOwner.Resolve(candidates, Array.Empty<object>(), Array.Empty<object>()).Scope);
    }

    [Fact]
    public void ConflictingOrDuplicateNativeRosterMembershipIsUnresolved()
    {
        object enemy = new();
        Assert.Equal(NativeCreatureTipOwnerScope.Unresolved,
            NativeCreatureTipOwner.Resolve(new[] { enemy }, new[] { enemy }, new[] { enemy }).Scope);
        Assert.Equal(NativeCreatureTipOwnerScope.Unresolved,
            NativeCreatureTipOwner.Resolve(new[] { enemy }, new[] { enemy, enemy }, Array.Empty<object>()).Scope);
        Assert.Equal(NativeCreatureTipOwnerScope.Unresolved,
            NativeCreatureTipOwner.Resolve(new[] { enemy }, Array.Empty<object>(), new[] { enemy, enemy }).Scope);
    }

    [Fact]
    public void ANewCurrentNodeDoesNotAuthorizeOldSameActorAnimationControl()
    {
        var oldNode = new SameActorNode("same actor");
        var replacementNode = new SameActorNode("same actor");
        Assert.Equal(oldNode, replacementNode); // Actor-value equality is not native node identity.
        Assert.NotSame(oldNode, replacementNode);
        Assert.Equal(NativeCreatureTipOwnerScope.Retired,
            NativeCreatureTipOwner.Resolve(new[] { oldNode }, new[] { replacementNode }, new[] { oldNode }).Scope);
        Assert.Equal(NativeCreatureTipOwnerScope.Current,
            NativeCreatureTipOwner.Resolve(new[] { replacementNode }, new[] { replacementNode }, new[] { oldNode }).Scope);
    }
}
