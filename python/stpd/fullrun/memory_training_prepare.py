"""Train-only tokenizer preparation for a caller-admitted observed Human source."""

from __future__ import annotations

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from spireagent.json_boundary import BoundaryError

from .confirmed_interaction import HISTORY_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE
from .memory_sequence_bridge import _projectable_episode_items, _segments
from .memory_token_inputs import project_memory_profile_snapshot
from .observed_input_sequence import ObservedInput, ObservedInputView
from .text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE
from .token_inputs import input_texts


def fit_observed_memory_tokenizer(view: ObservedInputView, *,
                                  max_events: int = 1024,
                                  max_settling_events: int = 64,
                                  input_profile: str = INPUT_PROFILE) -> tuple[bytes, int]:
    """Fit on current complete public pages, including unlabeled observations.

    The caller must already have passed its owning training-use ledger gate.
    No successor, delivery, hidden state, or future page enters this corpus.
    """
    if input_profile in {INPUT_PROFILE, HISTORY_INPUT_PROFILE}:
        scope = "partial_human_input_stream"
    elif input_profile in {V2_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE}:
        scope = "managed_engineering_control_inputs"
        if max_settling_events != 0:
            raise BoundaryError("memory_tokens", "v2_settling_unsupported")
    else:
        raise BoundaryError("memory_tokens", "unknown_text_menu_profile")
    if not isinstance(view, ObservedInputView) or view.stream_scope != scope:
        raise BoundaryError("memory_tokens", "human_observed_source_required" if
                            input_profile == INPUT_PROFILE else
                            "observed_source_profile_mismatch")
    if input_profile in {V2_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE} and any(
        not isinstance(item, ObservedInput)
        or item.source_kind != "managed_control_input_stream" for item in view.inputs
    ):
        raise BoundaryError("memory_tokens", "observed_source_profile_mismatch")
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
            page = project_memory_profile_snapshot(item.snapshot, input_profile)
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
