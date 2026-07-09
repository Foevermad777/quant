const state = {
  data: null,
  auto: true,
  timer: null,
  refreshMs: 5000,
};

const moneyFormatters = {
  CNY: new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }),
  USD: new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }),
};

document.getElementById("refreshBtn").addEventListener("click", () => loadOverview());
document.getElementById("autoBtn").addEventListener("click", (event) => {
  state.auto = !state.auto;
  event.currentTarget.setAttribute("aria-pressed", state.auto ? "true" : "false");
  event.currentTarget.textContent = state.auto ? "自动 5s" : "手动";
  schedule();
});

window.addEventListener("resize", () => {
  if (state.data) {
    drawExecutorChart("cnChart", state.data.executors.cn);
    drawExecutorChart("usChart", state.data.executors.us);
  }
});

loadOverview();
schedule();

function schedule() {
  if (state.timer) {
    clearInterval(state.timer);
  }
  state.timer = state.auto ? setInterval(loadOverview, state.refreshMs) : null;
}

async function loadOverview() {
  const subtitle = document.getElementById("subtitle");
  try {
    const response = await fetch("/api/overview", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    state.data = await response.json();
    render(state.data);
  } catch (error) {
    subtitle.textContent = `刷新失败: ${error.message}`;
  }
}

function render(data) {
  document.getElementById("subtitle").textContent = `${data.project_root} · ${data.generated_at}`;
  renderMetrics(data);
  renderScan(data.scan);
  renderAnalysisPool(data.scan);
  renderExecutor("cn", data.executors.cn);
  renderExecutor("us", data.executors.us);
  renderEvents(data.executors);
  renderLogs(data.logs);
  labelTables();
}

function renderMetrics(data) {
  const scan = data.scan;
  const cn = data.executors.cn;
  const us = data.executors.us;
  const metrics = [
    {
      label: "扫盘记录",
      value: scan.counts.analysis_history ?? 0,
      detail: `24h ${scan.counts.analysis_24h ?? 0} · ${formatTime(scan.latest_analysis_at)}`,
    },
    {
      label: "活跃信号",
      value: scan.counts.active_signals ?? 0,
      detail: `总计 ${scan.counts.decision_signals ?? 0} · ${formatTime(scan.latest_signal_at)}`,
    },
    {
      label: "A股账本",
      value: formatMoney(latestValue(cn), cn.currency),
      detail: `${formatPct(cn.return_rate)} · ${formatTime(cn.latest_activity_at)}`,
    },
    {
      label: "美股账本",
      value: formatMoney(latestValue(us), us.currency),
      detail: `${formatPct(us.return_rate)} · ${formatTime(us.latest_activity_at)}`,
    },
  ];
  document.getElementById("metricGrid").innerHTML = metrics
    .map((item) => `<article class="metric"><small>${escapeHtml(item.label)}</small><strong>${escapeHtml(String(item.value))}</strong><span>${escapeHtml(item.detail)}</span></article>`)
    .join("");
}

function renderScan(scan) {
  document.getElementById("scanStamp").textContent = scan.available ? formatTime(scan.latest_signal_at) : "unavailable";
  document.getElementById("analysisStamp").textContent = scan.available ? formatTime(scan.latest_analysis_at) : "unavailable";

  const strip = document.getElementById("signalStrip");
  const grouped = scan.active_signals_by_market_action || [];
  strip.innerHTML = grouped.length
    ? grouped.map((row) => `<span class="pill ${cssToken(row.action)}">${escapeHtml(row.market)} · ${escapeHtml(row.action)} · ${row.count}</span>`).join("")
    : `<span class="empty">暂无活跃信号</span>`;

  document.getElementById("signalsTable").innerHTML = tableRows(scan.recent_signals || [], (row) => [
    row.stock_code,
    row.market,
    badge(row.action),
    row.score ?? row.confidence ?? "--",
    row.plan_quality ?? "--",
    badge(row.status),
    formatShortTime(row.created_at),
  ]);
}

function renderAnalysisPool(scan) {
  const items = [
    ...(scan.pool_analysis?.cn || []).map((row) => ({ ...row, market: "cn" })),
    ...(scan.pool_analysis?.us || []).map((row) => ({ ...row, market: "us" })),
  ];
  document.getElementById("analysisPool").innerHTML = items.length
    ? items
        .map((row) => {
          const advice = row.operation_advice || "--";
          return `
            <article class="pool-item">
              <header>
                <strong>${escapeHtml(row.code)}</strong>
                <span class="pill ${cssToken(advice)}">${escapeHtml(row.market)} · ${escapeHtml(advice)}</span>
              </header>
              <p>${escapeHtml(row.name || "--")} · 分数 ${escapeHtml(row.sentiment_score ?? "--")} · ${escapeHtml(formatTime(row.created_at))}</p>
              <p>${escapeHtml(row.analysis_summary || "暂无摘要")}</p>
            </article>
          `;
        })
        .join("")
    : `<p class="empty">暂无池内分析</p>`;
}

function renderExecutor(key, executor) {
  document.getElementById(`${key}Activity`).textContent = executor.available ? formatTime(executor.latest_activity_at) : "unavailable";
  drawExecutorChart(`${key}Chart`, executor);

  const root = document.getElementById(`${key}Executor`);
  const account = executor.account || {};
  const snapshot = executor.latest_snapshot || {};
  const stats = [
    ["现金", formatMoney(account.cash ?? snapshot.cash, executor.currency)],
    ["市值", formatMoney(snapshot.market_value ?? 0, executor.currency)],
    ["总权益", formatMoney(snapshot.total_value ?? account.cash, executor.currency)],
    ["收益率", formatPct(executor.return_rate)],
  ];
  const positions = executor.positions || [];
  const discipline = executor.discipline || {};
  root.innerHTML = `
    <div class="stat-row">
      ${stats.map(([label, value]) => `<div class="stat"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
    <div class="status-strip">
      <span class="pill">${escapeHtml(String(executor.counts.trades ?? 0))} 成交</span>
      <span class="pill">${escapeHtml(String(executor.counts.order_attempts ?? 0))} 尝试</span>
      <span class="pill">${escapeHtml(String(executor.counts.signal_events ?? 0))} 事件</span>
      <span class="pill ${discipline.available ? "pass" : ""}">${discipline.available ? "G5 ready" : "G5 none"}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>数量</th>
            <th>成本</th>
            <th>止损</th>
            <th>目标</th>
            <th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          ${
            positions.length
              ? tableRows(positions, (row) => [row.stock_code, row.quantity, formatNumber(row.avg_cost), formatNumber(row.stop_loss), formatNumber(row.target_price), formatShortTime(row.updated_at)])
              : `<tr><td colspan="6" class="empty">暂无持仓</td></tr>`
          }
        </tbody>
      </table>
    </div>
  `;
}

function renderEvents(executors) {
  const rows = [
    ...(executors.cn.recent_events || []).map((row) => ({ ...row, market: "cn" })),
    ...(executors.us.recent_events || []).map((row) => ({ ...row, market: "us" })),
  ].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))).slice(0, 16);
  document.getElementById("eventsTable").innerHTML = rows.length
    ? tableRows(rows, (row) => [row.market, row.event_date, row.stock_code, badge(row.event_type), row.reason])
    : `<tr><td colspan="5" class="empty">暂无执行事件</td></tr>`;
}

function renderLogs(logs) {
  const names = {
    dsa_daily: "DSA",
    cn_executor: "A股执行",
    cn_g5: "A股 G5",
    us_dsa_daily: "美股 DSA",
    us_executor: "美股执行",
    us_g5: "美股 G5",
  };
  const entries = Object.entries(names).map(([key, label]) => ({ key, label, log: logs[key] || {} }));
  const latest = entries.map((item) => item.log.mtime).filter(Boolean).sort().pop();
  document.getElementById("logStamp").textContent = formatTime(latest);
  document.getElementById("logGrid").innerHTML = entries
    .map(({ label, log }) => {
      const tail = log.available ? (log.tail || []).join("\n") : "unavailable";
      return `<article class="log-box"><h3>${escapeHtml(label)}</h3><pre>${escapeHtml(tail)}</pre></article>`;
    })
    .join("");
}

function drawExecutorChart(canvasId, executor) {
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.floor(180 * ratio);
  ctx.scale(ratio, ratio);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fbfcfe";
  ctx.fillRect(0, 0, width, height);

  const series = executor.portfolio_series || [];
  const values = series.map((row) => Number(row.total_value)).filter(Number.isFinite);
  if (!values.length) {
    ctx.fillStyle = "#647084";
    ctx.font = "13px sans-serif";
    ctx.fillText("暂无权益曲线", 16, 34);
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const left = 42;
  const right = 16;
  const top = 18;
  const bottom = 28;
  const plotW = width - left - right;
  const plotH = height - top - bottom;

  ctx.strokeStyle = "#d9dee8";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = top + (plotH * i) / 3;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(width - right, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#0f766e";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = left + (plotW * index) / Math.max(1, values.length - 1);
    const y = top + plotH - ((value - min) / span) * plotH;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();

  ctx.fillStyle = "#647084";
  ctx.font = "12px sans-serif";
  ctx.fillText(formatCompact(max, executor.currency), 8, top + 4);
  ctx.fillText(formatCompact(min, executor.currency), 8, top + plotH);
}

function tableRows(rows, columns) {
  if (!rows.length) {
    return "";
  }
  return rows
    .map((row) => `<tr>${columns(row).map((value) => `<td>${value && value.__html ? value.__html : escapeHtml(value ?? "--")}</td>`).join("")}</tr>`)
    .join("");
}

function labelTables() {
  document.querySelectorAll("table").forEach((table) => {
    const headers = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      [...row.children].forEach((cell, index) => {
        if (headers[index]) {
          cell.setAttribute("data-label", headers[index]);
        }
      });
    });
  });
}

function badge(value) {
  const text = value ?? "--";
  return { __html: `<span class="pill ${cssToken(text)}">${escapeHtml(text)}</span>` };
}

function latestValue(executor) {
  return executor.latest_snapshot?.total_value ?? executor.account?.cash ?? null;
}

function formatMoney(value, currency) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  const formatter = moneyFormatters[currency] || moneyFormatters.CNY;
  return formatter.format(Number(value));
}

function formatCompact(value, currency) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  const number = Number(value);
  const prefix = currency === "USD" ? "$" : "¥";
  if (Math.abs(number) >= 1000000) {
    return `${prefix}${(number / 1000000).toFixed(1)}m`;
  }
  if (Math.abs(number) >= 1000) {
    return `${prefix}${(number / 1000).toFixed(0)}k`;
  }
  return `${prefix}${number.toFixed(0)}`;
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  return Number(value).toFixed(2);
}

function formatPct(value) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatTime(value) {
  if (!value) {
    return "--";
  }
  return String(value).replace("T", " ").replace(/\.\d+/, "").replace(/\+.*$/, "");
}

function formatShortTime(value) {
  if (!value) {
    return "--";
  }
  const normalized = formatTime(value);
  const match = normalized.match(/^\d{4}-(\d{2}-\d{2})\s+(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : normalized;
}

function cssToken(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
