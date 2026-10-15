# Repo Intelligence

Codebase analysis for developers: natural-language code search, architecture
discovery, and a ranked list of where the risk actually is.

[![CI](https://github.com/Akarshanagoud/repo-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/Akarshanagoud/repo-intelligence/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

**Docs:** [Architecture and use cases](docs/ARCHITECTURE.md) · [Verified results](docs/RESULTS.md)

```bash
pip install -e ".[dev]"
repointel summary .
repointel search "where do we validate the auth token" .
repointel arch . --mermaid
```

Zero dependencies, no API keys, no model downloads, no network. Point it at any
repository and it works.

---

## Dogfooding

Every example below is real output from running this tool on a sibling
repository in the same portfolio:

```
$ repointel insights ../promptguard

files              : 30 (21 source, 9 test)
lines              : 4,176
symbols            : 178 (108 public)
doc coverage       : 47% of public symbols
complexity         : mean 3.44, median 2.0, max 36
risk               : complexity x log(commits) from git history

highest-risk files (complex and frequently changed):
  FILE                                                    RISK   CPLX  COMMITS   DOCS
  src/promptguard/audit.py                                  50     72        1   100%
  src/promptguard/policy/schema.py                          47     68        1   100%
  src/promptguard/cli.py                                    38     55        1     0%

most complex functions (candidates for splitting):
  complexity  36  _validate_subset          src/promptguard/policy/schema.py:130
  complexity  12  extract_json              src/promptguard/policy/schema.py:31
```

It also found a genuine copy-paste I had made across two modules:

```
$ repointel duplicates ../promptguard
  x2  6 lines  if not findings:
        src/promptguard/input_guard.py:158
        src/promptguard/policy/engine.py:143
```

That is the same noisy-or risk calculation, duplicated. The tool found a real
defect in real code, which is the only test of a code-analysis tool that counts.

---

## The four ideas that make it work

### 1. Identifiers are split before anything else

A developer asks *"where do we validate the auth token?"* and the answer is a
method called `validateAuthToken`. Ordinary tokenisation sees one meaningless
word; the query sees three meaningful ones, and they never meet.

```python
split_identifier("validateAuthToken")   # ['validate', 'auth', 'token']
split_identifier("_check_bearer_cred")  # ['check', 'bearer', 'cred']
split_identifier("HTTPSConnection")     # ['https', 'connection']
split_identifier("parse2FA")            # ['parse', '2', 'fa']
```

This single step does more for code-search quality than any embedding choice.
The whole identifier is kept alongside its parts, so an exact search still
ranks the exact match first.

### 2. Chunks follow symbols, and tests are ranked below implementation

Chunking on line windows cuts functions in half; a chunk spanning the end of
one function and the start of another answers the wrong question. Chunks here
start at `def`/`class` and end where the symbol ends.

Then there is a problem nobody mentions: **tests are lexically perfect answers**.
`test_injection_is_blocked_by_default` contains every term of "where do we
block injection", so unranked search returns the test suite and not the code it
exercises. Test chunks therefore carry a rank penalty by default:

```
$ repointel search "detect prompt injection attacks" ../promptguard
1. [0.0284] src.promptguard.detectors.injection.detect
   src/promptguard/detectors/injection.py:240-286  (function)
```

`--rank-tests-equally` turns it off; `--no-tests` excludes them entirely.

### 3. Exact parsing where possible, labelled where not

Python is parsed with `ast`: real qualified names, real decorators, real
nesting, real McCabe complexity counting the branch points that create paths
(including boolean operators and comprehension filters). Other languages use
structural patterns after comments are stripped, so a commented-out function
does not become a symbol.

The difference is **labelled on every symbol** — `parser: "ast"` or
`parser: "pattern"` — rather than presented as equivalent. A tool that silently
mixes exact and approximate extraction gives you a diagram where some edges are
facts and others are guesses, with no way to tell which.

Files that fail to parse are reported, not silently dropped: a missing module
in an architecture graph is worse than a flagged one.

### 4. Risk is complexity × churn, and says so when churn is unknown

Complexity alone is a poor priority signal. A gnarly parser nobody has touched
in three years is working fine; a moderately complex module six people changed
last month is where the next defect comes from.

Churn comes from `git log`. When git is unavailable, the score degrades to
complexity alone **and the report says so** — rather than quietly reporting a
different metric under the same name:

```
risk               : complexity only (no git history available, so churn is
                     unknown rather than zero)
```

---

## Architecture discovery

The output is not a picture, it is a list of claims you can check:

```
$ repointel arch .

modules            : 8
internal edges     : 14
import cycles      : 0
layer violations   : 0

most depended-upon modules (expensive to change):
  src/repointel/scanner.py       fan-in   5 (71% of modules)
  src/repointel/parsing.py       fan-in   4 (57% of modules)

third-party dependencies: none (standard library only)
most-used standard library modules: dataclasses (8), typing (8), re (5)
```

Cycles get the most attention because they have the clearest cost: modules in a
cycle cannot be tested in isolation, extracted, or reasoned about in either
direction — and each individual import looks fine in review. Detection is
Tarjan's algorithm, written iteratively because a deep import graph would
otherwise blow the Python stack, and crashing on a large repository is not an
acceptable failure mode for an analysis tool.

`--mermaid` emits a package-level diagram. Package level rather than module
level on purpose: a module-level diagram of a real repository is an unreadable
hairball.

---

## Commands

| Command | Purpose |
|---|---|
| `summary` | What is this codebase, from measured facts |
| `search` | Natural-language code search |
| `symbol` | Jump to a definition, including methods |
| `similar` | Find code similar to a symbol |
| `arch` | Dependency graph, cycles, coupling, Mermaid |
| `insights` | Risk, complexity, doc gaps, untested modules |
| `duplicates` | Cross-file copy-paste detection |
| `files` | What was scanned, and exactly what was skipped |
| `check` | CI gate on cycles, complexity, docs, parse failures |

```bash
repointel check . --max-cycles 0 --max-complexity 25 --min-doc-coverage 0.4
```

Exit codes: `0` fine, `1` a check failed, `2` usage.

```python
from repointel import analyze

analysis = analyze("../some-repo")
analysis.search("how do we handle retries")   # ranked code chunks
analysis.graph.find_cycles()                  # import cycles
analysis.insights.riskiest()                  # complexity x churn
analysis.summary().render()                   # markdown overview
```

---

## Summaries state only what was measured

The default summariser is extractive and template-driven. It quotes docstrings
that already exist and reports counts — so it **cannot invent a module's
purpose**, which is the failure mode that makes generated codebase docs
untrustworthy: a confident paragraph about behaviour written by something that
only saw the imports.

```markdown
### `src/promptguard/types.py`
Core value types shared across input guards, output policies and audit.
Defines `Severity`, `GuardResult`, `Finding`, `to_dict`, `max_severity`.
Imported by 20 module(s); imports 0.
```

`--model llama3.2` lets a local Ollama model write the prose instead. It
receives only the *measured facts*, never the source, so there is nothing for it
to invent about behaviour it has not seen — and the output is labelled
`source: model` so a reader knows which they are reading.

---

## Scanning is opinionated, and shows its work

A tool that indexes `node_modules` and minified bundles produces search results
nobody wants. Exclusions: `.gitignore` (including nested files, with negation),
known vendor and build directories even when committed, binaries detected by a
NUL byte rather than by extension, and minified or generated files detected by
*shape* — mean line length over 400 characters, or a generation marker in the
header.

Every exclusion is counted, so "why is my file missing?" has an answer:

```
$ repointel files .
skipped:
  excluded directory: __pycache__              5
  unsupported extension                        4
  generated or minified content                1
  binary content                               1
```

---

## Project layout

```
src/repointel/
  scanner.py       Repository walking, gitignore, exclusion accounting
  parsing.py       AST symbols for Python, structural patterns elsewhere
  search.py        Identifier splitting, symbol chunking, hybrid search
  architecture.py  Dependency graph, cycles, layering, coupling, Mermaid
  insights.py      Complexity, git churn, risk, duplication, doc gaps
  summarize.py     Extractive summaries, optional local-model prose
  analyzer.py      The assembled pipeline
  cli.py           summary / search / arch / insights / check / ...
tests/             114 tests against a synthetic repo with planted defects
```

## Testing

The test fixture is a synthetic repository with **deliberately planted
properties**, so tests assert exact facts rather than "looks about right": a
known two-module import cycle, a function with known-high complexity, a
copy-pasted block across two files, a file that does not parse, a gitignored
directory, a minified bundle, a binary file with a `.py` extension, and a
generated file whose name looks perfectly normal.

```bash
pytest                                   # 114 tests, under a second
repointel check . --max-cycles 0         # the tool passes its own gate
ruff check src tests
```

CI runs the suite on 3.10–3.12 and then points the tool at itself: zero import
cycles, zero parse failures, and a working summary, search and architecture
report.

## Limitations

- Only Python gets exact parsing. TypeScript, JavaScript, Go, Java, Rust and
  Ruby use structural patterns — good enough for symbol inventory and imports,
  not for call graphs or type resolution. Every symbol says which it got.
- Import resolution is path-based. Dynamic imports, aliased module systems and
  build-tool path mapping (`tsconfig` paths, webpack aliases) are not resolved
  and land in the third-party bucket.
- Embeddings are lexical, not semantic. Identifier splitting closes much of the
  gap for code specifically, but a query using entirely different vocabulary
  from the code will still miss.
- The duplicate detector finds exact repeated blocks after normalisation. It
  will not find a function that was copied and meaningfully refactored.
- `.gitignore` support covers the common subset (globs, negation, directory-only,
  anchoring), not the full specification.

## License

MIT
