using System.Collections.Immutable;
using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;

// Read-only metadata comparison: every GodotSharp type/member referenced by the
// exact game assemblies against the Managed GodotSharp stub (and, as a scanner
// self-check, the shipped GodotSharp). Reports Godot API names and signatures
// plus caller namespace counts only; no game IL or source is emitted.

var games = new List<string>();
string? stubPath = null, referencePath = null, baselinePath = null, outputPath = null;
var callerMethods = false;
for (var i = 0; i < args.Length; i++)
{
    string Next() => i + 1 < args.Length ? args[++i] : throw new ArgumentException($"{args[i]} needs a value");
    switch (args[i])
    {
        case "--game": games.Add(Next()); break;
        case "--stub": stubPath = Next(); break;
        case "--reference": referencePath = Next(); break;
        case "--baseline": baselinePath = Next(); break;
        case "--output": outputPath = Next(); break;
        case "--caller-methods": callerMethods = true; break; // evidence-only detail
        default:
            Console.Error.WriteLine($"Unknown argument {args[i]}");
            return Usage();
    }
}
if (games.Count == 0 || stubPath == null) return Usage();

var stub = Loaded.Open(stubPath);
var stubModel = GodotModel.Build(stub);
Loaded? reference = referencePath == null ? null : Loaded.Open(referencePath);
var referenceModel = reference == null ? null : GodotModel.Build(reference);

var types = new SortedDictionary<string, TypeUse>(StringComparer.Ordinal);
var members = new SortedDictionary<string, MemberUse>(StringComparer.Ordinal);
var gameInputs = new List<object>();
var totalTypeRefs = 0;
var totalMemberRefs = 0;

