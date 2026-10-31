namespace STS2PlatformLiveUi;

/// <summary>Presentation draft only. A changed owner catalog invalidates every prior rank.</summary>
internal sealed class PlatformDeskSelection
{
    private readonly List<string> _ranking = [];
    private HashSet<string> _choices = new(StringComparer.Ordinal);
    internal string? CatalogKey { get; private set; }
    internal IReadOnlyList<string> Ranking => _ranking;
    internal bool Ready { get; private set; }
    internal bool Update(string? catalogKey, IEnumerable<string> choices, bool ready)
    {
        var next = new HashSet<string>(choices, StringComparer.Ordinal);
        bool changed = CatalogKey != catalogKey || !_choices.SetEquals(next);
        bool discarded = changed && _ranking.Count > 0;
        if (changed) _ranking.Clear();
        CatalogKey = catalogKey; _choices = next; Ready = ready && catalogKey is not null;
        return discarded;
    }
    internal void Toggle(string choice)
    {
        if (!Ready || !_choices.Contains(choice)) return;
        if (!_ranking.Remove(choice)) _ranking.Add(choice);
    }
    internal void Clear() => _ranking.Clear();
    internal string[]? Submission() => Ready && CatalogKey is not null && _ranking.Count > 0
        ? _ranking.ToArray() : null;
}
