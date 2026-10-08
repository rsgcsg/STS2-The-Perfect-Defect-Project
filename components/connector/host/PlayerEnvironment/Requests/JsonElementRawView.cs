using System;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace STS2Connector.PlayerEnvironment;

internal interface IResultJsonElementRawView
{
    bool Supported { get; }
    string FrameworkIdentity { get; }
    ReadOnlyMemory<byte> Borrow(JsonElement element);
}

/// <summary>Closed, read-only .NET 9 serialization ABI. This borrows existing
/// document UTF8 synchronously and never invokes a game/native method. A major
/// version/identity/signature mismatch is unsupported, with no public-API copy
/// or reflection fallback. The owning document must live through encoding.</summary>
internal sealed class JsonElementRawView : IResultJsonElementRawView
{
    internal static readonly JsonElementRawView Instance = new();
    public bool Supported { get; }
    public string FrameworkIdentity { get; }
    private JsonElementRawView()
    {
        Assembly assembly = typeof(JsonElement).Assembly;
        AssemblyName identity = assembly.GetName();
        string token = Convert.ToHexString(identity.GetPublicKeyToken() ?? Array.Empty<byte>()).ToLowerInvariant();
        string informational = assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "unavailable";
        FrameworkIdentity = $"runtime={Environment.Version};assembly={identity.FullName};mvid={assembly.ManifestModule.ModuleVersionId:D};informational={informational}";
        if (Environment.Version.Major != 9 || identity.Name != "System.Text.Json"
            || identity.Version != new Version(9, 0, 0, 0) || token != "cc7b13ffcd2ddd51") return;
        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes("{\"s\":\"\\u4E2D\\n\\\"\",\"n\":1.00e+2,\"a\":[null,true]}");
            using JsonDocument document = JsonDocument.Parse(bytes);
            JsonElement root = document.RootElement;
            if (!RawValue(ref root).Span.SequenceEqual(bytes)) return;
            JsonElement nested = root.GetProperty("n");
            if (!RawValue(ref nested).Span.SequenceEqual("1.00e+2"u8)) return;
            Supported = true;
        }
        catch (Exception exception) when (exception is MissingMethodException or TypeLoadException
            or MemberAccessException or NotSupportedException or BadImageFormatException)
        { /* No result admission is possible with an unproven private ABI. */ }
    }
    public ReadOnlyMemory<byte> Borrow(JsonElement element)
    {
        if (!Supported) throw new ResultCodecUnsupportedException(FrameworkIdentity);
        return RawValue(ref element);
    }

    // dotnet/runtime v9.0.0 JsonElement.cs: internal ReadOnlyMemory<byte>
    // GetRawValue(). This exact ABI is probed again on the running framework.
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "GetRawValue")]
    private static extern ReadOnlyMemory<byte> RawValue(ref JsonElement instance);
}

internal sealed class ResultCodecUnsupportedException(string identity)
    : Exception("The bounded terminal encoder's exact framework ABI is unsupported: " + identity);
