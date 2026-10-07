"""Tests for the BM25 retrieval over the attack-technique library."""

from gomjabbar.redteam.retrieval import BM25, Document

# Three toy notes with clearly different topics, so we know what SHOULD match.
DOCS = [
    Document(
        id="poison", text="Poison a tool description with hidden instructions to leak a secret."
    ),
    Document(id="shadow", text="Add a shadow tool that does not exist on the real server."),
    Document(id="inject", text="Inject instructions into the tool result returned to the agent."),
]


def test_query_returns_the_topically_closest_note_first():
    """A query about poisoned descriptions should rank the poison note first."""
    index = BM25(DOCS)
    results = index.top("hidden instructions in a tool description", k=3)
    assert results[0].id == "poison"  # best match is the poison note


def test_unrelated_query_returns_nothing():
    """A query sharing no words with any note returns an empty list, not padding."""
    index = BM25(DOCS)
    results = index.top("database index performance tuning", k=3)
    assert results == []  # score 0 everywhere, so nothing comes back


def test_shadow_query_finds_the_shadow_note():
    """The distinctive word 'shadow' should pull up the shadow note."""
    index = BM25(DOCS)
    results = index.top("a fake shadow tool", k=1)
    assert results[0].id == "shadow"


from gomjabbar.redteam.retrieval import load_library


def test_library_loads_the_real_notes():
    """Both shipped notes load, with their ids intact."""
    docs = load_library()
    ids = {d.id for d in docs}
    assert "tool-poisoning" in ids
    assert "authority-framing" in ids


def test_authority_query_finds_the_authority_note():
    """A query about enforced policy framing should surface the authority note."""
    index = BM25(load_library())
    results = index.top("enforced security policy compliance requirement", k=1)
    assert results[0].id == "authority-framing"