foreach (var gamePath in games)
{
    var game = Loaded.Open(gamePath);
    var md = game.Md;
    var godotRefs = md.AssemblyReferences
        .Where(h => md.GetString(md.GetAssemblyReference(h).Name) == "GodotSharp")
        .ToHashSet();
    gameInputs.Add(game.Identity(godotRefs.Count > 0));
    if (godotRefs.Count == 0) continue;

    bool IsGodotTypeRef(TypeReferenceHandle h)
    {
        var scope = md.GetTypeReference(h).ResolutionScope;
        while (scope.Kind == HandleKind.TypeReference) scope = md.GetTypeReference((TypeReferenceHandle)scope).ResolutionScope;
        return scope.Kind == HandleKind.AssemblyReference && godotRefs.Contains((AssemblyReferenceHandle)scope);
    }

    var typeKeyByRef = new Dictionary<TypeReferenceHandle, string>();
    foreach (var h in md.TypeReferences)
    {
        if (!IsGodotTypeRef(h)) continue;
        var name = TypeNames.Ref(md, h);
        typeKeyByRef[h] = name;
        totalTypeRefs++;
        if (!types.ContainsKey(name)) types[name] = new TypeUse(name);
    }

    var memberKeyByRef = new Dictionary<MemberReferenceHandle, string>();
    foreach (var h in md.MemberReferences)
    {
        var mr = md.GetMemberReference(h);
        string? parent = mr.Parent.Kind switch
        {
            HandleKind.TypeReference when typeKeyByRef.TryGetValue((TypeReferenceHandle)mr.Parent, out var t) => t,
            HandleKind.TypeSpecification => GenericDefinition(md, (TypeSpecificationHandle)mr.Parent, typeKeyByRef),
            _ => null
        };
        if (parent == null) continue;
        totalMemberRefs++;
        var name = md.GetString(mr.Name);
        string kind, sig;
        if (mr.GetKind() == MemberReferenceKind.Field)
        {
            kind = "field";
            sig = mr.DecodeFieldSignature(NameProvider.Instance, null);
        }
        else
        {
            kind = name == ".ctor" || name == ".cctor" ? "constructor" : "method";
            sig = Signatures.Method(mr.DecodeMethodSignature(NameProvider.Instance, null));
        }
        var key = $"{parent}::{name} {sig}";
        memberKeyByRef[h] = key;
        if (!members.ContainsKey(key)) members[key] = new MemberUse(key, parent, name, kind, sig);
        types[parent].MemberRefs++;
    }

    // IL sites, grouped by the caller's outermost declaring namespace.
    foreach (var typeHandle in md.TypeDefinitions)
    {
        var callerNamespace = TypeNames.OuterNamespace(md, typeHandle);
        var glueType = Glue.IsGeneratedNestedType(md, typeHandle);
        foreach (var methodHandle in md.GetTypeDefinition(typeHandle).GetMethods())
        {
            var method = md.GetMethodDefinition(methodHandle);
            if (method.RelativeVirtualAddress == 0) continue;
            var glue = glueType || Glue.IsGeneratedMethod(md.GetString(method.Name));
            var caller = callerMethods && !glue ? $"{TypeNames.Def(md, typeHandle)}::{md.GetString(method.Name)}" : null;
            var il = game.Pe.GetMethodBody(method.RelativeVirtualAddress).GetILBytes() ?? Array.Empty<byte>();
            Il.Walk(il, (_, token) =>
            {
                var handle = MetadataTokens.EntityHandle(token);
                if (handle.Kind == HandleKind.MethodSpecification)
                    handle = md.GetMethodSpecification((MethodSpecificationHandle)handle).Method;
                if (handle.Kind == HandleKind.MemberReference
                    && memberKeyByRef.TryGetValue((MemberReferenceHandle)handle, out var memberKey))
                {
                    members[memberKey].AddSite(callerNamespace, glue, caller);
                    types[members[memberKey].Type].AddSite(callerNamespace, glue, caller);
                }
                else if (handle.Kind == HandleKind.TypeReference
                    && typeKeyByRef.TryGetValue((TypeReferenceHandle)handle, out var typeKey))
                {
                    types[typeKey].AddSite(callerNamespace, glue, caller);
                }
            });
        }
    }
    foreach (var attributeHandle in md.CustomAttributes)
    {
        var ctor = md.GetCustomAttribute(attributeHandle).Constructor;
        if (ctor.Kind == HandleKind.MemberReference
            && memberKeyByRef.TryGetValue((MemberReferenceHandle)ctor, out var key)) members[key].AttributeUses++;
    }
}

foreach (var type in types.Values)
{
    type.InStub = stubModel.Types.ContainsKey(type.Name);
    type.InReference = referenceModel?.Types.ContainsKey(type.Name);
}
foreach (var member in members.Values)
{
    member.Stub = stubModel.Resolve(member);
    member.Reference = referenceModel?.Resolve(member);
}

var gaps = new SortedDictionary<string, object>(StringComparer.Ordinal);
foreach (var type in types.Values.Where(t => !t.InStub))
    gaps[$"type {type.Name}"] = new { kind = "missing_type", type = type.Name };
foreach (var member in members.Values.Where(m => m.Stub.Status != "present"))
{
    if (member.Stub.Status == "missing_type") continue; // already covered by its type gap
    gaps[$"member {member.Key}"] = new { kind = member.Stub.Status, type = member.Type, member = member.Name, member.Signature };
}
foreach (var member in members.Values.Where(m => m.Stub.Status == "present" && m.Stub.NotImplemented != null))
    gaps[$"not_implemented {member.Key}"] = new { kind = "not_implemented", type = member.Type, member = member.Name, member.Signature, exception = member.Stub.NotImplemented };

var accepted = new HashSet<string>(StringComparer.Ordinal);
if (baselinePath != null)
{
    using var baseline = JsonDocument.Parse(File.ReadAllText(baselinePath));
    foreach (var entry in baseline.RootElement.GetProperty("accepted_gaps").EnumerateArray())
        accepted.Add(entry.GetProperty("key").GetString()!);
}
var newGaps = baselinePath == null ? new List<string>() : gaps.Keys.Where(k => !accepted.Contains(k)).ToList();
var staleAccepted = baselinePath == null ? new List<string>() : accepted.Where(k => !gaps.ContainsKey(k)).OrderBy(k => k, StringComparer.Ordinal).ToList();

