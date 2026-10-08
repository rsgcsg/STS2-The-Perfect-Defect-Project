using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using STS2Connector.NativeUi;
using STS2Platform.GameMod;
using Xunit;

namespace STS2Connector;

// These inspect actual game IL/metadata; they do not initialize Godot or claim
// loaded execution, input-map conformance or gameplay effects.
public sealed class NativeCardOperationMethodTests
{
    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
    private static MethodInfo Method(Type owner, string name, params Type[] args) =>
        Assert.IsAssignableFrom<MethodInfo>(owner.GetMethod(name, Flags, args));
    private static FieldInfo Field(Type owner, string name, Type type)
    {
        var field = Assert.IsAssignableFrom<FieldInfo>(owner.GetField(name, Flags));
        Assert.Equal(type, field.FieldType); return field;
    }

    [Fact]
    public void MouseInvocationFieldsAndSignaturesMatchExactPinnedGame()
    {
        Type owner = typeof(NMouseCardPlay);
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId, owner.Assembly.ManifestModule.ModuleVersionId.ToString("D"));
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameAssemblySha256,
            Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(owner.Assembly.Location))).ToLowerInvariant());
        Assert.Equal(typeof(void), Method(owner, nameof(NMouseCardPlay.Start)).ReturnType);
        var multi = Method(owner, "MultiCreatureTargeting", typeof(TargetMode));
        Assert.Equal(typeof(Task), multi.ReturnType);
        Assert.Equal("targetMode", Assert.Single(multi.GetParameters()).Name);
        Assert.Equal(typeof(void), Method(owner, nameof(NMouseCardPlay._ExitTree)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NCardPlay), nameof(NCardPlay.CancelPlayCard)).ReturnType);
        Field(owner, "_cancellationTokenSource", typeof(CancellationTokenSource));
        Field(owner, "_isLeftMouseDown", typeof(bool));
        Field(owner, "_cancelShortcut", typeof(StringName));
        var start = Instructions(Method(owner, nameof(NMouseCardPlay.Start))).ToArray();
        int allocate = Array.FindIndex(start, i => i.Member is FieldInfo f && f.Name == "_cancellationTokenSource" && i.Op == OpCodes.Stfld);
        int body = Array.FindIndex(start, i => i.Member is MethodInfo m && m.Name == "StartAsync");
        Assert.True(allocate >= 0 && body > allocate);
        var stateMachine = multi.GetCustomAttribute<AsyncStateMachineAttribute>()!.StateMachineType;
        var stateBody = Instructions(Method(stateMachine, "MoveNext")).ToArray();
        Assert.Contains(stateBody, i => i.Member is MethodInfo m && m.Name == "ShowMultiCreatureTargetingVisuals");
        Assert.Contains(stateBody, i => i.Op == OpCodes.Ldftn && i.Member is MethodInfo predicate
            && Instructions(predicate).Any(body => body.Member is FieldInfo f && f.Name == "_isLeftMouseDown"));
    }

    [Fact]
    public void PlayZoneIsPureAndProspectiveLeftInputUsesNoPositionOrHitTest()
    {
        var playZone = Method(typeof(NMouseCardPlay), "IsCardInPlayZone");
        Assert.Equal(typeof(bool), playZone.ReturnType);
        var accessor = typeof(NativeMouseCardConfirmation).GetMethod(nameof(NativeMouseCardConfirmation.ReadNativePlayZone),
            BindingFlags.Static | BindingFlags.NonPublic)!;
        Assert.Equal("IsCardInPlayZone", accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!.Name);
        Assert.Equal(typeof(NMouseCardPlay), Assert.Single(accessor.GetParameters()).ParameterType);
        Assert.DoesNotContain(Instructions(playZone), i => i.Op == OpCodes.Stfld || i.Op == OpCodes.Stsfld);
        var nativeInput = Instructions(Method(typeof(NMouseCardPlay), nameof(NMouseCardPlay._Input), typeof(InputEvent))).ToArray();
        Assert.Contains(nativeInput, i => i.Member is MethodInfo m && m.Name == nameof(InputEvent.IsActionPressed));
        Assert.Contains(nativeInput, i => i.Member is MethodInfo m && m.Name == "get_ButtonIndex");
        Assert.DoesNotContain(nativeInput, i => i.Member is MethodInfo m &&
            (m.Name.Contains("Position", StringComparison.Ordinal) || m.Name.Contains("HitTest", StringComparison.Ordinal)));
        Assert.DoesNotContain(nativeInput, i => i.Member is MethodInfo m && m.Name is "TryPlayCard" or "MultiCreatureTargeting");
        var cancelZone = Instructions(Method(typeof(NMouseCardPlay), "IsCardInCancelZone"));
        Assert.Contains(cancelZone, i => i.Op == OpCodes.Stfld && i.Member?.Name == "_hasLeftCardCancelZoneOnce");
    }

    [Fact]
    public void InspectorCompletedDisplayAndAllReplacementSeamsExistAtExactNativeApis()
    {
        Type owner = typeof(NInspectCardScreen);
        Field(owner, "_cards", typeof(List<CardModel>));
        Field(owner, "_index", typeof(int)); Field(owner, "_card", typeof(NCard)); Field(owner, "_upgradeTickbox", typeof(NTickbox));
        foreach (var (name, args) in new[]
        {
            ("UpdateCardDisplay", Type.EmptyTypes), ("SetCard", new[] { typeof(int) }),
            ("ToggleShowUpgrade", new[] { typeof(NTickbox) }),
            (nameof(NInspectCardScreen.Open), new[] { typeof(List<CardModel>), typeof(int), typeof(bool) }),
            (nameof(NInspectCardScreen.Close), Type.EmptyTypes)
        }) Assert.Equal(typeof(void), Method(owner, name, args).ReturnType);
        var display = Instructions(Method(owner, "UpdateCardDisplay")).ToArray();
        Assert.Contains(display, i => i.Member is MethodInfo m && m.Name == "MutableClone");
        Assert.Contains(display, i => i.Member is MethodInfo m && m.Name == "set_Model" && m.DeclaringType == typeof(NCard));
        Assert.DoesNotContain(display, i => i.Member is MethodInfo m && m.Name == "set_IsTicked");
    }

    private sealed record Instruction(OpCode Op, MemberInfo? Member);
    private static readonly Dictionary<short, OpCode> Ops = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(field => field.FieldType == typeof(OpCode)).Select(field => (OpCode)field.GetValue(null)!).ToDictionary(op => op.Value);
    private static IEnumerable<Instruction> Instructions(MethodInfo method)
    {
        byte[] bytes = method.GetMethodBody()!.GetILAsByteArray()!;
        for (int offset = 0; offset < bytes.Length;)
        {
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
            offset += length; yield return new(op, member);
        }
    }
}
