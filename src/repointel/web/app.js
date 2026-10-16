/* Codebase Explorer: views over the repointel HTTP API. */
(function () {
  "use strict";
  const { h, s, api, fmt } = Kit;

  let overviewCache = null;
  const overview = async () => overviewCache || (overviewCache = await api.get("/api/overview"));
  const fileHref = (path, line) => "#/files?" + new URLSearchParams(line ? { path, line } : { path }).toString();
  const base = (p) => p.split("/").pop();

  function termSpans(text, terms) {
    if (!terms || !terms.length) return [];
    // Long terms match inside identifiers (split_identifier); short ones only as whole words.
    const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(terms.map((t) => (t.length <= 3 ? "\\b" + esc(t) + "\\b" : esc(t))).join("|"), "gi");
    const out = [];
    let m;
    while ((m = re.exec(text)) !== null) {
      out.push({ start: m.index, end: m.index + m[0].length, cls: "m-info" });
      if (!m[0].length) re.lastIndex++;
    }
    return out;
  }

  function codeBlock(lines, start, opts) {
    const o = opts || {};
    const box = h("div", { class: "code" });
    lines.forEach((text, i) => {
      const n = start + i;
      const hl = o.from !== undefined && n >= o.from && n <= (o.to || o.from);
      const row = h("div", { class: "ln" + (hl ? " hl" : ""), id: o.anchor ? "L" + n : null }, h("span", null, String(n)), h("span", null, o.terms ? Kit.highlight(text, termSpans(text, o.terms)) : text || " "));
      box.append(row);
    });
    return box;
  }

  // ------------------------------------------------------------------ overview
  async function overviewView(view) {
    const ov = await overview();
    const t = ov.totals;
    const sum = ov.summary;
    const pathInput = h("input", { class: "input", value: ov.root, "aria-label": "Repository path" });
    const analyzeBtn = h("button", { class: "btn btn-primary", type: "submit" }, "Analyze");
    const form = h("form", { class: "row", style: { flexWrap: "nowrap" } }, pathInput, analyzeBtn);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      Kit.withButton(analyzeBtn, async () => {
        analyzeBtn.textContent = "Analysing…";
        overviewCache = await api.post("/api/analyze", { path: pathInput.value });
        Kit.toast("Analysed " + overviewCache.scan.files + " files in " + fmt.ms(overviewCache.elapsed_ms));
        app.refresh();
      });
    });
    const langs = Object.entries(ov.scan.lines_by_language || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v, extra: [{ label: "Files", value: String(ov.scan.languages[k] || 0) }] }));
    view.append(
      Kit.pageHead(sum.name || base(ov.root), sum.overview || "", null),
      Kit.card({ body: h("div", { class: "stack", style: { gap: "8px" } }, h("span", { class: "small muted" }, "Local repository to analyse. Everything stays on this machine."), form) }),
      h(
        "div",
        { class: "grid cols-5 mt" },
        Kit.stat({ label: "Files", value: fmt.int(t.files), note: t.source_files + " source · " + t.test_files + " test" }),
        Kit.stat({ label: "Lines", value: fmt.compact(t.lines), note: "analysed in " + fmt.ms(ov.elapsed_ms) }),
        Kit.stat({ label: "Symbols", value: fmt.int(t.symbols), note: t.public_symbols + " public" }),
        Kit.stat({ label: "Doc coverage", value: fmt.pct(t.doc_coverage, 0), note: t.documented_public_symbols + " of " + t.public_symbols + " public symbols" }),
        Kit.stat({ label: "Complexity", value: fmt.num(t.mean_complexity, 1), note: "mean · max " + t.max_complexity }),
      ),
    );
    const keyModules = (sum.key_modules || []).map((m) => Object.assign({}, m));
    view.append(
      h(
        "div",
        { class: "split-r mt" },
        Kit.card({
          title: "Key modules",
          subtitle: "Ranked by how much of the codebase depends on them",
          flush: true,
          body: Kit.table({
            columns: [
              { key: "path", label: "Module", render: (m) => h("div", null, h("a", { href: fileHref(m.path) }, h("code", null, m.path)), h("div", { class: "small muted" }, m.purpose || "")) },
              { key: "key_symbols", label: "Defines", sortable: false, cls: "hide-sm", render: (m) => h("span", { class: "small" }, (m.key_symbols || []).slice(0, 4).join(", ")) },
              { key: "fan_in", label: "Used by", align: "right" },
              { key: "fan_out", label: "Uses", align: "right" },
            ],
            rows: keyModules,
          }),
        }),
        h(
          "div",
          { class: "stack" },
          Kit.chartCard({
            title: "Lines by language",
            chart: () => Kit.hbars(langs, { format: fmt.int, valueLabel: "Lines" }),
            table: () => ({ columns: [{ key: "label", label: "Language" }, { key: "value", label: "Lines", align: "right", format: fmt.int }], rows: langs }),
          }),
          Kit.card({
            title: "Layout and entry points",
            body: h(
              "div",
              { class: "stack", style: { gap: "10px" } },
              h("div", null, h("div", { class: "small muted" }, "Entry points"), h("div", { class: "row", style: { gap: "6px", marginTop: "4px" } }, (sum.entry_points || []).map((e) => h("code", { class: "tag" }, e)))),
              h("div", null, h("div", { class: "small muted" }, "Layout"), h("ul", { style: { margin: "4px 0 0", paddingLeft: "18px" } }, (sum.layout || []).map((l) => h("li", { class: "small" }, l.replace(/`/g, ""))))),
              h("div", { class: "row small ink-2", style: { gap: "4px 14px" } }, h("span", null, ov.modules + " modules"), h("span", null, ov.internal_edges + " internal imports"), h("span", null, ov.cycles + " cycles"), h("span", null, ov.layer_violations + " layer violations"), h("span", null, ov.parse_failures.length + " parse failures")),
            ),
          }),
        ),
      ),
    );
  }

  // ------------------------------------------------------------------ search
  async function searchView(view, params) {
    const ov = await overview();
    const suggestions = ov.suggestions || [];
    const q = h("input", { class: "input", type: "search", value: params.q || suggestions[0] || "", placeholder: "Describe what the code does", "aria-label": "Search query", style: { height: "40px", fontSize: "15px" } });
    const lang = Kit.select([{ value: "", label: "All languages" }].concat(Object.keys(ov.scan.languages).map((l) => ({ value: l, label: l }))), params.language || "", null, { style: { width: "160px" }, "aria-label": "Language" });
    const kind = Kit.select(["", "function", "method", "class", "block"].map((k) => ({ value: k, label: k ? fmt.title(k) : "All kinds" })), params.kind || "", null, { style: { width: "140px" }, "aria-label": "Kind" });
    const tests = h("input", { type: "checkbox", checked: params.tests !== "0" });
    const go = h("button", { class: "btn btn-primary", type: "submit", style: { height: "40px" } }, "Search");
    const results = h("div", null);
    const run = () =>
      Kit.withButton(go, async () => {
        const qs = new URLSearchParams({ q: q.value, language: lang.value, kind: kind.value, include_tests: String(tests.checked) });
        const hits = await Kit.busy(results, () => api.get("/api/search?" + qs.toString()));
        Kit.clear(results);
        results.append(
          Kit.card({
            title: hits.length + " results",
            subtitle: "BM25 over identifiers split into words, fused with vector similarity. Implementation ranks above tests.",
            flush: true,
            body: hits.length
              ? h(
                  "div",
                  null,
                  hits.map((hit, i) =>
                    h(
                      "div",
                      { class: "hit" },
                      h(
                        "div",
                        { class: "row-between" },
                        h("div", { class: "row", style: { gap: "8px" } }, h("span", { class: "muted small num" }, String(i + 1) + "."), h("a", { href: fileHref(hit.file, hit.start_line) }, h("code", null, hit.symbol || hit.citation)), Kit.badge(hit.kind, "", { icon: false }), hit.is_test ? Kit.badge("test", "", { icon: false }) : null),
                        h("span", { class: "small muted num" }, "score " + fmt.num(hit.score, 4)),
                      ),
                      h("div", { class: "row small muted", style: { gap: "4px 10px", marginTop: "4px" } }, h("span", null, hit.citation), hit.signature ? h("code", { class: "small" }, hit.signature) : null),
                      h("div", { class: "row", style: { gap: "4px", marginTop: "6px" } }, h("span", { class: "small muted" }, "matched"), hit.matched_terms.map((t) => h("span", { class: "tag" }, t)), h("span", { class: "small muted", style: { marginLeft: "8px" } }, "lexical " + fmt.num(hit.components.lexical, 2) + " · semantic " + fmt.num(hit.components.semantic, 3))),
                      hit.snippet ? codeBlock(hit.snippet.lines, hit.snippet.start, { terms: hit.matched_terms }) : null,
                    ),
                  ),
                )
              : Kit.empty("No results", "Try other words; identifiers are split, so 'auth token' matches validateAuthToken."),
          }),
        );
      });
    const form = h(
      "form",
      { class: "stack" },
      h("div", { class: "row", style: { flexWrap: "nowrap" } }, q, go),
      h("div", { class: "row", style: { gap: "10px 16px" } }, lang, kind, h("label", { class: "row small" }, tests, "Include tests"), suggestions.length ? h("span", { class: "chips", title: "Suggested from this codebase's docstrings" }, suggestions.map((x) => h("button", { class: "chip", type: "button", onclick: () => ((q.value = x), run()) }, x))) : null),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      run();
    });
    view.append(Kit.pageHead("Search", "Natural-language search over symbols, with the code that matched. Suggestions come from this codebase's own docstrings."), Kit.card({ body: form }), h("div", { class: "mt" }, results));
    if (q.value) run();
  }

  // ------------------------------------------------------------------ architecture
  function graphView(data) {
    const nodes = data.nodes.filter((n) => n.layer !== null && n.layer !== undefined);
    if (!nodes.length) return Kit.empty("No internal imports", "Modules here do not import each other.");
    const maxLayer = Math.max(...nodes.map((n) => n.layer));
    const cols = {};
    nodes.forEach((n) => (cols[n.layer] = cols[n.layer] || []).push(n));
    Object.values(cols).forEach((c) => c.sort((a, b) => b.fan_in - a.fan_in || a.path.localeCompare(b.path)));
    const rowH = 54;
    const boxH = 38;
    const rows = Math.max(...Object.values(cols).map((c) => c.length));
    const wrap = h("div", { class: "graph-wrap" });
    Kit.measure(wrap, (available) => draw(available));
    return wrap;

    function draw(available) {
    Kit.clear(wrap);
    // Fit the columns to the card; below a readable minimum the graph scrolls instead.
    const colW = Math.max(150, Math.min(220, Math.floor((available - 20) / (maxLayer + 1))));
    const boxW = colW - 36;
    const maxChars = Math.floor((boxW - 18) / 6.6);
    const W = (maxLayer + 1) * colW + 20;
    const H = rows * rowH + 20;
    const pos = {};
    Object.entries(cols).forEach(([layer, list]) => {
      const x = (maxLayer - Number(layer)) * colW + 10;
      const offset = ((rows - list.length) * rowH) / 2;
      list.forEach((n, i) => (pos[n.path] = { x, y: 10 + offset + i * rowH }));
    });
    const hubCut = [...nodes].sort((a, b) => b.fan_in - a.fan_in)[Math.min(2, nodes.length - 1)].fan_in;
    const svg = s("svg", { width: W, height: H, role: "img", "aria-label": "Module dependency graph. Importers on the left, the modules they depend on to the right." });
    const edgeEls = data.edges
      .filter((e) => pos[e.source] && pos[e.target])
      .map((e) => {
        const a = pos[e.source];
        const b = pos[e.target];
        const x1 = a.x + boxW;
        const y1 = a.y + boxH / 2;
        const x2 = b.x;
        const y2 = b.y + boxH / 2;
        const back = x2 <= x1;
        const d = back
          ? "M" + (a.x + boxW / 2) + "," + (a.y + boxH) + " C" + (a.x + boxW / 2) + "," + (a.y + boxH + 40) + " " + (b.x + boxW / 2) + "," + (b.y + boxH + 40) + " " + (b.x + boxW / 2) + "," + (b.y + boxH)
          : "M" + x1 + "," + y1 + " C" + (x1 + 40) + "," + y1 + " " + (x2 - 40) + "," + y2 + " " + x2 + "," + y2;
        const el = s("path", { class: "edge", d });
        el.dataset.source = e.source;
        el.dataset.target = e.target;
        svg.appendChild(el);
        return el;
      });
    const nodeEls = nodes.map((n) => {
      const p = pos[n.path];
      const g = s("g", { class: "node" + (n.fan_in >= hubCut && n.fan_in > 1 ? " hub" : ""), transform: "translate(" + p.x + "," + p.y + ")", tabindex: "0" });
      g.appendChild(s("rect", { width: boxW, height: boxH, rx: 7 }));
      const name = base(n.path);
      g.appendChild(s("text", { x: 10, y: 16 }, name.length > maxChars ? name.slice(0, maxChars - 1) + "…" : name));
      g.appendChild(s("text", { x: 10, y: 30, class: "sub" }, "used by " + n.fan_in + " · uses " + n.fan_out));
      const focus = (on) => {
        wrap.classList.toggle("focus", on);
        g.classList.toggle("on", on);
        edgeEls.forEach((el) => {
          el.classList.toggle("out", on && el.dataset.source === n.path);
          el.classList.toggle("in", on && el.dataset.target === n.path);
        });
        if (on) {
          nodeEls.forEach((other) => {
            const linked = edgeEls.some((el) => (el.dataset.source === n.path && el.dataset.target === other.path) || (el.dataset.target === n.path && el.dataset.source === other.path));
            other.el.classList.toggle("on", linked || other.path === n.path);
          });
        } else nodeEls.forEach((other) => other.el.classList.remove("on"));
      };
      g.addEventListener("pointerenter", (e) => {
        focus(true);
        Kit.tip.show(e.clientX, e.clientY, Kit.ttContent(n.path, [{ label: "Used by", value: String(n.fan_in), color: "var(--series-2)" }, { label: "Uses", value: String(n.fan_out), color: "var(--series-1)" }, { label: "Lines", value: fmt.int(n.lines) }, { label: "Instability", value: fmt.num(n.instability, 2) }]));
      });
      g.addEventListener("pointerleave", () => {
        focus(false);
        Kit.tip.hide();
      });
      g.addEventListener("focus", () => focus(true));
      g.addEventListener("blur", () => focus(false));
      g.addEventListener("click", () => app.go("files", { path: n.path }));
      svg.appendChild(g);
      return { path: n.path, el: g };
    });
    wrap.append(svg);
    }
  }

  async function architectureView(view) {
    const data = await api.get("/api/architecture");
    const copy = h("button", { class: "btn btn-sm", type: "button" }, "Copy Mermaid");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.mermaid);
        Kit.toast("Mermaid diagram copied");
      } catch (_) {
        Kit.download("architecture.mmd", data.mermaid, "text/plain");
      }
    });
    const ext = Object.entries(data.third_party_dependencies || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v }));
    const std = Object.entries(data.stdlib_dependencies || {}).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, v]) => ({ label: k, value: v }));
    view.append(
      Kit.pageHead("Architecture", "The module import graph: importers on the left, what they depend on to the right. Hover a module to trace its imports.", [copy]),
      h(
        "div",
        { class: "grid cols-4" },
        Kit.stat({ label: "Modules", value: fmt.int(data.modules), note: data.internal_edges + " internal imports" }),
        Kit.stat({ label: "Import cycles", value: String(data.cycles.length), note: data.cycles.length ? "modules that cannot be changed alone" : "the graph is acyclic" }),
        Kit.stat({ label: "Layer violations", value: String(data.layer_violations), note: "lower layers importing higher ones" }),
        Kit.stat({ label: "Third-party packages", value: String(ext.length), note: "imported by the code" }),
      ),
      data.cycles.length ? h("div", { class: "mt" }, Kit.banner({ kind: "warning", title: data.cycles.length + " import cycle" + (data.cycles.length > 1 ? "s" : ""), text: data.cycles.map((c) => c.modules.map(base).join(" → ")).join("; ") })) : null,
      Kit.card({ cls: "mt", title: "Dependency graph", subtitle: "Outlined modules are hubs: the most depended-upon, and the most expensive to change", actions: Kit.legend([{ label: "uses", color: "var(--series-1)", type: "line" }, { label: "used by", color: "var(--series-2)", type: "line" }]), body: graphView(data) }),
    );
    const hubs = data.nodes.filter((n) => !n.is_test && (n.fan_in || n.fan_out)).sort((a, b) => b.fan_in - a.fan_in);
    view.append(
      h(
        "div",
        { class: "split-r mt" },
        Kit.card({
          title: "Coupling",
          subtitle: "Instability is fan-out over total coupling: 0 is depended upon, 1 depends on others",
          flush: true,
          body: Kit.table({
            columns: [
              { key: "path", label: "Module", render: (n) => h("a", { href: fileHref(n.path) }, h("code", null, n.path)) },
              { key: "fan_in", label: "Used by", align: "right" },
              { key: "fan_out", label: "Uses", align: "right" },
              { key: "instability", label: "Instability", render: (n) => h("span", { class: "row", style: { flexWrap: "nowrap" } }, h("span", { style: { width: "64px" } }, Kit.meter(n.instability, "", "Instability")), h("span", { class: "num small" }, fmt.num(n.instability, 2))) },
              { key: "lines", label: "Lines", align: "right", cls: "hide-sm" },
            ],
            rows: hubs,
            maxHeight: 520,
          }),
        }),
        h(
          "div",
          { class: "stack" },
          Kit.chartCard({ title: "Third-party imports", chart: () => Kit.hbars(ext, { format: fmt.int, valueLabel: "Imports", emptyTitle: "No third-party imports", emptyText: "Standard library only." }), table: () => ({ columns: [{ key: "label", label: "Package" }, { key: "value", label: "Imports", align: "right" }], rows: ext }) }),
          Kit.chartCard({ title: "Most used standard library", chart: () => Kit.hbars(std, { format: fmt.int, valueLabel: "Imports" }), table: () => ({ columns: [{ key: "label", label: "Module" }, { key: "value", label: "Imports", align: "right" }], rows: std }) }),
        ),
      ),
    );
  }

  // ------------------------------------------------------------------ risk
  async function riskView(view) {
    const data = await api.get("/api/insights");
    const t = data.totals;
    const risky = data.riskiest_files;
    view.append(
      Kit.pageHead("Risk", t.churn_available ? "Risk is complexity weighted by how often a file changes, from git history." : "No git history was available, so risk is complexity alone, and says so."),
      h(
        "div",
        { class: "grid cols-4" },
        Kit.stat({ label: "Riskiest file", value: risky.length ? base(risky[0].path) : "—", note: risky.length ? "risk " + fmt.num(risky[0].risk, 0) + " · complexity " + risky[0].total_complexity : "" }),
        Kit.stat({ label: "Most complex symbol", value: data.most_complex_symbols.length ? data.most_complex_symbols[0].name : "—", note: data.most_complex_symbols.length ? "complexity " + data.most_complex_symbols[0].complexity : "" }),
        Kit.stat({ label: "Undocumented public API", value: fmt.int(t.public_symbols - t.documented_public_symbols), note: fmt.pct(t.doc_coverage, 0) + " documented" }),
        Kit.stat({ label: "Duplicate blocks", value: fmt.int(data.duplicates.length), note: "repeated across files" }),
      ),
    );
    const hist = data.complexity_histogram;
    view.append(
      h(
        "div",
        { class: "split-r mt" },
        Kit.card({
          title: "Riskiest files",
          subtitle: "Complex and frequently changed: where defects concentrate",
          flush: true,
          body: Kit.table({
            columns: [
              { key: "path", label: "File", render: (f) => h("a", { href: fileHref(f.path) }, h("code", null, f.path)) },
              { key: "risk", label: "Risk", render: (f) => h("span", { class: "row", style: { flexWrap: "nowrap" } }, h("span", { style: { width: "70px" } }, Kit.meter(f.risk / 100, f.risk > 75 ? "critical" : f.risk > 50 ? "warning" : "", "Risk")), h("span", { class: "num small" }, fmt.num(f.risk, 0))), sortValue: (f) => f.risk },
              { key: "total_complexity", label: "Complexity", align: "right" },
              { key: "commits", label: "Commits", align: "right", cls: "hide-sm" },
              { key: "doc_coverage", label: "Docs", align: "right", cls: "hide-sm", format: (v) => fmt.pct(v, 0) },
            ],
            rows: risky,
            sort: { key: "risk", dir: "desc" },
          }),
        }),
        Kit.chartCard({
          title: "Complexity distribution",
          subtitle: "Cyclomatic complexity per symbol",
          chart: () => Kit.columns({ labels: hist.map((b) => b.bucket), series: [{ name: "Symbols", color: "var(--series-1)", values: hist.map((b) => b.symbols) }], height: 220, tooltipTitle: (l) => "Complexity " + l }),
          table: () => ({ columns: [{ key: "bucket", label: "Complexity" }, { key: "symbols", label: "Symbols", align: "right" }], rows: hist }),
        }),
      ),
      h(
        "div",
        { class: "grid cols-2 mt" },
        Kit.card({
          title: "Most complex symbols",
          subtitle: "Candidates for splitting",
          flush: true,
          body: Kit.table({
            columns: [
              { key: "name", label: "Symbol", render: (x) => h("a", { href: fileHref(x.file, x.line) }, h("code", null, x.name)) },
              { key: "file", label: "Where", cls: "hide-sm", render: (x) => h("span", { class: "small muted" }, x.file + ":" + x.line) },
              { key: "complexity", label: "Complexity", align: "right" },
            ],
            rows: data.most_complex_symbols,
          }),
        }),
        Kit.card({
          title: "Undocumented public API",
          subtitle: "Most complex first",
          flush: true,
          body: Kit.table({
            columns: [
              { key: "name", label: "Symbol", render: (x) => h("a", { href: fileHref(x.file, x.line) }, h("code", null, x.name)) },
              { key: "kind", label: "Kind", cls: "hide-sm" },
              { key: "complexity", label: "Complexity", align: "right" },
            ],
            rows: data.undocumented_public_api,
            emptyTitle: "Everything public is documented",
          }),
        }),
      ),
    );
    if (data.duplicates.length) {
      view.append(
        Kit.card({
          cls: "mt",
          title: "Duplicate blocks",
          flush: true,
          body: Kit.table({ columns: [{ key: "lines", label: "Lines", align: "right" }, { key: "locations", label: "Found in", sortable: false, render: (d) => (d.locations || []).map((l) => h("div", null, h("code", null, typeof l === "string" ? l : (l.file || "") + ":" + (l.line || "")))) }], rows: data.duplicates }),
        }),
      );
    }
  }

  // ------------------------------------------------------------------ files
  async function filesView(view, params) {
    const files = await api.get("/api/files");
    const current = params.path || (files.find((f) => f.path.endsWith("search.py")) || files[0] || {}).path;
    const line = params.line ? Number(params.line) : undefined;
    const filter = h("input", { class: "input", type: "search", placeholder: "Filter files", "aria-label": "Filter files" });
    const list = h("div", { class: "list file-list" });
    const viewer = h("div", null);
    const drawList = () => {
      Kit.clear(list);
      const q = filter.value.toLowerCase();
      files
        .filter((f) => !q || f.path.toLowerCase().includes(q))
        .forEach((f) => {
          const item = h("a", { class: "list-item clickable" + (f.path === current ? " selected" : ""), href: fileHref(f.path), style: { color: "inherit", textDecoration: "none" } }, h("div", { style: { minWidth: "0", flex: "1" } }, h("div", { class: "truncate" }, h("code", null, f.path)), h("div", { class: "small muted" }, f.language + " · " + fmt.int(f.lines) + " lines" + (f.symbols ? " · " + f.symbols + " symbols" : "") + (f.is_test ? " · test" : ""))));
          list.append(item);
        });
    };
    filter.addEventListener("input", drawList);
    drawList();
    if (current) {
      const f = await api.get("/api/file?path=" + encodeURIComponent(current));
      const target = line ? f.symbols.find((sym) => sym.line === line) : null;
      const code = codeBlock(f.text.split("\n"), 1, { anchor: true, from: line, to: target ? target.end_line : line });
      viewer.append(
        Kit.card({
          title: f.path,
          subtitle: f.language + " · " + fmt.int(f.lines) + " lines · parsed with " + (f.parser || "patterns") + (f.truncated ? " · showing the first 4,000 lines" : ""),
          body: h("div", { class: "stack" }, f.module_docstring ? h("p", { class: "small ink-2" }, f.module_docstring.split("\n")[0]) : null, h("div", { style: { maxHeight: "680px", overflow: "auto" } }, code)),
        }),
      );
      const outline = Kit.card({
        title: "Outline",
        subtitle: f.symbols.length + " symbols",
        flush: true,
        body: h(
          "div",
          { class: "outline" },
          Kit.table({
            columns: [
              { key: "name", label: "Symbol", render: (sym) => h("a", { href: fileHref(f.path, sym.line) }, h("code", null, sym.name)) },
              { key: "kind", label: "Kind", cls: "hide-sm" },
              { key: "complexity", label: "Cx", align: "right" },
              { key: "documented", label: "Doc", align: "center", render: (sym) => (sym.documented ? "✓" : h("span", { class: "muted" }, "—")) },
            ],
            rows: f.symbols,
            sort: { key: "line", dir: "asc" },
            emptyTitle: "No symbols",
          }),
        ),
      });
      view.append(Kit.pageHead("Files", files.length + " files accepted by the scan. Symbols, complexity and documentation come from the same parse the analysis used."), h("div", { class: "grid", style: { gridTemplateColumns: "minmax(0, 3fr) minmax(0, 6fr) minmax(0, 3fr)", gap: "16px" }, id: "files-grid" }, Kit.card({ title: "Files", body: h("div", { class: "stack", style: { gap: "10px" } }, filter, h("div", { style: { margin: "0 -18px" } }, list)) }), viewer, outline));
      if (line) {
        requestAnimationFrame(() => {
          const el = document.getElementById("L" + line);
          if (el) el.scrollIntoView({ block: "center" });
        });
      }
    }
  }

  const app = Kit.createApp({
    title: "Codebase Explorer",
    health: "/health",
    healthLabel: (info) => info.files + " files · " + base(info.root),
    routes: [
      { path: "overview", label: "Overview", render: overviewView },
      { path: "search", label: "Search", render: searchView },
      { path: "architecture", label: "Architecture", render: architectureView },
      { path: "risk", label: "Risk", render: riskView },
      { path: "files", label: "Files", render: filesView },
    ],
  });
})();