object Counts(IEnumerable<MemberUse> list) => new
{
    members = list.Count(),
    il_sites = list.Sum(m => m.IlSites),
    game_logic_sites = list.Sum(m => m.IlSites - m.GlueSites),
    glue_only = list.Count(m => m.IlSites > 0 && m.IlSites == m.GlueSites),
    attribute_uses = list.Sum(m => m.AttributeUses)
};

var report = new
{
    schema = "spireagent.host-runtime/godot-stub-coverage-1",
    inputs = new { games = gameInputs, stub = stub.Identity(true), reference = reference?.Identity(true) },
    totals = new
    {
        godot_type_references = totalTypeRefs,
        godot_member_references = totalMemberRefs,
        distinct_types = types.Count,
        distinct_members = members.Count,
        missing_types = types.Values.Count(t => !t.InStub),
        members_on_missing_types = Counts(members.Values.Where(m => m.Stub.Status == "missing_type")),
        missing_members = Counts(members.Values.Where(m => m.Stub.Status == "missing_member")),
        signature_mismatches = Counts(members.Values.Where(m => m.Stub.Status == "signature_mismatch")),
        not_implemented = Counts(members.Values.Where(m => m.Stub.NotImplemented != null)),
        present = Counts(members.Values.Where(m => m.Stub.Status == "present")),
        reference_unresolved_types = referenceModel == null ? (int?)null : types.Values.Count(t => t.InReference != true),
        reference_unresolved_members = referenceModel == null ? (int?)null : members.Values.Count(m => m.Reference!.Status != "present"),
        gaps = gaps.Count,
        new_gaps = baselinePath == null ? (int?)null : newGaps.Count,
        stale_accepted = baselinePath == null ? (int?)null : staleAccepted.Count
    },
    missing_types = types.Values.Where(t => !t.InStub).Select(t => t.ToJson()),
    members = members.Values.Where(m => m.Stub.Status != "present" || m.Stub.NotImplemented != null).Select(m => m.ToJson()),
    present_members = members.Values.Where(m => m.Stub.Status == "present" && m.Stub.NotImplemented == null).Select(m => m.Key),
    stub_not_implemented = stubModel.NotImplemented.OrderBy(k => k.Key, StringComparer.Ordinal)
        .Select(k => new { key = k.Key, exception = k.Value, referenced_by_game = members.ContainsKey(k.Key) }),
    gap_keys = gaps.Keys,
    new_gaps = newGaps,
    stale_accepted = staleAccepted
};
var json = JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true });
if (outputPath != null) File.WriteAllText(outputPath, json + "\n"); else Console.WriteLine(json);

if (newGaps.Count > 0)
{
    Console.Error.WriteLine($"Godot stub coverage: {newGaps.Count} gap(s) not in the reviewed baseline:");
    foreach (var gap in newGaps.Take(50)) Console.Error.WriteLine($"  {gap}");
    return 4;
}
return 0;

static int Usage()
{
    Console.Error.WriteLine("Usage: GodotStubCoverage --game <dll> [--game <dll>...] --stub <GodotSharp.dll> [--reference <shipped GodotSharp.dll>] [--baseline <json>] [--output <json>]");
    return 2;
}

static string? GenericDefinition(MetadataReader md, TypeSpecificationHandle handle, Dictionary<TypeReferenceHandle, string> godot)
{
    var blob = md.GetBlobReader(md.GetTypeSpecification(handle).Signature);
    if (blob.ReadSignatureTypeCode() != SignatureTypeCode.GenericTypeInstance) return null;
    blob.ReadSignatureTypeCode();
    var generic = blob.ReadTypeHandle();
    return generic.Kind == HandleKind.TypeReference && godot.TryGetValue((TypeReferenceHandle)generic, out var name) ? name : null;
}

