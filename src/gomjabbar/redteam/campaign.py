"""Data types for a Hunter-seeker attack campaign.

What: an AttackRound records one attempt (the payload, the techniques it drew on, and
how it scored); a Campaign is the sequence of rounds against one target goal.
Why: the campaign loop and the reports read these; keeping them as plain records means
the attacker logic and the scoring never have to agree on a loose dict shape.
How: Pydantic models, built by the campaign loop (campaign.py) round by round.
"""

from __future__ import annotations

from pydantic import BaseModel


class AttackRound(BaseModel):
    """One attempt in a campaign: what the attacker tried, and how it did."""

    round_index: int                 # 0, 1, 2... which attempt this is
    mode: str                        # "poison" or "inject": where the payload is delivered
    payload_text: str                # the actual attack text the attacker wrote
    techniques: list[str]            # ids of the library notes it retrieved first
    episodes_run: int                # how many test episodes this payload was tried on
    episodes_won: int                # how many of them the attacker's goal fired on

    @property
    def succeeded(self) -> bool:
        """The round is a success if the attack won on every episode it was tried on."""
        # Won on all of them (and at least one ran). Partial wins don't count as success,
        # so the attacker keeps refining until it works everywhere, matching the fixed
        # attacks' "all tasks" bar.
        return self.episodes_run > 0 and self.episodes_won == self.episodes_run


class Campaign(BaseModel):
    """A full campaign against one goal: a sequence of rounds, best-effort to succeed."""

    goal: str                        # plain-language description of what the attacker wants
    target_model: str                # the model under attack (undefended, in setup A)
    rounds: list[AttackRound] = []   # filled in as the loop runs

    @property
    def won(self) -> bool:
        """The campaign won if any round succeeded."""
        return any(r.succeeded for r in self.rounds)

    @property
    def rounds_used(self) -> int:
        return len(self.rounds)
