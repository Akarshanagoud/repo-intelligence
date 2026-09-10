"""Developer insights: complexity, churn, documentation, and where risk sits.

Complexity alone is a poor priority signal. A gnarly 300-line parser that
nobody has touched in three years is working fine; a moderately complex module
that six people changed last month is where the next defect comes from.

So the headline output is **risk = complexity x churn**, which is the pairing
that actually predicts defects — a file that is both hard to understand and
frequently changed. Churn comes from `git log`, and when git is unavailable the
score degrades to complexity alone and *says so*, rather than quietly reporting
a different metric under the same name.

The other outputs are chosen to be actionable rather than merely true:

* **Untested modules ranked by complexity** — where to write the next test.
* **Public API without docstrings** — where to write the next docstring.
* **God files and long functions** — where to split.
* **Near-duplicate code** — where the copy-paste happened.
"""
from __future__ import annotations

import math
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parsing import ParsedFile, Symbol
from .scanner import ScanResult, SourceFile


# --- git churn ---------------------------------------------------------------
@dataclass
class ChurnStats:
    commits: int = 0
    authors: int = 0
    last_changed: str = ""
    #: True when git data was actually available.
    available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "commits": self.commits,
            "authors": self.authors,
            "last_changed": self.last_changed,
        }


def git_churn(root: str | Path, since: str = "1 year ago") -> dict[str, ChurnStats]:
    """Commits and distinct authors per file, from ``git log``.

    Returns an empty mapping when the directory is not a git repository or git
    is unavailable. Callers must treat that as "unknown", not "zero" — a file
    with no churn data is not a file that never changes.
    """
    root = Path(root)
    command = [
        "git", "-C", str(root), "log", f"--since={since}",
        "--pretty=format:__COMMIT__%an", "--name-only",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}

    commits: dict[str, int] = defaultdict(int)
    authors: dict[str, set[str]] = defaultdict(set)
    current_author = ""

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            current_author = line[len("__COMMIT__") :]
            continue
        path = line.replace("\\", "/")
        commits[path] += 1
        if current_author:
            authors[path].add(current_author)

    return {
        path: ChurnStats(
            commits=count,
            authors=len(authors[path]),
            available=True,
        )
        for path, count in commits.items()
    }


# --- file-level metrics ------------------------------------------------------
@dataclass
class FileInsight:
    path: str
    language: str
    lines: int
    symbols: int
    max_complexity: int
    total_complexity: int
    mean_complexity: float
    documented_public: int
    total_public: int
    is_test: bool
    commits: int = 0
    authors: int = 0
    churn_available: bool = False

    @property
    def doc_coverage(self) -> float:
        return self.documented_public / self.total_public if self.total_public else 1.0

    @property
    def risk(self) -> float:
        """Complexity weighted by how often the file changes.

        Log-scaled churn: the difference between one commit and ten matters far
        more than between fifty and sixty.
        """
        churn_factor = math.log1p(self.commits) if self.churn_available else 1.0
        return self.total_complexity * max(churn_factor, 0.5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "lines": self.lines,
            "symbols": self.symbols,
            "max_complexity": self.max_complexity,
            "total_complexity": self.total_complexity,
            "mean_complexity": round(self.mean_complexity, 2),
            "doc_coverage": round(self.doc_coverage, 3),
            "commits": self.commits if self.churn_available else None,
            "authors": self.authors if self.churn_available else None,
            "risk": round(self.risk, 1),
            "is_test": self.is_test,
        }


