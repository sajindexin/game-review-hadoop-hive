/* 版本详情页 */
(function () {
  "use strict";
  const S = window.SRW;
  const $ = S.$, $$ = S.$$;

  let DATA = null;
  let INDEX = null;
  let lang = "all";
  const filters = { phase: "all", verdict: "all" };
  let shown = 10;

  const UP = "👍", DOWN = "👎";

  function itemTitle(id) {
    const it = (DATA.patch.items || []).find((x) => x.item_id === id);
    return it ? it.title : id;
  }

  function avatarStyle(seed) {
    const [a, b] = S.palette(seed);
    return `background:linear-gradient(135deg,${a},${b})`;
  }

  /* 用评测 ID 末两位做头像文字，比首字母在数字 ID 上更有辨识度 */
  function avatarText(id) {
    const digits = String(id).replace(/\D/g, "");
    return digits ? digits.slice(-2) : "·";
  }

  /* --------------------------------------------------------------- 头部 */

  function renderHead() {
    const p = DATA.patch;
    const g = DATA.stats.all || {};
    document.title = `${p.title_zh || p.patch_name} · ${p.game} · Steam 评测分析台`;

    $("#crumbs").innerHTML = `<a href="index.html">全部游戏</a><span class="sep">›</span>
      <a href="index.html?q=${encodeURIComponent(p.game)}">${S.esc(p.game)}</a><span class="sep">›</span>
      <span>${S.esc(p.title_zh || p.patch_name)}</span>`;

    const cover = $("#cover");
    cover.querySelector(".cover-art").style.background = S.artStyle(p.appid, 120);
    cover.querySelector(".cover-initial").textContent = S.initialOf(p.game);
    cover.querySelector(".kicker").textContent = p.game;
    cover.querySelector(".title").textContent = p.title_zh || p.patch_name;
    cover.querySelector(".sub").textContent = `${p.patch_name} · ${S.fmtDate(p.release_date)}`;

    $("#gameName").textContent = p.game;
    $("#patchTitle").textContent = p.title_zh || p.patch_name;
    $("#patchSub").innerHTML = `${S.esc(p.patch_name)} · 发布于 ${S.fmtDate(p.release_date)} · ${
      DATA.delta_pp == null ? "" : S.statusText(DATA.delta_pp)
    }`;

    const tags = [
      `<span class="tag">${S.esc(p.platform || "PC / Steam")}</span>`,
      `<span class="tag gray">AppID ${S.esc(p.appid)}</span>`,
      `<span class="tag gray">${(p.items || []).length} 项改动</span>`,
      DATA.delta_pp == null
        ? ""
        : `<span class="tag ${DATA.delta_pp >= 0 ? "up" : "down"}">${S.fmtDelta(DATA.delta_pp)} pp</span>`,
    ];
    $("#tagRow").innerHTML = tags.join("");

    const rows = [
      ["更新名称", p.patch_name],
      ["发布日期", S.fmtDate(p.release_date)],
      ["统计窗口", `${S.fmtDate(p.window_start)} — ${S.fmtDate(p.window_end)}（UTC）`],
      ["窗口评论", `${S.fmtInt(DATA.window_reviews_total)} 条（其中 ${S.fmtInt(DATA.pool_reviews)} 条进入样本池）`],
    ];
    $("#metaTable").innerHTML = rows
      .map(([k, v]) => `<div class="meta-row"><span class="k">${k}</span><span class="v">${S.esc(v)}</span></div>`)
      .join("");

    const src = $("#sourceBtn");
    if (p.source_url) src.href = p.source_url;
    else src.style.display = "none";

    $("#windowNote").textContent = p.selection_note || "";

    $("#askBtn").href = "ask.html?patch=" + encodeURIComponent(p.patch_id);
    $("#askFull").href = "ask.html?patch=" + encodeURIComponent(p.patch_id);
  }

  /* --------------------------------------------------------------- 概览 */

  function renderStats() {
    const s = DATA.stats[lang] || {};
    const b = s.before || {}, r = s.release || {}, a = s.after || {};
    const delta = b.rate != null && a.rate != null ? a.rate - b.rate : null;

    const cards = [
      { label: "更新前推荐率", value: S.fmtPct(b.rate, 1), foot: `${S.fmtInt(b.reviews)} 条 · 前 14 天` },
      { label: "更新日推荐率", value: S.fmtPct(r.rate, 1), foot: `${S.fmtInt(r.reviews)} 条 · 当日` },
      { label: "更新后推荐率", value: S.fmtPct(a.rate, 1), foot: `${S.fmtInt(a.reviews)} 条 · 后 14 天` },
      {
        label: "净变化",
        value: S.fmtDelta(delta) + ' <small>pp</small>',
        foot: `Hive 口径 ${S.fmtInt(DATA.window_reviews_total)} 条 · 样本池 ${S.fmtInt(DATA.pool_reviews)} 条`,
        cls: delta == null ? "" : delta >= 0 ? "pos" : "neg",
      },
    ];
    $("#statGrid").innerHTML = cards
      .map(
        (c) => `<div class="stat-card ${c.cls || ""}">
          <div class="label">${c.label}</div>
          <div class="value">${c.value}</div>
          <div class="foot">${S.esc(c.foot)}</div>
        </div>`
      )
      .join("");
  }

  function renderLangChips() {
    const opts = [
      ["all", "全部语种"],
      ["chinese", "简中 + 繁中"],
      ["en", "英语"],
    ];
    $("#langChips").innerHTML = opts
      .map(([k, label]) => `<span class="chip ${lang === k ? "on" : ""}" data-lang="${k}">${label}</span>`)
      .join("");
    $$("[data-lang]").forEach((c) =>
      c.addEventListener("click", () => {
        lang = c.dataset.lang;
        renderLangChips();
        renderStats();
        renderCharts();
      })
    );
  }

  function trendFor(code) {
    if (code === "all") return DATA.trend || [];
    return (DATA.trend_lang || {})[code] || [];
  }

  function renderCharts() {
    const trend = trendFor(lang);
    S.lineChart($("#lineChart"), trend);

    const valid = trend.filter((p) => p.rate != null);
    if (valid.length) {
      const peak = valid.reduce((m, p) => (p.reviews > m.reviews ? p : m), valid[0]);
      const lo = valid.reduce((m, p) => (p.rate < m.rate ? p : m), valid[0]);
      $("#chartFoot").textContent =
        `峰值 ${peak.date}（${S.fmtInt(peak.reviews)} 条）· 低点 ${lo.date}（${S.fmtPct(lo.rate, 1)}）`;
    } else {
      $("#chartFoot").textContent = "该语种样本不足";
    }

    if (lang === "all") S.barChart($("#barChart"), DATA.trend || []);
    else S.barChart($("#barChart"), trend);
  }

  function renderPhases() {
    const s = DATA.stats[lang] || DATA.stats.all || {};
    const defs = [
      ["before", "更新前 14 天"],
      ["release", "更新当日"],
      ["after", "更新后 14 天"],
    ];
    $("#phaseGrid").innerHTML = defs
      .map(([k, name]) => {
        const d = s[k];
        if (!d) return `<div class="phase-card"><div class="name">${name}</div><div class="rate">—</div></div>`;
        return `<div class="phase-card">
          <div class="name">${name}</div>
          <div class="rate">${S.fmtPct(d.rate, 1)}</div>
          <div class="cnt">${S.fmtInt(d.reviews)} 条评价 · 推荐 ${S.fmtInt(d.recommended)}</div>
          <div class="bar-bg"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, d.rate || 0))}%"></div></div>
        </div>`;
      })
      .join("");
  }

  function renderLangRows() {
    const rows = DATA.language_compare || [];
    if (!rows.length) {
      $("#langRows").innerHTML = '<div class="empty">该版本没有可用的分语种统计</div>';
      return;
    }
    $("#langRows").innerHTML = rows
      .map((r) => {
        const cls = r.delta > 0 ? "pos" : r.delta < 0 ? "neg" : "";
        return `<div class="lang-row">
          <span class="lname">${S.esc(S.langName(r.lang))}</span>
          <span class="track">
            <span class="before" style="left:0;width:${r.before_rate}%;opacity:.5"></span>
            <span class="after" style="left:0;width:${r.after_rate}%;opacity:.85"></span>
          </span>
          <span class="delta ${cls}">${S.fmtDelta(r.delta)} pp</span>
        </div>`;
      })
      .join("");
  }

  /* ----------------------------------------------------------- 改动条目 */

  let openItem = null;

  function renderItems() {
    const items = DATA.patch.items || [];
    if (!items.length) {
      $("#itemList").innerHTML = '<div class="empty">该版本没有登记改动条目</div>';
      return;
    }

    $("#itemList").innerHTML = items
      .map((it) => {
        const words = (it.aliases || []).slice(0, 5).map((w) => `<span class="tag gray">${S.esc(w)}</span>`).join("");
        const isOpen = openItem === it.item_id;
        const evi = it.evidence || [];
        const panel = !isOpen
          ? ""
          : `<div class="evi-list">
              <div class="evi-title">
                引文（窗口内高赞命中评测，最多 4 条）
                <span class="muted small">关键词：${S.esc((it.aliases || []).join(" / "))}</span>
              </div>
              ${evi.length ? evi.map(evidenceCard).join("") : '<div class="empty small">该改动在窗口内没有被评测正文提到</div>'}
            </div>`;
        return `<div class="item-block ${isOpen ? "open" : ""}" data-item="${S.esc(it.item_id)}">
          <div class="item-card">
            <div class="idx">${S.esc(it.item_id)}</div>
            <div>
              <div class="t">${S.esc(it.title)}
                <span class="muted small">${S.esc(it.title_en || "")}</span>
              </div>
              <div class="d">${S.esc(it.description || "")}</div>
              <div class="words">
                ${it.section ? `<span class="tag">${S.esc(it.section)}</span>` : ""}
                ${words}
              </div>
            </div>
            <div class="metric">
              <div class="n">${S.fmtInt(it.mentions)}</div>
              <div class="u">窗口提及</div>
              <div class="toggle">${isOpen ? "收起引文 ▲" : "查看引文 ▼"}</div>
            </div>
          </div>
          ${panel}
        </div>`;
      })
      .join("");

    const n = items.filter((i) => i.mentions > 0).length;
    $("#itemNote").textContent =
      `共 ${items.length} 条改动，其中 ${n} 条在窗口内被评测正文直接提及。命中数采用「官方条目 + 别名表」` +
      `做不区分大小写的子串匹配，仅衡量该改动在评测中的能见度，不等价于情感极性；` +
      `多数 Steam 评测不会逐条点评更新内容，因此该数值普遍偏低属于正常现象。点击条目可展开引文并跳转 Steam 原文。`;

    $$("#itemList .item-card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = card.parentElement.dataset.item;
        openItem = openItem === id ? null : id;
        renderItems();
      });
    });
  }

  /* ----------------------------------------------------------- 评测样本 */

  function srcLink(rev, label) {
    const url = S.reviewUrl(rev, (DATA && DATA.patch && DATA.patch.appid) || "");
    if (!url) return `<span class="src-link muted">原文链接不可用</span>`;
    return `<a class="src-link" href="${url}" target="_blank" rel="noopener">${label}</a>`;
  }

  function evidenceCard(e) {
    return `<div class="evi">
      <div class="evi-head">
        <span class="verdict ${e.voted_up ? "up" : "down"}">${e.voted_up ? UP + " 推荐" : DOWN + " 不推荐"}</span>
        <span class="tag gray">${S.esc(S.langName(e.language))}</span>
        <span class="muted">${S.fmtTime(e.created_at)}</span>
        <span class="muted">游玩 ${e.playtime_hours} 小时</span>
        <span class="helpful">${UP} ${S.fmtInt(e.votes_up)}</span>
        <span class="right">${srcLink(e, "在 Steam 打开原文 →")}</span>
      </div>
      <div class="evi-body">${S.esc(e.text)}</div>
    </div>`;
  }

  function reviewCard(c) {
    const phaseName = { before: "更新前", release: "更新日", after: "更新后" }[c.phase];
    const hits = (c.items || []).map((id) => `<span class="tag">${S.esc(itemTitle(id))}</span>`).join("");
    return `<article class="review">
      <div class="avatar" style="${avatarStyle(c.review_id)}">${S.esc(avatarText(c.review_id))}</div>
      <div>
        <div class="head">
          <span class="name">评测者 #${S.esc(String(c.review_id).slice(-4))}</span>
          <span class="verdict ${c.voted_up ? "up" : "down"}">${c.voted_up ? UP + " 推荐" : DOWN + " 不推荐"}</span>
          <span class="tag gray">${S.esc(S.langName(c.language))}</span>
          <span class="tag gray">${phaseName}</span>
          <span class="right muted">${S.fmtTime(c.created_at)}</span>
        </div>
        <div class="body">${S.esc(c.text)}</div>
        <div class="foot">
          <span>游玩 ${c.playtime_hours} 小时</span>
          <span class="helpful">${UP} ${S.fmtInt(c.votes_up)} 人觉得有用</span>
          ${c.votes_funny ? `<span>😄 ${S.fmtInt(c.votes_funny)}</span>` : ""}
          ${hits}
          ${srcLink(c, "在 Steam 打开原文 →")}
        </div>
      </div>
    </article>`;
  }

  function filteredReviews() {
    return (DATA.comments || []).filter((c) => {
      if (filters.phase !== "all" && c.phase !== filters.phase) return false;
      if (filters.verdict === "up" && !c.voted_up) return false;
      if (filters.verdict === "down" && c.voted_up) return false;
      return true;
    });
  }

  function renderReviews() {
    const list = filteredReviews();
    const shownList = list.slice(0, shown);
    const host = $("#reviewList");
    host.innerHTML = shownList.length
      ? shownList.map(reviewCard).join("")
      : '<div class="empty">当前筛选条件下没有评论，换个条件试试</div>';
    $("#reviewCount").textContent = `共 ${list.length} 条样本，已显示 ${shownList.length} 条`;
    $("#loadMore").style.display = shownList.length < list.length ? "" : "none";
  }

  /* ----------------------------------------------------------- 问答入口 */

  function renderAsk() {
    const p = DATA.patch;
    const s = DATA.stats.all || {};
    const delta = DATA.delta_pp;
    $("#askIntro").textContent =
      `已载入 ${p.game}「${p.title_zh || p.patch_name}」的窗口统计与 ${(DATA.comments || []).length} 条评测样本，` +
      `可以直接问推荐率变化、语种差异或改动条目的讨论情况。`;

    const qs = [
      `${p.title_zh || p.patch_name} 更新后口碑变化如何？`,
      `中文玩家对这次更新的评价和英文玩家差多少？`,
      `这次更新里哪一项改动被提到最多？`,
      `帮我找几条不推荐的评测看看问题在哪。`,
    ];
    $("#askChips").innerHTML = qs
      .map(
        (q) =>
          `<a class="chip" href="ask.html?patch=${encodeURIComponent(p.patch_id)}&q=${encodeURIComponent(q)}">${S.esc(q)}</a>`
      )
      .join("");
  }

  /* --------------------------------------------------------------- 交互 */

  function bindTabs() {
    const map = {};
    $$("#tabs button").forEach((btn) => {
      map[btn.dataset.tab] = btn;
      btn.addEventListener("click", () => activate(btn.dataset.tab));
    });
    window.addEventListener("hashchange", () => activate((location.hash || "#overview").slice(1)));

    function activate(key) {
      if (!map[key]) key = "overview";
      $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === key));
      $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + key));
    }
    activate((location.hash || "#overview").slice(1));
  }

  function bindReviews() {
    $$("[data-filter]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const f = chip.dataset.filter;
        filters[f] = chip.dataset.value;
        $$(`[data-filter="${f}"]`).forEach((c) => c.classList.toggle("on", c === chip));
        shown = 10;
        renderReviews();
      });
    });
    $("#loadMore").addEventListener("click", () => {
      shown += 10;
      renderReviews();
    });
  }

  async function boot() {
    S.renderHeader("store");
    S.renderFooter();

    let id = S.param("id");
    try {
      INDEX = await S.loadIndex();
    } catch (e) {
      INDEX = null;
    }
    if (!id && INDEX) id = INDEX.games[0].patches[INDEX.games[0].patches.length - 1].patch_id;

    try {
      DATA = await S.loadPatch(id);
    } catch (err) {
      $("main").innerHTML = `<div class="empty" style="margin-top:40px">
        找不到版本「${S.esc(id)}」的数据。<a href="index.html">返回总览</a></div>`;
      return;
    }

    const game = INDEX && INDEX.games.find((g) => String(g.appid) === String(DATA.patch.appid));
    S.renderSubNav(
      [
        { label: "商店", href: "index.html" },
        { label: "版本更新", href: "index.html#patches" },
        { label: DATA.patch.game, href: "index.html?q=" + encodeURIComponent(DATA.patch.game) },
        { label: DATA.patch.title_zh || DATA.patch.patch_name, active: true },
        { label: "Agent 问答", href: "ask.html?patch=" + encodeURIComponent(DATA.patch.patch_id) },
      ],
      game ? `${game.patches.length} 个版本窗口` : ""
    );

    renderHead();
    renderStats();
    renderLangChips();
    renderCharts();
    renderPhases();
    renderLangRows();
    renderItems();
    renderReviews();
    renderAsk();
    bindTabs();

    const wantItem = S.param("item");
    if (wantItem) {
      openItem = wantItem;
      renderItems();
      location.hash = "#items";
    }
    if (S.param("tab")) location.hash = "#" + S.param("tab");
    bindReviews();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
