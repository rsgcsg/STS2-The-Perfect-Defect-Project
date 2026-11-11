using System;
using System.Collections.Generic;
using System.Reflection;
using System.Reflection.Emit;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// fast_chain only (<see cref="NativePatchStateCache.Enabled"/>): a field read that, once the field
/// has been read <see cref="CompileAfter"/> times, goes through a compiled getter instead of
/// <see cref="FieldInfo.GetValue"/>. Reading is all it does: the value (boxed the same way), its
/// live-ness and every exception are those of <see cref="FieldInfo.GetValue"/>. A target that is
/// null or not an instance of the declaring type goes to <see cref="FieldInfo.GetValue"/> itself,
/// so it fails the same way. Static, literal, pointer and by-ref-like fields always use reflection.
/// Off: reflection, as before. Nothing is cached except the getter delegate; no field value is
/// ever remembered.
/// </summary>
public sealed class NativeField
{
    /// <summary>
    /// Compiling costs about as much as this many reflection reads save (measured: ~40 us to emit
    /// and JIT one getter, ~15 ns saved per read), so a field read fewer times never pays for its
    /// getter and keeps using reflection; a hot field pays at most twice the best choice.
    /// </summary>
    internal const int CompileAfter = 2048;

    private static readonly MethodInfo ReflectionGet = typeof(FieldInfo).GetMethod(nameof(FieldInfo.GetValue), new[] { typeof(object) })!;
    private Func<object?, object?>? _get;
    private int _reads;

    private NativeField(FieldInfo info) => Info = info;

    public FieldInfo Info { get; }
    public string Name => Info.Name;
    public Type FieldType => Info.FieldType;
    public bool IsStatic => Info.IsStatic;
    public Type? DeclaringType => Info.DeclaringType;

    public static NativeField? Of(FieldInfo? info) => info == null ? null : new NativeField(info);

    /// <summary>Same as <c>Info.GetValue(target)</c>.</summary>
    public object? GetValue(object? target)
    {
        if (!NativePatchStateCache.Enabled) return Info.GetValue(target);
        if (_get is { } get) return get(target);
        // A racing increment only moves the moment of compiling; both getters read the same field.
        if (++_reads < CompileAfter) return Info.GetValue(target);
        Func<object?, object?> compiled;
        try { compiled = Compile(Info); }
        catch (Exception error)
        {
            // Never a silent fallback to reflection or a "missing"/unqualified answer: recorded for the
            // Program loop (fatal exit before the reply is written) and rethrown past every broad catch.
            string failure = $"compact_state_divergence: native field getter for {Info.DeclaringType?.FullName}.{Info.Name} failed to compile: {error.GetType().FullName}: {error.Message}";
            CompileFailure ??= failure;
            throw new NativeFieldCompileException(failure, error);
        }
        return (_get = compiled)(target);
    }

    /// <summary>
    /// fast_chain: set once a getter failed to compile. The Program loop checks it after every
    /// command and exits 70 with compact_state_divergence, whatever a caller's catch did.
    /// </summary>
    public static string? CompileFailure { get; private set; }

    /// <summary>
    /// <c>target is Owner o ? (object)o.field : info.GetValue(target)</c>, emitted directly
    /// (an expression tree costs about three times as much to compile).
    /// </summary>
    internal static Func<object?, object?> Compile(FieldInfo info)
    {
        Type type = info.FieldType;
        if (info.IsStatic || info.IsLiteral || info.DeclaringType is not { } owner || owner.ContainsGenericParameters
            || owner.IsByRefLike || Nullable.GetUnderlyingType(owner) != null
            || type.IsPointer || type.IsByRef || type.IsByRefLike || type.IsFunctionPointer || type.ContainsGenericParameters)
            return info.GetValue;
        var method = new DynamicMethod("native_get_" + info.Name, typeof(object), new[] { typeof(FieldInfo), typeof(object) },
            typeof(NativeField).Module, skipVisibility: true);
        ILGenerator il = method.GetILGenerator();
        Label reflection = il.DefineLabel();
        il.Emit(OpCodes.Ldarg_1);
        il.Emit(OpCodes.Isinst, owner);
        il.Emit(OpCodes.Brfalse, reflection);
        il.Emit(OpCodes.Ldarg_1);
        il.Emit(owner.IsValueType ? OpCodes.Unbox : OpCodes.Castclass, owner);
        il.Emit(OpCodes.Ldfld, info);
        if (type.IsValueType) il.Emit(OpCodes.Box, type);
        il.Emit(OpCodes.Ret);
        il.MarkLabel(reflection);
        il.Emit(OpCodes.Ldarg_0);
        il.Emit(OpCodes.Ldarg_1);
        il.Emit(OpCodes.Callvirt, ReflectionGet);
        il.Emit(OpCodes.Ret);
        return (Func<object?, object?>)method.CreateDelegate(typeof(Func<object?, object?>), info);
    }
}

/// <summary>A compiled field getter could not be built: fatal, never treated as an unqualified or missing read.</summary>
public sealed class NativeFieldCompileException(string message, Exception inner) : Exception(message, inner);

/// <summary>fast_chain only: dictionary capacities. Off: 0, the default constructor's capacity.</summary>
public static class NativeCapacity
{
    // Capacity never changes enumeration order: entries keep their insertion slots when a
    // dictionary grows, so the serialized key order is the same either way.
    public static int Of(int expected) => NativePatchStateCache.Enabled ? expected : 0;
}
