"""Tests for the Campaign and AttackRound data types."""

from gomjabbar.redteam.campaign import AttackRound, Campaign


def test_round_succeeds_only_when_it_wins_every_episode():
    """A round counts as a success only if the attack fired on all its episodes."""
    full = AttackRound(
        round_index=0,
        mode="poison",
        payload_text="x",
        techniques=["tool-poisoning"],
        episodes_run=3,
        episodes_won=3,
    )
    partial = AttackRound(
        round_index=1,
        mode="poison",
        payload_text="y",
        techniques=[],
        episodes_run=3,
        episodes_won=2,
    )
    none = AttackRound(
        round_index=2,
        mode="inject",
        payload_text="z",
        techniques=[],
        episodes_run=3,
        episodes_won=0,
    )
    assert full.succeeded is True
    assert partial.succeeded is False  # 2 of 3 is not a win
    assert none.succeeded is False


def test_campaign_won_if_any_round_succeeded():
    """The campaign is won as soon as one round wins everywhere, even after failures."""
    campaign = Campaign(goal="leak the vault key", target_model="gpt-oss-120b")
    campaign.rounds.append(
        AttackRound(
            round_index=0,
            mode="poison",
            payload_text="a",
            techniques=[],
            episodes_run=3,
            episodes_won=0,
        )
    )
    assert campaign.won is False  # first round failed
    campaign.rounds.append(
        AttackRound(
            round_index=1,
            mode="poison",
            payload_text="b",
            techniques=["authority-framing"],
            episodes_run=3,
            episodes_won=3,
        )
    )
    assert campaign.won is True  # second round succeeded
    assert campaign.rounds_used == 2
