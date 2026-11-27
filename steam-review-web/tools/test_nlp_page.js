// 用 CDP 驱动真实点击，验证排序、语种、阶段三个交互
const http = require('http');
const { spawn } = require('child_process');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9333;
const URL = process.argv[2] || 'http://127.0.0.1:8088/nlp.html?patch=sdv_20201221_15&lang=zh&phase=after';

function get(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
      let b = '';
      r.on('data', (d) => (b += d));
      r.on('end', () => res(JSON.parse(b)));
    }).on('error', rej);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const chrome = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-proxy-server',
    `--remote-debugging-port=${PORT}`, '--window-size=1360,900',
    '--user-data-dir=' + process.env.TEMP + '/cdp-nlp', 'about:blank',
  ]);
  await sleep(3000);

  const targets = await get('/json/list');
  const page = targets.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);

  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const d = JSON.parse(ev.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  });
  const send = (method, params) => new Promise((res) => {
    const i = ++id;
    pending.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params }));
  });

  await new Promise((r) => ws.addEventListener('open', r));
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: URL });
  await sleep(5000);

  const evalJS = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  // 1) 默认：按提及量排序，首行应是 性能
  const firstByTotal = await evalJS("document.querySelector('.topic-name').textContent");
  const totalRows = await evalJS("document.querySelectorAll('.topic-name').length");

  // 2) 点击「负向率」排序
  await evalJS("document.querySelector('[data-sort=neg]').click()");
  await sleep(400);
  const firstByNeg = await evalJS("document.querySelector('.topic-name').textContent");
  const negOrder = await evalJS(
    "Array.from(document.querySelectorAll('.topic-nums .n-rate')).slice(0,3).map(e=>e.textContent).join(',')"
  );

  // 3) 切英文：命中总数应变化
  const beforeLang = await evalJS("document.querySelector('.hs-num').textContent");
  await evalJS("document.querySelector('[data-lang=en]').click()");
  await sleep(400);
  const afterLang = await evalJS("document.querySelector('.hs-num').textContent");

  // 4) 阶段切回「全部」
  await evalJS("document.querySelector('[data-phase=all]').click()");
  await sleep(400);
  const afterPhase = await evalJS("document.querySelector('.hs-num').textContent");
  const scopeText = await evalJS("document.querySelector('#scopeNote').textContent.trim()");

  // 5) 条目排序：按负向数
  await evalJS("document.querySelector('[data-isort=neg]').click()");
  await sleep(400);
  const itemNegOrder = await evalJS(
    "Array.from(document.querySelectorAll('#itemTable tbody tr')).slice(0,3).map(tr=>tr.children[1].querySelector('.item-name').textContent + ':' + tr.children[4].textContent).join(' | ')"
  );

  console.log(JSON.stringify({
    firstByTotal, totalRows, firstByNeg, negOrder,
    beforeLang, afterLang, afterPhase, scopeText, itemNegOrder,
  }, null, 2));

  ws.close();
  chrome.kill();
  process.exit(0);
})();
