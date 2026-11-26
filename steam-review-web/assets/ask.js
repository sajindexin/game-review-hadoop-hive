/* Agent 问答页：默认本地规则引擎，可选切到 Servlet /api/ask */
(function () {
  "use strict";
  const S = window.SRW;
  const $ = S.$, $$ = S.$$;

  /* ---------------------------------------------------------------- 配置 */

  // 用 http:// 打开时默认走 server.py 的 /api/ask（联调模式），file:// 直开则回退本地规则引擎
  S.useBackend = /^https?:$/.test(location.protocol);
  S.backendBase = "";                         // 例如 "http://localhost:8080/review-api"

  let INDEX = null;
  let DATA = null;
  let busy = false;

  /* ------------------------------------------------------------ 渲染工具 */

  function bubbleHtml(html) {
    return `<div class="msg bot">
      <div class="who">AI</div>
      <div class="bubble">${html}</div>
    </div>`;
  }

  function userHtml(text) {
    return `<div class="msg user">
      <div class="who">你</div>
      <div class="bubble">${S.esc(text)}</div>
    </div>`;
  }

  function push(html) {
    const body = $("#chatBody");
    body.insertAdjacentHTML("beforeend", html);
    body.scrollTop = body.scrollHeight;
  }

  /* 回答比较长，落到该条消息顶部，方便看到结论而不是引文尾部 */
  function focusLast(selector) {
    const body = $("#chatBody");
    const nodes = body.querySelectorAll(selector);
    const last = nodes[nodes.length - 1];
    if (!last) return;
    const top = last.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
    body.scrollTop = Math.max(0, top - 8);
  }

  function typing() {
    push(`<div class="msg bot" id="typing">
      <div class="who">AI</div>
      <div class="bubble"><span class="muted">正在检索窗口统计与评测样本…</span></div>
    </div>`);
  }

  function stopTyping() {
    const t = document.getElementById("typing");
    if (t) t.remove();
  }

  const INTENT_LABEL = {
    overall: "综合口碑",
    language: "语种差异",
    items: "改动条目",
    trend: "趋势峰值",
    negative: "负面引文",
    evidence: "玩家原话",
  };

  const EVIDENCE_LABEL = {
    verified_quotes: "引文已逐条复核",
    mixed_evidence: "已复核 + 数据集原文",
    dataset_quotes: "数据集窗口样本（未复核）",
    none: "本次无引文",
  };

  const pct = (v) => (v == null ? "—" : v.toFixed(1) + "%");
  const pp = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + " pp");

  function quote(c) {
    const text = c.text.length > 240 ? c.text.slice(0, 240) + "…" : c.text;

    // 接通检索 Agent 后，后端会给出 detail（已复核 · 更新后 14 天 · zh-Hans · 推荐），信息比本地拼的更全
    const src = c.detail
      ? `评测者 #${String(c.review_id).slice(-4)} · ${c.detail}`
      : `评测者 #${String(c.review_id).slice(-4)} · ${S.langName(c.language)} · ${
          c.voted_up ? "推荐" : "不推荐"
        } · ${S.fmtTime(c.created_at)} · ${S.fmtInt(c.votes_up || 0)} 人觉得有用`;

    const badge = c.verified
      ? `<span class="verified-badge" title="${S.esc(c.verification || "已复核")}">已复核</span>`
      : `<span class="unverified-badge">未复核</span>`;

    const url = c.url || S.reviewUrl(c, (DATA && DATA.patch && DATA.patch.appid) || "");
    const link = url
      ? `<a class="src-link" href="${url}" target="_blank" rel="noopener">在 Steam 打开原文 →</a>`
      : "";

    return `<div class="quote">${badge}${S.esc(text)}<span class="src">${S.esc(src)}　${link}</span></div>`;
  }

  function kv(pairs) {
    return `<div class="kv">${pairs.map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("")}</div>`;
  }

  /* ---------------------------------------------------------- 本地应答器 */

  function localAnswer(q) {
    const p = DATA.patch;
    const all = DATA.stats.all || {};
    const b = all.before || {}, r = all.release || {}, a = all.after || {};
    const delta = DATA.delta_pp;
    const lower = q.toLowerCase();
    const has = (...ws) => ws.some((w) => lower.includes(w));

    const head = `<p><b>${S.esc(p.game)} · ${S.esc(p.title_zh || p.patch_name)}</b>（${S.fmtDate(
      p.release_date
    )} 上线）</p>`;

    // 1) 不推荐 / 差评 / 问题
    if (has("不推荐", "差评", "负面", "问题", "抱怨", "吐槽", "喷")) {
      const downs = (DATA.comments || []).filter((c) => !c.voted_up);
      if (!downs.length) return head + `<p>这个版本的抽样里没有抓到不推荐的评测，可能是样本都偏正面。</p>`;
      const pick = downs.slice().sort((x, y) => y.votes_up - x.votes_up).slice(0, 3);
      const themes = {};
      pick.forEach((c) => (c.items || []).forEach((id) => (themes[id] = (themes[id] || 0) + 1)));
      const themeTxt = Object.keys(themes).length
        ? `<p>这些负面评测里被点到的改动条目：${Object.keys(themes)
            .map((id) => S.esc((p.items.find((i) => i.item_id === id) || {}).title || id))
            .join("、")}。</p>`
        : `<p>负评基本是整体体验评价，没有集中指向某一项改动。</p>`;
      return `${head}
        <p>窗口内推荐率从前 14 天的 <b>${pct(b.rate)}</b> 到后 14 天的 <b>${pct(a.rate)}</b>（${pp(delta)}），
        下面是抽样中赞数较高的不推荐评测：</p>${themeTxt}
        ${pick.map(quote).join("")}
        <p class="muted small">口径：窗口内 ${S.fmtInt((b.reviews || 0) + (r.reviews || 0) + (a.reviews || 0))} 条有效评测抽样，
        未做情感模型推断，仅按 voted_up 区分。</p>`;
    }

    // 2) 语种对比
    if (has("语种", "中文", "英文", "语言", "地区", "简中", "繁中")) {
      const rows = (DATA.language_compare || []).filter((x) => x.lang !== "all");
      if (!rows.length) return head + "<p>这个版本缺少分语种统计。</p>";
      const worst = rows.slice().sort((x, y) => x.delta - y.delta)[0];
      const best = rows.slice().sort((x, y) => y.delta - x.delta)[0];
      const cn = rows.find((x) => x.lang === "chinese");
      return `${head}
        <p>分语种看，变化最大的是 <b>${S.langName(worst.lang)}</b>（${pp(worst.delta)}），
        相对最好的是 <b>${S.langName(best.lang)}</b>（${pp(best.delta)}）。</p>
        ${kv([
          ["简中 + 繁中", cn ? `${pct(cn.before_rate)} → ${pct(cn.after_rate)}（${pp(cn.delta)}）` : "样本不足"],
          ["英语", (() => { const e = rows.find((x) => x.lang === "en"); return e ? `${pct(e.before_rate)} → ${pct(e.after_rate)}（${pp(e.delta)}）` : "样本不足"; })()],
          ["窗口评论", S.fmtInt((b.reviews || 0) + (r.reviews || 0) + (a.reviews || 0))],
        ])}
        <p class="muted small">注意：小语种窗口样本可能只有几十条，波动大，不宜直接下结论。</p>`;
    }

    // 3) 改动条目
    if (has("改动", "条目", "更新内容", "哪一项", "改了什么", "内容", "提及")) {
      const items = (p.items || []).slice().sort((x, y) => y.mentions - x.mentions);
      if (!items.length) return head + "<p>这个版本没有登记改动条目。</p>";
      const top = items.slice(0, 4);
      return `${head}
        <p>这个版本登记了 <b>${items.length}</b> 项改动，按窗口内评测正文的提及次数排序：</p>
        <ul>${top
          .map(
            (i) =>
              `<li>${S.esc(i.title)}（${S.esc(i.section || "—")}）· 提及 ${S.fmtInt(i.mentions)} 次${
                i.description ? ` — ${S.esc(i.description)}` : ""
              }</li>`
          )
          .join("")}</ul>
        <p class="muted small">提及数是关键词近似匹配（官方条目 + 别名表），只能说明「有没有被说」，不代表「说好还是说坏」。</p>`;
    }

    // 4) 趋势 / 时机
    if (has("趋势", "峰值", "什么时候", "时间", "哪一天", "评论量", "波动")) {
      const trend = (DATA.trend || []).filter((x) => x.rate != null);
      if (!trend.length) return head + "<p>窗口内没有可用趋势数据。</p>";
      const peak = trend.reduce((m, x) => (x.reviews > m.reviews ? x : m), trend[0]);
      const low = trend.reduce((m, x) => (x.rate < m.rate ? x : m), trend[0]);
      return `${head}
        <p>评论量峰值出现在 <b>${peak.date}</b>（${S.fmtInt(peak.reviews)} 条，推荐率 ${pct(peak.rate)}），
        推荐率低点出现在 <b>${low.date}</b>（${pct(low.rate)}）。</p>
        ${kv([
          ["更新前均值", pct(b.rate)],
          ["更新当日", pct(r.rate)],
          ["更新后均值", pct(a.rate)],
        ])}
        <p class="muted small">窗口为 −14 ～ +14 天（UTC），更新当天单独成段，避免发布日峰值稀释前后对比。</p>`;
    }

    // 5) 默认：整体口碑
    const status = S.statusText(delta);
    const sample = (DATA.comments || []).filter((c) => (c.items || []).length).slice(0, 2);
    const fallback = sample.length ? sample : (DATA.comments || []).slice(0, 2);
    return `${head}
      <p>结论：<b>${status}</b>。更新前 14 天推荐率 ${pct(b.rate)}（${S.fmtInt(b.reviews)} 条），
      更新当日 ${pct(r.rate)}（${S.fmtInt(r.reviews)} 条），更新后 14 天 ${pct(a.rate)}（${S.fmtInt(
      a.reviews
    )} 条），净变化 <b>${pp(delta)}</b>。</p>
      ${kv([
        ["窗口评论", S.fmtInt((b.reviews || 0) + (r.reviews || 0) + (a.reviews || 0))],
        ["改动条目", (p.items || []).length + " 项"],
        ["样本量", (DATA.comments || []).length + " 条"],
      ])}
      ${fallback.map(quote).join("")}
      <p class="muted small">可以继续问：中文玩家怎么看、这次改了哪些内容、哪条改动被讨论最多。</p>`;
  }

  /* -------------------------------------------------------- 后端调用（可选） */

  async function backendAnswer(q) {
    const url = (S.backendBase || "") + "/api/ask";
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch_id: DATA.patch.patch_id, question: q }),
    });
    if (!res.ok) throw new Error("接口返回 " + res.status);
    const json = await res.json();
    // 约定返回：{ answer: "markdown/html 文本", citations: [{review_id, text}], stats: {...} }
    const cites = (json.citations || []).map(quote).join("");
    const st = json.stats || {};
    const chips = [
      ["更新前", pct(st.before_rate)],
      ["更新当日", pct(st.release_rate)],
      ["更新后", pct(st.after_rate)],
      ["净变化", pp(st.delta_pp)],
      ["识别意图", INTENT_LABEL[json.intent] || "综合口碑"],
    ]
      .map(([k, v]) => `<span>${k} <b>${v}</b></span>`)
      .join("");
    const evLabel = json.evidence_status && json.evidence_status !== "none"
      ? EVIDENCE_LABEL[json.evidence_status]
      : "";
    const srcNote = json.source
      ? `<div class="src-badge">来源：检索 Agent（agent-retrieval）${evLabel ? " · " + evLabel : ""}</div>`
      : "";
    const fbNote = json.fallback
      ? `<div class="src-badge warn">检索 Agent 不可用，已回退本地规则引擎</div>`
      : "";
    return `${fbNote}<div>${json.answer || ""}</div>
      ${srcNote}
      <div class="kv">${chips}</div>
      ${cites}`;
  }

  /* ---------------------------------------------------------------- 交互 */

  async function ask(text) {
    if (!text || busy) return;
    busy = true;
    push(userHtml(text));
    $("#askInput").value = "";
    typing();

    let html;
    try {
      html = S.useBackend ? await backendAnswer(text) : localAnswer(text);
    } catch (err) {
      html = `<p>问答接口暂时不可用（${S.esc(err.message)}），已回退到本地规则引擎。</p>` + localAnswer(text);
    }
    await new Promise((r) => setTimeout(r, 260));
    stopTyping();
    push(bubbleHtml(html));
    focusLast(".msg.bot");
    busy = false;
  }

  function renderSuggestions() {
    const p = DATA.patch;
    const base = [
      `${p.title_zh || p.patch_name} 更新后口碑变化如何？`,
      "中文玩家和英文玩家的评价差多少？",
      "这次更新里哪一项改动被提到最多？",
      "帮我找几条不推荐的评测看看问题在哪。",
      "评论量峰值出现在哪一天？",
    ];
    $("#suggestions").innerHTML = base
      .map((q) => `<button class="q" data-q="${S.esc(q)}">${S.esc(q)}</button>`)
      .join("");
    $$("#suggestions .q").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
  }

  function renderContext() {
    const p = DATA.patch;
    const all = DATA.stats.all || {};
    const b = all.before || {}, a = all.after || {};
    $("#ctxLine").textContent = `${p.game}｜${p.patch_name}｜窗口 ${S.fmtDate(p.window_start)} — ${S.fmtDate(
      p.window_end
    )}`;
    $("#ctxCard").innerHTML = [
      `版本：<b style="color:#fff">${S.esc(p.title_zh || p.patch_name)}</b>`,
      `发布：${S.fmtDate(p.release_date)}`,
      `更新前推荐率：${pct(b.rate)}（${S.fmtInt(b.reviews)} 条）`,
      `更新后推荐率：${pct(a.rate)}（${S.fmtInt(a.reviews)} 条）`,
      `净变化：${pp(DATA.delta_pp)}`,
      `改动条目：${(p.items || []).length} 项`,
      `抽样评测：${(DATA.comments || []).length} 条`,
    ]
      .map((x) => `<div>${x}</div>`)
      .join("");
    $("#modeTag").textContent = S.useBackend ? "接口模式（/api/ask）" : "本地规则引擎";
  }

  function renderSelect() {
    const sel = $("#patchSelect");
    sel.innerHTML = INDEX.games
      .map(
        (g) => `<optgroup label="${S.esc(g.name)}">${g.patches
          .map(
            (p) =>
              `<option value="${p.patch_id}" ${p.patch_id === DATA.patch.patch_id ? "selected" : ""}>${S.esc(
                p.title_zh || p.patch_name
              )}（${p.release_date}）</option>`
          )
          .join("")}</optgroup>`
      )
      .join("");
    sel.addEventListener("change", () => {
      location.href = "ask.html?patch=" + encodeURIComponent(sel.value);
    });
  }

  function greet() {
    const p = DATA.patch;
    push(
      bubbleHtml(`<p>已载入 <b>${S.esc(p.game)} · ${S.esc(p.title_zh || p.patch_name)}</b> 的版本窗口数据。</p>
      <p>我可以回答推荐率变化、分语种差异、改动条目讨论度，也能把支撑结论的评测原文摘出来。下方示例问题可直接点。</p>`)
    );
    const q = S.param("q");
    if (q) setTimeout(() => ask(q), 320);
  }

  async function boot() {
    S.renderHeader("ask");
    S.renderFooter();
    S.renderSubNav(
      [
        { label: "商店", href: "index.html" },
        { label: "版本更新", href: "index.html#patches" },
        { label: "Agent 问答", active: true },
      ],
      "本地数据包 · 4,884,580 条评测"
    );

    let id = S.param("patch");
    try {
      INDEX = await S.loadIndex();
    } catch (e) {
      INDEX = null;
    }
    if (!id) {
      if (!INDEX) {
        $("#chatBody").innerHTML = '<div class="empty">数据加载失败，请确认 data 目录存在</div>';
        return;
      }
      id = INDEX.games[0].patches[0].patch_id;
    }

    try {
      DATA = await S.loadPatch(id);
    } catch (err) {
      $("#chatBody").innerHTML = `<div class="empty">找不到版本数据「${S.esc(id)}」，<a href="index.html">返回总览</a></div>`;
      return;
    }

    if (INDEX) renderSelect();
    renderContext();
    renderSuggestions();
    greet();

    $("#askForm").addEventListener("submit", (e) => {
      e.preventDefault();
      ask($("#askInput").value.trim());
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
