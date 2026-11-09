using System;
using System.Collections.Generic;
using System.Linq.Expressions;
using System.Reflection;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// fast_chain only (<see cref="NativePatchStateCache.Enabled"/>): a field read through a
/// compiled getter instead of <see cref="FieldInfo.GetValue"/>. Reading is all it does: the
/// value (boxed the same way), its live-ness and every exception are those of
/// <see cref="FieldInfo.GetValue"/>. A target that is null or not an instance of the
/// declaring type goes to <see cref="FieldInfo.GetValue"/> itself, so it fails the same way.
/// Literal, pointer and by-ref-like fields always use reflection. Off: reflection, as before.
/// Nothing is cached except the getter delegate; no field value is ever remembered.
/// </summary>
public sealed class NativeField
{
    private static readonly MethodInfo ReflectionGet = typeof(FieldInfo).GetMethod(nameof(FieldInfo.GetValue), new[] { typeof(object) })!;
    private Func<object?, object?>? _get;

    private NativeField(FieldInfo info) => Info = info;

    public FieldInfo Info { get; }
    public string Name => Info.Name;
    public Type FieldType => Info.FieldType;
    public bool IsStatic => Info.IsStatic;
    public Type? DeclaringType => Info.DeclaringType;

    public static NativeField? Of(FieldInfo? info) => info == null ? null : new NativeField(info);

    /// <summary>Same as <c>Info.GetValue(target)</c>.</summary>
    public object? GetValue(object? target) =>
        NativePatchStateCache.Enabled ? (_get ??= Compile(Info))(target) : Info.GetValue(target);

    internal static Func<object?, object?> Compile(FieldInfo info)
    {
        Type type = info.FieldType;
        if (info.IsLiteral || info.DeclaringType is not { } owner || owner.ContainsGenericParameters || owner.IsByRefLike
            || type.IsPointer || type.IsByRef || type.IsByRefLike || type.IsFunctionPointer)
            return info.GetValue;
        var target = Expression.Parameter(typeof(object), "target");
        Expression read = info.IsStatic
            ? Expression.Convert(Expression.Field(null, info), typeof(object))
            : Expression.Condition(Expression.TypeIs(target, owner),
                Expression.Convert(Expression.Field(Expression.Convert(target, owner), info), typeof(object)),
                Expression.Call(Expression.Constant(info), ReflectionGet, target));
        return Expression.Lambda<Func<object?, object?>>(read, target).Compile();
    }
}

/// <summary>fast_chain only: dictionary capacities. Off: 0, the default constructor's capacity.</summary>
public static class NativeCapacity
{
    // Capacity never changes enumeration order: entries keep their insertion slots when a
    // dictionary grows, so the serialized key order is the same either way.
    public static int Of(int expected) => NativePatchStateCache.Enabled ? expected : 0;
}
