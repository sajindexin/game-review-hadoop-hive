const $ = (sel) => document.querySelector(sel);
const state = { games: [], patches: [], current: null, loading: false };

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pct(x) {
  return x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`;
}

function pp(x) {
  if (x === null || x === undefined) return "-";
  return `${x >= 0 ? "+" : ""}${x.toFixed(1)} pp`;
}

async function boot() {
  const meta = await api("/api/meta");
  $("#meta").textContent = `${meta.docs.toLocaleString()} 条窗口评测样本 · 检索 ${meta.encoder.name}`;
  const data = await api("/api/games");
  state.games = data.games;
  const gameSel = $("#game");
  state.games.forEach((g) => gameSel.appendChild(new Option(g.game_name, g.appid)));
  gameSel.addEventListener("change", () => loadPatches(gameSel.value));
  await loadPatches(state.games[0].appid);
}

async function loadPatches(appid) {
  state.loading = true;
  let data;
  try {
    data = await api(`/api/games/${appid}/patches`);
  } catch (err) {
    state.loading = false;
    throw err;
  }
  state.patches = data.patches;
  const sel = $("#patch");
  sel.innerHTML = "";
  state.patches.forEach((p) => sel.appendChild(new Option(`${p.patch_name}（${p.release_date}）`, p.patch_id)));
  sel.onchange = () => loadPatch(sel.value);
  if (!state.patches.length) {
    $("#patchCard").textContent = "该游戏没有版本数据";
    state.loading = false;
    return;
  }
  const last = state.patches[state.patches.length - 1].patch_id;
  sel.value = last;           // 下拉框选中值必须和实际加载的版本一致
  await loadPatch(last);
}

async function loadPatch(patchId) {
  $("#patch").value = patchId;
  state.loading = true;
  let data;
  try {
    data = await api(`/api/patches/${patchId}`);
  } catch (err) {
    if ($("#patch").value === patchId) state.loading = false;
    throw err;
  }
  if ($("#patch").value !== patchId) return;   // 异步切换后丢弃旧响应
  state.current = data;
  state.loading = false;
  const m = data.metrics.all || {};
  const card = $("#patchCard");
  card.innerHTML = "";
  card.appendChild(el("div", "card-title", `${data.game.game_name} · ${data.patch.patch_name}`));
  const grid = el("div", "stats");
  [["更新前", pct(m.before_rate), `${(m.before_reviews || 0).toLocaleString()} 条`],
   ["更新后", pct(m.after_rate), `${(m.after_reviews || 0).toLocaleString()} 条`],
   ["变化", pp(m.change_percentage_points), "百分点"]].forEach(([k, v, s]) => {
    const cell = el("div", "stat");
    cell.appendChild(el("div", "stat-k", k));
    cell.appendChild(el("div", "stat-v", v));
    cell.appendChild(el("div", "stat-s", s));
    grid.appendChild(cell);
  });
  card.appendChild(grid);

  const ul = $("#items");
  ul.innerHTML = "";
  (data.items || []).forEach((it) => {
    const li = el("li");
    const a = el("a", null, it.title);
    a.href = it.source_url || "#";
    a.target = "_blank";
    li.appendChild(a);
    li.appendChild(el("span", "muted", ` ${it.description}`));
    ul.appendChild(li);
  });

  $("#chips").innerHTML = "";
  const p = data.patch;
  [`${p.patch_name} 更新后推荐率降了多少？`, `${p.patch_name} 改了什么？`,
   `玩家怎么评价${(data.items[0] || {}).title || "这次更新"}？`,
   `哪个版本推荐率下降最多？`].forEach((q) => {
    const chip = el("button", "chip", q);
    chip.onclick = () => ask(q);
    $("#chips").appendChild(chip);
  });
}

function addMessage(role, build) {
  const wrap = el("div", `msg ${role}`);
  const bubble = el("div", "bubble");
  build(bubble);
  wrap.appendChild(bubble);
  $("#messages").appendChild(wrap);
  wrap.scrollIntoView({ behavior: "smooth", block: "end" });
  return bubble;
}

async function ask(question) {
  if (!question.trim() || !state.current || state.loading) return;
  const ctx = state.current;                 // 固定本次请求的上下文，避免切版本后串味
  addMessage("user", (b) => b.appendChild(el("p", null, question)));
  const thinking = addMessage("bot", (b) => b.appendChild(el("p", "muted", "检索指标、公告和玩家评测…")));
  try {
    const res = await api("/api/agent/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, appid: ctx.game.appid, patch_id: ctx.patch.patch_id }),
    });
    thinking.parentElement.remove();
    renderAnswer(res);
    if (state.current !== ctx) {
      addMessage("bot", (b) => b.appendChild(el("p", "muted", `（上面这个问题是按 ${ctx.patch.patch_name} 回答的）`)));
    }
  } catch (err) {
    thinking.innerHTML = "";
    thinking.appendChild(el("p", "error", `请求失败：${err.message}`));
  }
}

function renderAnswer(res) {
  addMessage("bot", (b) => {
    (res.answer || "").split("\n").forEach((line) => {
      if (!line.trim()) return;
      b.appendChild(el("p", line.startsWith("玩家原话") ? "subhead" : null, line));
    });

    if (res.numbers && res.numbers.length) {
      const row = el("div", "numbers");
      res.numbers.forEach((n) => {
        const cell = el("div", "num");
        cell.appendChild(el("div", "num-k", n.label));
        cell.appendChild(el("div", "num-v", n.display || n.value));
        row.appendChild(cell);
      });
      b.appendChild(row);
    }

    if (res.citations && res.citations.length) {
      const list = el("div", "cites");
      res.citations.forEach((c) => {
        const item = el("div", `cite ${c.type}`);
        item.appendChild(el("span", "badge", { metric: "指标", review: "原文", announcement: "公告", topic: "主题情感" }[c.type] || c.type));
        if (c.verified) item.appendChild(el("span", "badge verified", "已复核"));
        const title = c.url ? el("a", null, c.title) : el("span", null, c.title);
        if (c.url) { title.href = c.url; title.target = "_blank"; }
        item.appendChild(title);
        if (c.detail) item.appendChild(el("span", "muted", ` ${c.detail}`));
        if (c.type === "topic" && c.source) item.appendChild(el("div", "muted", c.source));
        list.appendChild(item);
      });
      b.appendChild(list);
    }

    if (res.status === "need_context" && res.suggestions && res.suggestions.length) {
      const row = el("div", "chips");
      res.suggestions.forEach((q) => {
        const chip = el("button", "chip", q);
        chip.onclick = () => ask(q);
        row.appendChild(chip);
      });
      b.appendChild(row);
    }

    const links = res.links || {};
    if (links.stats || links.evidence || (links.evidence_items || []).length) {
      const row = el("div", "links");
      const entries = [["查看统计", links.stats]];
      if (Array.isArray(links.evidence_items) && links.evidence_items.length) {
        links.evidence_items.forEach((it) => {
          entries.push([`查看原文：${it.title || it.item_id}`, it.url]);
        });
      } else {
        entries.push(["查看原文", links.evidence]);
      }
      entries.forEach(([label, href]) => {
        if (!href) return;
        const a = el("a", null, label);
        a.href = href;
        row.appendChild(a);
      });
      b.appendChild(row);
    }
  });
}

$("#askForm").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.loading) return;          // 版本还在加载：保留输入，不清空、不发请求
  const input = $("#question");
  const q = input.value.trim();
  if (!q || !state.current) return;
  input.value = "";
  ask(q);
});

boot().catch((err) => {
  $("#meta").textContent = `初始化失败：${err.message}`;
});
