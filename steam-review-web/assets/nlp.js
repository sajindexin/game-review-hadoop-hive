/* NLP 洞察页：11 类主题情感分布 + 75 项官方改动统计 */
(function () {
  "use strict";
  const S = window.SRW;
  const $ = S.$, $$ = S.$$;

  let T = null;          // topics.js
  let I = null;          // items.js
  let INDEX = null;

  const state = {
    game: "",            // appid，空=全部
    patch: "",           // patch_id，空=全部版本（全时间线）
    lang: "all",         // all | zh | en
    phase: "all",        // all | before | release | after
    topicSort: "total",  // total | neg
    itemSort: "total",   // total | neg | pos
  };

  const LANGS = ["zh", "en"];
  const LANG_CN = { zh: "中文", en: "英文", all: "中英合计" };
  const PHASE_CN = { all: "全部阶段", before: "更新前 14 天", release: "发布当天", after: "更新后 14 天" };

  const blank = () => ({ positive: 0, negative: 0, neutral: 0 });

  /* ------------------------------------------------------------ 切片计算 */

  /** 合并多个 {positive,negative,neutral} 快照 */
  function merge(into, from) {
    if (!from) return into;
    into.positive += from.positive || 0;
    into.negative += from.negative || 0;
    into.neutral += from.neutral || 0;
    return into;
  }

  function langsToUse() {
    return state.lang === "all" ? LANGS : [state.lang];
  }

  /**
   * 返回当前筛选下的主题分布 {aspect: {positive,negative,neutral}}
   * 口径分两种：选定具体版本 → 窗口内；否则 → 全时间线
   */
  function sliceTopics() {
    const out = {};
    const langs = langsToUse();

    if (state.patch) {
      const p = T.by_patch[state.patch] || {};
      const phase = state.phase;
      langs.forEach((lg) => {
        const bucket = (p[lg] || {})[phase] || {};
        Object.keys(bucket).forEach((a) => {
          out[a] = merge(out[a] || blank(), bucket[a]);
        });
      });
      return out;
    }

    // 全时间线：按游戏或全局
    if (state.game) {
      const g = T.by_game[state.game] || {};
      langs.forEach((lg) => {
        const bucket = g[lg] || {};
        Object.keys(bucket).forEach((a) => {
          out[a] = merge(out[a] || blank(), bucket[a]);
        });
      });
      return out;
    }

    langs.forEach((lg) => {
      const bucket = T.global[lg] || {};
      Object.keys(bucket).forEach((a) => {
        out[a] = merge(out[a] || blank(), bucket[a]);
      });
    });
    return out;
  }

  function totalOf(v) {
    return (v.positive || 0) + (v.negative || 0) + (v.neutral || 0);
  }

  /* ------------------------------------------------------------ 渲染 */

  function renderHero() {
    const dist = sliceTopics();
    let pos = 0, neg = 0, neu = 0, all = 0;
    Object.values(dist).forEach((v) => {
      pos += v.positive;
      neg += v.negative;
      neu += v.neutral;
      all += totalOf(v);
    });

    const pct = (n) => (all ? ((n / all) * 100).toFixed(1) : "0.0");
    $("#heroStat").innerHTML = `
      <div class="hs-main">
        <div class="hs-num">${S.fmtInt(all)}</div>
        <div class="hs-label">主题命中条数</div>
      </div>
      <div class="hs-split">
        <span class="pos"><b>${pct(pos)}%</b>正向 ${S.fmtInt(pos)}</span>
        <span class="neg"><b>${pct(neg)}%</b>负向 ${S.fmtInt(neg)}</span>
        <span class="neu"><b>${pct(neu)}%</b>中性 ${S.fmtInt(neu)}</span>
      </div>`;
  }

  function renderScope() {
    let scope, count;
    if (state.patch) {
      const m = T.patch_meta[state.patch];
      const phaseTxt = state.phase === "all" ? "窗口全段" : PHASE_CN[state.phase];
      scope = `${m.game} · ${m.title_zh || m.patch_name}｜${phaseTxt}（${m.window_start} ~ ${m.window_end}）`;
    } else if (state.game) {
      const g = INDEX.games.find((x) => x.appid === state.game);
      scope = `${g ? g.name : state.game}｜全时间线（不限版本窗口）`;
    } else {
      scope = "全部 12 款游戏｜全时间线（不限版本窗口）";
    }
    const dist = sliceTopics();
    count = Object.values(dist).reduce((s, v) => s + totalOf(v), 0);
    $("#scopeNote").innerHTML =
      `<span class="scope-label">当前口径</span>${S.esc(scope)}` +
      `<span class="scope-sep"></span>语种：${LANG_CN[state.lang]}` +
      `<span class="scope-sep"></span>命中 ${S.fmtInt(count)} 条`;
  }

  function renderTopics() {
    const dist = sliceTopics();
    const aspects = T.aspects;

    const MIN_N = 10; // 负向率排序的最小样本门槛，避免「3 条里 1 条差评=33%」这类噪声

    const rows = aspects
      .map((a) => {
        const v = dist[a.id] || blank();
        const t = totalOf(v);
        const negRate = t ? v.negative / t : 0;
        return Object.assign({}, a, v, { total: t, negRate, thin: t < MIN_N });
      })
      .filter((r) => r.total > 0)
      .sort((x, y) => {
        if (state.topicSort === "neg") {
          // 样本不足的门槛内的主题一律排后面，并保持内部按负向率排
          if (x.thin !== y.thin) return x.thin ? 1 : -1;
          return y.negRate - x.negRate || y.total - x.total;
        }
        return y.total - x.total;
      });

    const max = rows.length ? rows[0].total : 1;
    const grand = rows.reduce((s, r) => s + r.total, 0) || 1;

    $("#topicSub").textContent = `${rows.length} 类主题有命中 · 共 ${S.fmtInt(grand)} 条`;

    if (!rows.length) {
      $("#topicBars").innerHTML = '<div class="empty">当前筛选下没有主题命中，试试放宽版本或阶段</div>';
      return;
    }

    $("#topicBars").innerHTML = rows
      .map((r) => {
        const w = (r.total / max) * 100;
        const p1 = (r.positive / r.total) * 100;
        const p2 = (r.negative / r.total) * 100;
        const p3 = (r.neutral / r.total) * 100;
        return `<div class="topic-row">
          <div class="topic-name" title="${S.esc(r.id)}">${S.esc(r.name_zh)}</div>
          <div class="topic-bar-wrap">
            <div class="topic-bar" style="width:${w.toFixed(2)}%">
              ${p1 > 0 ? `<span class="seg pos" style="width:${p1.toFixed(2)}%" title="正向 ${r.positive}"></span>` : ""}
              ${p2 > 0 ? `<span class="seg neg" style="width:${p2.toFixed(2)}%" title="负向 ${r.negative}"></span>` : ""}
              ${p3 > 0 ? `<span class="seg neu" style="width:${p3.toFixed(2)}%" title="中性 ${r.neutral}"></span>` : ""}
            </div>
          </div>
          <div class="topic-nums">
            <span class="n-total">${S.fmtInt(r.total)}</span>
            <span class="pos">${S.fmtInt(r.positive)}</span>
            <span class="neg">${S.fmtInt(r.negative)}</span>
            <span class="neu">${S.fmtInt(r.neutral)}</span>
            <span class="n-rate ${r.negRate >= 0.5 && !r.thin ? "hot" : ""}${r.thin ? " thin" : ""}"
                  title="${r.thin ? "样本仅 " + r.total + " 条，负向率不稳定" : "负向率"}">${(r.negRate * 100).toFixed(0)}%</span>
          </div>
        </div>`;
      })
      .join("");
  }

  /* 75 项官方改动 */
  function renderItems() {
    let rows = (I.items || []).slice();

    // 游戏 / 版本筛选
    if (state.game) rows = rows.filter((x) => x.appid === state.game);
    if (state.patch) rows = rows.filter((x) => x.patch_id === state.patch);

    rows.sort((x, y) => {
      if (state.itemSort === "neg") return y.sentiment.negative - x.sentiment.negative || y.total - x.total;
      if (state.itemSort === "pos") return y.sentiment.positive - x.sentiment.positive || y.total - x.total;
      return y.total - x.total;
    });

    const inScope = rows.length;
    const hit = rows.filter((r) => r.total > 0).length;
    $("#itemSub").textContent = `${inScope} 项在范围内 · ${hit} 项有评论命中`;

    $("#itemNote").innerHTML =
      `条目情感统计合计 <b>${S.fmtInt(I.items.reduce((s, x) => s + x.total, 0))}</b> 条，` +
      `以中文评论为主（1,585 / 1,593，英文仅 8 条），因此<b>不提供语种细分</b>；` +
      `「窗口内」指命中该条目的评论落在其所属版本的前后 14 天窗口内。`;

    const tbody = $("#itemTable tbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="empty">当前筛选下没有官方改动</div></td></tr>';
      return;
    }

    tbody.innerHTML = rows
      .map((r, i) => {
        const s = r.sentiment;
        const win = (r.bucket.pre_14d || 0) + (r.bucket.release_day || 0) + (r.bucket.post_14d || 0);
        const zero = r.total === 0;
        return `<tr class="${zero ? "row-zero" : ""}">
          <td class="muted">${i + 1}</td>
          <td>
            <span class="item-name">${S.esc(r.title)}</span>
            <span class="item-id">${S.esc(r.item_id)}</span>
          </td>
          <td class="muted small">${S.esc(r.game)}<br><span class="muted">${S.esc(r.patch_title_zh || r.patch_name)} · ${S.fmtDate(r.release_date)}</span></td>
          <td class="num pos">${zero ? "—" : S.fmtInt(s.positive)}</td>
          <td class="num neg">${zero ? "—" : S.fmtInt(s.negative)}</td>
          <td class="num neu">${zero ? "—" : S.fmtInt(s.neutral)}</td>
          <td class="num strong">${zero ? "0" : S.fmtInt(r.total)}</td>
          <td class="num muted">${win || "0"}</td>
        </tr>`;
      })
      .join("");
  }

  function renderMethod() {
    $("#method").innerHTML = `
      <ul class="method-list">
        <li><b>第一层（通用主题）</b>：11 类主题为 <code>content / level_design / multiplayer / graphics / performance / story / controls / monetization / cheating / audio / community</code>，
            一条评论可命中多个主题，逐条标注 positive / negative / neutral 并保留证据子句。</li>
        <li><b>第二层（官方改动）</b>：把同一批评论按官方别名匹配到 75 项改动，
            窗口按<b>条目自身所属版本</b>计算（pre_14d / release_day / post_14d），不按评论日期去猜同游戏其它版本。</li>
        <li><b>语种</b>：中文语料 ${S.fmtInt(T.sources.zh)} 行、英文语料 ${S.fmtInt(T.sources.en)} 行，分两套规则词表分别标注；
            英文结果不覆盖俄语、日语等其它语种。</li>
        <li><b>阶段口径</b>：选定具体版本时按该版本窗口切分；不选版本时展示全时间线（2010–2026），两者不可直接对比样本量。</li>
        <li><b>中性偏多</b>：规则标注要求命中情感词才判正/负，只提到主题但无明确褒贬的评论记为中性，因此中性占比天然偏高。</li>
      </ul>`;
  }

  /* ------------------------------------------------------------ 交互 */

  function fillSelectors() {
    const gsel = $("#fGame");
    gsel.innerHTML =
      '<option value="">全部游戏</option>' +
      INDEX.games
        .map((g) => `<option value="${g.appid}">${S.esc(g.name)}</option>`)
        .join("");

    syncPatchOptions();
  }

  function syncPatchOptions() {
    const psel = $("#fPatch");
    const list = [];
    Object.entries(T.patch_meta).forEach(([pid, m]) => {
      if (state.game && m.appid !== state.game) return;
      list.push({ pid, m });
    });
    list.sort((a, b) => (a.m.release_date < b.m.release_date ? 1 : -1));

    psel.innerHTML =
      '<option value="">全部版本（全时间线）</option>' +
      list
        .map(
          (x) =>
            `<option value="${x.pid}">${S.esc(x.m.game)} · ${S.esc(x.m.title_zh || x.m.patch_name)}（${S.fmtDate(x.m.release_date)}）</option>`
        )
        .join("");

    // 若当前选中的版本已不在列表里，清空
    if (state.patch && !list.some((x) => x.pid === state.patch)) {
      state.patch = "";
    }
    psel.value = state.patch;
    updateResetBtn();
  }

  function updateResetBtn() {
    const active = state.game || state.patch || state.lang !== "all" || state.phase !== "all";
    $("#fReset").hidden = !active;
  }

  function refresh() {
    renderHero();
    renderScope();
    renderTopics();
    renderItems();
    updateResetBtn();
    // 阶段只有在选定版本时才有意义
    const dis = !state.patch;
    $$("#fPhase .chip").forEach((b) => {
      b.disabled = dis;
      b.classList.toggle("dim", dis);
    });
  }

  function bind() {
    $("#fGame").addEventListener("change", (e) => {
      state.game = e.target.value;
      syncPatchOptions();
      refresh();
    });

    $("#fPatch").addEventListener("change", (e) => {
      state.patch = e.target.value;
      if (state.patch) {
        const m = T.patch_meta[state.patch];
        if (m && !state.game) {
          state.game = m.appid;
          $("#fGame").value = m.appid;
          syncPatchOptions();
          $("#fPatch").value = state.patch;
        }
      }
      refresh();
    });

    $$("#fLang .chip").forEach((b) =>
      b.addEventListener("click", () => {
        state.lang = b.dataset.lang;
        $$("#fLang .chip").forEach((x) => x.classList.toggle("on", x === b));
        refresh();
      })
    );

    $$("#fPhase .chip").forEach((b) =>
      b.addEventListener("click", () => {
        if (!state.patch) return;
        state.phase = b.dataset.phase;
        $$("#fPhase .chip").forEach((x) => x.classList.toggle("on", x === b));
        refresh();
      })
    );

    $$("[data-sort]").forEach((b) =>
      b.addEventListener("click", () => {
        state.topicSort = b.dataset.sort;
        $$("[data-sort]").forEach((x) => x.classList.toggle("on", x === b));
        renderTopics();
      })
    );

    $$("[data-isort]").forEach((b) =>
      b.addEventListener("click", () => {
        state.itemSort = b.dataset.isort;
        $$("[data-isort]").forEach((x) => x.classList.toggle("on", x === b));
        renderItems();
      })
    );

    $("#fReset").addEventListener("click", () => {
      state.game = "";
      state.patch = "";
      state.lang = "all";
      state.phase = "all";
      $("#fGame").value = "";
      syncPatchOptions();
      $$("#fLang .chip").forEach((x) => x.classList.toggle("on", x.dataset.lang === "all"));
      $$("#fPhase .chip").forEach((x) => x.classList.toggle("on", x.dataset.phase === "all"));
      refresh();
    });
  }

  async function boot() {
    S.renderHeader("nlp");
    S.renderSubNav(
      [
        { label: "主题情感", href: "#", active: true },
        { label: "官方改动", href: "#itemTable" },
        { label: "口径说明", href: "#method" },
        { label: "回版本页", href: "index.html" },
      ],
      "数据更新于 2026.11.27"
    );
    S.renderFooter();

    try {
      [T, I, INDEX] = await Promise.all([S.loadNlpTopics(), S.loadNlpItems(), S.loadIndex()]);
    } catch (err) {
      $("main").innerHTML = `<div class="empty" style="margin-top:40px">NLP 数据加载失败：${S.esc(err.message)}</div>`;
      return;
    }

    // 支持 ?patch= / ?game= / ?lang= / ?phase= 深链
    // 注意：下拉框选项由 fillSelectors() 动态生成，必须先生成再回填 value
    const p = S.param("patch");
    if (p && T.patch_meta[p]) {
      state.patch = p;
      state.game = T.patch_meta[p].appid;
    }
    const g = S.param("game");
    if (g && INDEX.games.some((x) => x.appid === g)) {
      state.game = g;
    }
    const lg = S.param("lang");
    if (lg && ["all", "zh", "en"].includes(lg)) {
      state.lang = lg;
      $$("#fLang .chip").forEach((x) => x.classList.toggle("on", x.dataset.lang === lg));
    }
    const ph = S.param("phase");
    if (ph && ["all", "before", "release", "after"].includes(ph)) state.phase = ph;

    fillSelectors();
    // 回填下拉框显示值
    $("#fGame").value = state.game || "";
    syncPatchOptions();
    $("#fPatch").value = state.patch || "";
    $$("#fPhase .chip").forEach((x) => x.classList.toggle("on", x.dataset.phase === state.phase));

    bind();
    refresh();
    renderMethod();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
