"""Runnable tour. Analyses this repository, then a sibling one if present.

    python examples/quickstart.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repointel import analyze, split_identifier  # noqa: E402
from repointel.analyzer import AnalysisConfig  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> None:
    rule("1. Identifier splitting is what makes code search work")
    for identifier in (
        "validateAuthToken", "_check_bearer_cred", "HTTPSConnection", "parse2FA",
    ):
        print(f"  {identifier:<24} -> {split_identifier(identifier)}")

    rule("2. Scanning shows exactly what it kept and what it dropped")
    analysis = analyze(ROOT, AnalysisConfig())
    summary = analysis.scan_result.summary()
    print(f"  {summary['files']} files, {summary['total_lines']:,} lines")
    for language, count in list(summary["languages"].items())[:4]:
        print(f"    {language:<12} {count:>3} files")
    print("  skipped:")
    for reason, count in list(summary["skipped"].items())[:5]:
        print(f"    {reason:<44} {count}")

    rule("3. Natural-language search over this repository")
    for query in (
        "split an identifier into words",
        "detect an import cycle",
        "compute cyclomatic complexity",
    ):
        print(f"\n  Q: {query}")
        for hit in analysis.search(query, top_k=2):
            label = hit.chunk.symbol or hit.chunk.file
            print(f"     [{hit.score:.4f}] {label}")
            print(f"              {hit.chunk.citation}")

    rule("4. Jump to a definition, including methods inside a class")
    for name in ("find_cycles", "CodeIndex"):
        matches = analysis.index.find_symbol(name, limit=1)
        if matches:
            print(f"  {name:<16} -> {matches[0].citation}  ({matches[0].kind})")

    rule("5. Architecture: cycles, hubs and coupling")
    print(analysis.render_architecture())

    rule("6. Insights: where the risk actually is")
    print(analysis.render_insights())

    rule("7. Duplicate detection")
    duplicates = analysis.duplicates()
    if duplicates:
        for entry in duplicates[:3]:
            print(f"  x{entry['occurrences']}  {entry['preview'][:60]}")
            for location in entry["locations"][:3]:
                print(f"        {location['file']}:{location['line']}")
    else:
        print("  no cross-file duplicate blocks found")

    rule("8. A summary built only from measured facts")
    print(analysis.summary().render()[:1600])

    sibling = ROOT.parent / "promptguard"
    if sibling.exists():
        rule("9. Pointed at a different repository entirely")
        other = analyze(sibling, AnalysisConfig(build_search_index=False))
        totals = other.insights.totals()
        print(f"  {sibling.name}: {totals['files']} files, {totals['lines']:,} lines, "
              f"{totals['symbols']} symbols")
        print(f"  cycles: {len(other.graph.find_cycles())}, "
              f"doc coverage: {totals['doc_coverage']:.0%}")
        worst = other.insights.most_complex_symbols(1)
        if worst:
            print(f"  most complex: {worst[0].name} (complexity {worst[0].complexity}) "
                  f"at {worst[0].file}:{worst[0].line}")

    print(f"\nDone in {analysis.elapsed_ms:.0f} ms, with no dependencies installed.")


if __name__ == "__main__":
    main()
