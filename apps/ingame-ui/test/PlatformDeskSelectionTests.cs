using STS2PlatformLiveUi;
using Xunit;

public sealed class PlatformDeskSelectionTests
{
    [Fact]
    public void MouseCancellationCompactsRanksAndNeverExecutesAnEmptyList()
    {
        var draft = new PlatformDeskSelection();
        draft.Update("current", ["a", "b", "c"], true);
        Assert.Null(draft.Submission()!);
        draft.Toggle("b"); draft.Toggle("a"); draft.Toggle("c");
        Assert.Equal(["b", "a", "c"], draft.Submission()!);
        draft.Toggle("a");
        Assert.Equal(["b", "c"], draft.Submission()!);
        draft.Toggle("b");
        Assert.Equal(["c"], draft.Submission()!);
        draft.Clear(); Assert.Null(draft.Submission()!);
    }

    [Fact]
    public void PresentationRefreshKeepsRanksButChangedOwnerBasisDiscardsThem()
    {
        var draft = new PlatformDeskSelection();
        draft.Update("scene-1", ["a", "b"], true); draft.Toggle("b");
        Assert.False(draft.Update("scene-1", ["a", "b"], true));
        Assert.Equal(["b"], draft.Submission()!);
        Assert.True(draft.Update("scene-2", ["a", "b"], true));
        Assert.Null(draft.Submission()!);
        draft.Toggle("a");
        Assert.True(draft.Update("scene-2", ["b", "c"], true));
        Assert.Null(draft.Submission()!);
    }

    [Fact]
    public void ModelOwnershipAndUnstableSceneDisableSubmissionWithoutInventingChoices()
    {
        var draft = new PlatformDeskSelection();
        draft.Update("scene", ["a", "b"], true); draft.Toggle("a");
        draft.Update("scene", ["a", "b"], false);
        Assert.Null(draft.Submission()!);
        draft.Toggle("b");
        draft.Update("scene", ["a", "b"], true);
        Assert.Equal(["a"], draft.Submission()!);
        draft.Toggle("not-in-catalog"); Assert.Equal(["a"], draft.Submission()!);
        draft.Update(null, [], true); Assert.Null(draft.Submission()!);
    }
}
