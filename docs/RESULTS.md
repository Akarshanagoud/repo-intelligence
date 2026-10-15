# Verified results

Output captured by actually running this repository. Nothing here is hand-written.

| | |
|---|---|
| Date | 2026-10-15 |
| Commit | `e86095d` |
| Python | Python 3.11.9 |
| OS | Windows 11 |

Timing figures (ms, rps) vary by machine and run. Counts and accuracy figures are deterministic.

## Reproduce

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]" pytest
pytest -q
python examples\quickstart.py
```

## Test suite

```text
........................................................................ [ 63%]
..........................................                               [100%]
114 passed in 0.62s
```

## `examples/quickstart.py`

```text

==============================================================================
1. Identifier splitting is what makes code search work
==============================================================================
  validateAuthToken        -> ['validate', 'auth', 'token']
  _check_bearer_cred       -> ['check', 'bearer', 'cred']
  HTTPSConnection          -> ['https', 'connection']
  parse2FA                 -> ['parse', '2', 'fa']

==============================================================================
2. Scanning shows exactly what it kept and what it dropped
==============================================================================
  18 files, 4,811 lines
    python        14 files
    markdown       2 files
    yaml           1 files
    toml           1 files
  skipped:
    unsupported extension                        2
    excluded directory: __pycache__              2
    excluded directory: .git                     1
    excluded directory: .pytest_cache            1
    excluded directory: .ruff_cache              1

==============================================================================
3. Natural-language search over this repository
==============================================================================

  Q: split an identifier into words
     [0.0328] src.repointel.search.split_identifier
              src/repointel/search.py:51-59
     [0.0308] .github/workflows/ci.yml
              .github/workflows/ci.yml:1-82

  Q: detect an import cycle
     [0.0323] src.repointel.architecture.Cycle
              src/repointel/architecture.py:114-125
     [0.0297] src.repointel.scanner.is_binary
              src/repointel/scanner.py:207-214

  Q: compute cyclomatic complexity
     [0.0323] src.repointel.parsing.cyclomatic_complexity
              src/repointel/parsing.py:130-147
     [0.0295] src.repointel.parsing.approximate_complexity
              src/repointel/parsing.py:391-399

==============================================================================
4. Jump to a definition, including methods inside a class
==============================================================================
  find_cycles      -> src/repointel/architecture.py:129-311  (class)
  CodeIndex        -> src/repointel/search.py:287-480  (class)

==============================================================================
5. Architecture: cycles, hubs and coupling
==============================================================================
modules            : 18
internal edges     : 38
import cycles      : 0
layer violations   : 0

most depended-upon modules (expensive to change):
  src/repointel/scanner.py                         fan-in   9 (53% of modules)
  src/repointel/parsing.py                         fan-in   8 (47% of modules)
  src/repointel/__init__.py                        fan-in   4 (24% of modules)
  src/repointel/summarize.py                       fan-in   4 (24% of modules)
  src/repointel/analyzer.py                        fan-in   3 (18% of modules)
  src/repointel/architecture.py                    fan-in   3 (18% of modules)
  src/repointel/insights.py                        fan-in   3 (18% of modules)
  src/repointel/search.py                          fan-in   3 (18% of modules)

most dependent modules (hard to understand in isolation):
  src/repointel/__init__.py                        fan-out   7  instability 0.64
  src/repointel/analyzer.py                        fan-out   6  instability 0.67
  src/repointel/summarize.py                       fan-out   4  instability 0.50
  src/repointel/cli.py                             fan-out   3  instability 0.75
  examples/quickstart.py                           fan-out   2  instability 1.00

third-party dependencies by import count:
  pytest                           3

most-used standard library modules: __future__ (10), pathlib (9), dataclasses (7), typing (7), ast (6), re (5), collections (4), sys (3)

==============================================================================
6. Insights: where the risk actually is
==============================================================================
files              : 18 (14 source, 4 test)
lines              : 4,811
symbols            : 170 (136 public)
doc coverage       : 38% of public symbols
complexity         : mean 4.45, median 2.0, max 36
risk               : complexity x log(commits) from git history

