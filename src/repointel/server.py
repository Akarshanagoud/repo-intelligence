"""HTTP service and web UI: a codebase explorer over one analysis pass.

    pip install -e ".[server]"
    repointel ui [PATH]            # opens http://127.0.0.1:8770/ui/

The service analyses a local repository at start-up (``REPOINTEL_PATH``, or
the working directory) and can re-analyse another local path on request. File
contents are only ever served for files the scan itself accepted, so the UI
cannot be used to read arbitrary paths.
"""
from __future__ import annotations

import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .analyzer import Analysis, analyze

WEB_DIR = Path(__file__).parent / "web"
COMPLEXITY_BUCKETS = [(1, 1, "1"), (2, 5, "2-5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 10**9, "21+")]
MAX_FILE_LINES = 4000


class AnalyzeRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


def _layers(nodes: list[str], imports: dict[str, set[str]]) -> dict[str, int]:
    """Longest dependency chain below each module; modules on a cycle share a layer."""
    layer: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(node: str) -> int:
        if node in layer:
            return layer[node]
        if node in visiting:  # on a cycle: stop here rather than recurse forever
            return 0
        visiting.add(node)
        value = 1 + max((depth(dep) for dep in imports.get(node, ()) if dep in imports), default=-1)
        visiting.discard(node)
        layer[node] = value
        return value

    for node in nodes:
        depth(node)
    return layer


class Explorer:
    """The current analysis plus the lookups the views need."""

    def __init__(self, root: Path) -> None:
        self._lock = threading.Lock()
        self.load(root)

    def load(self, root: Path) -> None:
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(status_code=404, detail=f"not a directory: {path}")
        analysis = analyze(path)
        with self._lock:
            self.analysis: Analysis = analysis
            self.files = {f.relative: f for f in analysis.scan_result.files}
            self.parsed = {p.file: p for p in analysis.parsed}

    def suggestions(self, limit: int = 4) -> list[str]:
        """Example queries taken from this codebase's own docstrings.

        Generated rather than hard-coded, so they fit whatever repository is
        loaded and never plant matching text in the code being searched.
        """
        documented = [
            s for p in self.analysis.parsed for s in p.symbols
            if s.kind in ("function", "method") and not s.name.startswith("_") and s.docstring
        ]
        documented.sort(key=lambda s: -s.complexity)
        out: list[str] = []
        for symbol in documented:
            first = re.split(r"(?<=[.!?])\s", symbol.docstring.strip(), maxsplit=1)[0]
            words = re.sub(r"[^A-Za-z0-9 ]+", " ", first).lower().split()
            if 3 <= len(words) <= 9:
                phrase = " ".join(words)
                if phrase not in out:
                    out.append(phrase)
            if len(out) == limit:
                break
        return out

    # -- views ----------------------------------------------------------------
    def overview(self) -> dict[str, Any]:
        a = self.analysis
        summary = a.summary()
        data = summary.to_dict() if hasattr(summary, "to_dict") else {}
        graph = a.graph.to_dict()
        return {
            "version": __version__,
            "root": str(a.root),
            "elapsed_ms": round(a.elapsed_ms, 1),
            "scan": a.scan_result.summary(),
            "summary": data,
            "totals": a.insights.to_dict()["totals"],
            "index": a.index.stats() if a.index else None,
            "parse_failures": [{"file": p.file, "error": p.parse_error} for p in a.parse_failures],
            "cycles": len(graph["cycles"]),
            "layer_violations": graph["layer_violations"],
            "internal_edges": graph["internal_edges"],
            "modules": graph["modules"],
            "suggestions": self.suggestions(),
        }

    def search(self, q: str, top_k: int, language: str | None, kind: str | None, include_tests: bool) -> list[dict[str, Any]]:
        hits = self.analysis.search(q, top_k=top_k, language=language or None, kind=kind or None, include_tests=include_tests)
        out = []
        for hit in hits:
            row = hit.to_dict()
            source = self.files.get(row["file"])
            if source is not None:
                lines = source.text.splitlines()
                start = max(1, row["start_line"])
                end = min(row["end_line"], start + 24)
                row["snippet"] = {"start": start, "lines": lines[start - 1 : end], "truncated": row["end_line"] > end}
            out.append(row)
        return out

    def file(self, path: str) -> dict[str, Any]:
        source = self.files.get(path)
        if source is None:
            raise HTTPException(status_code=404, detail="not a file in this analysis")
        parsed = self.parsed.get(path)
        lines = source.text.splitlines()
        return {
            "path": path,
            "language": source.language,
            "lines": len(lines),
            "is_test": source.is_test,
            "truncated": len(lines) > MAX_FILE_LINES,
            "text": "\n".join(lines[:MAX_FILE_LINES]),
            "parser": parsed.parser if parsed else None,
            "module_docstring": parsed.module_docstring if parsed else "",
            "symbols": [
                {
                    "name": s.name,
                    "qualified_name": s.qualified_name,
                    "kind": s.kind,
                    "line": s.line,
                    "end_line": s.end_line,
                    "complexity": s.complexity,
                    "documented": bool(s.docstring),
                    "signature": s.signature,
                }
                for s in (parsed.symbols if parsed else [])
            ],
        }

    def files_list(self) -> list[dict[str, Any]]:
        rows = []
        for rel, source in sorted(self.files.items()):
            parsed = self.parsed.get(rel)
            symbols = parsed.symbols if parsed else []
            rows.append(
                {
                    "path": rel,
                    "language": source.language,
                    "lines": source.lines,
                    "is_test": source.is_test,
                    "symbols": len(symbols),
                    "max_complexity": max((s.complexity for s in symbols), default=0),
                }
            )
        return rows

    def architecture(self) -> dict[str, Any]:
        graph = self.analysis.graph
        summary = graph.to_dict()
        imports = {path: {d for d in node.imports if d in graph.nodes} for path, node in graph.nodes.items()}
        connected = sorted({s for s, _ in graph.edges} | {t for _, t in graph.edges})
        layers = _layers(connected, {n: imports[n] for n in connected})
        return {
            **summary,
            "cycles": [c.to_dict() for c in graph.find_cycles()],
            "nodes": [
                {
                    "path": path,
                    "package": node.package,
                    "lines": node.lines,
                    "symbols": node.symbols,
                    "fan_in": node.fan_in,
                    "fan_out": node.fan_out,
                    "instability": round(node.instability, 2),
                    "is_test": node.is_test,
                    "layer": layers.get(path),
                }
                for path, node in sorted(graph.nodes.items())
            ],
            "edges": [{"source": s, "target": t} for s, t in sorted(set(graph.edges))],
            "mermaid": self.analysis.mermaid(),
        }

    def insights(self) -> dict[str, Any]:
        data = self.analysis.insights.to_dict()
        complexities = [s.complexity for p in self.analysis.parsed for s in p.symbols]
        counts = Counter()
        for value in complexities:
            for low, high, label in COMPLEXITY_BUCKETS:
                if low <= value <= high:
                    counts[label] += 1
                    break
        data["complexity_histogram"] = [{"bucket": label, "symbols": counts.get(label, 0)} for _, _, label in COMPLEXITY_BUCKETS]
        data["duplicates"] = self.analysis.duplicates()[:20]
        return data


def create_app(root: Path | str = ".") -> FastAPI:
    explorer = Explorer(Path(root))
    app = FastAPI(title="repointel", version=__version__, description="Codebase search, architecture and risk.")
    app.state.explorer = explorer

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "root": str(explorer.analysis.root), "files": len(explorer.files)}

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        return explorer.overview()

    @app.post("/api/analyze")
    def reanalyze(body: AnalyzeRequest) -> dict[str, Any]:
        explorer.load(Path(body.path))
        return explorer.overview()

    @app.get("/api/search")
    def search(q: str, top_k: int = 12, language: str = "", kind: str = "", include_tests: bool = True) -> list[dict[str, Any]]:
        if not q.strip():
            return []
        return explorer.search(q, max(1, min(top_k, 50)), language, kind, include_tests)

    @app.get("/api/files")
    def files() -> list[dict[str, Any]]:
        return explorer.files_list()

    @app.get("/api/file")
    def file(path: str) -> dict[str, Any]:
        return explorer.file(path)

    @app.get("/api/architecture")
    def architecture() -> dict[str, Any]:
        return explorer.architecture()

    @app.get("/api/insights")
    def insights() -> dict[str, Any]:
        return explorer.insights()

    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse("/ui/")

    return app


app = create_app(os.environ.get("REPOINTEL_PATH", "."))
