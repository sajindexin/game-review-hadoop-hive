/* 首页：焦点更新 + 游戏列表（带筛选） + 版本表 */
(function () {
  "use strict";
  const S = window.SRW;
  const $ = S.$, $$ = S.$$;

  let INDEX = null;
  let sortKey = "date";          // 版本表排序
  let gFilter = "all";           // 游戏列表口碑筛选
  let gSort = "reviews";         // 游戏列表排序
  let keyword = (S.param("q") || "").trim();

  const allPatches = () => {
    const out = [];
    INDEX.games.forEach((g) =>
      g.patches.forEach((p) => out.push(Object.assign({ game: g.name, appid: g.appid }, p)))
    );
    return out;
  };

  const patchHref = (id) => "patch.html?id=" + encodeURIComponent(id);

  function deltaClass(v) {
    if (v == null) return "";
    return v > 0 ? "pos" : v < 0 ? "neg" : "";
  }

  /* ------------------------------------------------------------ 头图 */
  function renderHero() {
    const list = allPatches();
    const latest = list.slice().sort((a, b) => (a.release_date < b.release_date ? 1 : -1))[0];
    const hero = $("#heroMain");
    hero.href = patchHref(latest.patch_id);
    hero.querySelector(".art").style.background = S.artStyle(latest.appid, 120);
    hero.querySelector("h1").textContent = latest.title_zh || latest.patch_name;
    hero.querySelector("p").textContent =
      `${latest.game} · ${latest.patch_name}｜${S.fmtDate(latest.release_date)} 上线，窗口内 ${S.fmtInt(latest.window_reviews)} 条评测，` +
      `推荐率 ${S.fmtPct(latest.before_rate, 1)} → ${S.fmtPct(latest.after_rate, 1)}（${S.fmtDelta(latest.delta_pp)} pp）`;

    const side = $("#heroSide");
    const picks = list
      .slice()
      .sort((a, b) => Math.abs(b.delta_pp || 0) - Math.abs(a.delta_pp || 0))
      .slice(0, 3);
    side.innerHTML = picks
      .map(
        (p) => `<a class="mini-card" href="${patchHref(p.patch_id)}">
          <span class="thumb" style="background:${S.artStyle(p.appid, 160)}"></span>
          <span>
            <span class="t">${S.esc(p.title_zh || p.patch_name)}</span>
            <span class="s">${S.esc(p.game)} · ${S.fmtDelta(p.delta_pp)} pp · ${S.fmtDate(p.release_date)}</span>
          </span>
        </a>`
      )
      .join("");
  }

  /* ------------------------------------------------------------ 游戏列表 */
  function visibleGames() {
    const kw = keyword.toLowerCase();
    let games = INDEX.games.filter((g) => {
      if (gFilter === "down" && !(g.avg_delta != null && g.avg_delta <= -1)) return false;
      if (gFilter === "up" && !(g.avg_delta != null && g.avg_delta >= 1)) return false;
      if (gFilter === "flat" && !(g.avg_delta != null && Math.abs(g.avg_delta) < 1)) return false;
      if (!kw) return true;
      return (
        g.name.toLowerCase().includes(kw) ||
        g.patches.some((p) => ((p.title_zh || "") + " " + p.patch_name).toLowerCase().includes(kw))
      );
    });

    if (gSort === "reviews") games.sort((a, b) => b.reviews_total - a.reviews_total);
    if (gSort === "delta") games.sort((a, b) => (a.avg_delta || 0) - (b.avg_delta || 0));
    if (gSort === "name") games.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
    return games;
  }

  function renderGames() {
    const games = visibleGames();
    const total = INDEX.games.reduce((s, g) => s + g.patches.length, 0);
    $("#gameCount").textContent = `${games.length} / ${INDEX.games.length} 款游戏 · 共 ${total} 个版本窗口`;

    const active = gFilter !== "all" || !!keyword;
    $("#filterHint").textContent = active
      ? `筛选后 ${games.length} 款 · ${games.reduce((s, g) => s + g.patches.length, 0)} 个版本`
      : "共 12 款游戏，点击胶囊进入版本详情";
    $("#clearFilter").hidden = !active;

    const grid = $("#gameGrid");
    if (!games.length) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1">没有匹配「${S.esc(keyword)}」的游戏，换个关键词试试</div>`;
      return;
    }

    grid.innerHTML = games
      .map((g) => {
        const d = g.avg_delta;
        const cls = d == null ? "gray" : d >= 0 ? "up" : "down";
        const latest = g.patches[g.patches.length - 1];
        return `<a class="capsule" href="${patchHref(g.latest)}">
          <span class="art" style="background:${S.artStyle(g.appid, 135)}"></span>
          <span class="initial">${S.esc(S.initialOf(g.name))}</span>
          <span class="badge">${g.patches.length} 个版本</span>
          <span class="txt">
            <span class="n">${S.esc(g.name)}</span>
            <span class="m">${S.fmtInt(g.reviews_total)} 条评测 · 平均 ${S.fmtDelta(d)} pp</span>
            <span class="m"><span class="tag ${cls}">${S.statusText(d)}</span> 最新：${S.esc(
              latest.title_zh || latest.patch_name
            )}</span>
          </span>
        </a>`;
      })
      .join("");
  }

  /* ------------------------------------------------------------ 版本表 */
  function renderTable() {
    const kw = keyword.toLowerCase();
    let rows = allPatches().filter(
      (p) =>
        !kw ||
        p.game.toLowerCase().includes(kw) ||
        (p.patch_name || "").toLowerCase().includes(kw) ||
        (p.title_zh || "").toLowerCase().includes(kw)
    );

    if (sortKey === "date") rows.sort((a, b) => (a.release_date < b.release_date ? 1 : -1));
    if (sortKey === "impact") rows.sort((a, b) => (a.delta_pp || 0) - (b.delta_pp || 0));
    if (sortKey === "volume") rows.sort((a, b) => b.window_reviews - a.window_reviews);

    const tbody = $("#patchTable tbody");
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="8"><div class="empty">没有匹配「${S.esc(keyword)}」的版本</div></td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((p, i) => {
        const cls = deltaClass(p.delta_pp);
        return `<tr data-href="${patchHref(p.patch_id)}">
          <td class="muted">${i + 1}</td>
          <td>${S.esc(p.game)}</td>
          <td><span style="color:#fff">${S.esc(p.title_zh || p.patch_name)}</span>
            <span class="muted small"> ${S.esc(p.patch_name !== p.title_zh ? p.patch_name : "")}</span></td>
          <td class="muted">${S.fmtDate(p.release_date)}</td>
          <td class="num">${S.fmtPct(p.before_rate, 1)}</td>
          <td class="num">${S.fmtPct(p.after_rate, 1)}</td>
          <td class="num ${cls}">${S.fmtDelta(p.delta_pp)} pp</td>
          <td class="num muted">${S.fmtInt(p.window_reviews)}</td>
        </tr>`;
      })
      .join("");
  }

  /* --------------------------------------------------------------- 交互 */
  function bind() {
    $$("[data-gfilter]").forEach((btn) =>
      btn.addEventListener("click", () => {
        gFilter = btn.dataset.gfilter;
        $$("[data-gfilter]").forEach((b) => b.classList.toggle("on", b === btn));
        renderGames();
      })
    );

    $$("[data-gsort]").forEach((btn) =>
      btn.addEventListener("click", () => {
        gSort = btn.dataset.gsort;
        $$("[data-gsort]").forEach((b) => b.classList.toggle("on", b === btn));
        renderGames();
      })
    );

    $$("[data-sort]").forEach((btn) =>
      btn.addEventListener("click", () => {
        sortKey = btn.dataset.sort;
        $$("[data-sort]").forEach((b) => b.classList.toggle("on", b === btn));
        renderTable();
      })
    );

    const local = $("#localSearch");
    local.value = keyword;
    let timer = null;
    local.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        keyword = local.value.trim();
        renderGames();
        renderTable();
        syncUrl();
      }, 160);
    });

    $("#clearFilter").addEventListener("click", () => {
      keyword = "";
      gFilter = "all";
      local.value = "";
      $$("[data-gfilter]").forEach((b) => b.classList.toggle("on", b.dataset.gfilter === "all"));
      renderGames();
      renderTable();
      syncUrl();
    });

    $("#patchTable").addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-href]");
      if (tr) location.href = tr.dataset.href;
    });
  }

  function syncUrl() {
    const u = new URL(location.href);
    if (keyword) u.searchParams.set("q", keyword);
    else u.searchParams.delete("q");
    history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
  }

  async function boot() {
    S.renderHeader("store");
    S.renderSubNav(
      [
        { label: "商店", href: "index.html", active: true },
        { label: "版本更新", href: "#patches" },
        { label: "Agent 问答", href: "ask.html" },
      ],
      keyword ? `筛选：${keyword}` : "数据更新于 2026.11.24"
    );
    S.renderFooter();

    try {
      INDEX = await S.loadIndex();
    } catch (err) {
      $("main").innerHTML = `<div class="empty" style="margin-top:40px">数据加载失败：${S.esc(err.message)}</div>`;
      return;
    }
    renderHero();
    renderGames();
    renderTable();
    bind();

    const gi = $("#globalSearch");
    if (gi && keyword) gi.value = keyword;
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