sealed class Loaded
{
    public required string Path { get; init; }
    public required PEReader Pe { get; init; }
    public required MetadataReader Md { get; init; }

    public static Loaded Open(string path)
    {
        var full = System.IO.Path.GetFullPath(path);
        var pe = new PEReader(File.OpenRead(full), PEStreamOptions.PrefetchEntireImage);
        if (!pe.HasMetadata) throw new InvalidDataException($"{full} is not a managed assembly.");
        return new Loaded { Path = full, Pe = pe, Md = pe.GetMetadataReader() };
    }

    public object Identity(bool referencesGodot) => new
    {
        file_name = System.IO.Path.GetFileName(Path),
        sha256 = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(Path))),
        module_mvid = Md.GetGuid(Md.GetModuleDefinition().Mvid).ToString("D"),
        references_godotsharp = referencesGodot
    };
}

sealed record Resolution(string Status, string? FoundOn, IReadOnlyList<string> Candidates, string? NotImplemented);

sealed class GodotModel
{
    public Dictionary<string, string?> Types { get; } = new(StringComparer.Ordinal); // name -> base name
    readonly Dictionary<string, string> _baseSpecs = new(StringComparer.Ordinal); // name -> full base signature
    readonly Dictionary<string, Dictionary<string, List<string>>> _members = new(StringComparer.Ordinal); // type -> name -> sigs
    public Dictionary<string, string> NotImplemented { get; } = new(StringComparer.Ordinal);

    public static GodotModel Build(Loaded assembly)
    {
        var md = assembly.Md;
        var model = new GodotModel();
        foreach (var handle in md.TypeDefinitions)
        {
            var type = md.GetTypeDefinition(handle);
            var name = TypeNames.Def(md, handle);
            string? baseName = type.BaseType.IsNil ? null : type.BaseType.Kind switch
            {
                HandleKind.TypeDefinition => TypeNames.Def(md, (TypeDefinitionHandle)type.BaseType),
                HandleKind.TypeReference => TypeNames.Ref(md, (TypeReferenceHandle)type.BaseType),
                HandleKind.TypeSpecification => md.GetTypeSpecification((TypeSpecificationHandle)type.BaseType)
                    .DecodeSignature(NameProvider.Instance, null).Split('<')[0],
                _ => null
            };
            model.Types[name] = baseName;
            if (type.BaseType.Kind == HandleKind.TypeSpecification)
                model._baseSpecs[name] = md.GetTypeSpecification((TypeSpecificationHandle)type.BaseType).DecodeSignature(NameProvider.Instance, null);
            var byName = model._members[name] = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            void Add(string member, string sig)
            {
                if (!byName.TryGetValue(member, out var list)) byName[member] = list = new List<string>();
                list.Add(sig);
            }
            foreach (var fieldHandle in type.GetFields())
            {
                var field = md.GetFieldDefinition(fieldHandle);
                Add(md.GetString(field.Name), field.DecodeSignature(NameProvider.Instance, null));
            }
            foreach (var methodHandle in type.GetMethods())
            {
                var method = md.GetMethodDefinition(methodHandle);
                var member = md.GetString(method.Name);
                var sig = Signatures.Method(method.DecodeSignature(NameProvider.Instance, null));
                Add(member, sig);
                if (method.RelativeVirtualAddress == 0) continue;
                var thrown = Il.ThrowOnly(md, assembly.Pe.GetMethodBody(method.RelativeVirtualAddress).GetILBytes() ?? Array.Empty<byte>());
                if (thrown != null) model.NotImplemented[$"{name}::{member} {sig}"] = thrown;
            }
        }
        return model;
    }

