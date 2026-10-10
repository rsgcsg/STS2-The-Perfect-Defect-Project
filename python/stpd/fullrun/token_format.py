"""Stable text wrapper identity shared by training inputs and M2 profile metadata."""

FORMAT = "separate-obs-act-text-v1"


def input_texts(state: str, actions: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Delimit roles explicitly. Encode separately, then concatenate IDs for B."""
    return "OBS\n" + state + "\n", tuple("ACT\n" + a + "\n" for a in actions)