@dataclass
class RepoInsights:
    files: list[FileInsight] = field(default_factory=list)
    churn_available: bool = False
    #: Symbols from source files only. Test symbols are tracked separately
    #: because including them distorts every ranking: nobody writes docstrings
    #: for `test_returns_400_on_bad_input`, so counting tests in documentation
    #: coverage reports a number that cannot be improved and should not be.
    symbols: list[Symbol] = field(default_factory=list)
    test_symbols: list[Symbol] = field(default_factory=list)

    # -- aggregate ------------------------------------------------------------
    @property
    def source_files(self) -> list[FileInsight]:
        return [f for f in self.files if not f.is_test]

    def totals(self) -> dict[str, Any]:
        source = self.source_files
        tests = [f for f in self.files if f.is_test]
        public = [s for s in self.symbols if s.is_public and s.kind != "constant"]
        documented = [s for s in public if s.has_docstring]
        complexities = [s.complexity for s in self.symbols] or [0]

        return {
            "files": len(self.files),
            "source_files": len(source),
            "test_files": len(tests),
            "test_ratio": round(len(tests) / len(self.files), 3) if self.files else 0.0,
            "lines": sum(f.lines for f in self.files),
            "symbols": len(self.symbols),
            "public_symbols": len(public),
            "documented_public_symbols": len(documented),
            "doc_coverage": round(len(documented) / len(public), 3) if public else 1.0,
            "mean_complexity": round(statistics.fmean(complexities), 2),
            "median_complexity": round(statistics.median(complexities), 2),
            "max_complexity": max(complexities),
            "churn_available": self.churn_available,
        }

    # -- rankings -------------------------------------------------------------
    def riskiest(self, limit: int = 10) -> list[FileInsight]:
        return sorted(self.source_files, key=lambda f: -f.risk)[:limit]

    def most_complex_symbols(self, limit: int = 10) -> list[Symbol]:
        return sorted(
            (s for s in self.symbols if s.kind in {"function", "method"}),
            key=lambda s: -s.complexity,
        )[:limit]

    def longest_symbols(self, limit: int = 10) -> list[Symbol]:
        return sorted(self.symbols, key=lambda s: -s.line_count)[:limit]

    def god_files(self, line_threshold: int = 500, limit: int = 10) -> list[FileInsight]:
        """Files large enough that they are probably several files."""
        return sorted(
            (f for f in self.source_files if f.lines >= line_threshold),
            key=lambda f: -f.lines,
        )[:limit]

    def undocumented_public_api(self, limit: int = 15) -> list[Symbol]:
        """Public symbols with no docstring, most complex first.

        Ordered by complexity because the undocumented function nobody can
        understand is a bigger problem than the undocumented one-liner.
        """
        return sorted(
            (
                s for s in self.symbols
                if s.is_public and not s.has_docstring
                and s.kind in {"function", "class", "method"}
            ),
            key=lambda s: -s.complexity,
        )[:limit]

    def untested_modules(
        self,
        test_files: list[str],
        test_contents: str = "",
        limit: int = 10,
    ) -> list[FileInsight]:
        """Source modules with no plausible test, ranked by complexity.

        A module counts as tested if its stem appears in a test *filename* or
        anywhere in test *source*. Searching the content matters: `test_guard.py`
        exercises `input_guard.py` without naming it in the filename, and a
        filename-only heuristic would report a well-tested module as untested -
        the kind of false positive that gets a whole report ignored.

        Still a prioritisation signal, not a coverage report, and labelled as
        such wherever it is displayed.
        """
        haystack = (" ".join(test_files) + " " + test_contents).lower()
        candidates = [
            f for f in self.source_files
            if Path(f.path).stem.lower() not in ("__init__", "conftest")
            and Path(f.path).stem.lower() not in haystack
        ]
        return sorted(candidates, key=lambda f: -f.total_complexity)[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "totals": self.totals(),
            "riskiest_files": [f.to_dict() for f in self.riskiest()],
            "most_complex_symbols": [s.to_dict() for s in self.most_complex_symbols()],
            "god_files": [f.to_dict() for f in self.god_files()],
            "undocumented_public_api": [
                s.to_dict() for s in self.undocumented_public_api()
            ],
        }


def analyse(
    scan_result: ScanResult,
    parsed: list[ParsedFile],
    churn: dict[str, ChurnStats] | None = None,
) -> RepoInsights:
    """Combine scan, parse and churn data into ranked insights."""
    by_path = {p.file: p for p in parsed}
    churn_data = churn if churn is not None else {}
    insights = RepoInsights(churn_available=bool(churn_data))

    for source in scan_result.files:
        parsed_file = by_path.get(source.relative)
        if parsed_file is None:
            continue
        symbols = parsed_file.symbols
        if source.is_test:
            insights.test_symbols.extend(symbols)
        else:
            insights.symbols.extend(symbols)

        complexities = [s.complexity for s in symbols] or [0]
        public = [s for s in symbols if s.is_public and s.kind != "constant"]
        stats = churn_data.get(source.relative, ChurnStats())

        insights.files.append(
            FileInsight(
                path=source.relative,
                language=source.language,
                lines=source.lines,
                symbols=len(symbols),
                max_complexity=max(complexities),
                total_complexity=sum(complexities),
                mean_complexity=statistics.fmean(complexities) if symbols else 0.0,
                documented_public=sum(1 for s in public if s.has_docstring),
                total_public=len(public),
                is_test=source.is_test,
                commits=stats.commits,
                authors=stats.authors,
                churn_available=stats.available,
            )
        )

    return insights