    // The runtime resolves a MemberRef on its parent and then up the base chain.
    public Resolution Resolve(MemberUse use)
    {
        if (!Types.ContainsKey(use.Type)) return new Resolution("missing_type", null, Array.Empty<string>(), null);
        var candidates = new List<string>();
        string? type = use.Type;
        string? last = null;
        for (; type != null && _members.TryGetValue(type, out var byName); last = type, type = Types[type])
        {
            if (!byName.TryGetValue(use.Name, out var sigs)) continue;
            if (sigs.Contains(use.Signature))
            {
                NotImplemented.TryGetValue($"{type}::{use.Name} {use.Signature}", out var thrown);
                return new Resolution("present", type, Array.Empty<string>(), thrown);
            }
            candidates.AddRange(sigs.Select(s => $"{type}::{use.Name} {s}"));
        }
        // The chain left the stub (e.g. Array<T> : List<T>): the runtime keeps
        // searching the BCL base, so compare against the running BCL by reflection.
        if (type != null && last != null)
        {
            var external = ExternalBase.Members(type, _baseSpecs.GetValueOrDefault(last));
            if (external != null)
            {
                if (external.TryGetValue(use.Name, out var sigs))
                {
                    if (sigs.Contains(use.Signature)) return new Resolution("present", type, Array.Empty<string>(), null);
                    candidates.AddRange(sigs.Select(s => $"{type}::{use.Name} {s}"));
                }
            }
        }
        return new Resolution(candidates.Count > 0 ? "signature_mismatch" : "missing_member", null, candidates, null);
    }
}

// Godot's C# source generators emit these engine-invoked bridge members into
// every script class; Managed never calls them because no native engine exists.
static class Glue
{
    static readonly HashSet<string> Methods = new(StringComparer.Ordinal)
    {
        "InvokeGodotClassMethod", "HasGodotClassMethod", "InvokeGodotClassStaticMethod",
        "SetGodotClassPropertyValue", "GetGodotClassPropertyValue",
        "RaiseGodotClassSignalCallbacks", "HasGodotClassSignal",
        "SaveGodotObjectData", "RestoreGodotObjectData",
        "GetGodotMethodList", "GetGodotPropertyList", "GetGodotSignalList", "GetGodotPropertyDefaultValues"
    };
    static readonly HashSet<string> NestedTypes = new(StringComparer.Ordinal) { "MethodName", "PropertyName", "SignalName" };

    public static bool IsGeneratedMethod(string name) => Methods.Contains(name);
    public static bool IsGeneratedNestedType(MetadataReader md, TypeDefinitionHandle handle)
    {
        var type = md.GetTypeDefinition(handle);
        return !type.GetDeclaringType().IsNil && NestedTypes.Contains(md.GetString(type.Name));
    }
}

static class ExternalBase
{
    // Members of a BCL generic/non-generic base reachable through the hierarchy,
    // only when the stub passes its own generic parameters through unchanged.
    public static Dictionary<string, HashSet<string>>? Members(string typeName, string? baseSpec)
    {
        if (baseSpec != null)
        {
            var open = baseSpec.IndexOf('<');
            var arguments = baseSpec[(open + 1)..^1].Split(',');
            if (!arguments.Select((a, i) => a == $"!{i}").All(x => x)) return null;
        }
        var type = typeof(object).Assembly.GetType(typeName) ?? Type.GetType(typeName);
        if (type == null) return null;
        var result = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
        void Add(string name, string sig)
        {
            if (!result.TryGetValue(name, out var set)) result[name] = set = new HashSet<string>(StringComparer.Ordinal);
            set.Add(sig);
        }
        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        for (var current = type; current != null; current = current.BaseType)
        {
            var definition = current.IsGenericType ? current.GetGenericTypeDefinition() : current;
            foreach (var method in definition.GetMethods(flags).Where(m => m.IsPublic || m.IsFamily || m.IsFamilyOrAssembly))
                Add(method.Name, Sig(!method.IsStatic, Name(method.ReturnType),
                    method.IsGenericMethod ? method.GetGenericArguments().Length : 0, method.GetParameters()));
            foreach (var ctor in definition.GetConstructors(flags).Where(m => m.IsPublic || m.IsFamily || m.IsFamilyOrAssembly))
                Add(ctor.Name, Sig(!ctor.IsStatic, "void", 0, ctor.GetParameters()));
        }
        return result;
    }

