// NexusAI Apple-like — Portfolio, Watchlist, Advisor sections
(() => {
  const D = window.NEXUS_DATA;

  // ============================================================
  // PORTFOLIO
  // ============================================================
  // Holdings table state
  let _plHorizon = "ALL";              // ALL | 1Y | 6M | 3M | 1M
  let _sortKey = "value";              // ticker|account|shares|avg_cost|price|value|weight|pl
  let _sortDir = "desc";               // asc | desc
  const HORIZONS = ["ALL", "1Y", "6M", "3M", "1M"];

  // Per-position P/L for the active horizon (falls back to ALL if unavailable)
  function plFor(p) {
    const per = p.periods && p.periods[_plHorizon];
    if (per) return { pl: per.pl, pct: per.pct };
    return { pl: p.pl, pct: p.plPct };
  }

  function sortedPositions() {
    const arr = [...D.positions];
    const dir = _sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av, bv;
      if (_sortKey === "pl") { av = plFor(a).pct; bv = plFor(b).pct; }
      else if (_sortKey === "ticker") {
        av = (a.ticker || "").toLowerCase(); bv = (b.ticker || "").toLowerCase();
        return av < bv ? -dir : av > bv ? dir : 0;
      }
      else if (_sortKey === "account") {
        av = _accountName(a).toLowerCase(); bv = _accountName(b).toLowerCase();
        return av < bv ? -dir : av > bv ? dir : 0;
      }
      else { av = a[_sortKey]; bv = b[_sortKey]; }
      return (av - bv) * dir;
    });
    return arr;
  }

  function sortArrow(key) {
    if (_sortKey !== key) return `<span class="sort-ar" style="opacity:.25;">↕</span>`;
    return `<span class="sort-ar">${_sortDir === "asc" ? "↑" : "↓"}</span>`;
  }

  function _accountName(p) {
    const a = (D.accounts || []).find(a => a.id === p.account_id);
    return a ? a.name : "—";
  }

  function holdingsBody() {
    return sortedPositions().map(p => {
      const pv = plFor(p);
      return `
        <tr data-row-ticker="${p.ticker}">
          <td>
            <div class="t-ticker">
              <div>
                <div>${p.ticker}</div>
                <div class="muted" style="font-size:11px; font-weight:400;">${p.sector}</div>
              </div>
            </div>
          </td>
          <td><span class="muted">${_accountName(p)}</span></td>
          <td class="t-right">${fmtNum(p.shares, 4)}</td>
          <td class="t-right">${fmt$(p.avg_cost)}</td>
          <td class="t-right h-strong">${fmt$(p.price)}</td>
          <td class="t-right h-strong">${fmt$(p.value)}</td>
          <td class="t-right">${p.weight.toFixed(2)}%</td>
          <td class="t-right ${pv.pl >= 0 ? 'pl-pos' : 'pl-neg'}">
            <div style="font-weight:600;">${pv.pl >= 0 ? '▲' : '▼'} ${fmtPct(pv.pct)}</div>
            <div style="font-size:11px;">${fmt$(pv.pl, { signed: true, compact: true })}</div>
          </td>
          <td class="t-right">${renderSparkline((p.spark && p.spark.length >= 2) ? p.spark : syntheticSpark(pv.pct), { w: 64, h: 18 })}</td>
        </tr>`;
    }).join("");
  }

  function _accountOptions(selectedId) {
    if (!D.accounts.length) return `<option value="">No accounts yet — add one first</option>`;
    return D.accounts.map(a => `<option value="${a.id}" ${a.id === selectedId ? "selected" : ""}>${a.name}</option>`).join("");
  }

  function _holdEditRow(p) {
    const id = p.id != null ? p.id : "";
    return `
      <div class="hold-edit-row" data-id="${id}" style="display:grid; grid-template-columns:1fr 1.3fr 0.8fr 0.8fr auto; gap:8px; align-items:center; padding:4px 0;">
        <input class="aaf-input he-ticker" value="${p.ticker || ""}" placeholder="Ticker" style="text-transform:uppercase;"/>
        <select class="aaf-input he-account">${_accountOptions(p.account_id)}</select>
        <input class="aaf-input he-shares" type="number" step="any" value="${p.shares != null ? p.shares : ""}" placeholder="Shares"/>
        <input class="aaf-input he-cost" type="number" step="any" value="${p.avg_cost != null ? p.avg_cost : ""}" placeholder="Avg cost"/>
        <button class="acct-icon-btn danger he-del" title="Remove">${Icon("trash", 13)}</button>
      </div>`;
  }

  function holdingsEditorHTML() {
    const rows = D.positions.map(_holdEditRow).join("");
    return `
      <div style="padding:12px; background:var(--surface-2); border-radius:var(--r-md); margin-bottom:12px;">
        <div style="display:grid; grid-template-columns:1fr 1.3fr 0.8fr 0.8fr auto; gap:8px; font-size:11px; color:var(--text-2); font-weight:600; text-transform:uppercase; letter-spacing:.05em; padding-bottom:6px;">
          <span>Ticker</span><span>Account</span><span>Shares</span><span>Avg cost</span><span></span>
        </div>
        <div id="he-rows">${rows}</div>
        <div style="margin-top:8px;">
          <button class="btn-ghost" id="he-add">${Icon("plus", 12)} Add holding</button>
        </div>
        <div class="aaf-actions" style="margin-top:10px;">
          <button class="btn-ghost" id="he-cancel">Cancel</button>
          <button class="btn-primary" id="he-save">${Icon("check", 12)} Save holdings</button>
        </div>
        <div id="he-status" class="muted" style="font-size:12px; margin-top:6px;"></div>
      </div>`;
  }

  function renderPortfolio() {
    const totalValue = D.positions.reduce((s, p) => s + p.value, 0);
    const TH = (key, label, right) => `
      <th class="${right ? 't-right' : ''} th-sort" data-sort="${key}" style="cursor:pointer; user-select:none;">
        ${label} ${sortArrow(key)}
      </th>`;
    return `
      <div class="grid g-3 mb-m">
        <div class="stat-tile">
          <div class="label">Total value</div>
          <div class="v">${fmt$(totalValue, { dec: 2 })}</div>
          <div class="e">${D.positions.length} positions</div>
        </div>
        <div class="stat-tile">
          <div class="label">Total cost basis</div>
          <div class="v">${fmt$(D.totalCost, { dec: 2 })}</div>
          <div class="e">Across ${new Set(D.positions.map(p => p.account_id)).size} accounts</div>
        </div>
        <div class="stat-tile">
          <div class="label">Unrealized P/L</div>
          <div class="v ${D.totalPL >= 0 ? 'pl-pos' : 'pl-neg'}">${fmt$(D.totalPL, { signed: true })}</div>
          <div class="e ${D.totalPL >= 0 ? 'pl-pos' : 'pl-neg'}">${fmtPct(D.totalPLPct)} · all-time</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>Holdings</h3>
          <div class="flex gap-s" style="align-items:center;">
            <span class="muted" style="font-size:11px;">P/L horizon</span>
            <div class="range-pills" id="pl-horizon-pills">
              ${HORIZONS.map(h => `<button data-hz="${h}" class="${h === _plHorizon ? 'active' : ''}">${h}</button>`).join("")}
            </div>
            <button class="btn-ghost" id="hold-import-btn" title="CSV columns: ticker, shares, avg_cost, account_name">${Icon("plus", 12)} Import CSV</button>
            <button class="btn-primary" id="hold-edit-btn">${Icon("edit", 12)} Edit holdings</button>
            <input type="file" id="hold-csv-file" accept=".csv" hidden/>
          </div>
        </div>
        <div class="muted" style="font-size:11px; padding:0 16px 8px;">CSV needs columns: ticker, shares, avg_cost, account_name (unmatched account names are created automatically).</div>
        <div id="hold-editor" hidden></div>
        <div style="overflow-x:auto;">
          <table class="tbl">
            <thead>
              <tr>
                ${TH("ticker", "Ticker", false)}
                ${TH("account", "Account", false)}
                ${TH("shares", "Shares", true)}
                ${TH("avg_cost", "Avg cost", true)}
                ${TH("price", "Price", true)}
                ${TH("value", "Value", true)}
                ${TH("weight", "Weight", true)}
                ${TH("pl", "P/L", true)}
                <th class="t-right">Trend</th>
              </tr>
            </thead>
            <tbody id="holdings-body">
              ${holdingsBody()}
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid g-2 mt-m">
        <div class="card">
          <div class="card-head"><h3>Risk metrics</h3><div class="meta">Trailing 1Y · daily returns</div></div>
          <div class="card-body">
            ${riskCardBody()}
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h3>Concentration</h3><div class="meta">Position weight</div></div>
          <div class="card-body">
            ${D.positions.slice(0, 6).map(p => `
              <div class="bar-row">
                <span>${p.ticker}</span>
                <div class="bar-track"><div class="bar-fill" style="width:0%;" data-w="${p.weight}"></div></div>
                <span class="t-right h-strong">${p.weight.toFixed(2)}%</span>
              </div>
            `).join("")}
            <div class="risk-item mt-m">${Icon("alert", 14)}<span>Top 6 positions = ${D.positions.slice(0,6).reduce((s,p)=>s+p.weight,0).toFixed(1)}% of portfolio. Consider trimming any &gt;7%.</span></div>
          </div>
        </div>
      </div>
    `;
  }

  function riskCardBody() {
    const r = D.riskMetrics || {};
    const has = r.sharpe != null || r.sortino != null || r.maxDrawdown != null;
    const fmtN = (v) => (v == null ? "—" : v.toFixed(2));
    const sharpeE = r.benchmarkSharpe != null ? `vs SPY ${r.benchmarkSharpe.toFixed(2)}` : "annualized";
    const cov = r.coverage != null && r.coverage < 95 ? ` · ${r.coverage}% priced` : "";
    if (!has) {
      return `
        <div class="grid g-3" style="gap:10px;">
          ${[0,1,2].map(() => `<div class="stat-tile"><div class="skeleton skeleton-row" style="width:60%;"></div><div class="skeleton skeleton-row" style="width:40%; height:20px;"></div></div>`).join("")}
        </div>
        <div class="divider"></div>
        <div class="skeleton skeleton-row" style="width:90%;"></div>
        <div class="skeleton skeleton-row" style="width:70%;"></div>`;
    }
    const shClass = r.sharpe != null && r.sharpe >= 1 ? "pl-pos" : "";
    return `
      <div class="grid g-3" style="gap:10px;">
        <div class="stat-tile"><div class="label">Portfolio Sharpe</div><div class="v ${shClass}">${fmtN(r.sharpe)}</div><div class="e">${sharpeE}</div></div>
        <div class="stat-tile"><div class="label">Max drawdown</div><div class="v">${r.maxDrawdown == null ? "—" : r.maxDrawdown.toFixed(1) + "%"}</div><div class="e">trailing 1Y</div></div>
        <div class="stat-tile"><div class="label">Sortino</div><div class="v">${fmtN(r.sortino)}</div><div class="e">downside-adjusted</div></div>
      </div>
      <div class="divider"></div>
      <div style="font-size:13px; color:var(--text-2); line-height:1.55;">
        ${riskNarrative(r)}${cov}
      </div>`;
  }

  function riskNarrative(r) {
    if (r.sharpe == null) return "Insufficient price history to assess risk-adjusted returns.";
    const beat = r.benchmarkSharpe != null && r.sharpe > r.benchmarkSharpe;
    const lead = beat
      ? "Risk-adjusted returns outpace the S&P 500"
      : "Risk-adjusted returns trail the S&P 500";
    const dd = r.maxDrawdown != null ? ` Worst peak-to-trough drop this year was ${Math.abs(r.maxDrawdown).toFixed(1)}%.` : "";
    return `${lead} (Sharpe ${r.sharpe.toFixed(2)} vs ${r.benchmarkSharpe != null ? r.benchmarkSharpe.toFixed(2) : "—"}).${dd} See the Advisor tab for concentration trims.`;
  }

  function syntheticSpark(plPct) {
    const out = [];
    let v = 100;
    for (let i = 0; i < 18; i++) {
      v += (plPct / 18) + (Math.sin(i * 0.6) * 1.5);
      out.push(v);
    }
    return out;
  }

  function _reloadPortfolioSection() {
    fetch("/api/snapshot").then(r => r.json()).then(s => {
      if (!s.ok) return;
      D.positions = s.positions; D.portfolioValue = s.portfolioValue; D.accounts = s.accounts;
      D.totalCost = s.totalCost; D.totalPL = s.totalPL; D.totalPLPct = s.totalPLPct;
      D.sectorWeights = s.sectorWeights; if (s.advisorPlan) D.advisorPlan = s.advisorPlan;
      if (s.riskMetrics) D.riskMetrics = s.riskMetrics;
      const el = document.getElementById("section-portfolio");
      if (el) { el.innerHTML = renderPortfolio(); hydratePortfolio(); }
      if (window.AppleSections && window.AppleSections.renderOverview) {
        const ow = document.getElementById("section-overview");
        if (ow) ow.innerHTML = AppleSections.renderOverview();
        if (window.Nexus && Nexus.getActive() === "overview" && AppleSections.hydrateOverview) AppleSections.hydrateOverview();
      }
    }).catch(() => {});
  }

  function wireHoldingsEditor() {
    const editBtn = document.getElementById("hold-edit-btn");
    const editor = document.getElementById("hold-editor");
    const importBtn = document.getElementById("hold-import-btn");
    const fileInput = document.getElementById("hold-csv-file");
    if (!editBtn || editBtn.dataset.bound) return;
    editBtn.dataset.bound = "1";

    editBtn.addEventListener("click", () => {
      if (editor.hidden) {
        editor.innerHTML = holdingsEditorHTML();
        editor.hidden = false;
        wireEditorRows();
      } else {
        editor.hidden = true; editor.innerHTML = "";
      }
    });

    function wireEditorRows() {
      editor.querySelector("#he-cancel").addEventListener("click", () => {
        editor.hidden = true; editor.innerHTML = "";
      });
      editor.querySelector("#he-add").addEventListener("click", () => {
        const rows = editor.querySelector("#he-rows");
        const div = document.createElement("div");
        div.className = "hold-edit-row";
        div.dataset.id = "";
        div.style.cssText = "display:grid; grid-template-columns:1fr 1.3fr 0.8fr 0.8fr auto; gap:8px; align-items:center; padding:4px 0;";
        div.innerHTML = `
          <input class="aaf-input he-ticker" placeholder="Ticker" style="text-transform:uppercase;"/>
          <select class="aaf-input he-account">${_accountOptions()}</select>
          <input class="aaf-input he-shares" type="number" step="any" placeholder="Shares"/>
          <input class="aaf-input he-cost" type="number" step="any" placeholder="Avg cost"/>
          <button class="acct-icon-btn danger he-del" title="Remove">${Icon("trash", 13)}</button>`;
        rows.appendChild(div);
        div.querySelector(".he-del").addEventListener("click", () => _removeHoldingRow(div));
        div.querySelector(".he-ticker").focus();
      });
      editor.querySelectorAll(".he-del").forEach(btn => {
        btn.addEventListener("click", () => _removeHoldingRow(btn.closest(".hold-edit-row")));
      });
      // Existing holdings are deleted immediately (no separate "removed"
      // staging state); new unsaved rows just disappear from the form.
      function _removeHoldingRow(row) {
        const id = row.dataset.id;
        if (id) {
          fetch(`/api/holdings/${id}`, { method: "DELETE" }).catch(() => {});
        }
        row.remove();
      }
      editor.querySelector("#he-save").addEventListener("click", () => {
        const rows = [...editor.querySelectorAll(".hold-edit-row")];
        const jobs = [];
        rows.forEach(row => {
          const ticker = (row.querySelector(".he-ticker").value || "").trim().toUpperCase();
          const account_id = parseInt(row.querySelector(".he-account").value, 10);
          const shares = parseFloat(row.querySelector(".he-shares").value);
          const avg_cost = parseFloat(row.querySelector(".he-cost").value);
          if (!ticker || !(shares > 0) || isNaN(account_id)) return;
          const id = row.dataset.id ? parseInt(row.dataset.id, 10) : undefined;
          jobs.push(fetch("/api/holdings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, ticker, shares, avg_cost: isNaN(avg_cost) ? 0 : avg_cost, account_id }),
          }).then(r => r.json()));
        });
        const status = editor.querySelector("#he-status");
        status.textContent = "Saving…";
        Promise.all(jobs).then(results => {
          const failed = results.filter(r => !r.ok);
          if (failed.length === 0) {
            if (window.NexusToast) NexusToast(`Saved ${results.length} holdings — refreshing prices`, "ok");
            editor.hidden = true; editor.innerHTML = "";
            setTimeout(_reloadPortfolioSection, 2500);
          } else {
            status.textContent = `${failed.length} row(s) failed to save.`;
            if (window.NexusToast) NexusToast("Some holdings failed to save", "err");
          }
        }).catch(() => { status.textContent = "Save failed."; if (window.NexusToast) NexusToast("Save failed", "err"); });
      });
    }

    importBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      importBtn.disabled = true;
      const orig = importBtn.innerHTML;
      importBtn.innerHTML = "Importing…";
      fetch("/api/portfolio/import", { method: "POST", body: fd })
        .then(r => r.json()).then(res => {
          if (res.ok) {
            importBtn.innerHTML = orig;
            if (window.NexusToast) NexusToast(`Imported ${res.count} holdings${res.dropped ? ` (${res.dropped} skipped)` : ""}`, "ok");
            setTimeout(_reloadPortfolioSection, 2500);
          } else {
            if (window.NexusToast) NexusToast(res.error || "Import failed", "err");
            importBtn.innerHTML = orig;
          }
        }).catch(() => { importBtn.innerHTML = orig; })
        .finally(() => { importBtn.disabled = false; fileInput.value = ""; });
    });
  }

  function hydratePortfolio() {
    requestAnimationFrame(() => {
      document.querySelectorAll("[data-w]").forEach(el => { el.style.width = el.dataset.w + "%"; });
    });

    wireHoldingsEditor();

    const body = document.getElementById("holdings-body");

    // P/L horizon pills
    const pills = document.getElementById("pl-horizon-pills");
    if (pills && !pills.dataset.bound) {
      pills.dataset.bound = "1";
      pills.addEventListener("click", (e) => {
        const b = e.target.closest("[data-hz]");
        if (!b) return;
        _plHorizon = b.dataset.hz;
        pills.querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.hz === _plHorizon));
        if (body) body.innerHTML = holdingsBody();
      });
    }

    // Sortable column headers
    document.querySelectorAll(".th-sort").forEach(th => {
      if (th.dataset.bound) return;
      th.dataset.bound = "1";
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (_sortKey === key) {
          _sortDir = _sortDir === "asc" ? "desc" : "asc";
        } else {
          _sortKey = key;
          _sortDir = (key === "ticker" || key === "account") ? "asc" : "desc";
        }
        // Re-render whole section so header arrows update too
        const el = document.getElementById("section-portfolio");
        if (el) { el.innerHTML = renderPortfolio(); hydratePortfolio(); }
      });
    });
  }

  // ============================================================
  // WATCHLIST
  // ============================================================
  function renderWatchlist() {
    const wl = D.watchlist || [];
    const alerts = wl.filter(w => (w.buyBelow && w.price && w.price <= w.buyBelow) || (w.sellAbove && w.price && w.price >= w.sellAbove));
    const withTargets = wl.filter(w => w.buyBelow || w.sellAbove).length;
    const alertE = alerts.length ? alerts.map(a => a.ticker).slice(0, 3).join(", ") : "none triggered";
    return `
      <div class="grid g-3 mb-m">
        <div class="stat-tile"><div class="label">Watching</div><div class="v">${wl.length}</div><div class="e">tickers</div></div>
        <div class="stat-tile"><div class="label">Alerts triggered</div><div class="v" style="color:${alerts.length ? 'var(--green)' : 'var(--text)'};">${alerts.length}</div><div class="e">${alertE}</div></div>
        <div class="stat-tile"><div class="label">With price targets</div><div class="v">${withTargets}</div><div class="e">of ${wl.length} tracked</div></div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>Watchlist</h3>
          <button class="btn-primary" id="wl-add-btn">${Icon("plus", 12)} Add ticker</button>
        </div>
        <div id="wl-add-form" hidden style="padding:0 0 12px;">
          <div class="aaf-grid" style="grid-template-columns:repeat(4,1fr); gap:8px;">
            <input class="aaf-input" id="wlf-ticker" placeholder="Ticker (e.g. TSM)" style="text-transform:uppercase;"/>
            <input class="aaf-input" id="wlf-buy" type="number" step="0.01" placeholder="Buy below $"/>
            <input class="aaf-input" id="wlf-sell" type="number" step="0.01" placeholder="Sell above $"/>
            <input class="aaf-input" id="wlf-note" placeholder="Note (optional)"/>
          </div>
          <div class="aaf-actions">
            <button class="btn-ghost" id="wlf-cancel">Cancel</button>
            <button class="btn-primary" id="wlf-save">${Icon("check", 12)} Add to watchlist</button>
          </div>
        </div>
        <div>
          ${wl.length === 0 ? `<div class="muted" style="padding:16px; text-align:center; font-size:13px;">No tickers yet. Click “Add ticker” to start tracking price targets.</div>` : ""}
          ${wl.map(w => {
            const buyHit = w.buyBelow && w.price <= w.buyBelow;
            const sellHit = w.sellAbove && w.price >= w.sellAbove;
            const status = buyHit ? "BUY target hit" : sellHit ? "SELL target hit" : "Monitoring";
            const dot = buyHit ? "green" : sellHit ? "red" : "amber";
            const buyDist = w.buyBelow ? ((w.price - w.buyBelow) / w.buyBelow * 100) : null;
            return `
              <div class="wl-row" data-row-ticker="${w.ticker}">
                <div class="t-ticker">
                  <div>
                    <div style="font-weight:600;">${w.ticker}</div>
                    <div class="muted" style="font-size:11px;">${w.note}</div>
                  </div>
                </div>
                <div>
                  <div style="font-weight:600; font-variant-numeric: tabular-nums;">${fmt$(w.price)}</div>
                  <div class="delta ${w.change >= 0 ? 'up' : 'down'}" style="font-size:11px; padding:1px 6px;">${fmtPct(w.change)}</div>
                </div>
                <div class="wl-target">
                  <span class="pill">BUY ≤ ${w.buyBelow ? '$' + w.buyBelow : '—'}</span>
                  ${w.sellAbove ? `<span class="pill">SELL ≥ $${w.sellAbove}</span>` : ''}
                  ${buyDist !== null ? `<span class="muted" style="font-size:11px;">${buyDist > 0 ? '+' : ''}${buyDist.toFixed(1)}%</span>` : ''}
                </div>
                <div class="wl-status">
                  <span class="dot ${dot}"></span>${status}
                </div>
                <button class="acct-icon-btn danger wl-remove" data-id="${w.id}" title="Remove">${Icon("trash", 13)}</button>
              </div>
            `;
          }).join("")}
        </div>
      </div>

      <div class="card mt-m">
        <div class="card-head"><h3>Macro & company news</h3></div>
        <div class="card-body">
          ${D.news.map(n => `
            <div class="news-item">
              <span class="news-tag">${n.ticker}</span>
              <div>
                <div class="news-headline">${n.headline}</div>
                <div class="news-meta">${n.source} · ${n.time}</div>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;
  }

  function saveWatchlistItem(item) {
    return fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: item.id, ticker: item.ticker, buyBelow: item.buyBelow, sellAbove: item.sellAbove, note: item.note || "" }),
    }).then(r => r.json());
  }

  function refreshWatchlistSection() {
    fetch("/api/snapshot").then(r => r.json()).then(s => {
      if (!s.ok) return;
      D.watchlist = s.watchlist;
      if (window.Nexus && Nexus.getActive() === "watchlist") {
        const el = document.getElementById("section-watchlist");
        if (el) { el.innerHTML = renderWatchlist(); hydrateWatchlist(); }
      }
    }).catch(() => {});
  }

  // Always-available helper (used by Advisor actions/suggestions + watchlist form).
  // Upserts by ticker: if the ticker's already on the list, updates that row
  // (via its existing id) instead of creating a duplicate.
  window.NexusAddWatch = (ticker, buyBelow, sellAbove, note) => {
    ticker = (ticker || "").toUpperCase();
    if (!ticker) return;
    const existing = (D.watchlist || []).find(w => w.ticker === ticker);
    saveWatchlistItem({ id: existing && existing.id, ticker, buyBelow: buyBelow || null, sellAbove: sellAbove || null, note: note || "" })
      .then(resp => {
        if (resp.ok) {
          if (window.NexusToast) NexusToast(`${ticker} added to watchlist`, "ok");
          refreshWatchlistSection();
        } else if (window.NexusToast) {
          NexusToast(resp.error || "Couldn't add to watchlist", "err");
        }
      })
      .catch(() => { if (window.NexusToast) NexusToast("Couldn't add to watchlist", "err"); });
  };

  function hydrateWatchlist() {
    const addBtn = document.getElementById("wl-add-btn");
    const form = document.getElementById("wl-add-form");
    if (addBtn && form && !addBtn.dataset.bound) {
      addBtn.dataset.bound = "1";
      addBtn.addEventListener("click", () => {
        form.hidden = !form.hidden;
        if (!form.hidden) document.getElementById("wlf-ticker").focus();
      });
      document.getElementById("wlf-cancel").addEventListener("click", () => { form.hidden = true; });
      document.getElementById("wlf-save").addEventListener("click", () => {
        const ticker = (document.getElementById("wlf-ticker").value || "").trim().toUpperCase();
        if (!ticker) return;
        const buy = parseFloat(document.getElementById("wlf-buy").value);
        const sell = parseFloat(document.getElementById("wlf-sell").value);
        const note = (document.getElementById("wlf-note").value || "").trim();
        const existing = D.watchlist.find(w => w.ticker === ticker);
        saveWatchlistItem({
          id: existing && existing.id, ticker,
          buyBelow: isNaN(buy) ? null : buy, sellAbove: isNaN(sell) ? null : sell, note,
        }).then(resp => {
          if (!resp.ok) { if (window.NexusToast) NexusToast(resp.error || "Save failed", "err"); return; }
          form.hidden = true;
          refreshWatchlistSection();
        }).catch(() => { if (window.NexusToast) NexusToast("Save failed", "err"); });
      });
    }

    document.querySelectorAll(".wl-remove").forEach(btn => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        fetch(`/api/watchlist/${btn.dataset.id}`, { method: "DELETE" })
          .then(() => refreshWatchlistSection())
          .catch(() => { if (window.NexusToast) NexusToast("Couldn't remove", "err"); });
      });
    });
  }

  // ============================================================
  // ADVISOR
  // ============================================================
  function renderAdvisor() {
    const plan = D.advisorPlan;
    return `
      <div class="grid" style="grid-template-columns: 1.5fr 1fr; gap: 16px;">
        <div>
          <!-- Profile chip -->
          <div class="card card-pad">
            <div class="flex-between">
              <div class="flex gap-m">
                <div class="avatar" style="width:42px; height:42px; font-size:16px;">${D.profile.name.split(" ").map(s=>s[0]).join("")}</div>
                <div>
                  <div style="font-weight:600; font-size:15px;">${D.profile.name}</div>
                  <div class="muted" style="font-size:12px;">${D.profile.age} · ${D.profile.risk} · ${D.profile.horizon}yr horizon · ${D.profile.goals.join(", ")}</div>
                </div>
              </div>
              <button class="btn-ghost" id="adv-edit-profile">${Icon("edit", 12)} Edit profile</button>
            </div>
          </div>

          <!-- Fit assessment -->
          <div class="card mt-m hero" style="padding-top:22px;">
            <div class="flex gap-s">
              <span class="verdict-pill" style="background:var(--accent); color:white;">PLAN GENERATED</span>
              <span class="muted" style="font-size:11px;">Rule-based · updates live</span>
            </div>
            <div style="font-size:14px; line-height:1.6; margin-top:14px;">${plan.fit}</div>
          </div>

          <!-- Targets -->
          <div class="card mt-m">
            <div class="card-head"><h3>Target allocation</h3><div class="meta">${D.profile.risk} · ${D.profile.horizon}yr horizon</div></div>
            <div class="card-body">
              ${plan.targets.map(t => `
                <div class="alloc-row">
                  <div class="label-cell">${t.category}</div>
                  <div class="vs">
                    <div style="flex:1;">
                      <div style="font-size:11px; color:var(--text-2); margin-bottom:2px;">Current ${t.current.toFixed(1)}% → Target ${t.target.toFixed(0)}%</div>
                      <div style="position:relative; height:8px; background:var(--surface-2); border-radius:4px; overflow:hidden;">
                        <div style="position:absolute; left:0; top:0; bottom:0; width:${t.current}%; background:${t.gap >= 0 ? 'var(--amber)' : 'var(--accent)'}; opacity:0.5;"></div>
                        <div style="position:absolute; left:${t.target}%; top:-2px; bottom:-2px; width:2px; background:var(--text);"></div>
                      </div>
                    </div>
                  </div>
                  <div class="alloc-gap ${t.gap >= 0 ? 'pos' : 'neg'}">${(t.gap > 0 ? '+' : '') + t.gap.toFixed(1)}%</div>
                  <div><span class="alloc-action ${t.gap < -3 ? 'increase' : t.gap > 3 ? 'reduce' : 'ontarget'}">${t.gap < -3 ? 'Buy' : t.gap > 3 ? 'Trim' : 'Hold'}</span></div>
                </div>
              `).join("")}
            </div>
          </div>

          <!-- Action items -->
          <div class="card mt-m">
            <div class="card-head"><h3>Action items</h3><div class="meta">In priority order</div></div>
            <div class="card-body">
              ${plan.actions.map(a => `
                <div class="action-item">
                  <div class="action-priority">${a.priority}</div>
                  <div>
                    <div class="action-title">
                      <span class="action-tag ${a.action}">${a.action}</span>
                      <span style="font-family:ui-monospace, 'SF Mono', monospace; font-size:13px;">${a.ticker}</span>
                    </div>
                    <div class="action-desc">${a.desc}</div>
                    <div class="action-reason">${a.reason}</div>
                  </div>
                  <button class="btn-ghost adv-action-btn" data-ticker="${a.ticker}" data-act="${a.action}">${a.action === "buy" ? "Set buy alert" : a.action === "trim" ? "Set sell alert" : "Mark done"}</button>
                </div>
              `).join("")}
            </div>
          </div>

          <!-- Suggested tickers -->
          <div class="card mt-m">
            <div class="card-head"><h3>Suggested to fill gaps</h3></div>
            <div class="card-body">
              <div class="grid g-2">
                ${plan.suggested.map(s => `
                  <div class="suggested-card">
                    <div class="flex-between" style="align-items: flex-start;">
                      <div>
                        <div class="suggested-ticker">${s.ticker}</div>
                        <div class="suggested-cat">${s.category} · target ${s.weight}%</div>
                      </div>
                      <button class="btn-ghost adv-suggest-btn" data-ticker="${s.ticker}" style="padding:5px 8px;" title="Add to watchlist">${Icon("plus", 12)}</button>
                    </div>
                    <div class="suggested-rationale">${s.rationale}</div>
                  </div>
                `).join("")}
              </div>
            </div>
          </div>

          <!-- Risks -->
          <div class="card mt-m mb-m">
            <div class="card-head"><h3>Risks to watch</h3></div>
            <div class="card-body">
              ${plan.risks.map(r => `<div class="risk-item">${Icon("alert", 14)}<span>${r}</span></div>`).join("")}
              <div class="muted mt-m" style="font-size:12px;">${Icon("info", 12)} Rebalance ${plan.rebalance.toLowerCase()}</div>
            </div>
          </div>
        </div>

        <!-- No AI chat in this version — everything above is computed
             automatically from your holdings using deterministic rules
             (target allocation by risk profile, concentration/leverage
             flags, tax-loss candidates), not an LLM. -->
        <div>
          <div class="card" style="position:sticky; top:84px;">
            <div class="card-head"><h3>${Icon("info", 14)} How this plan is built</h3></div>
            <div class="card-body">
              <div class="muted" style="font-size:13px; line-height:1.6;">
                Targets, actions, and risks on this page are computed automatically from your
                risk profile, horizon, and current holdings — no AI chat in this version, just
                rule-based math you can audit. Update your risk tolerance/horizon in Settings to
                see the plan change.
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function hydrateAdvisor() {
    // Edit profile → open Settings panel
    const editBtn = document.getElementById("adv-edit-profile");
    if (editBtn && !editBtn.dataset.bound) {
      editBtn.dataset.bound = "1";
      editBtn.addEventListener("click", () => {
        const sb = document.getElementById("settings-btn");
        if (sb) sb.click();
      });
    }

    // "Set buy/sell alert" action buttons → add ticker to watchlist
    document.querySelectorAll(".adv-action-btn").forEach(btn => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        const t = btn.dataset.ticker, act = btn.dataset.act;
        if (act === "buy" || act === "trim") {
          if (window.NexusAddWatch) window.NexusAddWatch(t, null, null, act === "buy" ? "Advisor: accumulate" : "Advisor: trim candidate");
          btn.textContent = "✓ On watchlist";
        } else {
          btn.textContent = "✓ Done";
        }
        btn.disabled = true;
      });
    });

    // Suggested ticker "+" → add to watchlist
    document.querySelectorAll(".adv-suggest-btn").forEach(btn => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        if (window.NexusAddWatch) window.NexusAddWatch(btn.dataset.ticker, null, null, "Advisor: fills allocation gap");
        btn.innerHTML = "✓";
        btn.disabled = true;
      });
    });

    // No AI chat in this version — see renderAdvisor()'s "How this plan is
    // built" card instead of a chat panel.
  }

  // Merge
  Object.assign(window.AppleSections, { renderPortfolio, hydratePortfolio, renderWatchlist, hydrateWatchlist, renderAdvisor, hydrateAdvisor });
})();
