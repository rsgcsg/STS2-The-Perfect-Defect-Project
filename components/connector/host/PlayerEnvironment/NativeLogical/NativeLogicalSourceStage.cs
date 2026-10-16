using System.Collections.Generic;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>Detached source metadata shared by the production Hub and portable
/// linked-store consumers. No native owner or Host service dependency.</summary>
public sealed record NativeLogicalSourceStage(string Kind, string OwnerRef, string SubjectRef,
    IReadOnlyDictionary<string, string> Arguments, string Evidence, bool? NoSubmission);
