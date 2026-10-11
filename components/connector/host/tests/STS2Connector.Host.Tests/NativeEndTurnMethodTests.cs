using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Actions;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Saves;
using STS2Connector.NativeUi;
using STS2Platform.GameMod;
using Xunit;

namespace STS2Connector;

// Exact ABI/IL only: these tests do not mount a Godot scene or claim enqueue,
// native execution, loaded identity, effects or a historical timeout repair.
public sealed class NativeEndTurnMethodTests
{
    private const BindingFlags Flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
    private static MethodInfo Method(Type type, string name) => Assert.IsAssignableFrom<MethodInfo>(type.GetMethod(name, Flags));
    [Fact]
    public void ExactButtonFactoryDisablesThenRequestsItsCapturedTurnAction()
    {
        var type = typeof(NEndTurnButton);
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId, type.Assembly.ManifestModule.ModuleVersionId.ToString("D"));
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameAssemblySha256,
            Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(type.Assembly.Location))).ToLowerInvariant());
        var method = Method(type, nameof(NEndTurnButton.CallReleaseLogic));
        Assert.True(method.IsPublic); Assert.False(method.IsStatic); Assert.Empty(method.GetParameters()); Assert.Equal(typeof(void), method.ReturnType);
        var body = Instructions(method).ToArray();
        int turn = Array.FindIndex(body, i => i.Member?.Name == "get_TurnNumber");
        int disable = Array.FindIndex(body, i => i.Member?.Name == "SetState");
        int create = Array.FindIndex(body, i => i.Op == OpCodes.Newobj && i.Member?.DeclaringType == typeof(EndPlayerTurnAction));
        int request = Array.FindIndex(body, i => i.Member?.DeclaringType == typeof(ActionQueueSynchronizer) && i.Member.Name == nameof(ActionQueueSynchronizer.RequestEnqueue));
        Assert.True(turn >= 0 && disable > turn && create > disable && request > create);
        Assert.Contains(body, i => i.Member?.Name == nameof(CombatManager.IsPlayerReadyToEndTurn));
        Assert.Contains(body, i => i.Op == OpCodes.Newobj && i.Member?.DeclaringType == typeof(UndoEndPlayerTurnAction));
        Assert.DoesNotContain(body, i => i.Member?.DeclaringType == typeof(PlayerCmd));
        var longPress = Instructions(Method(typeof(NEndTurnLongPressBar), nameof(NEndTurnLongPressBar._Process))).ToArray();
        Assert.Contains(longPress, i => i.Member?.DeclaringType == type && i.Member.Name == nameof(NEndTurnButton.CallReleaseLogic));
    }
    [Fact]
    public void NativeQueuedActionRetainsCapturedTurnCheckAndNativeBackOutSemantics()
    {
        var body = Instructions(Method(typeof(EndPlayerTurnAction), "ExecuteAction")).ToArray();
        int captured = Array.FindIndex(body, i => i.Member is FieldInfo f && f.Name == "_turnNumber");
        int current = Array.FindIndex(body, i => i.Member?.Name == "get_TurnNumber");
        int end = Array.FindIndex(body, i => i.Member?.DeclaringType == typeof(PlayerCmd) && i.Member.Name == nameof(PlayerCmd.EndTurn));
        Assert.True(captured >= 0 && current >= 0 && end > captured && end > current);
        Assert.Contains(body.Take(end), i => i.Op.FlowControl == FlowControl.Cond_Branch);
        Assert.Equal(OpCodes.Ldc_I4_1, body[end - 2].Op); // canBackOut=true; final optional argument is null.
        Assert.Equal(OpCodes.Ldnull, body[end - 1].Op);
    }
    [Fact]
    public void NativeTurnPhaseCancelsOnlyWaitingActionsAndCancellationNeedsGodotDeferredCompletion()
    {
        var body = Instructions(Method(typeof(ActionQueueSet), nameof(ActionQueueSet.StartCancellingAllPlayerDrivenCombatActions))).ToArray();
        int state = Array.FindIndex(body, i => i.Member?.Name == "get_State");
        int cancel = Array.FindIndex(body, i => i.Member?.DeclaringType == typeof(GameAction) && i.Member.Name == nameof(GameAction.Cancel));
        int remove = Array.FindIndex(body, i => i.Member?.Name == "RemoveAt");
        Assert.True(state >= 0 && cancel > state && remove > cancel);
        Assert.Equal("ldc.i4." + (int)GameActionState.WaitingForExecution, body[state + 1].Op.Name);
        Assert.Contains(body.Skip(state + 2).Take(cancel - state - 2), i => i.Op.FlowControl == FlowControl.Cond_Branch);
        var cancellation = Instructions(Method(typeof(GameAction), nameof(GameAction.Cancel))).ToArray();
        Assert.Contains(cancellation, i => i.Member?.DeclaringType == typeof(Godot.Callable) && i.Member.Name == nameof(Godot.Callable.CallDeferred));
        // Cancellation execution therefore belongs to the Godot-backed gate;
        // this source test does not patch or pretend that deferred completion.
    }
    [Fact]
    public void ConnectorCopiesOnlyTheAllowlistedButtonCombatStateAndDispatchesTypedFactory()
    {
        Assert.Equal(typeof(CombatState), typeof(NEndTurnButton).GetField("_combatState", Flags)!.FieldType);
        Assert.DoesNotContain(typeof(NEndTurnButton).GetProperties(BindingFlags.Public | BindingFlags.Instance), p => p.PropertyType == typeof(CombatState));
        var accessor = Method(typeof(NativeEndTurnInput), "ButtonCombatState");
        Assert.Equal("_combatState", accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!.Name);
        Assert.Equal(UnsafeAccessorKind.Field, accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!.Kind);
        Assert.Equal(typeof(CombatState).MakeByRefType(), accessor.ReturnType);
        Assert.Equal(typeof(NEndTurnButton), Assert.Single(accessor.GetParameters()).ParameterType);
        var capture = Instructions(Method(typeof(NativeEndTurnInput), nameof(NativeEndTurnInput.Capture))).ToArray();
        Assert.Contains(capture, i => i.Member?.Name == nameof(NativeEndTurnInput.ReadFtueSeen));
        Assert.Contains(Instructions(Method(typeof(NativeEndTurnInput), nameof(NativeEndTurnInput.ReadFtueSeen))),
            i => i.Member?.Name == nameof(SaveManager.SeenFtue));
        Assert.Contains(capture, i => i.Member?.Name == nameof(CombatManager.IsPlayerReadyToEndTurn));
        Assert.DoesNotContain(capture, i => i.Member?.DeclaringType == typeof(NEndTurnButton) && i.Op == OpCodes.Stfld);
        var members = typeof(NativeEndTurnInput).GetNestedTypes(Flags).Append(typeof(NativeEndTurnInput))
            .SelectMany(t => t.GetMethods(Flags)).Where(m => m.GetMethodBody() != null)
            .SelectMany(Instructions).Select(i => i.Member).OfType<MethodInfo>().ToArray();
        Assert.Contains(members, m => m.DeclaringType == typeof(NEndTurnButton) && m.Name == nameof(NEndTurnButton.CallReleaseLogic));
        Assert.DoesNotContain(members, m => m.DeclaringType == typeof(PlayerCmd) || m.Name is "ForceClick" or "ShouldShowPlayableCardsFtue" or "SecretEndTurnLogicViaFtue" or "MarkFtueAsComplete" or "HasCardsToPlay" or "set_IsLongPressEnabled");
    }
    [Fact]
    public void NativeWithdrawalUsesOnlyPinnedDisabledStateAndPureNativeHandGetter()
    {
        var state = typeof(NEndTurnButton).GetField("_state", Flags)!;
        Assert.True(state.FieldType.IsEnum);
        Assert.Equal(new[] { "Enabled", "Disabled", "Hidden" }, Enum.GetNames(state.FieldType));
        Assert.Equal(1, Convert.ToInt32(Enum.Parse(state.FieldType, "Disabled")));
        var getter = Method(typeof(NEndTurnButton), "get_CanTurnBeEnded");
        Assert.Equal(typeof(bool), getter.ReturnType); Assert.Empty(getter.GetParameters());
        Assert.DoesNotContain(Instructions(getter), i => i.Op == OpCodes.Stfld || i.Op == OpCodes.Stsfld);
        var accessor = Method(typeof(NativeEndTurnInput), nameof(NativeEndTurnInput.ReadCanTurnBeEnded));
        Assert.Equal("get_CanTurnBeEnded", accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!.Name);
        Assert.Equal(UnsafeAccessorKind.Method, accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!.Kind);
    }
    private sealed record Instruction(OpCode Op, MemberInfo? Member, int Offset, int? Target);
    private static readonly Dictionary<short, OpCode> Ops = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(field => field.FieldType == typeof(OpCode)).Select(field => (OpCode)field.GetValue(null)!).ToDictionary(op => op.Value);
    private static IEnumerable<Instruction> Instructions(MethodInfo method)
    {
        byte[] bytes = method.GetMethodBody()!.GetILAsByteArray()!;
        for (int offset = 0; offset < bytes.Length;)
        {
            int start = offset;
            short code = bytes[offset++];
            if (code == 0xfe) code = unchecked((short)(0xfe00 | bytes[offset++]));
            OpCode op = Ops[code]; MemberInfo? member = null;
            if (op.OperandType is OperandType.InlineField or OperandType.InlineMethod or OperandType.InlineType or OperandType.InlineTok)
                member = method.Module.ResolveMember(BitConverter.ToInt32(bytes, offset), method.DeclaringType!.GetGenericArguments(), method.GetGenericArguments());
            int length = op.OperandType switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(bytes, offset),
                _ => 4
            };
            int? target = op.OperandType switch
            {
                OperandType.ShortInlineBrTarget => offset + 1 + unchecked((sbyte)bytes[offset]),
                OperandType.InlineBrTarget => offset + 4 + BitConverter.ToInt32(bytes, offset),
                _ => null
            };
            offset += length; yield return new(op, member, start, target);
        }
    }
}
