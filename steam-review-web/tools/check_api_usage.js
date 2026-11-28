// 用 CDP 抓包，确认页面数据确实来自 /api/*，而不是回退到 data/*.js
const http = require('http');

const PORT = 9344;
const BASE = 'http://127.0.0.1:8077';

function get(p) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path: p }, (r) => {
      let b = '';
      r.on('data', (d) => (b += d));
      r.on('end', () => res(JSON.parse(b)));
    }).on('error', rej);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function checkPage(ws, send, path) {
  const apiReqs = [];
  const staticDataReqs = [];
  const listener = (e) => {
    const d = JSON.parse(e.data);
    if (d.method !== 'Network.requestWillBeSent') return;
    const u = d.params.request.url;
    if (u.includes('/api/')) apiReqs.push(u.replace(BASE, ''));
    if (u.includes('/data/')) staticDataReqs.push(u.replace(BASE, ''));
  };
  ws.addEventListener('message', listener);

  await send('Page.navigate', { url: BASE + path });
  await sleep(6000);

  ws.removeEventListener('message', listener);
  return { apiReqs: [...new Set(apiReqs)], staticDataReqs: [...new Set(staticDataReqs)] };
}

(async () => {
  const targets = await get('/json/list');
  const page = targets.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);

  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (e) => {
    const d = JSON.parse(e.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  });
  const send = (m, p) => new Promise((r) => {
    const i = ++id;
    pending.set(i, r);
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
  });

  await new Promise((r) => ws.addEventListener('open', r));
  await send('Network.enable');
  await send('Page.enable');

  const pages = [
    ['index.html', '/index.html'],
    ['patch.html', '/patch.html?id=sdv_20201221_15'],
    ['nlp.html', '/nlp.html?patch=sdv_20201221_15&lang=zh&phase=after'],
  ];

  let allOk = true;
  for (const [label, p] of pages) {
    const r = await checkPage(ws, send, p);
    const viaApi = r.apiReqs.length > 0 && r.staticDataReqs.length === 0;
    allOk = allOk && viaApi;
    console.log(`=== ${label}`);
    console.log(`  /api 请求 ${r.apiReqs.length} 个: ${r.apiReqs.join(', ') || '(无)'}`);
    console.log(`  data/*.js 请求 ${r.staticDataReqs.length} 个: ${r.staticDataReqs.join(', ') || '(无)'}`);
    console.log(`  → ${viaApi ? '走 API ✓' : '疑似回退到静态文件 ✗'}`);
  }

  console.log('');
  console.log(allOk ? '结论：三个页面全部通过 API 取数，未发生回退' : '结论：存在回退，需要排查');

  ws.close();
  process.exit(allOk ? 0 : 1);
})();
