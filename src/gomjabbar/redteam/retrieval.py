"""BM25 keyword retrieval over the attack-technique library.

What: given a short query (what the attacker is trying to do) and a set of technique
notes, return the few notes whose words best match the query.
Why: the attacker agent uses this to ground its payloads in documented techniques
instead of inventing blind. This is the retrieval (RAG) component of Hunter-seeker.
How: a from-scratch BM25 scorer. No external search library, no model, no network.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens. Good enough for short English notes."""
    # \w+ grabs runs of letters/digits/underscore; everything else is a separator.
    return re.findall(r"\w+", text.lower())


@dataclass
class Document:
    """One technique note the attacker can retrieve."""

    id: str  # short name, e.g. "tool-poisoning"
    text: str  # the note's full text, which we search over


class BM25:
    """A tiny BM25 index. Build it once from the documents, then query it many times."""

    # The two standard BM25 knobs. k1 controls how much repeated words help;
    # b controls how strongly long documents are penalised. These are the usual defaults.
    def __init__(self, documents: list[Document], k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        # Tokenize every document once and remember its length in words.
        self.doc_tokens = [_tokenize(d.text) for d in documents]
        self.doc_len = [len(toks) for toks in self.doc_tokens]
        # Average document length, used to normalise for length. Guard against no docs.
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        # Document frequency: for each word, how many documents contain it at least once.
        self.df: dict[str, int] = {}
        for toks in self.doc_tokens:
            for word in set(toks):  # set(): count each word once per document
                self.df[word] = self.df.get(word, 0) + 1
        self.n_docs = len(documents)

    def _idf(self, word: str) -> float:
        """Inverse document frequency: rare words score high, common words score low."""
        # A word in no document has df 0. The +0.5 terms are BM25's standard smoothing,
        # and max(..., 0) stops a word that is in every document from going negative.
        df = self.df.get(word, 0)
        return max(math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0), 0.0)

    def _score(self, query_tokens: list[str], index: int) -> float:
        """BM25 score of one document (by index) against the query tokens."""
        toks = self.doc_tokens[index]
        length = self.doc_len[index]
        score = 0.0
        for word in query_tokens:
            # How many times this query word appears in this document.
            tf = toks.count(word)
            if tf == 0:
                continue
            idf = self._idf(word)
            # The BM25 term-frequency formula: tf helps, but with diminishing returns (k1),
            # and long documents are discounted (b and avg_len).
            denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
            score += idf * (tf * (self.k1 + 1)) / denom
        return score

    def top(self, query: str, k: int = 3) -> list[Document]:
        """Return the k documents that best match the query, best first."""
        query_tokens = _tokenize(query)
        # Score every document, keep its index so we can sort then fetch the Document.
        scored = [(self._score(query_tokens, i), i) for i in range(self.n_docs)]
        # Sort by score descending; a document with score 0 matched nothing.
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [self.documents[i] for score, i in scored[:k] if score > 0]


# The folder holding the attack-technique notes, next to this file.
LIBRARY_DIR = Path(__file__).parent / "library"


def load_library(directory: Path = LIBRARY_DIR) -> list[Document]:
    """Read every .md note in the library into a searchable Document.

    Each note is YAML frontmatter between two '---' lines, then a markdown body.
    We search over the summary plus the body, and keep the id for reference.
    """
    documents = []
    # sorted() so the order is stable run to run (and tests are deterministic).
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        # A well-formed note starts with '---', so splitting on '---' gives
        # ['', '<frontmatter>', '<body>']. maxsplit=2 leaves any '---' in the body alone.
        _, frontmatter, body = raw.split("---", 2)
        meta = yaml.safe_load(frontmatter)  # the key-value block as a dict
        text = meta["summary"] + "\n" + body  # search over summary plus body
        documents.append(Document(id=meta["id"], text=text))
    return documents
