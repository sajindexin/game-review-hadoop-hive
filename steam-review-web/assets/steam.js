/* ==========================================================================
   steam.js —— 公共逻辑：导航、封面生成、格式化、SVG 图表
   本文件不依赖任何 CDN，离线可用。
   ========================================================================== */

(function () {
  "use strict";

  const SRW = (window.SRW = window.SRW || {});

  /* ------------------------------------------------------------------ 工具 */

  SRW.$ = (sel, root) => (root || document).querySelector(sel);
  SRW.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  SRW.esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  SRW.fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString("zh-CN"));
  SRW.fmtPct = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d) + "%");
  SRW.fmtDelta = (n) => (n == null ? "—" : (n > 0 ? "+" : "") + Number(n).toFixed(2));
  SRW.pad2 = (n) => (n < 10 ? "0" + n : "" + n);

  SRW.param = (key) => new URLSearchParams(location.search).get(key);

  /* 把 yyyy-mm-dd 变成 2023.07.06 这种 Steam 常见写法 */
  SRW.fmtDate = (s) => (s ? String(s).slice(0, 10).replace(/-/g, ".") : "—");
  SRW.fmtTime = (s) => (s ? String(s).slice(0, 16).replace(/-/g, ".") : "—");

  SRW.langName = (code) => {
    const map = {
      all: "全部语种", chinese: "简中 + 繁中", "zh-Hans": "简体中文", "zh-Hant": "繁体中文",
      en: "英语", ru: "俄语", "pt-BR": "葡萄牙语（巴西）", pt: "葡萄牙语", ja: "日语", ko: "韩语",
      de: "德语", es: "西班牙语", fr: "法语", it: "意大利语", pl: "波兰语", tr: "土耳其语",
      th: "泰语", vi: "越南语", uk: "乌克兰语", id: "印尼语", cs: "捷克语", sv: "瑞典语",
      nl: "荷兰语", hu: "匈牙利语", ro: "罗马尼亚语", da: "丹麦语", fi: "芬兰语", no: "挪威语",
      el: "希腊语", bg: "保加利亚语", ms: "马来语", "es-419": "拉美西班牙语",
    };
    return map[code] || code;
  };

  /* ------------------------------------------------------------------ 配色 */

  const PALETTES = [
    ["#2d5f7c", "#16394f", "#66c0f4"], // 海蓝（Rust 深潜）
    ["#6b4a2a", "#2f2114", "#d8a15a"], // 锈色
    ["#3d5c34", "#1c2f18", "#a4d007"], // 星露谷绿
    ["#4a3f66", "#241d33", "#9d8bd6"], // 紫罗兰（艾尔登法环）
    ["#7a3b2e", "#33170f", "#e0a05a"], // 赤金（黑神话）
    ["#33465c", "#171f28", "#7fb2e0"], // 冷灰蓝
    ["#5c4a1f", "#2a2110", "#e6c15a"], // 沙漠
    ["#2f4f4f", "#152525", "#63c7c7"], // 暗青
    ["#5a3247", "#24121c", "#e07fa6"], // 洋红
    ["#3b3b52", "#191922", "#8f8fd0"], // 钢蓝
    ["#274b52", "#10201f", "#5fd0c0"], // 翡翠
    ["#4f3a2f", "#1f1512", "#c99b6c"], // 棕土
  ];

  SRW.palette = (seed) => {
    const s = String(seed || "");
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTES[h % PALETTES.length];
  };

  /* 程序化「封面」：无外链图片，纯 CSS 渐变 + 纹理，保证离线好看 */
  SRW.artStyle = (seed, angle) => {
    const [a, b, c] = SRW.palette(seed);
    const deg = angle || 135;
    return [
      `radial-gradient(120% 90% at 78% 12%, ${c}55 0%, transparent 58%)`,
      `radial-gradient(90% 80% at 12% 92%, ${a}cc 0%, transparent 62%)`,
      `linear-gradient(${deg}deg, ${a} 0%, ${b} 78%, #0d1319 100%)`,
    ].join(",");
  };

  SRW.initialOf = (name) => {
    const s = String(name || "?").trim();
    if (/^[\x00-\x7F]+$/.test(s)) return s.slice(0, 1).toUpperCase();
    return s.slice(0, 1);
  };

  /* ------------------------------------------------------------------ 导航 */

  SRW.renderHeader = function (active) {
    const nav = [
      ["index.html", "商店", "store"],
      ["index.html#patches", "版本", "patches"],
      ["nlp.html", "NLP 洞察", "nlp"],
      ["ask.html", "问答", "ask"],
    ];
    const html = `
      <div class="wrap gh-inner">
        <a class="gh-logo" href="index.html"><span class="mark"></span>STEAM<span style="font-size:11px;font-weight:400;letter-spacing:2px;color:#7b8d9a;margin-left:2px">REVIEW LAB</span></a>
        <nav class="gh-nav">
          ${nav.map(([href, label, key]) =>
            `<a href="${href}" class="${active === key ? "active" : ""}">${label}</a>`).join("")}
        </nav>
        <div class="gh-right">
          <label class="gh-search">
            <span class="icon">⌕</span>
            <input id="globalSearch" type="search" placeholder="搜索游戏或版本…" autocomplete="off">
          </label>
          <a class="gh-link" href="ask.html">数据分析台</a>
        </div>
      </div>`;
    const el = document.getElementById("siteHeader");
    if (el) el.innerHTML = html;

    const input = document.getElementById("globalSearch");
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && input.value.trim()) {
          location.href = "index.html?q=" + encodeURIComponent(input.value.trim());
        }
      });
    }
  };

  SRW.renderSubNav = function (items, meta) {
    const el = document.getElementById("subNav");
    if (!el) return;
    el.innerHTML = `<div class="wrap">
      ${items.map((it) => `<a href="${it.href || "#"}" class="${it.active ? "active" : ""}">${it.label}</a>`).join("")}
      ${meta ? `<span class="spacer"></span><span class="meta">${meta}</span>` : ""}
    </div>`;
  };

  SRW.renderFooter = function (extra) {
    const el = document.getElementById("siteFooter");
    if (!el) return;
    el.innerHTML = `<div class="wrap">
      <div class="row">
        <span>Steam 评测分析台 · 课程大作业演示</span>
        <span>数据源：4,884,580 条清洗评测（Hive / Hadoop）· 26 个版本窗口</span>
        <span>页面样式仅作学习用途，与 Valve 无关</span>
        ${extra || ""}
      </div>
    </div>`;
  };

  /* -------------------------------------------------------------- 数据加载 */

  SRW.loadScript = (src) =>
    new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("加载失败：" + src));
      document.head.appendChild(s);
    });

  SRW.loadIndex = async function () {
    if (!window.SRW_INDEX) await SRW.loadScript("data/index.js");
    return window.SRW_INDEX;
  };

  SRW.loadPatch = async function (patchId) {
    window.SRW_PATCH = null;
    await SRW.loadScript("data/patches/" + encodeURIComponent(patchId) + ".js");
    if (!window.SRW_PATCH) throw new Error("找不到版本数据：" + patchId);
    return window.SRW_PATCH;
  };

  /* NLP 结果包：主题情感切片 + 75 项官方改动统计 */
  SRW.loadNlpTopics = async function () {
    if (!window.SRW_NLP_TOPICS) await SRW.loadScript("data/nlp/topics.js");
    return window.SRW_NLP_TOPICS;
  };

  SRW.loadNlpItems = async function () {
    if (!window.SRW_NLP_ITEMS) await SRW.loadScript("data/nlp/items.js");
    return window.SRW_NLP_ITEMS;
  };

  /* -------------------------------------------------------------- SVG 图表 */

  const NS = "http://www.w3.org/2000/svg";

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  /**
   * 推荐率折线图
   * points: [{offset, rate, reviews, date}]
   */
  SRW.lineChart = function (host, points, opts) {
    opts = opts || {};
    host.innerHTML = "";
    const valid = (points || []).filter((p) => p.rate != null);
    if (!valid.length) {
      host.innerHTML = '<div class="empty">该语种在窗口内没有可用的推荐率数据</div>';
      return;
    }

    const W = 880, H = 250, PL = 44, PR = 14, PT = 14, PB = 30;
    const iw = W - PL - PR, ih = H - PT - PB;
    const svg = el("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}` });

    const defs = el("defs", {});
    const grad = el("linearGradient", { id: "areaFill", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": "#66c0f4", "stop-opacity": "0.32" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": "#66c0f4", "stop-opacity": "0" }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    const rates = valid.map((p) => p.rate);
    let lo = Math.min.apply(null, rates), hi = Math.max.apply(null, rates);
    const pad = Math.max(1.2, (hi - lo) * 0.25);
    lo = Math.max(0, Math.floor((lo - pad) / 5) * 5);
    hi = Math.min(100, Math.ceil((hi + pad) / 5) * 5);
    if (hi - lo < 5) { hi = Math.min(100, lo + 5); }

    const x = (o) => PL + ((o - valid[0].offset) / Math.max(1, valid[valid.length - 1].offset - valid[0].offset)) * iw;
    const y = (v) => PT + ih - ((v - lo) / (hi - lo)) * ih;

    // 网格 + y 轴
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks;
      const yy = y(v);
      svg.appendChild(el("line", { class: "grid-line", x1: PL, y1: yy, x2: W - PR, y2: yy }));
      const t = el("text", { x: PL - 8, y: yy + 3, "text-anchor": "end" });
      t.textContent = v.toFixed(0) + "%";
      svg.appendChild(t);
    }

    // 发布日高亮带（offset 0 前后各半天）
    const zeroX = x(0);
    const half = iw / Math.max(1, valid[valid.length - 1].offset - valid[0].offset) / 2;
    svg.appendChild(el("rect", { class: "release-band", x: zeroX - half, y: PT, width: half * 2, height: ih }));
    svg.appendChild(el("line", { class: "release-line", x1: zeroX, y1: PT, x2: zeroX, y2: PT + ih }));
    const rt = el("text", { x: zeroX, y: PT + 10, "text-anchor": "middle" });
    rt.textContent = "更新日";
    rt.setAttribute("fill", "#66c0f4");
    svg.appendChild(rt);

    // 面积 + 折线
    let dLine = "", dArea = "";
    valid.forEach((p, i) => {
      const px = x(p.offset), py = y(p.rate);
      dLine += (i ? " L" : "M") + px + " " + py;
    });
    dArea = dLine + ` L${x(valid[valid.length - 1].offset)} ${PT + ih} L${x(valid[0].offset)} ${PT + ih} Z`;
    svg.appendChild(el("path", { class: "area", d: dArea }));
    svg.appendChild(el("path", { class: "line", d: dLine }));

    // 数据点
    valid.forEach((p) => {
      svg.appendChild(el("circle", { class: "dot", cx: x(p.offset), cy: y(p.rate), r: 2.6 }));
    });

    // x 轴
    [-14, -10, -5, 0, 5, 10, 14].forEach((o) => {
      const pt = valid.find((p) => p.offset === o);
      if (!pt) return;
      const t = el("text", { x: x(o), y: H - 10, "text-anchor": "middle" });
      t.textContent = o === 0 ? "D0" : (o > 0 ? "+" + o : "" + o);
      svg.appendChild(t);
    });

    host.appendChild(svg);

    // 悬浮提示
    const tip = document.createElement("div");
    tip.className = "tooltip";
    host.appendChild(tip);

    svg.addEventListener("mousemove", (e) => {
      const box = host.getBoundingClientRect();
      const ratio = W / box.width;
      const mx = (e.clientX - box.left) * ratio;
      let best = null, bd = 1e9;
      valid.forEach((p) => {
        const d = Math.abs(x(p.offset) - mx);
        if (d < bd) { bd = d; best = p; }
      });
      if (!best) return;
      tip.innerHTML = `<b>${best.date}</b><br><span class="k">推荐率</span> ${best.rate.toFixed(2)}%
        <br><span class="k">评论</span> ${SRW.fmtInt(best.reviews)} 条`;
      tip.style.left = Math.min(box.width - 130, Math.max(0, (x(best.offset) / ratio) + 12)) + "px";
      tip.style.top = Math.max(0, (y(best.rate) / ratio) - 18) + "px";
      tip.classList.add("on");
      SRW.$$(".dot", svg).forEach((dot) => dot.classList.remove("on"));
    });
    svg.addEventListener("mouseleave", () => tip.classList.remove("on"));
  };

  /** 每日评论量柱状图 */
  SRW.barChart = function (host, points) {
    host.innerHTML = "";
    const valid = points || [];
    if (!valid.length) return;
    const W = 880, H = 130, PL = 44, PR = 14, PT = 10, PB = 26;
    const iw = W - PL - PR, ih = H - PT - PB;
    const max = Math.max.apply(null, valid.map((p) => p.reviews)) || 1;
    const svg = el("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}` });

    for (let i = 0; i <= 2; i++) {
      const yy = PT + (ih * i) / 2;
      svg.appendChild(el("line", { class: "grid-line", x1: PL, y1: yy, x2: W - PR, y2: yy }));
    }
    const bw = iw / valid.length;
    valid.forEach((p, i) => {
      const h = (p.reviews / max) * ih;
      const rect = el("rect", {
        class: "bar" + (p.offset === 0 ? " on" : ""),
        x: PL + i * bw + Math.max(1, bw * 0.12),
        y: PT + ih - h,
        width: Math.max(2, bw * 0.76),
        height: Math.max(1, h),
        rx: 1,
      });
      svg.appendChild(rect);
      const t = el("title", {});
      t.textContent = `${p.date}｜${p.reviews} 条｜推荐率 ${p.rate == null ? "—" : p.rate.toFixed(1) + "%"}`;
      rect.appendChild(t);
    });
    [0, 7, 14, 21, 28].forEach((i) => {
      if (!valid[i]) return;
      const t = el("text", { x: PL + i * bw + bw / 2, y: H - 8, "text-anchor": "middle" });
      t.textContent = valid[i].offset === 0 ? "D0" : (valid[i].offset > 0 ? "+" + valid[i].offset : valid[i].offset);
      svg.appendChild(t);
    });
    host.appendChild(svg);
  };

  SRW.statusText = (delta) => {
    if (delta == null) return "数据不足";
    if (delta <= -5) return "口碑明显下滑";
    if (delta < -1) return "略有回落";
    if (delta <= 1) return "基本持平";
    return "口碑回升";
  };

  /* 真实 Steam 评测页永久链接：/profiles/<steamid>/recommended/<appid>/
     同一用户对同一游戏只有一条推荐，因此 appid 就能定位到这条评测。
     实测该路径返回 200 且标题为「… :: Review for <游戏名>」。 */
  SRW.reviewUrl = (review, appid) => {
    if (!review || !review.steamid) return "";
    const target = appid || review.appid || review.review_id;
    if (!target) return "";
    return (
      "https://steamcommunity.com/profiles/" +
      encodeURIComponent(review.steamid) +
      "/recommended/" +
      encodeURIComponent(target) +
      "/"
    );
  };
})();
