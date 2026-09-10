"""Command line interface.

    repointel summary .                      # what is this codebase
    repointel search . "where do we validate tokens"
    repointel arch . --mermaid               # dependency graph and cycles
    repointel insights .                     # risk, complexity, gaps
    repointel symbol . AuditLog              # jump to a definition
    repointel duplicates .                   # copy-paste detection
    repointel check . --max-cycles 0         # CI gate

Exit codes: 0 fine, 1 a check failed, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys

from .analyzer import Analysis, AnalysisConfig, analyze
from .scanner import LANGUAGES
from .summarize import summarise_with_model

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _analysis(args: argparse.Namespace, with_index: bool = False) -> Analysis:
    config = AnalysisConfig(
        languages=args.languages.split(",") if getattr(args, "languages", None) else None,
        include_tests=not getattr(args, "no_tests", False),
        use_git_churn=not getattr(args, "no_git", False),
        build_search_index=with_index,
    )
    return analyze(args.path, config)


def cmd_summary(args: argparse.Namespace) -> int:
    analysis = _analysis(args)
    summary = analysis.summary()

    if args.model:
        summary = summarise_with_model(summary, model=args.model)

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
        return EXIT_OK

    print(summary.render())
    if analysis.parse_failures:
        print(f"\n_{len(analysis.parse_failures)} file(s) could not be parsed:_")
        for failure in analysis.parse_failures[:5]:
            print(f"- `{failure.file}`: {failure.parse_error}")
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    analysis = _analysis(args, with_index=True)
    hits = analysis.search(
        args.query,
        top_k=args.top_k,
        language=args.language,
        kind=args.kind,
        path_prefix=args.prefix,
        include_tests=not args.no_tests,
        rank_tests_equally=args.rank_tests_equally,
    )

    if args.json:
        print(json.dumps([h.to_dict(include_text=args.show_code) for h in hits], indent=2))
        return EXIT_OK

    if not hits:
        print("no matches")
        return EXIT_OK

    for rank, hit in enumerate(hits, start=1):
        marker = " [test]" if hit.chunk.is_test else ""
        label = hit.chunk.symbol or hit.chunk.file
        print(f"{rank}. [{hit.score:.4f}] {label}{marker}")
        print(f"   {hit.chunk.citation}  ({hit.chunk.kind})")
        if hit.chunk.signature:
            print(f"   {hit.chunk.signature[:110]}")
        if hit.matched_terms:
            print(f"   matched: {', '.join(hit.matched_terms[:8])}")
        if args.show_code:
            for line in hit.chunk.text.splitlines()[:8]:
                print(f"      {line}")
        print()
    return EXIT_OK


def cmd_arch(args: argparse.Namespace) -> int:
    analysis = _analysis(args)

    if args.mermaid:
        print(analysis.mermaid())
        return EXIT_OK
    if args.json:
        print(json.dumps(analysis.graph.to_dict(), indent=2))
        return EXIT_OK

    print(analysis.render_architecture())
    return EXIT_OK


def cmd_insights(args: argparse.Namespace) -> int:
    analysis = _analysis(args)
    if args.json:
        print(json.dumps(analysis.insights.to_dict(), indent=2))
        return EXIT_OK
    print(analysis.render_insights())
    return EXIT_OK


def cmd_symbol(args: argparse.Namespace) -> int:
    analysis = _analysis(args, with_index=True)
    assert analysis.index is not None
    matches = analysis.index.find_symbol(args.name, limit=args.top_k)

    if args.json:
        print(json.dumps([c.to_dict(include_text=args.show_code) for c in matches], indent=2))
        return EXIT_OK if matches else EXIT_FAILED

    if not matches:
        print(f"no symbol matching {args.name!r}")
        return EXIT_FAILED

    for chunk in matches:
        print(f"{chunk.symbol or chunk.file}  ({chunk.kind})")
        print(f"  {chunk.citation}")
        if chunk.signature:
            print(f"  {chunk.signature[:120]}")
        if chunk.docstring:
            print(f"  {chunk.docstring.splitlines()[0][:110]}")
        if args.show_code:
            for line in chunk.text.splitlines()[:15]:
                print(f"    {line}")
        print()
    return EXIT_OK


def cmd_similar(args: argparse.Namespace) -> int:
    """Find code similar to a named symbol. Useful for spotting duplication."""
    analysis = _analysis(args, with_index=True)
    assert analysis.index is not None
    matches = analysis.index.find_symbol(args.name, limit=1)
    if not matches:
        print(f"no symbol matching {args.name!r}", file=sys.stderr)
        return EXIT_FAILED

    source = matches[0]
    hits = analysis.index.similar_to(source.chunk_id, limit=args.top_k)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2))
        return EXIT_OK

    print(f"code similar to {source.symbol or source.file} ({source.citation}):\n")
    for hit in hits:
        print(f"  [{hit.score:.3f}] {hit.chunk.symbol or hit.chunk.file}")
        print(f"          {hit.chunk.citation}")
    return EXIT_OK


def cmd_duplicates(args: argparse.Namespace) -> int:
    analysis = _analysis(args)
    duplicates = analysis.duplicates()

    if args.json:
        print(json.dumps(duplicates, indent=2))
        return EXIT_OK

    if not duplicates:
        print("no cross-file duplicate blocks found")
        return EXIT_OK

    print(f"{len(duplicates)} duplicated block(s) across files:\n")
    for entry in duplicates[: args.top_k]:
        print(f"  x{entry['occurrences']}  {entry['lines']} lines  {entry['preview']}")
        for location in entry["locations"][:4]:
            print(f"        {location['file']}:{location['line']}")
        print()
    return EXIT_OK


def cmd_files(args: argparse.Namespace) -> int:
    analysis = _analysis(args)
    if args.json:
        print(json.dumps(analysis.scan_result.summary(), indent=2))
        return EXIT_OK

    summary = analysis.scan_result.summary()
    print(f"{summary['files']} files, {summary['total_lines']:,} lines")
    print("\nby language:")
    for language, count in summary["languages"].items():
        lines = summary["lines_by_language"][language]
        print(f"  {language:<14} {count:>4} files  {lines:>8,} lines")
    if summary["skipped"]:
        print("\nskipped:")
        for reason, count in summary["skipped"].items():
            print(f"  {reason:<40} {count}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    """CI gate over architecture and quality thresholds."""
    analysis = _analysis(args)
    cycles = analysis.graph.find_cycles()
    totals = analysis.insights.totals()
    failures: list[str] = []

    if args.max_cycles is not None and len(cycles) > args.max_cycles:
        failures.append(
            f"{len(cycles)} import cycle(s), limit is {args.max_cycles}: "
            + "; ".join(c.render() for c in cycles[:3])
        )
    if args.max_complexity is not None:
        worst = analysis.insights.most_complex_symbols(1)
        if worst and worst[0].complexity > args.max_complexity:
            failures.append(
                f"{worst[0].name} in {worst[0].file} has complexity "
                f"{worst[0].complexity}, limit is {args.max_complexity}"
            )
    if args.min_doc_coverage is not None and totals["doc_coverage"] < args.min_doc_coverage:
        failures.append(
            f"doc coverage {totals['doc_coverage']:.0%} is below "
            f"{args.min_doc_coverage:.0%}"
        )
    if args.max_parse_failures is not None:
        count = len(analysis.parse_failures)
        if count > args.max_parse_failures:
            failures.append(f"{count} file(s) failed to parse")

    payload = {
        "cycles": len(cycles),
        "max_complexity": totals["max_complexity"],
        "doc_coverage": totals["doc_coverage"],
        "parse_failures": len(analysis.parse_failures),
        "failures": failures,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"import cycles      : {len(cycles)}")
        print(f"max complexity     : {totals['max_complexity']}")
        print(f"doc coverage       : {totals['doc_coverage']:.0%}")
        print(f"parse failures     : {len(analysis.parse_failures)}")
        for failure in failures:
            print(f"\nFAIL: {failure}", file=sys.stderr)
        if not failures:
            print("\nall checks passed")

    return EXIT_FAILED if failures else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repointel",
        description="AI-assisted codebase analysis: search, architecture and insights.",
    )
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", nargs="?", default=".", help="repository root")
        p.add_argument("--languages", help="comma-separated filter, e.g. python,go")
        p.add_argument("--no-tests", action="store_true", help="exclude test files")
        p.add_argument("--no-git", action="store_true", help="skip git churn lookup")
        p.add_argument("--json", action="store_true")

    summary = sub.add_parser("summary", help="describe the codebase")
    add_common(summary)
    summary.add_argument("--model", help="Ollama model to write the prose overview")
    summary.set_defaults(func=cmd_summary)

    search = sub.add_parser("search", help="natural-language code search")
    search.add_argument("query")
    add_common(search)
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument("--language", choices=sorted(set(LANGUAGES.values())))
    search.add_argument("--kind", help="function, class, method, interface, ...")
    search.add_argument("--prefix", help="restrict to a path prefix")
    search.add_argument("--show-code", action="store_true")
    search.add_argument(
        "--rank-tests-equally", action="store_true",
        help="do not down-rank test code (it is down-ranked by default)",
    )
    search.set_defaults(func=cmd_search)

    arch = sub.add_parser("arch", help="dependency graph, cycles and coupling")
    add_common(arch)
    arch.add_argument("--mermaid", action="store_true", help="emit a Mermaid diagram")
    arch.set_defaults(func=cmd_arch)

    insights = sub.add_parser("insights", help="risk, complexity and gaps")
    add_common(insights)
    insights.set_defaults(func=cmd_insights)

    symbol = sub.add_parser("symbol", help="find a definition")
    symbol.add_argument("name")
    add_common(symbol)
    symbol.add_argument("--top-k", type=int, default=5)
    symbol.add_argument("--show-code", action="store_true")
    symbol.set_defaults(func=cmd_symbol)

    similar = sub.add_parser("similar", help="find code similar to a symbol")
    similar.add_argument("name")
    add_common(similar)
    similar.add_argument("--top-k", type=int, default=5)
    similar.set_defaults(func=cmd_similar)

    duplicates = sub.add_parser("duplicates", help="cross-file duplicate blocks")
    add_common(duplicates)
    duplicates.add_argument("--top-k", type=int, default=10)
    duplicates.set_defaults(func=cmd_duplicates)

    files = sub.add_parser("files", help="what was scanned, and what was skipped")
    add_common(files)
    files.set_defaults(func=cmd_files)

    check = sub.add_parser("check", help="CI gate on architecture and quality")
    add_common(check)
    check.add_argument("--max-cycles", type=int)
    check.add_argument("--max-complexity", type=int)
    check.add_argument("--min-doc-coverage", type=float)
    check.add_argument("--max-parse-failures", type=int)
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from . import __version__

        print(f"repointel {__version__}")
        return EXIT_OK

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
