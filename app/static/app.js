(function(){
  "use strict";

  let currentDatasetId = null;
  let currentJobId = null;

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

  function readParams(){
    return {
      dataset_id: currentDatasetId,
      session_open_utc: $("sessionOpen").value || "00:00",
      range_minutes: parseInt($("rangeMinutes").value, 10),
      breakout_search_minutes: parseInt($("searchMinutes").value, 10),
      entry_mode: $("entryMode").value,
      sl_pct_of_range: parseFloat($("slPct").value),
      tp_rr: parseFloat($("tpRR").value),
      sl_move_on_half_tp: $("slMove").value,
      spread_pips: parseFloat($("spreadPips").value),
      slippage_pips: parseFloat($("slippagePips").value),
      session_max_hours: parseFloat($("sessionMaxHours").value),
      skip_weekends: $("skipWeekends").checked,
    };
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
      (p.sl_move_on_half_tp !== "none"
        ? `When price reaches 50% of the target, the stop moves to <b>${p.sl_move_on_half_tp === "breakeven" ? "breakeven" : "half risk"}</b>. `
        : "") +
      `Trades are force-closed after <b>${p.session_max_hours}h</b> if neither level is hit. ` +
      (p.skip_weekends ? "Weekends are skipped." : "Weekends are included.");
  }

  function showDatasetMeta(data){
    const range = `${(data.start||"").slice(0,10)} → ${(data.end||"").slice(0,10)}`;
    const el = $("datasetMeta");
    if (el){ el.textContent = `${data.symbol} · ${data.rows.toLocaleString()} bars · ${range} UTC`; }
    setStatus(`${data.symbol} · ${data.rows.toLocaleString()} bars · ${range}`, true);
  }

  async function handleUpload(e){
    const file = e.target.files[0];
    if (!file) return;

    const wrap = $("uploadProgressWrap");
    const bar = $("uploadProgressBar");
    const label = $("uploadProgressLabel");
    wrap.hidden = false;
    bar.style.width = "0%";
    label.textContent = "Uploading… 0%";

    try{
      const form = new FormData();
      form.append("file", file);
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
    } catch(err){
      // A failed upload must never leave the previous dataset active: the
      // Generate button would silently re-run the OLD data and the report
      // would appear unchanged, as if the upload had succeeded.
      currentDatasetId = null;
      $("btnGenerate").disabled = true;
      $("datasetMeta").textContent = "Upload failed — no dataset loaded";
      setStatus("Upload failed", false);
      alert("Error: " + err.message);
    } finally {
      wrap.hidden = true;
    }
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

  function renderStats(stats){
    const totalRClass = stats.total_r > 0 ? "pos" : (stats.total_r < 0 ? "neg" : "neutral");
    const avgRClass = stats.average_r > 0 ? "pos" : (stats.average_r < 0 ? "neg" : "neutral");

    const cards = [
      statCardHtml("Total trades", stats.total_trades, "neutral"),
      statCardHtml("Win rate", fmtPct(stats.win_rate) + (stats.win_rate_no_be !== null ? ` <span class="stat-sub">ex-BE ${fmtPct(stats.win_rate_no_be)}</span>` : ""), "neutral"),
      statCardHtml("Total R", (stats.total_r > 0 ? "+" : "") + fmt(stats.total_r), totalRClass),
      statCardHtml("EV (Avg R / trade)", (stats.average_r > 0 ? "+" : "") + fmt(stats.average_r), avgRClass,
        `<span class="stat-sub stat-verdict">${evVerdict(stats.expectancy_r)}</span>`),
      statCardHtml("Profit factor", stats.profit_factor !== null ? fmt(stats.profit_factor) : "—", "neutral"),
      statCardHtml("Max drawdown", fmt(stats.max_drawdown_r) + "R", "neg"),
      statCardHtml("Longest win streak", stats.longest_win_streak, "pos"),
      statCardHtml("Longest loss streak", stats.longest_loss_streak, "neg"),
    ];
    $("statGrid").innerHTML = cards.join("");
  }

  function renderDirectionBreakdown(bd){
    const rows = ["long", "short"].map(dir => {
      const d = bd[dir];
      const wr = d.win_rate !== null ? fmtPct(d.win_rate) : "—";
      return `<div class="breakdown-row">
        <span class="label">${dir}</span>
        <span class="value">${d.trades} trades · ${wr} win rate</span>
      </div>`;
    });
    $("directionBreakdown").innerHTML = rows.join("");
  }

  function renderDowBreakdown(dow){
    const order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
    const keys = Object.keys(dow).sort((a,b) => order.indexOf(a) - order.indexOf(b));
    if (keys.length === 0){
      $("dowBreakdown").innerHTML = `<div class="breakdown-row"><span class="label">No trades</span></div>`;
      return;
    }
    const rows = keys.map(k => {
      const d = dow[k];
      const rClass = d.total_r > 0 ? "pos" : (d.total_r < 0 ? "neg" : "");
      return `<div class="breakdown-row">
        <span class="label">${k}</span>
        <span class="value ${rClass}">${d.trades} trades · ${(d.total_r>0?'+':'')}${fmt(d.total_r)}R</span>
      </div>`;
    });
    $("dowBreakdown").innerHTML = rows.join("");
  }

  function renderTable(trades){
    const tbody = document.querySelector("#tradeTable tbody");
    tbody.innerHTML = trades.map(t => {
      const rClass = t.r_multiple > 0 ? "r-pos" : (t.r_multiple < 0 ? "r-neg" : "");
      const dirClass = t.direction === "long" ? "dir-long" : "dir-short";
      return `<tr>
        <td>${t.date}</td>
        <td class="${dirClass}">${t.direction}</td>
        <td>${fmt(t.range_low)}–${fmt(t.range_high)}</td>
        <td>${fmt(t.entry_price)}</td>
        <td>${t.sl_moved ? fmt(t.sl_price) + " → " + fmt(t.sl_final) : fmt(t.sl_price)}</td>        <td>${fmt(t.tp_price)}</td>
        <td>${fmt(t.exit_price)}</td>
        <td><span class="reason-tag">${t.exit_reason}</span></td>
        <td class="${rClass}">${t.r_multiple > 0 ? '+' : ''}${fmt(t.r_multiple)}</td>
      </tr>`;
    }).join("");
  }

  let equityState = null;

  function renderEquityCurve(curve){
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
    if (curve.length === 0){
      ctx.fillStyle = "#565f70";
      ctx.font = "12px 'JetBrains Mono'";
      ctx.fillText("No trades to plot", 20, h/2);
      return;
    }

    const pad = { l: 44, r: 16, t: 16, b: 24 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const values = curve.map(p => p.cumulative_r);
    const minV = Math.min(0, ...values);
    const maxV = Math.max(0, ...values);
    const pts = curve.map((p, i) => ({
      x: pad.l + (i / Math.max(1, curve.length - 1)) * plotW,
      y: maxV === minV ? pad.t + plotH/2
                       : pad.t + plotH - ((p.cumulative_r - minV) / (maxV - minV)) * plotH,
      date: p.date,
      r: p.cumulative_r,
    }));

    equityState = { ctx, w, h, pad, pts, minV, maxV };
    drawEquityChart();
  }

  function drawEquityChart(){
    const s = equityState;
    if (!s) return;
    const { ctx, w, h, pad, pts } = s;
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    ctx.clearRect(0,0,w,h);

    const zeroY = pad.t + plotH - ((0 - s.minV) / (s.maxV - s.minV)) * plotH;
    ctx.strokeStyle = "#232937";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, zeroY);
    ctx.lineTo(w - pad.r, zeroY);
    ctx.stroke();

    ctx.fillStyle = "#565f70";
    ctx.font = "10px 'JetBrains Mono'";
    ctx.fillText(s.maxV.toFixed(1), 4, pad.t + 8);
    ctx.fillText(s.minV.toFixed(1), 4, h - pad.b);

    const finalPositive = pts[pts.length - 1].r >= 0;
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

  async function handleGenerate(){
    if (!currentDatasetId){
      alert("Load a dataset first.");
      return;
    }
    const btn = $("btnGenerate");
    btn.disabled = true;
    btn.classList.add("loading");
    btn.querySelector(".btn-label").textContent = "Running backtest…";

    try{
      const params = readParams();
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
            const pct = msg.total ? Math.min(99, Math.round(msg.done / msg.total * 100)) : 0;
            progressBar.style.width = pct + "%";
            progressLabel.textContent = "Backtesting… " + pct + "%";
          } else if (msg.type === "done"){
            progressBar.style.width = "100%";
            progressLabel.textContent = "Backtesting… 100%";
            data = msg.result;
          } else if (msg.type === "error"){
            throw new Error(msg.detail || "Backtest failed");
          }
        }
      }
      if (!data) throw new Error("Backtest produced no result");

      currentJobId = data.job_id;

      $("emptyState").style.display = "none";
      $("resultsSection").style.display = "flex";

      renderStats(data.stats);
      renderDirectionBreakdown(data.breakdown_direction);
      renderDowBreakdown(data.breakdown_day_of_week);
      renderTable(data.trades);
      renderEquityCurve(data.equity_curve);
    } catch(e){
      alert("Error: " + e.message);
    } finally {
      btn.disabled = false;
      btn.classList.remove("loading");
      btn.querySelector(".btn-label").textContent = "Generate backtest";
      $("progressWrap").hidden = true;
      $("progressBar").style.width = "0%";
    }
  }

  function handleExport(){
    if (!currentJobId){ return; }
    window.location.href = `/api/backtest/export/${currentJobId}`;
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
  $("btnExport").addEventListener("click", handleExport);

  // Equity chart hover: crosshair + tooltip with date and cumulative R
  const equityCanvas = $("equityChart");
  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  equityCanvas.closest(".chart-panel").appendChild(tooltip);

  equityCanvas.addEventListener("mousemove", (e) => {
    if (!equityState) return;
    const rect = equityCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const { ctx, w, h, pad, pts } = equityState;
    let idx = 0, best = Infinity;
    pts.forEach((p, i) => {
      const d = Math.abs(p.x - mx);
      if (d < best){ best = d; idx = i; }
    });
    const p = pts[idx];

    drawEquityChart();
    ctx.save();
    ctx.strokeStyle = "rgba(201,161,59,0.45)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3,3]);
    ctx.beginPath();
    ctx.moveTo(p.x, pad.t);
    ctx.lineTo(p.x, h - pad.b);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#c9a13b";
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    const rClass = p.r > 0 ? "pos" : (p.r < 0 ? "neg" : "");
    tooltip.innerHTML = `<div class="tt-date">${p.date}</div><div class="tt-r ${rClass}">${p.r > 0 ? "+" : ""}${p.r.toFixed(2)} R</div>`;
    tooltip.style.display = "block";
    tooltip.style.left = (p.x > w - 150 ? p.x - 158 : p.x + 14) + "px";
    tooltip.style.top = Math.max(4, p.y - 28) + "px";
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