# --- duplication -------------------------------------------------------------
def find_duplicates(
    files: list[SourceFile], window: int = 6, min_matches: int = 2
) -> list[dict[str, Any]]:
    """Find repeated blocks of normalised source lines.

    Normalisation strips whitespace, comments and string literals, so a copy
    that was reindented or had its messages reworded is still detected. Windows
    of identical *blank-ish* lines are skipped, since every file has those.
    """
    fingerprints: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for source in files:
        lines = [_normalise_line(line, source.language) for line in source.text.splitlines()]
        for start in range(len(lines) - window + 1):
            block = lines[start : start + window]
            # A window of trivial lines (closing brackets, imports) is not a
            # meaningful duplicate.
            if sum(1 for line in block if len(line) > 12) < window - 1:
                continue
            key = "\n".join(block)
            fingerprints[key].append((source.relative, start + 1))

    duplicates = [
        {
            "occurrences": len(locations),
            "lines": window,
            "locations": [{"file": f, "line": line} for f, line in sorted(locations)],
            "preview": key.splitlines()[0][:90],
        }
        for key, locations in fingerprints.items()
        if len(locations) >= min_matches and len({f for f, _ in locations}) > 1
    ]
    duplicates.sort(key=lambda d: -d["occurrences"])
    return duplicates[:20]


def _normalise_line(line: str, language: str) -> str:
    import re

    stripped = line.strip()
    if language == "python":
        stripped = re.sub(r"#.*$", "", stripped)
    else:
        stripped = re.sub(r"//.*$", "", stripped)
    # Collapse string literals so reworded messages still match.
    stripped = re.sub(r'"[^"]*"', '""', stripped)
    stripped = re.sub(r"'[^']*'", "''", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def render_insights(
    insights: RepoInsights, test_files: list[str], test_contents: str = ""
) -> str:
    """Human-readable insight report."""
    totals = insights.totals()
    lines = [
        f"files              : {totals['files']} "
        f"({totals['source_files']} source, {totals['test_files']} test)",
        f"lines              : {totals['lines']:,}",
        f"symbols            : {totals['symbols']} "
        f"({totals['public_symbols']} public)",
        f"doc coverage       : {totals['doc_coverage']:.0%} of public symbols",
        f"complexity         : mean {totals['mean_complexity']}, "
        f"median {totals['median_complexity']}, max {totals['max_complexity']}",
    ]

    if insights.churn_available:
        lines.append("risk               : complexity x log(commits) from git history")
    else:
        lines.append(
            "risk               : complexity only (no git history available, "
            "so churn is unknown rather than zero)"
        )

    riskiest = insights.riskiest(8)
    if riskiest:
        lines.append("\nhighest-risk files (complex and frequently changed):")
        header = f"  {'FILE':<52}{'RISK':>8}{'CPLX':>7}{'COMMITS':>9}{'DOCS':>7}"
        lines.append(header)
        for file in riskiest:
            commits = str(file.commits) if file.churn_available else "-"
            lines.append(
                f"  {file.path:<52}{file.risk:>8.0f}{file.total_complexity:>7}"
                f"{commits:>9}{file.doc_coverage:>7.0%}"
            )

    complex_symbols = insights.most_complex_symbols(6)
    if complex_symbols:
        lines.append("\nmost complex functions (candidates for splitting):")
        for symbol in complex_symbols:
            lines.append(
                f"  complexity {symbol.complexity:>3}  {symbol.name:<34} "
                f"{symbol.file}:{symbol.line}"
            )

    untested = insights.untested_modules(test_files, test_contents, limit=6)
    if untested:
        lines.append("\nmodules with no obvious test, by complexity:")
        for file in untested:
            lines.append(f"  complexity {file.total_complexity:>4}  {file.path}")

    undocumented = insights.undocumented_public_api(6)
    if undocumented:
        lines.append("\nundocumented public API, most complex first:")
        for symbol in undocumented:
            lines.append(
                f"  complexity {symbol.complexity:>3}  {symbol.name:<34} "
                f"{symbol.file}:{symbol.line}"
            )

    god = insights.god_files(limit=5)
    if god:
        lines.append("\nlargest files (candidates for splitting):")
        for file in god:
            lines.append(f"  {file.lines:>5} lines  {file.path}")

    return "\n".join(lines)
