using System.Collections;
using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

[Collection("Native run hook read isolation")]
public sealed class NativeRunHookReadQualificationTests
{
    [Fact]
    public void SubscriberIsRejectedBeforeItsDelegateOrNativeIteratorExecutes()
    {
        RunState run = (RunState)RuntimeHelpers.GetUninitializedObject(typeof(RunState));
        var registry = (IList)typeof(ModHelper).GetField("_runHookSubscribers", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        Assert.Empty(registry);
        Assert.True(NativeRunHookReadQualification.Preflight(run));
        int calls = 0;
        try
        {
            ModHelper.SubscribeForRunStateHooks("native-purity-test", _ => { calls++; return Array.Empty<AbstractModel>(); });
            Assert.False(NativeRunHookReadQualification.Preflight(run));
            // The intentionally uninitialized native run cannot be enumerated.
            // Rejecting its subscriber before that point is the tested fact.
            Assert.False(NativeRunHookReadQualification.TryCaptureListeners(run, out var listeners, out var gap));
            Assert.Empty(listeners);
            Assert.Equal("native_run_hook_iterator_not_pure_qualified", gap);
            Assert.Equal(0, calls);
        }
        finally { registry.Clear(); }
        Assert.True(NativeRunHookReadQualification.Preflight(run));
    }

    [Fact]
    public void PatchedIteratorBodyFailsBeforeEnumerationAndRestoresAfterUnpatch()
    {
        RunState run = (RunState)RuntimeHelpers.GetUninitializedObject(typeof(RunState));
        MethodInfo iterator = typeof(RunState).GetMethod(nameof(RunState.IterateHookListeners))!;
        Type body = iterator.GetCustomAttribute<IteratorStateMachineAttribute>()!.StateMachineType;
        MethodInfo moveNext = body.GetMethod("MoveNext", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;
        var harmony = new Harmony("sts2-platform.native-read-qualification-test");
        try
        {
            harmony.Patch(moveNext, prefix: new HarmonyMethod(typeof(NativeRunHookReadQualificationTests), nameof(Block)));
            Assert.False(NativeRunHookReadQualification.Preflight(run));
            Assert.False(NativeRunHookReadQualification.TryCaptureListeners(run, out _, out _));
        }
        finally { harmony.UnpatchAll(harmony.Id); }
        Assert.True(NativeRunHookReadQualification.Preflight(run));
    }

    private static bool Block() => false;
}

[CollectionDefinition("Native run hook read isolation", DisableParallelization = true)]
public sealed class NativeRunHookReadIsolation { }