highest-risk files (complex and frequently changed):
  FILE                                                    RISK   CPLX  COMMITS   DOCS
  src/repointel/architecture.py                            100    144        1    54%
  src/repointel/search.py                                   94    136        1    53%
  src/repointel/parsing.py                                  75    108        1    32%
  src/repointel/insights.py                                 64     93        1    40%
  src/repointel/scanner.py                                  64     93        1    31%
  src/repointel/summarize.py                                51     73        1    55%
  src/repointel/cli.py                                      49     71        1    17%
  src/repointel/analyzer.py                                 15     22        1    15%

most complex functions (candidates for splitting):
  complexity  29  scan                               src/repointel/scanner.py:233
  complexity  26  parse_python                       src/repointel/parsing.py:187
  complexity  24  walk                               src/repointel/parsing.py:201
  complexity  17  chunk_file                         src/repointel/search.py:134
  complexity  17  search                             src/repointel/search.py:356
  complexity  15  resolve_import                     src/repointel/architecture.py:323

modules with no obvious test, by complexity:
  complexity   22  src/repointel/analyzer.py
  complexity   16  examples/quickstart.py
  complexity    0  docs/RESULTS.md

undocumented public API, most complex first:
  complexity  34  DependencyGraph                    src/repointel/architecture.py:129
  complexity  26  parse_python                       src/repointel/parsing.py:187
  complexity  24  walk                               src/repointel/parsing.py:201
  complexity  16  RepoInsights                       src/repointel/insights.py:147
  complexity  14  main                               examples/quickstart.py:21
  complexity  11  cmd_symbol                         src/repointel/cli.py:114

largest files (candidates for splitting):
    504 lines  src/repointel/architecture.py
    500 lines  src/repointel/search.py

==============================================================================
7. Duplicate detection
==============================================================================
  no cross-file duplicate blocks found

==============================================================================
8. A summary built only from measured facts
==============================================================================
# repo-intelligence

repo-intelligence is a python codebase of 4,811 lines across 18 files, defining 170 symbols (136 public). The module graph has 38 internal dependencies between 18 modules.

**Languages**: python (14 files), markdown (2 files), yaml (1 files), toml (1 files)

**Entry points**
- `examples/quickstart.py`
- `repointel/cli.py (console script)`
- `src/repointel/cli.py`
- `src/repointel/summarize.py`

**Layout**
- `src/repointel/` (9 files)
- `tests/` (4 files) - test suite
- `.github/` (1 files)
- `docs/` (1 files)
- `examples/` (1 files)

**Key modules**

### `src/repointel/scanner.py`
Repository scanning.
Defines `scan`, `GitignoreRules`, `ignored`, `ScanResult`, `is_binary`.
Imported by 9 module(s); imports 0.

### `src/repointel/parsing.py`
Symbol extraction.
Defines `parse_python`, `walk`, `parse_with_patterns`, `cyclomatic_complexity`, `approximate_complexity`.
Imported by 8 module(s); imports 1.

### `src/repointel/__init__.py`
repointel - AI-assisted codebase analysis: search, architecture and insights.
Imported by 4 module(s); imports 7.

### `src/repointel/analyzer.py`
The one object that ties scanning, parsing, indexing and analysis together.
Defines `Analysis`, `analyze`, `parse_failures`, `search`, `to_dict`.
Imported by 3 module(s); imports 6.

### `src/repointel/summarize.py`
Repository and module summarisation.
Defines `build_observations`, `summarise_module`, `RepoSummary`, `render`, `find_entry_points`.
Imported by 4 module(s); imports 4.

### `src/repointel/insights.py`
Developer insights: complexity, churn, documentation, and where risk si

==============================================================================
9. Pointed at a different repository entirely
==============================================================================
  promptguard: 36 files, 4,787 lines, 178 symbols
  cycles: 0, doc coverage: 47%
  most complex: _validate_subset (complexity 36) at src/promptguard/policy/schema.py:130

Done in 175 ms, with no dependencies installed.
```
