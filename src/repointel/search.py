"""Natural-language search over source code.

Code search fails differently from prose search, and the difference is almost
entirely about **identifiers**.

A developer asks "where do we validate the auth token?" and the answer is a
method called `validateAuthToken` or `_check_bearer_credentials`. Ordinary
tokenisation sees one meaningless word; the query sees three meaningful ones,
and they never meet. So identifiers are split on camelCase, snake_case,
PascalCase and digit boundaries before anything else happens — that single step
does more for code-search quality than any embedding choice.

Two further things code search needs that prose search does not:

* **Chunking on symbol boundaries**, not line windows. A function cut in half
  answers no question, and a window spanning the end of one function and the
  start of another answers the wrong one.
* **Field weighting.** A query term appearing in a function's *name* is far
  stronger evidence than the same term in its body, and flat scoring throws
  that away.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .parsing import ParsedFile, Symbol
from .scanner import SourceFile

_IDENTIFIER_SPLIT = re.compile(
    r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+"
)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")

#: Language keywords carry no retrieval signal - every file has them.
STOPWORDS = frozenset(
    """
    the a an and or of to in is are was were be been for from as at by on that
    this it its with if else elif for while return def class import self none
    true false null void public private static const let var function func type
    struct interface new not in do end then
    """.split()  # noqa: SIM905 - readable as prose
)


def split_identifier(identifier: str) -> list[str]:
    """Split an identifier into its constituent words.

    ``validateAuthToken`` -> ``[validate, auth, token]``
    ``_check_bearer_cred`` -> ``[check, bearer, cred]``
    ``HTTPSConnection``    -> ``[https, connection]``
    ``parse2FA``           -> ``[parse, 2, fa]``
    """
    return [part.lower() for part in _IDENTIFIER_SPLIT.findall(identifier) if part]


def tokenize_code(text: str, keep_stopwords: bool = False) -> list[str]:
    """Tokenise source text, expanding identifiers into their parts.

    The whole identifier is kept *as well as* its parts, so an exact search for
    ``validateAuthToken`` still ranks the exact match first.
    """
    tokens: list[str] = []
    for match in _TOKEN.findall(text):
        lowered = match.lower()
        tokens.append(lowered)
        parts = split_identifier(match)
        if len(parts) > 1:
            tokens.extend(parts)
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


# --- chunking ----------------------------------------------------------------
@dataclass
class CodeChunk:
    """A retrievable unit of code, anchored to its source."""

    chunk_id: str
    file: str
    text: str
    start_line: int
    end_line: int
    language: str
    kind: str = "block"
    symbol: str = ""
    signature: str = ""
    docstring: str = ""
    complexity: int = 1
    is_test: bool = False
    #: Every symbol defined inside this chunk, including nested ones.
    #:
    #: A class is chunked whole rather than once per method, so without this
    #: a method is unreachable by name - and "jump to definition" that cannot
    #: find a method is not jump to definition.
    contains: list[str] = field(default_factory=list)
    embedding: list[float] | None = None

    @property
    def citation(self) -> str:
        return f"{self.file}:{self.start_line}-{self.end_line}"

    def to_dict(self, include_text: bool = False) -> dict[str, Any]:
        payload = {
            "chunk_id": self.chunk_id,
            "file": self.file,
            "citation": self.citation,
            "symbol": self.symbol,
            "kind": self.kind,
            "signature": self.signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "complexity": self.complexity,
            "is_test": self.is_test,
        }
        if include_text:
            payload["text"] = self.text
        return payload


def _symbols_within(parsed: ParsedFile, start: int, end: int) -> list[str]:
    return sorted(
        {s.name for s in parsed.symbols if start <= s.line <= end}
    )


def chunk_file(
    source: SourceFile, parsed: ParsedFile, max_lines: int = 120
) -> list[CodeChunk]:
    """Split a file on symbol boundaries, falling back to windows.

    Symbols are preferred because a chunk that starts at ``def`` and ends at the
    last line of that function is a unit that answers a question. Nested
    symbols are skipped in favour of their parent so a class and its methods do
    not each produce overlapping chunks of the same text.
    """
    lines = source.text.splitlines()
    chunks: list[CodeChunk] = []

    top_level = [
        s for s in parsed.symbols
        if s.parent is None and s.kind in {"function", "class", "struct", "interface", "method", "type", "enum", "trait", "module"}
    ]
    top_level.sort(key=lambda s: s.line)

    covered: set[int] = set()
    for index, symbol in enumerate(top_level):
        start = symbol.line
        end = symbol.end_line
        if end <= start:
            # Pattern-parsed symbols have no reliable extent; use the next
            # symbol's start as the boundary.
            end = (
                top_level[index + 1].line - 1
                if index + 1 < len(top_level)
                else min(start + max_lines, len(lines))
            )
        end = min(max(end, start), len(lines))
        if end - start + 1 > max_lines * 3:
            end = start + max_lines * 3

        text = "\n".join(lines[start - 1 : end])
        if not text.strip():
            continue
        covered.update(range(start, end + 1))
        chunks.append(
            CodeChunk(
                chunk_id=f"{source.relative}::{start}",
                file=source.relative,
                text=text,
                start_line=start,
                end_line=end,
                language=source.language,
                kind=symbol.kind,
                symbol=symbol.qualified_name or symbol.name,
                signature=symbol.signature,
                docstring=symbol.docstring,
                complexity=symbol.complexity,
                is_test=source.is_test,
                contains=_symbols_within(parsed, start, end),
            )
        )

    # Anything not inside a symbol - module docstrings, constants, config files -
    # still needs to be searchable.
    uncovered = [i for i in range(1, len(lines) + 1) if i not in covered]
    if uncovered:
        run_start = uncovered[0]
        previous = uncovered[0]
        for current in uncovered[1:] + [None]:
            if current is not None and current == previous + 1 and previous - run_start < max_lines:
                previous = current
                continue
            text = "\n".join(lines[run_start - 1 : previous])
            if len(text.strip()) > 40:
                chunks.append(
                    CodeChunk(
                        chunk_id=f"{source.relative}::block::{run_start}",
                        file=source.relative,
                        text=text,
                        start_line=run_start,
                        end_line=previous,
                        language=source.language,
                        kind="block",
                        is_test=source.is_test,
                    )
                )
            if current is None:
                break
            run_start = previous = current

    chunks.sort(key=lambda c: c.start_line)
    return chunks


# --- embedding ---------------------------------------------------------------
def _hash_index(token: str, dimensions: int) -> tuple[int, int]:
    digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
    value = struct.unpack("<Q", digest)[0]
    return value % dimensions, 1 if (value >> 63) & 1 else -1


class CodeEmbedder:
    """Hashed bag-of-identifier-parts embedding.

    Deterministic, no download, and specialised for code by construction: the
    identifier splitting happens before hashing, so ``validateAuthToken`` and a
    query for "validate auth" land in overlapping buckets.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        counts = Counter(tokenize_code(text))
        for token, count in counts.items():
            index, sign = _hash_index(token, self.dimensions)
            vector[index] += sign * (1 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- index -------------------------------------------------------------------
@dataclass
class SearchHit:
    chunk: CodeChunk
    score: float
    components: dict[str, float] = field(default_factory=dict)
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self, include_text: bool = False) -> dict[str, Any]:
        return {
            **self.chunk.to_dict(include_text),
            "score": round(self.score, 5),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "matched_terms": self.matched_terms[:8],
        }


