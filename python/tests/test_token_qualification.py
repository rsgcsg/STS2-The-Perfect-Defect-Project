"""Pure token qualification merge contracts; no model worker is constructed."""

from typing import Any

from stpd.fullrun.token_qualification import (
    consumer_physical_independence,
    merge_qualification_fact,
)


def _evidence(*claims: tuple[str, Any]) -> list[dict[str, Any]]:
    return [
        {"origin": origin, "facts": {"physical_game_independence": value}}
        for origin, value in claims
    ]


def test_unresolved_physical_claim_keeps_the_producer_default() -> None:
    evidence = _evidence(("current_dev_admission", "unresolved"))

    assert merge_qualification_fact(
        evidence, "physical_game_independence", "unresolved"
    ) == "unresolved"


def test_shared_physical_game_survives_unresolved_admission() -> None:
    evidence = _evidence(
        ("a" * 64, "shared_physical_game"),
        ("current_dev_admission", "unresolved"),
    )

    assert merge_qualification_fact(
        evidence, "physical_game_independence", "unresolved"
    ) == "shared_physical_game"


def test_distinct_physical_claims_remain_conflicting() -> None:
    evidence = _evidence(
        ("a" * 64, "shared_physical_game"),
        ("b" * 64, "independent"),
        ("current_dev_admission", "unresolved"),
    )

    expected = {
        "status": "conflicting_reported_facts",
        "claims": [
            {"origin": "a" * 64, "value": "shared_physical_game"},
            {"origin": "b" * 64, "value": "independent"},
        ],
    }
    assert merge_qualification_fact(
        evidence, "physical_game_independence", "unresolved"
    ) == expected
    assert consumer_physical_independence(evidence) == expected
