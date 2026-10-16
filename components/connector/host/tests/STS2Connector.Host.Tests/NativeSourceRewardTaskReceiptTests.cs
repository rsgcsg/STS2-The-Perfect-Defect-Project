using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Rewards;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

/// <summary>Pure production lifecycle-core fixtures only. The typed button getter/hook and SceneTree remain unqualified.</summary>
[Collection("Native source carrier observer isolation")]
public sealed class NativeSourceRewardTaskReceiptTests
{
    private static T Bare<T>() => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static NativeSourceInputPrefix Prefix(object button, Reward reward, string callback = "NRewardButton.OnRelease") =>
        new("activate", button, reward, new Dictionary<string, object>(), callback, callback);
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void SynchronousOriginalBoolResultWaitsForItsOwnExceptionFreeScopeFinish(bool result)
    {
        object token = new(); var reward = Bare<CardReward>(); var button = new object();
        var terminals = new List<(object Token, string Delivery, string? Reason)>();
        using var registration = NativeSourceInputProvider.Register(_ => token,
            (actual, delivery, reason) => terminals.Add((actual, delivery, reason)), observeOriginalRewardTasks: true);
        var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, Task.FromResult(result)));
        Assert.Empty(terminals); NativeSourceInputProvider.Finish(original);
        Assert.Equal((token, "delivered", (string?)null), Assert.Single(terminals));
        NativeSourceInputProvider.Finish(original); Assert.Single(terminals);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void PendingOriginalTaskCompletesTheOriginalTokenWhileALaterInputIsActive(bool result)
    {
        object first = new(), next = new(), currentToken = first; var reward = Bare<CardReward>(); var button = new object();
        var terminals = new List<(object Token, string Delivery)>();
        using var registration = NativeSourceInputProvider.Register(_ => currentToken,
            (actual, delivery, _) => terminals.Add((actual, delivery)), observeOriginalRewardTasks: true);
        var task = new TaskCompletionSource<bool>(); var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, task.Task));
        NativeSourceInputProvider.Finish(original); Assert.Empty(terminals);
        currentToken = next; var later = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        try { task.SetResult(result); Assert.Equal((first, "delivered"), Assert.Single(terminals)); }
        finally { NativeSourceInputProvider.Finish(later); }
        Assert.Equal((next, "unknown"), terminals[1]);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void FaultedOrCanceledOriginalTaskNeverBecomesDelivered(bool canceled)
    {
        var reward = Bare<CardReward>(); var task = new TaskCompletionSource<bool>();
        var terminals = new List<(string Delivery, string? Reason)>();
        using var registration = NativeSourceInputProvider.Register(_ => new(),
            (_, delivery, reason) => terminals.Add((delivery, reason)), observeOriginalRewardTasks: true);
        var button = new object(); var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, task.Task)); NativeSourceInputProvider.Finish(original);
        if (canceled) task.SetCanceled(); else task.SetException(new InvalidOperationException("native fixture"));
        Assert.Equal(("unknown", canceled ? "native_source_original_reward_task_canceled" : "native_source_original_reward_task_failed"), Assert.Single(terminals));
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void ScopeExceptionWinsOverSynchronousOrLaterNormalTaskResult(bool pending)
    {
        var reward = Bare<CardReward>(); var task = new TaskCompletionSource<bool>();
        var terminals = new List<(string Delivery, string? Reason)>();
        using var registration = NativeSourceInputProvider.Register(_ => new(),
            (_, delivery, reason) => terminals.Add((delivery, reason)), observeOriginalRewardTasks: true);
        var button = new object(); var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, pending ? task.Task : Task.FromResult(false)));
        NativeSourceInputProvider.Finish(original, new InvalidOperationException("original callback failed"));
        if (pending) task.SetResult(true);
        Assert.Equal(("unknown", "native_source_input_boundary_threw"), Assert.Single(terminals));
    }
    [Theory]
    [InlineData("other_reward")]
    [InlineData("other_owner")]
    [InlineData("other_callback")]
    [InlineData("not_opted_in")]
    public void OnlyTheOptedInExactButtonRewardAndCallbackCanBind(string invalid)
    {
        var reward = Bare<CardReward>(); var otherSameType = Bare<CardReward>();
        var button = new object();
        var terminals = new List<string>();
        using var registration = NativeSourceInputProvider.Register(_ => new(), (_, delivery, _) => terminals.Add(delivery),
            observeOriginalRewardTasks: invalid != "not_opted_in");
        var original = NativeSourceInputProvider.Begin(Prefix(button, reward,
            invalid == "other_callback" ? "other.callback" : "NRewardButton.OnRelease"))!;
        Assert.False(NativeSourceInputProvider.BindOriginalRewardTaskCore(invalid == "other_owner" ? new object() : button, invalid == "other_reward" ? otherSameType : reward, Task.FromResult(true)));
        NativeSourceInputProvider.Finish(original); Assert.Equal("unknown", Assert.Single(terminals));
    }
    [Fact]
    public void DuplicateOriginalTaskIsIdempotentAndDifferentTaskCannotReplaceIt()
    {
        var reward = Bare<CardReward>(); var originalTask = new TaskCompletionSource<bool>(); var different = new TaskCompletionSource<bool>();
        var terminals = new List<(string Delivery, string? Reason)>();
        using var registration = NativeSourceInputProvider.Register(_ => new(),
            (_, delivery, reason) => terminals.Add((delivery, reason)), observeOriginalRewardTasks: true);
        var button = new object(); var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, originalTask.Task));
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, originalTask.Task));
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, different.Task));
        NativeSourceInputProvider.Finish(original); originalTask.SetResult(true); different.SetResult(false);
        Assert.Equal(("unknown", "native_source_original_reward_task_conflict"), Assert.Single(terminals));
    }
    [Fact]
    public void ClosedOriginalRegistrationDoesNotTransferItsLateTaskToTheNewSink()
    {
        var reward = Bare<CardReward>(); var task = new TaskCompletionSource<bool>(); int old = 0, newer = 0;
        using var first = NativeSourceInputProvider.Register(_ => new(), (_, _, _) => old++, observeOriginalRewardTasks: true);
        var button = new object(); var original = NativeSourceInputProvider.Begin(Prefix(button, reward))!;
        Assert.True(NativeSourceInputProvider.BindOriginalRewardTaskCore(button, reward, task.Task)); NativeSourceInputProvider.Finish(original); first.Dispose();
        using var next = NativeSourceInputProvider.Register(_ => new(), (_, _, _) => newer++, observeOriginalRewardTasks: true);
        task.SetResult(true); Assert.Equal(0, old); Assert.Equal(0, newer);
    }
}