#: Field weights. A term in a symbol name is much stronger evidence than the
#: same term buried in a function body.
FIELD_WEIGHTS = {"symbol": 3.0, "signature": 2.0, "docstring": 1.5, "body": 1.0}

#: Test code is searchable but ranked below implementation by default.
#:
#: Tests are lexically ideal answers - `test_injection_is_blocked_by_default`
#: contains every query term - so without this penalty they crowd out the code
#: they exercise, and "where do we detect injection?" returns the test suite.
#: A developer looking for the test can ask for it with `include_tests`.
TEST_RANK_PENALTY = 0.55


class CodeIndex:
    """Hybrid lexical and vector search over code chunks."""

    K1 = 1.4
    B = 0.7

    def __init__(self, dimensions: int = 256) -> None:
        self.embedder = CodeEmbedder(dimensions)
        self.chunks: list[CodeChunk] = []
        self._term_frequencies: list[Counter[str]] = []
        self._lengths: list[int] = []
        self._document_frequency: Counter[str] = Counter()
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._average_length = 0.0

    def add(self, chunks: list[CodeChunk]) -> None:
        for chunk in chunks:
            index = len(self.chunks)
            # Weighted token bag: name tokens are repeated so BM25 sees them as
            # more frequent, which is the simplest correct way to weight fields.
            tokens: list[str] = []
            tokens += tokenize_code(chunk.symbol) * int(FIELD_WEIGHTS["symbol"])
            tokens += tokenize_code(" ".join(chunk.contains)) * int(FIELD_WEIGHTS["symbol"])
            tokens += tokenize_code(chunk.signature) * int(FIELD_WEIGHTS["signature"])
            tokens += tokenize_code(chunk.docstring) * int(FIELD_WEIGHTS["docstring"])
            tokens += tokenize_code(chunk.text)

            chunk.embedding = self.embedder.embed(
                f"{chunk.symbol} {chunk.signature} {chunk.docstring} {chunk.text}"
            )
            counts = Counter(tokens)
            self.chunks.append(chunk)
            self._term_frequencies.append(counts)
            self._lengths.append(len(tokens) or 1)
            for term in counts:
                self._document_frequency[term] += 1
                self._postings[term].append(index)

        self._average_length = (
            sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        )

    def _idf(self, term: str) -> float:
        total = len(self.chunks)
        frequency = self._document_frequency.get(term, 0)
        return math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))

    def _lexical(self, query: str, limit: int) -> list[tuple[int, float, list[str]]]:
        terms = tokenize_code(query)
        if not terms:
            return []
        scores: dict[int, float] = defaultdict(float)
        matched: dict[int, set[str]] = defaultdict(set)

        for term in set(terms):
            idf = self._idf(term)
            for index in self._postings.get(term, ()):
                frequency = self._term_frequencies[index][term]
                length = self._lengths[index]
                numerator = frequency * (self.K1 + 1)
                denominator = frequency + self.K1 * (
                    1 - self.B + self.B * length / (self._average_length or 1)
                )
                scores[index] += idf * numerator / denominator
                matched[index].add(term)

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [(i, s, sorted(matched[i])) for i, s in ranked]

    def search(
        self,
        query: str,
        top_k: int = 10,
        language: str | None = None,
        kind: str | None = None,
        path_prefix: str | None = None,
        include_tests: bool = True,
        rank_tests_equally: bool = False,
    ) -> list[SearchHit]:
        """Hybrid search: BM25 over weighted fields fused with vector similarity."""
        if not self.chunks:
            return []

        candidate_k = max(top_k * 6, 40)
        lexical = self._lexical(query, candidate_k)

        query_vector = self.embedder.embed(query)
        dense = sorted(
            (
                (index, cosine(query_vector, chunk.embedding or []))
                for index, chunk in enumerate(self.chunks)
            ),
            key=lambda pair: -pair[1],
        )[:candidate_k]

        # Reciprocal rank fusion: BM25 is unbounded, cosine is not, and only
        # rank is comparable between them.
        fused: dict[int, dict[str, Any]] = {}
        for rank, (index, score, terms) in enumerate(lexical, start=1):
            entry = fused.setdefault(
                index, {"score": 0.0, "components": {}, "terms": []}
            )
            entry["score"] += 1.0 / (60 + rank)
            entry["components"]["lexical"] = score
            entry["terms"] = terms
        for rank, (index, score) in enumerate(dense, start=1):
            if score <= 0:
                continue
            entry = fused.setdefault(
                index, {"score": 0.0, "components": {}, "terms": []}
            )
            entry["score"] += 1.0 / (60 + rank)
            entry["components"]["semantic"] = score

        hits: list[SearchHit] = []
        for index, payload in fused.items():
            chunk = self.chunks[index]
            if language and chunk.language != language:
                continue
            if kind and chunk.kind != kind:
                continue
            if path_prefix and not chunk.file.startswith(path_prefix):
                continue
            if chunk.is_test and not include_tests:
                continue

            score = payload["score"]
            components = dict(payload["components"])
            if chunk.is_test and not rank_tests_equally:
                components["test_penalty"] = TEST_RANK_PENALTY
                score *= TEST_RANK_PENALTY

            hits.append(
                SearchHit(
                    chunk=chunk,
                    score=score,
                    components=components,
                    matched_terms=payload["terms"],
                )
            )

        hits.sort(key=lambda h: -h.score)
        return hits[:top_k]

    def find_symbol(self, name: str, limit: int = 10) -> list[CodeChunk]:
        """Exact and partial symbol lookup, for jump-to-definition.

        Searches nested symbols too, so a method defined inside a class chunk
        is still reachable by its own name.
        """
        target = name.lower()

        exact = [
            c for c in self.chunks
            if c.symbol.rsplit(".", 1)[-1].lower() == target
            or any(inner.lower() == target for inner in c.contains)
        ]
        partial = [
            c for c in self.chunks
            if c not in exact
            and (
                target in c.symbol.lower()
                or any(target in inner.lower() for inner in c.contains)
            )
        ]
        return (exact + partial)[:limit]

    def similar_to(self, chunk_id: str, limit: int = 5) -> list[SearchHit]:
        """Find code similar to a given chunk. Useful for spotting duplication."""
        source = next((c for c in self.chunks if c.chunk_id == chunk_id), None)
        if source is None or source.embedding is None:
            return []
        scored = [
            SearchHit(chunk=chunk, score=cosine(source.embedding, chunk.embedding or []))
            for chunk in self.chunks
            if chunk.chunk_id != chunk_id
        ]
        scored.sort(key=lambda h: -h.score)
        return scored[:limit]

    def stats(self) -> dict[str, Any]:
        return {
            "chunks": len(self.chunks),
            "vocabulary": len(self._document_frequency),
            "mean_chunk_lines": round(
                sum(c.end_line - c.start_line + 1 for c in self.chunks) / len(self.chunks), 1
            )
            if self.chunks
            else 0,
            "dimensions": self.embedder.dimensions,
        }

    def __len__(self) -> int:
        return len(self.chunks)


def build_index(
    files: list[SourceFile], parsed: list[ParsedFile], dimensions: int = 256
) -> CodeIndex:
    """Chunk and index a parsed repository."""
    index = CodeIndex(dimensions)
    by_path = {p.file: p for p in parsed}
    chunks: list[CodeChunk] = []
    for source in files:
        parsed_file = by_path.get(source.relative)
        if parsed_file is None:
            continue
        chunks.extend(chunk_file(source, parsed_file))
    index.add(chunks)
    return index


def symbol_chunks(symbols: list[Symbol]) -> dict[str, Symbol]:
    return {s.qualified_name or s.name: s for s in symbols}