    static string Sig(bool instance, string returnType, int arity, ParameterInfo[] parameters) =>
        $"{(instance ? "instance " : "")}{returnType}" + (arity > 0 ? $" <{arity}>" : "")
        + $" ({string.Join(", ", parameters.Select(p => Name(p.ParameterType)))})";

    static string Name(Type type)
    {
        if (type.IsByRef) return Name(type.GetElementType()!) + "&";
        if (type.IsPointer) return Name(type.GetElementType()!) + "*";
        if (type.IsSZArray) return Name(type.GetElementType()!) + "[]";
        if (type.IsGenericParameter) return (type.DeclaringMethod != null ? "!!" : "!") + type.GenericParameterPosition;
        if (type == typeof(void)) return "void";
        if (type == typeof(IntPtr)) return "native int";
        if (type == typeof(UIntPtr)) return "native uint";
        if (type == typeof(string)) return "string";
        if (type == typeof(object)) return "object";
        if (type.IsPrimitive) return NameProvider.Instance.GetPrimitiveType(Type.GetTypeCode(type) switch
        {
            TypeCode.Boolean => PrimitiveTypeCode.Boolean, TypeCode.Char => PrimitiveTypeCode.Char,
            TypeCode.SByte => PrimitiveTypeCode.SByte, TypeCode.Byte => PrimitiveTypeCode.Byte,
            TypeCode.Int16 => PrimitiveTypeCode.Int16, TypeCode.UInt16 => PrimitiveTypeCode.UInt16,
            TypeCode.Int32 => PrimitiveTypeCode.Int32, TypeCode.UInt32 => PrimitiveTypeCode.UInt32,
            TypeCode.Int64 => PrimitiveTypeCode.Int64, TypeCode.UInt64 => PrimitiveTypeCode.UInt64,
            TypeCode.Single => PrimitiveTypeCode.Single, _ => PrimitiveTypeCode.Double
        });
        if (type.IsGenericType && !type.IsGenericTypeDefinition)
            return $"{Name(type.GetGenericTypeDefinition())}<{string.Join(",", type.GetGenericArguments().Select(Name))}>";
        if (type.IsNested) return $"{Name(type.DeclaringType!)}/{type.Name}";
        return $"{type.Namespace}.{type.Name}";
    }
}

class Sites
{
    public int IlSites { get; private set; }
    public int GlueSites { get; private set; }
    public SortedDictionary<string, int> Callers { get; } = new(StringComparer.Ordinal); // non-glue sites
    public SortedSet<string>? CallerMethods { get; private set; }
    public void AddSite(string callerNamespace, bool glue, string? callerMethod)
    {
        IlSites++;
        if (glue) { GlueSites++; return; }
        Callers[callerNamespace] = Callers.GetValueOrDefault(callerNamespace) + 1;
        if (callerMethod != null) (CallerMethods ??= new(StringComparer.Ordinal)).Add(callerMethod);
    }
}

sealed class TypeUse(string name) : Sites
{
    public string Name { get; } = name;
    public int MemberRefs { get; set; }
    public bool InStub { get; set; }
    public bool? InReference { get; set; }
    public object ToJson() => new { type = Name, member_refs = MemberRefs, il_sites = IlSites, glue_sites = GlueSites, in_reference = InReference, callers = Callers, caller_methods = CallerMethods };
}

