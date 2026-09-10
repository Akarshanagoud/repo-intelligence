"""The one object that ties scanning, parsing, indexing and analysis together."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .architecture import DependencyGraph, build_graph, graph_to_mermaid, render_graph
from .insights import (
    ChurnStats,
    RepoInsights,
    analyse,
    find_duplicates,
    git_churn,
    render_insights,
)
from .parsing import ParsedFile, parse_all
from .scanner import ScanResult, scan
from .search import CodeIndex, SearchHit, build_index
from .summarize import RepoSummary, summarise_repository


@dataclass
class AnalysisConfig:
    languages: list[str] | None = None
    include_tests: bool = True
    respect_gitignore: bool = True
    use_git_churn: bool = True
    churn_since: str = "1 year ago"
    build_search_index: bool = True
    dimensions: int = 256

    def to_dict(self) -> dict[str, Any]:
        return {
            "languages": self.languages,
            "include_tests": self.include_tests,
            "respect_gitignore": self.respect_gitignore,
            "use_git_churn": self.use_git_churn,
        }


@dataclass
class Analysis:
    """Everything known about a repository after one pass."""

    root: Path
    scan_result: ScanResult
    parsed: list[ParsedFile]
    graph: DependencyGraph
    insights: RepoInsights
    index: CodeIndex | None = None
    churn: dict[str, ChurnStats] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    @property
    def parse_failures(self) -> list[ParsedFile]:
        return [p for p in self.parsed if p.parse_error]

    def search(self, query: str, top_k: int = 10, **kwargs: Any) -> list[SearchHit]:
        if self.index is None:
            raise RuntimeError("search index was not built for this analysis")
        return self.index.search(query, top_k=top_k, **kwargs)

    def summary(self) -> RepoSummary:
        return summarise_repository(
            self.scan_result, self.parsed, self.graph, self.insights
        )

    def test_contents(self) -> str:
        return "\n".join(f.text for f in self.scan_result.test_files)

    def duplicates(self) -> list[dict[str, Any]]:
        return find_duplicates(self.scan_result.files)

    def render_architecture(self) -> str:
        return render_graph(self.graph)

    def render_insights(self) -> str:
        return render_insights(
            self.insights,
            [f.relative for f in self.scan_result.test_files],
            self.test_contents(),
        )

    def mermaid(self) -> str:
        return graph_to_mermaid(self.graph)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "scan": self.scan_result.summary(),
            "parse_failures": [
                {"file": p.file, "error": p.parse_error} for p in self.parse_failures
            ],
            "architecture": self.graph.to_dict(),
            "insights": self.insights.to_dict(),
            "index": self.index.stats() if self.index else None,
        }


def analyze(
    root: str | Path, config: AnalysisConfig | None = None
) -> Analysis:
    """Run the full analysis pipeline over a repository."""
    settings = config or AnalysisConfig()
    started = time.perf_counter()
    base = Path(root).resolve()

    scan_result = scan(
        base,
        languages=settings.languages,
        include_tests=settings.include_tests,
        respect_gitignore=settings.respect_gitignore,
    )
    parsed = parse_all(scan_result.files)

    meta = {
        f.relative: {"lines": f.lines, "is_test": f.is_test}
        for f in scan_result.files
    }
    graph = build_graph(parsed, meta)

    churn = git_churn(base, settings.churn_since) if settings.use_git_churn else {}
    insights = analyse(scan_result, parsed, churn)

    index = (
        build_index(scan_result.files, parsed, settings.dimensions)
        if settings.build_search_index
        else None
    )

    return Analysis(
        root=base,
        scan_result=scan_result,
        parsed=parsed,
        graph=graph,
        insights=insights,
        index=index,
        churn=churn,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
