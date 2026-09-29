"""Train-only tokenizer preparation for a caller-admitted observed Human source."""

from __future__ import annotations

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from spireagent.json_boundary import BoundaryError

from .memory_sequence_bridge import _projectable_episode_items, _segments
from .memory_token_inputs import project_memory_snapshot
from .observed_input_sequence import ObservedInputView
from .token_inputs import input_texts


def fit_observed_memory_tokenizer(view: ObservedInputView, *,
                                  max_events: int = 1024,
                                  max_settling_events: int = 64) -> tuple[bytes, int]:
    """Fit on current complete public pages, including unlabeled observations.

    The view must already have passed the Workbench training-use ledger gate.
    No successor, delivery, hidden state, or future page enters this corpus.
    """
    if not isinstance(view, ObservedInputView) or view.stream_scope != "partial_human_input_stream":
        raise BoundaryError("memory_tokens", "human_observed_source_required")
    segments, diagnostics = _segments(view)
    if diagnostics or not segments or len(view.inputs) > max_events:
        raise BoundaryError("memory_tokens", "observed_sequence_limit_or_gap")
    texts: list[str] = []
    for segment in segments:
        projected = _projectable_episode_items(segment, max_settling_events)
        if not any(item.choice_mask for item in projected):
            raise BoundaryError("memory_tokens", "episode_without_label")
        for item in projected:
            if item.snapshot is None:
                raise BoundaryError("memory_tokens", "missing_observation")
            page = project_memory_snapshot(item.snapshot)
            if not page.action_texts or (item.choice_mask and
                                         page.action_ids.count(item.selected_action_id) != 1):
                raise BoundaryError("memory_tokens", "incomplete_observed_menu")
            state, actions = input_texts(page.state_text, page.action_texts)
            texts.extend((state, *actions))
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(texts, trainer=trainers.BpeTrainer(
        vocab_size=8192, min_frequency=2, show_progress=False,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=[],
    ))
    return tokenizer.to_str().encode("utf-8"), len(segments)