sealed class MemberUse(string key, string type, string name, string kind, string signature) : Sites
{
    public string Key { get; } = key;
    public string Type { get; } = type;
    public string Name { get; } = name;
    public string Kind { get; } = kind;
    public string Signature { get; } = signature;
    public int AttributeUses { get; set; }
    public Resolution Stub { get; set; } = null!;
    public Resolution? Reference { get; set; }
    public object ToJson() => new
    {
        key = Key,
        type = Type,
        member = Name,
        kind = Kind,
        signature = Signature,
        status = Stub.Status,
        not_implemented = Stub.NotImplemented,
        stub_candidates = Stub.Candidates,
        reference_status = Reference?.Status,
        il_sites = IlSites,
        glue_sites = GlueSites,
        attribute_uses = AttributeUses,
        callers = Callers,
        caller_methods = CallerMethods
    };
}

static class Signatures
{
    public static string Method(MethodSignature<string> sig) =>
        $"{(sig.Header.IsInstance ? "instance " : "")}{sig.ReturnType}"
        + (sig.GenericParameterCount > 0 ? $" <{sig.GenericParameterCount}>" : "")
        + $" ({string.Join(", ", sig.ParameterTypes)})";
}

static class TypeNames
{
    public static string Def(MetadataReader md, TypeDefinitionHandle handle)
    {
        var type = md.GetTypeDefinition(handle);
        var name = md.GetString(type.Name);
        var declaring = type.GetDeclaringType();
        if (!declaring.IsNil) return $"{Def(md, declaring)}/{name}";
        var ns = md.GetString(type.Namespace);
        return ns.Length == 0 ? name : $"{ns}.{name}";
    }

    public static string Ref(MetadataReader md, TypeReferenceHandle handle)
    {
        var type = md.GetTypeReference(handle);
        var name = md.GetString(type.Name);
        if (type.ResolutionScope.Kind == HandleKind.TypeReference)
            return $"{Ref(md, (TypeReferenceHandle)type.ResolutionScope)}/{name}";
        var ns = md.GetString(type.Namespace);
        return ns.Length == 0 ? name : $"{ns}.{name}";
    }

    public static string OuterNamespace(MetadataReader md, TypeDefinitionHandle handle)
    {
        var type = md.GetTypeDefinition(handle);
        while (!type.GetDeclaringType().IsNil) type = md.GetTypeDefinition(type.GetDeclaringType());
        var ns = md.GetString(type.Namespace);
        return ns.Length == 0 ? "<global>" : ns;
    }
}

sealed class NameProvider : ISignatureTypeProvider<string, object?>
{
    public static readonly NameProvider Instance = new();
    public string GetPrimitiveType(PrimitiveTypeCode code) => code switch
    {
        PrimitiveTypeCode.Boolean => "bool",
        PrimitiveTypeCode.Char => "char",
        PrimitiveTypeCode.SByte => "int8",
        PrimitiveTypeCode.Byte => "uint8",
        PrimitiveTypeCode.Int16 => "int16",
        PrimitiveTypeCode.UInt16 => "uint16",
        PrimitiveTypeCode.Int32 => "int32",
        PrimitiveTypeCode.UInt32 => "uint32",
        PrimitiveTypeCode.Int64 => "int64",
        PrimitiveTypeCode.UInt64 => "uint64",
        PrimitiveTypeCode.Single => "float32",
        PrimitiveTypeCode.Double => "float64",
        PrimitiveTypeCode.String => "string",
        PrimitiveTypeCode.Object => "object",
        PrimitiveTypeCode.IntPtr => "native int",
        PrimitiveTypeCode.UIntPtr => "native uint",
        PrimitiveTypeCode.TypedReference => "typedref",
        PrimitiveTypeCode.Void => "void",
        _ => code.ToString()
    };
    public string GetTypeFromDefinition(MetadataReader md, TypeDefinitionHandle handle, byte rawTypeKind) => TypeNames.Def(md, handle);
    public string GetTypeFromReference(MetadataReader md, TypeReferenceHandle handle, byte rawTypeKind) => TypeNames.Ref(md, handle);
    public string GetTypeFromSpecification(MetadataReader md, object? context, TypeSpecificationHandle handle, byte rawTypeKind) =>
        md.GetTypeSpecification(handle).DecodeSignature(this, context);
    public string GetSZArrayType(string elementType) => elementType + "[]";
    public string GetArrayType(string elementType, ArrayShape shape) => $"{elementType}[{new string(',', shape.Rank - 1)}]";
    public string GetByReferenceType(string elementType) => elementType + "&";
    public string GetPointerType(string elementType) => elementType + "*";
    public string GetPinnedType(string elementType) => elementType + " pinned";
    public string GetGenericInstantiation(string genericType, ImmutableArray<string> typeArguments) =>
        $"{genericType}<{string.Join(",", typeArguments)}>";
    public string GetGenericTypeParameter(object? context, int index) => $"!{index}";
    public string GetGenericMethodParameter(object? context, int index) => $"!!{index}";
    public string GetFunctionPointerType(MethodSignature<string> signature) => $"method {Signatures.Method(signature)}";
    public string GetModifiedType(string modifier, string unmodifiedType, bool isRequired) =>
        $"{unmodifiedType} {(isRequired ? "modreq" : "modopt")}({modifier})";
}

