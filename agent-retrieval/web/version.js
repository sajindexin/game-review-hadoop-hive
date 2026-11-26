const app = document.getElementById("app");
const patchId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
const pct = (x) => (x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`);
const pp = (x) => (x === null || x === undefined ? "-" : `${x >= 0 ? "+" : ""}${x.toFixed(1)} pp`);
const num = (x) => (x || 0).toLocaleString();

async function boot() {
  const res = await fetch(`/api/patches/${encodeURIComponent(patchId)}`);
  if (!res.ok) {
    app.textContent = "版本不存在";
    return;
  }
  const data = await res.json();
  const m = data.metrics.all || {};
  app.innerHTML = "";

  const head = el("div", "head");
  head.appendChild(el("h1", null, `${data.game.game_name} · ${data.patch.patch_name}`));
  const back = el("a", "back", "← 返回问答");
  back.href = "/";
  head.appendChild(back);
  app.appendChild(head);
  app.appendChild(el("div", "note", `发布日期 ${data.patch.release_date} · 窗口 ${data.patch.window_start} ~ ${data.patch.window_end}`));

  const grid = el("div", "grid3");
  [["更新前 14 天", pct(m.before_rate), `${num(m.before_reviews)} 条`],
   ["更新后 14 天", pct(m.after_rate), `${num(m.after_reviews)} 条`],
   ["变化", pp(m.change_percentage_points), "百分点"]].forEach(([k, v, s]) => {
    const box = el("div", "big");
    box.appendChild(el("div", "k", k));
    box.appendChild(el("div", "v", v));
    box.appendChild(el("div", "s", s));
    grid.appendChild(box);
  });
  app.appendChild(grid);

  const panel = el("div", "panel");
  panel.appendChild(el("div", "card-title", `官方改动（${data.items.length} 项）`));
  data.items.forEach((it) => {
    const row = el("div", "row");
    row.appendChild(el("div", "t", it.title));
    row.appendChild(el("div", "d", it.description));
    if (it.source_url) {
      const a = el("a", null, "官方公告");
      a.href = it.source_url;
      a.target = "_blank";
      row.appendChild(a);
    }
    panel.appendChild(row);
  });
  app.appendChild(panel);

  const langs = Object.keys(data.metrics);
  if (langs.length > 1) {
    const lp = el("div", "panel");
    lp.appendChild(el("div", "card-title", "分语种推荐率"));
    langs.forEach((g) => {
      const mm = data.metrics[g];
      const row = el("div", "row");
      row.appendChild(el("div", "t", { all: "全部", chinese: "中文", en: "英文" }[g] || g));
      row.appendChild(el("div", "d", `${pct(mm.before_rate)} → ${pct(mm.after_rate)}（${num(mm.before_reviews)} / ${num(mm.after_reviews)} 条）`));
      row.appendChild(el("div", "s", pp(mm.change_percentage_points)));
      lp.appendChild(row);
    });
    app.appendChild(lp);
  }

  // 玩家原文：/versions/{id}?tab=feedback 或 ?item=xxx 时定位到这里
  const qp = new URLSearchParams(location.search);
  const itemFilter = qp.get("item");
  const quotesPanel = el("div", "panel");
  quotesPanel.id = "quotes";
  quotesPanel.appendChild(el("div", "card-title", itemFilter ? `玩家原文（${itemFilter}）` : "玩家原文（已复核）"));
  app.appendChild(quotesPanel);

  try {
    const vres = await fetch("/api/retrieve/verified", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "", patch_id: patchId, item_id: itemFilter || undefined, top_k: 20 }),
    }).then((r) => r.json());
    const quotes = vres.quotes || [];
    if (!quotes.length) {
      quotesPanel.appendChild(el("div", "note", "该版本（或该改动）暂无已复核引文。"));
    } else {
      quotesPanel.appendChild(el("div", "note",
        `显示 ${quotes.length} 条 / 共 ${vres.available ?? quotes.length} 条，来自上游版本分析模块已复核引文库；点标题可回 Steam 原页。`));
      quotes.forEach((q) => {
        const card = el("div", "quote");
        const head = el("div", "quote-head");
        head.appendChild(el("span", "quote-item", q.item_title || q.item_id || ""));
        head.appendChild(el("span", "badge verified", "已复核"));
        head.appendChild(el("span", "quote-meta", `${q.language_std} · ${(q.created_at || "").slice(0, 10)} · ${q.voted_up ? "推荐" : "不推荐"}`));
        card.appendChild(head);
        card.appendChild(el("div", "quote-text", `“${q.text}”`));
        if (q.url) {
          const a = el("a", "quote-link", "查看原文");
          a.href = q.url;
          a.target = "_blank";
          card.appendChild(a);
        }
        quotesPanel.appendChild(card);
      });
    }
  } catch (err) {
    quotesPanel.appendChild(el("div", "note", `原文加载失败：${err.message}`));
  }

  if (qp.get("tab") === "feedback") {
    quotesPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  app.appendChild(el("div", "note", "数据版本 v1 · 指标来自上游版本分析模块 Hive 窗口统计，改动目录来自官方公告。"));
}

boot().catch((err) => {
  app.textContent = `加载失败：${err.message}`;
});
