(function(){
  "use strict";

  let currentDatasetId = null;
  let currentJobId = null;
  let lastResult = null;
  let lastSim = null;
  let statusBase = "";

  const $ = (id) => document.getElementById(id);

  function setStatus(text, on){
    const el = $("dataStatus");
    el.innerHTML = `<span class="dot ${on ? 'dot-on' : 'dot-off'}"></span><span>${text}</span>`;
  }

  function fmt(n, decimals=2){
    if (n === null || n === undefined) return "—";
    return Number(n).toFixed(decimals);
  }

  function fmtPct(n){
    if (n === null || n === undefined) return "—";
    return (Number(n) * 100).toFixed(1) + "%";
  }

  function fmtElapsed(ms){
    if (ms < 1000) return Math.round(ms) + "ms";
    const s = ms / 1000;
    if (s < 60) return s.toFixed(1) + "s";
    return Math.floor(s / 60) + "m " + Math.round(s % 60) + "s";
  }

  function fmtUsd(n){
    if (n === null || n === undefined) return "—";
    const s = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? "-" : "") + "$" + s;
  }

  function fmtUsdSigned(n){
    if (n === null || n === undefined) return "—";
    return (n > 0 ? "+" : "") + fmtUsd(n);
  }

  function fmtAxis(v){
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + "k";
    return v.toFixed(1);
  }

  function readCapitalParams(){
    return {
      enabled: $("capitalEnabled").checked,
      initial_capital: parseFloat($("capitalInitial").value) || 0,
      risk_pct: parseFloat($("capitalRiskPct").value) || 0,
      mode: $("capitalMode").value,
    };
  }

  function readParams(){
    return {
      dataset_id: currentDatasetId,
      session_open_utc: $("sessionOpen").value || "00:00",
      range_minutes: parseInt($("rangeMinutes").value, 10),
      breakout_search_minutes: parseInt($("searchMinutes").value, 10),
      entry_mode: $("entryMode").value,
      sl_pct_of_range: parseFloat($("slPct").value),
      tp_rr: parseFloat($("tpRR").value),
      sl_ladder: readLadder(),
      spread_pips: parseFloat($("spreadPips").value),
      slippage_pips: parseFloat($("slippagePips").value),
      session_max_hours: parseFloat($("sessionMaxHours").value),
      skip_weekends: $("skipWeekends").checked,
    };
  }

  function readLadder(){
    const rows = [];
    document.querySelectorAll("#ladderRows .ladder-row").forEach(row => {
      const trigger = parseFloat(row.querySelector(".ladder-trigger").value);
      const sl = parseFloat(row.querySelector(".ladder-sl").value);
      if (!isNaN(trigger) && !isNaN(sl)) rows.push([trigger, sl]);
    });
    return rows;
  }

  function updateSummary(){
    const p = readParams();
    const entryDesc = p.entry_mode === "market"
      ? "market entry at the next bar's open after the breakout candle closes"
      : `a resting limit order back at the range boundary, expiring after ${p.breakout_search_minutes} min if unfilled`;

    $("summaryText").innerHTML =
      `Over the full loaded dataset. ` +
      `Mark the <b>${p.range_minutes}-minute</b> opening range starting at <b>${p.session_open_utc} UTC</b>. ` +
      `If an M1 candle closes outside the range within <b>${p.breakout_search_minutes} minutes</b> of session open, take ` +
      `${entryDesc}. ` +
      `Stop loss sits at <b>${p.sl_pct_of_range}× range</b> from the midpoint ` +
      `(0.5 = exact midpoint), take profit at <b>${p.tp_rr}R</b>. ` +
      ladderSummary(p.sl_ladder) +
      `Trades are force-closed after <b>${p.session_max_hours}h</b> if neither level is hit. ` +
      (p.skip_weekends ? "Weekends are skipped." : "Weekends are included.");
  }

  function ladderSummary(ladder){
    if (ladder.length === 0){
      return "The stop stays at its initial level for the whole trade. ";
    }
    const sorted = [...ladder].sort((a, b) => a[0] - b[0]);
    const parts = sorted.map(([trigger, sl]) => {
      const slLabel = sl === 0 ? "breakeven" : (sl > 0 ? "+" : "") + sl + "R";
      return `${trigger}R → ${slLabel}`;
    });
    return `As price advances, the stop moves: <b>${parts.join(" · ")}</b>. `;
  }

  function showDatasetMeta(data){
    const range = `${(data.start||"").slice(0,10)} → ${(data.end||"").slice(0,10)}`;
    const el = $("datasetMeta");
    const nFiles = (data.files || []).length;
    const perFile = (data.files || []).map(f =>
      `<span class="meta-file">${f.source} — ${f.rows.toLocaleString()} bars</span>`).join("");
    if (el){
      el.innerHTML =
        `${data.symbol} · ${nFiles} file${nFiles === 1 ? "" : "s"} · ` +
        `${data.rows.toLocaleString()} bars · ${range} UTC` +
        (perFile ? `<span class="meta-file-list">${perFile}</span>` : "");
    }
    statusBase = `${data.symbol} · ${data.rows.toLocaleString()} bars · ${range}`;
    setStatus(statusBase, true);
  }

  async function handleUpload(e){
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    const wrap = $("uploadProgressWrap");
    const bar = $("uploadProgressBar");
    const label = $("uploadProgressLabel");
    wrap.hidden = false;
    bar.style.width = "0%";
    label.textContent = "Uploading… 0%";

    try{
      const form = new FormData();
      files.forEach(f => form.append("files", f));
      form.append("symbol", $("uploadSymbol").value || "XAUUSD");
      form.append("source_tz", $("uploadTz").value || "auto");

      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/data/upload");
        xhr.upload.onprogress = (ev) => {
          if (ev.lengthComputable){
            const pct = Math.min(99, Math.round(ev.loaded / ev.total * 100));
            bar.style.width = pct + "%";
            label.textContent = "Uploading… " + pct + "%";
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300){
            try { resolve(JSON.parse(xhr.responseText)); }
            catch(err){ reject(new Error("Bad server response")); }
          } else {
            try { reject(new Error(JSON.parse(xhr.responseText).detail || "Upload failed")); }
            catch(err){ reject(new Error("Upload failed")); }
          }
        };
        xhr.onerror = () => reject(new Error("Network error during upload"));
        xhr.send(form);
      });

      bar.style.width = "100%";
      label.textContent = "Uploading… 100%";
      currentDatasetId = data.dataset_id;
      showDatasetMeta(data);
      $("btnGenerate").disabled = false;
      $("btnClear").disabled = false;
    } catch(err){
      // A failed upload must never leave the previous dataset active: the
      // Generate button would silently re-run the OLD data and the report
      // would appear unchanged, as if the upload had succeeded.
      currentDatasetId = null;
      $("btnGenerate").disabled = true;
      $("btnClear").disabled = true;
      $("datasetMeta").textContent = "Upload failed — no dataset loaded";
      setStatus("Upload failed", false);
      alert("Error: " + err.message);
    } finally {
      wrap.hidden = true;
    }
  }

  async function handleDemo(){
    const btn = $("btnDemo");
    btn.disabled = true;
    try{
      const res = await fetch("/api/data/demo", { method: "POST" });
      if (!res.ok){
        const err = await res.json();
        throw new Error(err.detail || "Demo dataset unavailable");
      }
      const data = await res.json();
      currentDatasetId = data.dataset_id;
      showDatasetMeta(data);
      $("btnGenerate").disabled = false;
      $("btnClear").disabled = false;
      await handleGenerate({ start_date: "2026-05-01", end_date: "2026-08-12" });
    } catch(err){
      currentDatasetId = null;
      $("btnGenerate").disabled = true;
      $("datasetMeta").textContent = "Demo load failed";
      setStatus("Demo load failed", false);
      alert("Error: " + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function handleClear(){
    const id = currentDatasetId;
    if (id){
      // Best-effort: drop the dataset server-side to free memory.
      try { await fetch(`/api/data/${id}`, { method: "DELETE" }); }
      catch(err){ /* local reset continues regardless */ }
    }
    currentDatasetId = null;
    currentJobId = null;
    lastResult = null;
    lastSim = null;
    calendarMonth = null;
    yearNav = null;
    $("btnGenerate").disabled = true;
    $("btnClear").disabled = true;
    $("datasetMeta").innerHTML = "No dataset loaded";
    setStatus("No dataset loaded", false);
    $("resultsSection").style.display = "none";
    $("emptyState").style.display = "";
    const input = $("fileUpload");
    input.value = "";
    equityState = null;
  }

  function statCardHtml(label, value, cls, sub){
    return `<div class="stat-card ${cls}">
      <div class="stat-label">${label}</div>
      <div class="stat-value ${cls === 'pos' || cls === 'neg' ? cls : ''}">${value}</div>
      ${sub || ""}
    </div>`;
  }

  function evVerdict(ev){
    if (ev === null || ev === undefined) return "";
    if (ev >= 0.2) return "Good expectancy — keep trading this edge.";
    if (ev > 0) return "Positive but thin — raise the RR (wider TP / tighter SL) or improve the win rate.";
    if (ev === 0) return "Flat — no edge. Costs alone will sink it.";
    return "Negative expectancy — this setup loses in the long run. Revisit the entry rules or abandon it.";
  }

  function apiErrorMessage(err){
    if (!err) return "Capital simulation failed";
    const d = err.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length && d[0]){
      const loc = d[0].loc || [];
      const field = loc[loc.length - 1];
      const pretty = { initial_capital: "Initial capital", risk_pct: "Risk per trade", mode: "Risk basis" }[field] || field;
      const msg = (d[0].msg || "invalid").replace(/^Value error, /, "");
      return "Capital simulation: " + (pretty ? pretty + " — " : "") + msg;
    }
    return "Capital simulation failed";
  }

  async function refreshSim(){
    const cap = readCapitalParams();
    if (!cap.enabled || !currentJobId){
      lastSim = null;
      renderAll();
      return;
    }
    if (!isFinite(cap.initial_capital) || cap.initial_capital <= 0){
      lastSim = null;
      renderAll();
      setStatus("Initial capital must be a number greater than 0", false);
      return;
    }
    if (!isFinite(cap.risk_pct) || cap.risk_pct <= 0 || cap.risk_pct >= 10){
      lastSim = null;
      renderAll();
      setStatus("Risk per trade must be a number between 0 and 10%", false);
      return;
    }
    try{
      const res = await fetch(`/api/backtest/simulate/${currentJobId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          initial_capital: cap.initial_capital,
          risk_pct: cap.risk_pct,
          mode: cap.mode,
        }),
      });
      if (!res.ok){
        lastSim = null;
        renderAll();
        try{
          const err = await res.json();
          setStatus(apiErrorMessage(err), false);
        } catch(e){ /* no JSON body */ }
        return;
      }
      lastSim = await res.json();
    } catch(e){
      lastSim = null;
    }
    renderAll();
  }

  function dollarTotals(trades, pnl){
    const out = { long: 0, short: 0, dow: {} };
    (trades || []).forEach((t, i) => {
      const p = pnl ? (pnl[i] || 0) : 0;
      out[t.direction] += p;
      const dow = new Date(t.date + "T00:00:00Z").toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
      out.dow[dow] = (out.dow[dow] || 0) + p;
    });
    return out;
  }

  function renderAll(){
    if (!lastResult) return;
    const sim = lastSim;
    const trades = sim ? lastResult.trades : null;
    renderStats(lastResult.stats, sim);
    renderCalendar(lastResult.trades, sim ? sim.pnl : null);
    renderDowBreakdown(lastResult.breakdown_day_of_week, trades, sim ? sim.pnl : null);
    renderMonthBreakdown(lastResult.trades, sim ? sim.pnl : null);
    renderAvgMonthBreakdown(lastResult.trades, sim ? sim.pnl : null);
    renderTable(lastResult.trades, sim ? sim.pnl : null);
    renderEquityCurve(lastResult.equity_curve, sim ? sim.curve : null, sim ? readCapitalParams().initial_capital : null);
    const sub = document.querySelector(".chart-panel .panel-sub");
    if (sub) sub.textContent = sim ? "Equity ($)" : "Cumulative R";
  }

  function bestWorstPeriods(trades){
    if (!trades || trades.length === 0) return null;
    const byDow = {}, byMonth = {};
    trades.forEach(t => {
      const date = new Date(t.date + "T00:00:00Z");
      const dow = date.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
      byDow[dow] = (byDow[dow] || 0) + t.r_multiple;
      const mk = date.toLocaleDateString("en-US", { month: "long", timeZone: "UTC" });
      byMonth[mk] = (byMonth[mk] || 0) + t.r_multiple;
    });
    const best = o => Object.entries(o).reduce((a, b) => b[1] > a[1] ? b : a);
    const worst = o => Object.entries(o).reduce((a, b) => b[1] < a[1] ? b : a);
    return { bestDow: best(byDow), worstDow: worst(byDow), bestMonth: best(byMonth), worstMonth: worst(byMonth) };
  }

  function renderStats(stats, sim){
    const totalRClass = stats.total_r > 0 ? "pos" : (stats.total_r < 0 ? "neg" : "neutral");
    const avgRClass = stats.average_r > 0 ? "pos" : (stats.average_r < 0 ? "neg" : "neutral");

    const cards = [];
    if (sim){
      cards.push(statCardHtml("Final equity", fmtUsd(sim.stats.final_equity),
        sim.stats.total_pnl > 0 ? "pos" : (sim.stats.total_pnl < 0 ? "neg" : "neutral"),
        `<span class="stat-sub">${(stats.total_r > 0 ? "+" : "")}${fmt(stats.total_r)}R · ${fmtUsdSigned(sim.stats.total_pnl)} · ${sim.stats.total_return_pct > 0 ? "+" : ""}${sim.stats.total_return_pct}%</span>`));
    } else {
      cards.push(statCardHtml("Total R", (stats.total_r > 0 ? "+" : "") + fmt(stats.total_r), totalRClass));
    }
    cards.push(
      statCardHtml("Total trades", stats.total_trades, "neutral"),
      statCardHtml("Win rate", fmtPct(stats.win_rate) + (stats.win_rate_no_be !== null ? ` <span class="stat-sub">ex-BE ${fmtPct(stats.win_rate_no_be)}</span>` : ""), "neutral"),
      statCardHtml("EV (Avg R / trade)", (stats.average_r > 0 ? "+" : "") + fmt(stats.average_r), avgRClass,
        `<span class="stat-sub stat-verdict">${sim ? fmtUsdSigned(sim.stats.avg_pnl) + " · " : ""}${evVerdict(stats.expectancy_r)}</span>`),
      statCardHtml("Profit factor", stats.profit_factor !== null ? fmt(stats.profit_factor) : "—", "neutral",
        sim && sim.stats.profit_factor_usd !== null ? `<span class="stat-sub">${fmt(sim.stats.profit_factor_usd)} in $</span>` : ""),
      statCardHtml("Max drawdown", fmt(stats.max_drawdown_r) + "R", "neg",
        sim ? `<span class="stat-sub">${fmtUsd(sim.stats.max_drawdown_usd)} · ${sim.stats.max_drawdown_pct}%</span>` : ""),
      statCardHtml("Longest win streak", stats.longest_win_streak, "pos"),
      statCardHtml("Longest loss streak", stats.longest_loss_streak, "neg"));

    const bw = bestWorstPeriods(lastResult.trades);
    if (bw){
      const [bd, wd] = [bw.bestDow, bw.worstDow];
      const [bm, wm] = [bw.bestMonth, bw.worstMonth];
      cards.push(
        statCardHtml("Best / worst day",
          `<span class="bw-big">${bd[0]} · ${(bd[1] > 0 ? "+" : "")}${fmt(bd[1])}R</span>`,
          "pos",
          `<span class="stat-sub bw-worst">Worst day: ${wd[0]} · ${fmt(wd[1])}R</span>`),
        statCardHtml("Best / worst month",
          `<span class="bw-big">${bm[0]} · ${(bm[1] > 0 ? "+" : "")}${fmt(bm[1])}R</span>`,
          "pos",
          `<span class="stat-sub bw-worst">Worst month: ${wm[0]} · ${fmt(wm[1])}R</span>`));
    } else {
      cards.push(
        statCardHtml("Best / worst day", "—", "neutral"),
        statCardHtml("Best / worst month", "—", "neutral"));
    }
    $("statGrid").innerHTML = cards.join("");
  }

  let calendarMonth = null;
  let yearNav = null;

  function calCellValue(v, usd){
    if (usd){
      const abs = Math.abs(v);
      return (v > 0 ? "+" : v < 0 ? "-" : "") + "$" + (abs >= 1000 ? (abs / 1000).toFixed(1) + "k" : abs.toFixed(0));
    }
    return (v > 0 ? "+" : "") + fmt(v, 1) + "R";
  }

  function renderCalendar(trades, pnl){
    const grid = $("calGrid");
    const label = $("calMonthLabel");
    const totalEl = $("calTotal");
    const next = $("calNext");
    if (!trades || trades.length === 0){
      grid.innerHTML = "";
      label.textContent = "";
      totalEl.textContent = "No trades";
      totalEl.className = "cal-total";
      next.disabled = true;
      return;
    }

    const perDay = {};
    trades.forEach((t, i) => {
      const v = perDay[t.date] || { n: 0, r: 0, usd: 0 };
      v.n++;
      v.r += t.r_multiple;
      v.usd += pnl ? (pnl[i] || 0) : 0;
      perDay[t.date] = v;
    });

    const lastDate = trades[trades.length - 1].date;
    const lastY = +lastDate.slice(0, 4);
    const lastM = +lastDate.slice(5, 7);

    if (!calendarMonth ||
        calendarMonth.getUTCFullYear() > lastY ||
        (calendarMonth.getUTCFullYear() === lastY && calendarMonth.getUTCMonth() + 1 > lastM)){
      calendarMonth = new Date(Date.UTC(lastY, lastM - 1, 1));
    }

    const y = calendarMonth.getUTCFullYear();
    const m = calendarMonth.getUTCMonth();
    const monthKey = `${y}-${String(m + 1).padStart(2, "0")}`;
    const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const firstDow = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7;

    const useUsd = pnl != null;
    const initialCap = readCapitalParams().initial_capital;
    const calPct = (val) => initialCap > 0
      ? (val > 0 ? "+" : "") + (val / initialCap * 100).toFixed(2) + "%"
      : "";
    let mTotal = 0, mR = 0, mN = 0, absMax = 1e-9;
    Object.entries(perDay).forEach(([d, v]) => {
      if (!d.startsWith(monthKey)) return;
      const val = useUsd ? v.usd : v.r;
      mTotal += val;
      mR += v.r;
      mN += v.n;
      absMax = Math.max(absMax, Math.abs(val));
    });

    label.textContent = new Date(Date.UTC(y, m, 1))
      .toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
    const totalCls = mR > 0 ? "pos" : (mR < 0 ? "neg" : "");
    totalEl.textContent = `${mN} trade${mN === 1 ? "" : "s"} · ${(mR > 0 ? "+" : "")}${fmt(mR)}R` +
      (useUsd ? ` · ${fmtUsdSigned(mTotal)}` : "");
    totalEl.className = "cal-total " + totalCls;
    next.disabled = (y === lastY && m + 1 === lastM);

    const dowNames = ["Mo", "Tu", "We", "Th", "Fr", "Week total"];
    let cells = dowNames.map(d => `<div class="cal-dow">${d}</div>`).join("");
    let weekR = 0, weekUsd = 0, slotsInWeek = 0;
    const flushWeek = () => {
      while (slotsInWeek < 5){
        cells += `<div class="cal-day empty"></div>`;
        slotsInWeek++;
      }
      const has = (useUsd ? weekUsd : weekR) !== 0;
      const val = useUsd ? weekUsd : weekR;
      let cls = "cal-day week-total";
      let inner = "";
      if (has){
        const alpha = Math.min(1, 0.08 + 0.30 * (Math.abs(val) / absMax));
        const color = val > 0 ? `rgba(107,197,147,${alpha})` : `rgba(217,114,103,${alpha})`;
        cls += " has-value";
        inner += `<span class="cal-val" style="background:${color}">${calCellValue(val, useUsd)}</span>`;
        if (useUsd){
          inner += `<span class="cal-val cal-extra">${(weekR > 0 ? "+" : "")}${fmt(weekR, 1)}R · ${calPct(weekUsd)}</span>`;
        }
      } else {
        inner += `<span class="cal-val cal-flat">—</span>`;
      }
      cells += `<div class="${cls}">${inner}</div>`;
      weekR = 0;
      weekUsd = 0;
      slotsInWeek = 0;
    };
    for (let d = 1; d <= daysInMonth; d++){
      const dowIdx = (firstDow + d - 1) % 7;
      if (dowIdx < 5){
        while (slotsInWeek < dowIdx){
          cells += `<div class="cal-day empty"></div>`;
          slotsInWeek++;
        }
        const key = `${monthKey}-${String(d).padStart(2, "0")}`;
        const v = perDay[key];
        let cls = "cal-day";
        let inner = `<span class="cal-num">${d}</span>`;
        if (v && (useUsd ? v.usd : v.r) !== 0){
          const val = useUsd ? v.usd : v.r;
          const alpha = Math.min(1, 0.08 + 0.30 * (Math.abs(val) / absMax));
          const color = val > 0 ? `rgba(107,197,147,${alpha})` : `rgba(217,114,103,${alpha})`;
          cls += " has-value";
          inner += `<span class="cal-val" style="background:${color}">${calCellValue(val, useUsd)}</span>`;
        }
        cells += `<div class="${cls}">${inner}</div>`;
        slotsInWeek++;
        if (v){
          weekR += v.r;
          weekUsd += v.usd;
        }
      }
      if (dowIdx === 6) flushWeek();
    }
    flushWeek();
    grid.innerHTML = cells;
  }

  function renderDowBreakdown(dow, trades, pnl){
    const order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
    const keys = Object.keys(dow).sort((a,b) => order.indexOf(a) - order.indexOf(b));
    if (keys.length === 0){
      $("dowBreakdown").innerHTML = `<div class="breakdown-row"><span class="label">No trades</span></div>`;
      return;
    }
    const dt = trades ? dollarTotals(trades, pnl) : null;
    const rows = keys.map(k => {
      const d = dow[k];
      const rClass = d.total_r > 0 ? "pos" : (d.total_r < 0 ? "neg" : "");
      const usd = dt && dt.dow[k] ? ` · ${fmtUsdSigned(dt.dow[k])}` : "";
      return `<div class="breakdown-row">
        <span class="label">${k}</span>
        <span class="value ${rClass}">${d.trades} trades · ${(d.total_r>0?'+':'')}${fmt(d.total_r)}R${usd}</span>
      </div>`;
    });
    $("dowBreakdown").innerHTML = rows.join("");
  }

  function renderMonthBreakdown(trades, pnl){
    const list = $("monthBreakdown");
    const label = $("yearLabel");
    const next = $("yearNext");
    if (!trades || trades.length === 0){
      list.innerHTML = `<div class="breakdown-row"><span class="label">No trades</span></div>`;
      label.textContent = "";
      next.disabled = true;
      return;
    }
    const byMonth = {};
    trades.forEach((t, i) => {
      const key = t.date.slice(0, 7);
      const v = byMonth[key] || { n: 0, r: 0, usd: 0 };
      v.n++;
      v.r += t.r_multiple;
      v.usd += pnl ? (pnl[i] || 0) : 0;
      byMonth[key] = v;
    });
    const lastYear = +trades[trades.length - 1].date.slice(0, 4);
    if (!yearNav || yearNav > lastYear) yearNav = lastYear;
    const yearKeys = Object.keys(byMonth).filter(k => k.startsWith(String(yearNav) + "-")).sort();
    const rows = yearKeys.map(k => {
      const v = byMonth[k];
      const rClass = v.r > 0 ? "pos" : (v.r < 0 ? "neg" : "");
      const labelText = new Date(k + "-01T00:00:00Z")
        .toLocaleDateString("en-US", { month: "long", timeZone: "UTC" });
      const usd = pnl ? ` · ${fmtUsdSigned(v.usd)}` : "";
      return `<div class="breakdown-row">
        <span class="label">${labelText}</span>
        <span class="value ${rClass}">${v.n} trade${v.n === 1 ? "" : "s"} · ${(v.r > 0 ? "+" : "")}${fmt(v.r)}R${usd}</span>
      </div>`;
    });
    list.innerHTML = rows.length
      ? rows.join("")
      : `<div class="breakdown-row"><span class="label">No trades</span></div>`;
    label.textContent = String(yearNav);
    next.disabled = yearNav >= lastYear;
  }

  const MONTH_ORDER = ["January","February","March","April","May","June","July","August","September","October","November","December"];

  function renderAvgMonthBreakdown(trades, pnl){
    if (!trades || trades.length === 0){
      $("avgMonthBreakdown").innerHTML = `<div class="breakdown-row"><span class="label">No trades</span></div>`;
      return;
    }
    const byMonth = {};
    trades.forEach((t, i) => {
      const name = new Date(t.date + "T00:00:00Z")
        .toLocaleDateString("en-US", { month: "long", timeZone: "UTC" });
      const v = byMonth[name] || { n: 0, r: 0, usd: 0 };
      v.n++;
      v.r += t.r_multiple;
      v.usd += pnl ? (pnl[i] || 0) : 0;
      byMonth[name] = v;
    });
    const rows = MONTH_ORDER.filter(m => byMonth[m]).map(name => {
      const v = byMonth[name];
      const avgR = v.r / v.n;
      const rClass = v.r > 0 ? "pos" : (v.r < 0 ? "neg" : "");
      const avgCls = avgR > 0 ? "pos" : (avgR < 0 ? "neg" : "");
      const usd = pnl
        ? ` · <span class="dim">${fmtUsdSigned(v.usd)} · ${fmtUsdSigned(v.usd / v.n)} avg</span>`
        : "";
      return `<div class="breakdown-row">
        <span class="label">${name}</span>
        <span class="value ${rClass}">${v.n} trade${v.n === 1 ? "" : "s"} · ${(v.r > 0 ? "+" : "")}${fmt(v.r)}R · <span class="avg ${avgCls}">${(avgR > 0 ? "+" : "")}${fmt(avgR)}R avg</span>${usd}</span>
      </div>`;
    });
    $("avgMonthBreakdown").innerHTML = rows.join("");
  }

  function renderTable(trades, pnl){
    const tbody = document.querySelector("#tradeTable tbody");
    tbody.innerHTML = trades.map((t, i) => {
      const rClass = t.r_multiple > 0 ? "r-pos" : (t.r_multiple < 0 ? "r-neg" : "");
      const dirClass = t.direction === "long" ? "dir-long" : "dir-short";
      const p = pnl ? pnl[i] : null;
      const pClass = p > 0 ? "r-pos" : (p < 0 ? "r-neg" : "");
      return `<tr>
        <td>${t.date}</td>
        <td class="${dirClass}">${t.direction}</td>
        <td class="num">${fmt(t.range_low)}–${fmt(t.range_high)}</td>
        <td class="num">${fmt(t.entry_price)}</td>
        <td class="num">${t.sl_moved ? fmt(t.sl_price) + " → " + fmt(t.sl_final) : fmt(t.sl_price)}</td>
        <td class="num">${fmt(t.tp_price)}</td>
        <td class="num">${fmt(t.exit_price)}</td>
        <td><span class="reason-tag">${t.exit_reason}</span></td>
        <td class="num ${rClass}">${t.r_multiple > 0 ? '+' : ''}${fmt(t.r_multiple)}</td>
        ${pnl ? `<td class="num ${pClass}">${fmtUsdSigned(p)}</td>` : ""}
      </tr>`;
    }).join("");
  }

  let equityState = null;

  function renderEquityCurve(curveR, curve$, initialCap){
    const canvas = $("equityChart");
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = rect.width || canvas.parentElement.clientWidth;
    const h = 220;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.height = h + "px";
    ctx.scale(dpr, dpr);

    equityState = null;
    ctx.clearRect(0,0,w,h);
    const curve = curve$ || curveR;
    if (curve.length === 0){
      ctx.fillStyle = "#565f70";
      ctx.font = "12px 'JetBrains Mono'";
      ctx.fillText("No trades to plot", 20, h/2);
      return;
    }

    const pad = { l: 44, r: 16, t: 16, b: 24 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;

    const valOf = p => p.cumulative_r !== undefined ? p.cumulative_r : p.equity;
    const values = curve.map(valOf);
    const zeroVal = curve$ ? initialCap : 0;
    const minV = Math.min(zeroVal, ...values);
    const maxV = Math.max(zeroVal, ...values);
    const toY = (v) => maxV === minV ? pad.t + plotH/2
                                     : pad.t + plotH - ((v - minV) / (maxV - minV)) * plotH;
    const pts = curve.map((p, i) => ({
      x: pad.l + (i / Math.max(1, curve.length - 1)) * plotW,
      y: toY(valOf(p)),
      date: p.date,
      v: valOf(p),
    }));

    // No R overlay when the dollar equity curve is primary: one curve only.
    equityState = { ctx, w, h, pad, pts, minV, maxV, zeroVal, pts2: null, usd: !!curve$, initCap: curve$ ? initialCap : null };
    drawEquityChart();
  }

  function drawEquityChart(){
    const s = equityState;
    if (!s) return;
    const { ctx, w, h, pad, pts } = s;
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    ctx.clearRect(0,0,w,h);

    const zeroY = pad.t + plotH - ((s.zeroVal - s.minV) / (s.maxV - s.minV)) * plotH;
    ctx.strokeStyle = "#232937";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, zeroY);
    ctx.lineTo(w - pad.r, zeroY);
    ctx.stroke();

    ctx.fillStyle = "#565f70";
    ctx.font = "10px 'JetBrains Mono'";
    ctx.fillText(fmtAxis(s.maxV), 4, pad.t + 8);
    ctx.fillText(fmtAxis(s.minV), 4, h - pad.b);

    const finalPositive = pts[pts.length - 1].v >= s.zeroVal;
    ctx.strokeStyle = finalPositive ? "#6bc593" : "#d97267";
    ctx.lineWidth = 1.75;
    ctx.beginPath();
    pts.forEach((p, i) => {
      if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();

    ctx.lineTo(pts[pts.length - 1].x, zeroY);
    ctx.lineTo(pts[0].x, zeroY);
    ctx.closePath();
    ctx.fillStyle = finalPositive ? "rgba(107,197,147,0.08)" : "rgba(217,114,103,0.08)";
    ctx.fill();
  }

  async function handleGenerate(opts){
    if (!currentDatasetId){
      alert("Load a dataset first.");
      return;
    }
    const btn = $("btnGenerate");
    btn.disabled = true;
    btn.classList.add("loading");
    btn.querySelector(".btn-label").textContent = "Running backtest…";
    let elapsedTimer = null;

    try{
      const params = readParams();
      if (opts){
        if (opts.start_date) params.start_date = opts.start_date;
        if (opts.end_date) params.end_date = opts.end_date;
      }
      const res = await fetch("/api/backtest/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok){
        const err = await res.json();
        throw new Error(err.detail || "Backtest failed");
      }

      const progressWrap = $("progressWrap");
      const progressBar = $("progressBar");
      const progressLabel = $("progressLabel");
      progressWrap.hidden = false;

      const t0 = performance.now();
      let pct = 0;
      const setElapsed = () => {
        progressLabel.textContent = "Backtesting… " + pct + "% · " + fmtElapsed(performance.now() - t0);
      };
      elapsedTimer = setInterval(setElapsed, 200);

      let data = null;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for(;;){
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) !== -1){
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const msg = JSON.parse(line);
          if (msg.type === "progress"){
            pct = msg.total ? Math.min(99, Math.round(msg.done / msg.total * 100)) : 0;
            progressBar.style.width = pct + "%";
            setElapsed();
          } else if (msg.type === "done"){
            clearInterval(elapsedTimer);
            progressBar.style.width = "100%";
            pct = 100;
            const elapsed = fmtElapsed(performance.now() - t0);
            progressLabel.textContent = "Backtesting… 100% · " + elapsed;
            setStatus(statusBase + " · " + elapsed, true);
            data = msg.result;
          } else if (msg.type === "error"){
            throw new Error(msg.detail || "Backtest failed");
          }
        }
      }
      if (!data) throw new Error("Backtest produced no result");

      currentJobId = data.job_id;
      lastResult = data;
      calendarMonth = null;
      yearNav = null;

      $("emptyState").style.display = "none";
      $("resultsSection").style.display = "flex";

      await refreshSim();
    } catch(e){
      alert("Error: " + e.message);
    } finally {
      if (elapsedTimer) clearInterval(elapsedTimer);
      btn.disabled = false;
      btn.classList.remove("loading");
      btn.querySelector(".btn-label").textContent = "Generate backtest";
      $("progressWrap").hidden = true;
      $("progressBar").style.width = "0%";
    }
  }

  function handleExport(){
    if (!currentJobId){ return; }
    const cap = readCapitalParams();
    if (cap.enabled){
      window.location.href = `/api/backtest/export/${currentJobId}?capital=1&initial_capital=${cap.initial_capital}&risk_pct=${cap.risk_pct}&mode=${cap.mode}`;
    } else {
      window.location.href = `/api/backtest/export/${currentJobId}`;
    }
  }

  // Tab switching in the params sidebar
  const paramTabs = document.querySelectorAll("#paramTabs .tab");
  const paramPages = document.querySelectorAll(".params-panel .tab-page");
  paramTabs.forEach(btn => {
    btn.addEventListener("click", () => {
      paramTabs.forEach(b => {
        const active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      });
      paramPages.forEach(p => {
        p.classList.toggle("active", p.dataset.page === btn.dataset.tab);
      });
      document.querySelector(".params-panel").scrollTop = 0;
    });
  });

  // Wire up events
  $("fileUpload").addEventListener("change", handleUpload);
  $("btnGenerate").addEventListener("click", handleGenerate);
  $("btnClear").addEventListener("click", handleClear);
  $("btnDemo").addEventListener("click", handleDemo);
  $("btnExport").addEventListener("click", handleExport);

  $("calPrev").addEventListener("click", () => {
    if (!calendarMonth) return;
    calendarMonth = new Date(Date.UTC(calendarMonth.getUTCFullYear(), calendarMonth.getUTCMonth() - 1, 1));
    renderCalendar(lastResult ? lastResult.trades : null, lastSim ? lastSim.pnl : null);
  });
  $("calNext").addEventListener("click", () => {
    if (!calendarMonth) return;
    calendarMonth = new Date(Date.UTC(calendarMonth.getUTCFullYear(), calendarMonth.getUTCMonth() + 1, 1));
    renderCalendar(lastResult ? lastResult.trades : null, lastSim ? lastSim.pnl : null);
  });

  $("yearPrev").addEventListener("click", () => {
    if (!yearNav) return;
    yearNav--;
    renderMonthBreakdown(lastResult ? lastResult.trades : null, lastSim ? lastSim.pnl : null);
  });
  $("yearNext").addEventListener("click", () => {
    if (!yearNav) return;
    yearNav++;
    renderMonthBreakdown(lastResult ? lastResult.trades : null, lastSim ? lastSim.pnl : null);
  });

  // Stop ladder editor
  function syncLadderEmpty(){
    $("ladderEmpty").style.display = $("ladderRows").children.length === 0 ? "" : "none";
  }

  function addLadderRow(trigger, sl){
    const row = document.createElement("div");
    row.className = "ladder-row";
    row.innerHTML = `
      <input type="number" class="ladder-trigger" min="0.05" step="0.05" value="${trigger}">
      <span class="ladder-arrow">R → stop R</span>
      <input type="number" class="ladder-sl" step="0.05" value="${sl}">
      <button type="button" class="ladder-remove" title="Remove step">×</button>`;
    row.querySelectorAll("input").forEach(el => el.addEventListener("input", updateSummary));
    row.querySelector(".ladder-remove").addEventListener("click", () => {
      row.remove();
      syncLadderEmpty();
      updateSummary();
    });
    $("ladderRows").appendChild(row);
  }

  $("ladderAdd").addEventListener("click", () => {
    addLadderRow(0.5, 0.0);
    syncLadderEmpty();
    updateSummary();
  });

  [[1.0, -0.5]].forEach(([trigger, sl]) => addLadderRow(trigger, sl));
  syncLadderEmpty();

  // Capital simulation: recompute (and re-render) on any change, debounced.
  let simTimer = null;
  const scheduleSim = () => {
    clearTimeout(simTimer);
    simTimer = setTimeout(refreshSim, 400);
  };
  $("capitalEnabled").addEventListener("change", () => {
    scheduleSim();
    $("capitalControls").classList.toggle("disabled", !$("capitalEnabled").checked);
  });
  $("capitalInitial").addEventListener("input", scheduleSim);
  $("capitalRiskPct").addEventListener("input", scheduleSim);
  $("capitalMode").addEventListener("change", scheduleSim);

  // Equity chart hover: crosshair + tooltip with date and cumulative R
  const equityCanvas = $("equityChart");
  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  equityCanvas.closest(".chart-panel").appendChild(tooltip);

  equityCanvas.addEventListener("mousemove", (e) => {
    if (!equityState) return;
    const rect = equityCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const s = equityState;
    const { ctx, w, h, pad, pts } = s;

    // 1:1 crosshair: clamp the mouse x into the plot area, then ride the
    // line segment crossing that x (piecewise-linear interpolation).
    const plotW = w - pad.l - pad.r;
    const cx = Math.min(w - pad.r, Math.max(pad.l, mx));
    const t = pts.length > 1 ? Math.min(pts.length - 1, Math.max(0, (cx - pad.l) / plotW * (pts.length - 1))) : 0;
    const i0 = Math.max(0, Math.floor(t));
    const i1 = Math.min(pts.length - 1, i0 + 1);
    const f = t - i0;
    const iy = pts[i1].y + (pts[i0].y - pts[i1].y) * (1 - f);

    // nearest point for the tooltip data
    let idx = 0, best = Infinity;
    pts.forEach((p, i) => {
      const d = Math.abs(p.x - cx);
      if (d < best){ best = d; idx = i; }
    });
    const p = pts[idx];

    drawEquityChart();
    ctx.save();
    ctx.strokeStyle = "rgba(201,161,59,0.45)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3,3]);
    ctx.beginPath();
    ctx.moveTo(cx, pad.t);
    ctx.lineTo(cx, h - pad.b);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#c9a13b";
    ctx.beginPath();
    ctx.arc(cx, iy, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    if (s.usd){
      const pnl = p.v - s.initCap;
      const pct = s.initCap ? (pnl / s.initCap) * 100 : 0;
      const pnlClass = pnl > 0 ? "pos" : (pnl < 0 ? "neg" : "");
      const pctClass = pct > 0 ? "pos" : (pct < 0 ? "neg" : "");
      tooltip.innerHTML = `<div class="tt-date">${p.date}</div>` +
        `<div class="tt-r">${fmtUsd(p.v)}</div>` +
        `<div class="tt-r ${pnlClass}">${fmtUsdSigned(pnl)}</div>` +
        `<div class="tt-r ${pctClass}">${pct > 0 ? "+" : ""}${pct.toFixed(2)}%</div>`;
    } else {
      const rClass = p.v > 0 ? "pos" : (p.v < 0 ? "neg" : "");
      tooltip.innerHTML = `<div class="tt-date">${p.date}</div>` +
        `<div class="tt-r ${rClass}">${p.v > 0 ? "+" : ""}${p.v.toFixed(2)} R</div>`;
    }
    tooltip.style.display = "block";
    tooltip.style.left = (cx > w - 150 ? cx - 158 : cx + 14) + "px";
    tooltip.style.top = Math.max(4, iy - 28) + "px";
  });

  equityCanvas.addEventListener("mouseleave", () => {
    if (!equityState) return;
    drawEquityChart();
    tooltip.style.display = "none";
  });

  document.querySelectorAll(".params-panel input, .params-panel select").forEach(el => {
    el.addEventListener("input", updateSummary);
  });

  window.addEventListener("resize", () => {
    if ($("resultsSection").style.display !== "none"){
      // no-op: chart redraws on next generate; acceptable for v1
    }
  });

  if (window.lucide) lucide.createIcons();

  updateSummary();
})();