static class Il
{
    static readonly Dictionary<ushort, OperandType> Operands = typeof(OpCodes)
        .GetFields(BindingFlags.Public | BindingFlags.Static)
        .Select(field => (OpCode)field.GetValue(null)!)
        .ToDictionary(op => unchecked((ushort)op.Value), op => op.OperandType);

    public static void Walk(byte[] il, Action<ushort, int> token)
    {
        var i = 0;
        while (i < il.Length)
        {
            ushort code = il[i++];
            if (code == 0xFE) code = (ushort)(0xFE00 | il[i++]);
            switch (Operands[code])
            {
                case OperandType.InlineNone: break;
                case OperandType.ShortInlineBrTarget:
                case OperandType.ShortInlineI:
                case OperandType.ShortInlineVar: i += 1; break;
                case OperandType.InlineVar: i += 2; break;
                case OperandType.InlineField:
                case OperandType.InlineMethod:
                case OperandType.InlineTok:
                case OperandType.InlineType:
                    token(code, BitConverter.ToInt32(il, i));
                    i += 4;
                    break;
                case OperandType.InlineI8:
                case OperandType.InlineR: i += 8; break;
                case OperandType.InlineSwitch: i += 4 + 4 * BitConverter.ToInt32(il, i); break;
                default: i += 4; break;
            }
        }
    }

    // A stub body that only constructs and throws an exception (no return path).
    public static string? ThrowOnly(MetadataReader md, byte[] il)
    {
        var opcodes = new List<ushort>();
        var tokens = new List<int>();
        var i = 0;
        while (i < il.Length)
        {
            ushort code = il[i++];
            if (code == 0xFE) code = (ushort)(0xFE00 | il[i++]);
            opcodes.Add(code);
            var operand = Operands[code];
            var size = operand switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(il, i),
                _ => 4
            };
            tokens.Add(operand is OperandType.InlineMethod ? BitConverter.ToInt32(il, i) : 0);
            i += size;
        }
        if (opcodes.Count == 0 || opcodes[^1] != 0x7A || opcodes.Contains(0x2A)) return null;
        var newobj = opcodes.LastIndexOf(0x73);
        if (newobj < 0) return "throw";
        var ctor = MetadataTokens.EntityHandle(tokens[newobj]);
        return ctor.Kind switch
        {
            HandleKind.MemberReference => md.GetMemberReference((MemberReferenceHandle)ctor).Parent.Kind == HandleKind.TypeReference
                ? TypeNames.Ref(md, (TypeReferenceHandle)md.GetMemberReference((MemberReferenceHandle)ctor).Parent) : "throw",
            HandleKind.MethodDefinition => TypeNames.Def(md, md.GetMethodDefinition((MethodDefinitionHandle)ctor).GetDeclaringType()),
            _ => "throw"
        };
    }
}
