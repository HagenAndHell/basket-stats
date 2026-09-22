// Browser E2E for basket-stats UI: headless Chromium via CDP against a running server.
// Run: node test_ui.js [baseUrl]   (needs `ws`; default http://localhost:8765)
const { spawn } = require("child_process");
const http = require("http");
const WebSocket = require("ws");

const CHROME = process.env.CHROME || "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";
const BASE = process.argv[2] || "http://localhost:8765";
const PORT = 9335;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const getJSON = u => new Promise((res, rej) => http.get(u, r => { let b = ""; r.on("data", d => b += d); r.on("end", () => res(JSON.parse(b))); }).on("error", rej));
let failures = 0;
const check = (name, cond, detail = "") => { console.log(`${cond ? "PASS" : "FAIL"}  ${name}${cond ? "" : "   -> " + detail}`); if (!cond) failures++; };

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--no-sandbox", "--disable-gpu", "--autoplay-policy=no-user-gesture-required", `--remote-debugging-port=${PORT}`, "about:blank"], { stdio: "ignore" });
  try {
    await sleep(1500);
    const targets = await getJSON(`http://127.0.0.1:${PORT}/json`);
    const sock = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
    let id = 0; const pending = {}; const errors = [];
    const send = (method, params = {}) => new Promise(r => { const i = ++id; pending[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
    sock.on("message", m => { const d = JSON.parse(m); if (d.id && pending[d.id]) { pending[d.id](d.result); delete pending[d.id]; } else if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || JSON.stringify(d.params)); });
    await new Promise(r => sock.on("open", r));
    await send("Runtime.enable"); await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    const ev = async expr => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(300); } return false; };

    await send("Page.navigate", { url: `${BASE}/#match=8439241` });
    check("match loads", await waitFor("document.getElementById('match-title').textContent.includes('116–70')"));
    check("events listed", await waitFor("document.querySelectorAll('#ev-body tr').length > 100"));
    check("events unsynced initially", await ev("[...document.querySelectorAll('#ev-body tr')].every(r => r.classList.contains('nosync'))"));
    check("youtube player mounted", await waitFor("!!document.querySelector('#player iframe')", 15000));
    check("period selector Q1-Q4", await ev("[...document.querySelectorAll('#sync-period option')].map(o=>o.textContent).join()") === "Q1,Q2,Q3,Q4");

    // sync Q1 via UI (player time is 0 in headless; fake with P.time override)
    await ev("P.time = () => 300");
    await ev("document.getElementById('sync-clock').value='0:00'; document.getElementById('btn-anchor').click()");
    await sleep(600);
    check("anchor listed", await waitFor("document.getElementById('sync-list').textContent.includes('0:00 ↔ 5:00.0')"));
    check("clock input advanced to 10:00", await ev("document.getElementById('sync-clock').value") === "10:00");
    await ev("P.time = () => 1100");
    await ev("document.getElementById('btn-anchor').click()"); await sleep(600);
    check("two anchors, moved to Q2", await ev("document.getElementById('sync-list').textContent.includes('10:00 ↔ 18:20.0') && document.getElementById('sync-period').value === '2'"));
    check("Q1 events now have video time", await waitFor("[...document.querySelectorAll('#ev-body tr')].filter(r => r.cells[0].textContent==='Q1').every(r => !r.classList.contains('nosync'))"));
    const first = await ev("(()=>{const r=[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1'); return [...r.cells].map(c=>c.textContent)})()");
    check("first Q1 event 0:54 2-pt Oppsal", first[1] === "0:54" && first[3] === "2-pt made" && first[2] === "H", JSON.stringify(first));
    check("first Q1 event video = 5:00 + 54*800/600", first[6] === "6:12.0", first[6]);

    // clicking an event seeks the player 5s before
    await ev("P.seek = t => { window._seek = t }");
    await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1').click()");
    check("event click seeks 5s early", Math.abs(await ev("window._seek") - (372 - 5)) < 0.01, await ev("window._seek"));

    // game clock display
    await ev("P.time = () => 700");
    check("game clock shows Q1 5:00", await waitFor("document.getElementById('gtime').textContent === 'Q1 5:00'"));

    // tag via keyboard
    await ev("document.getElementById('tag-team').value='home'; fillTagPlayers();");
    await ev("document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'o', bubbles:true}))");
    check("tag added via key O", await waitFor("S.data.tags.length === 1"));
    const tag = await ev("JSON.stringify(S.data.tags[0])");
    check("tag has game clock", JSON.parse(tag).period === 1 && Math.abs(JSON.parse(tag).clock - 300) < 0.01, tag);
    check("tag appears in events list", await ev("[...document.querySelectorAll('#ev-body tr')].some(r => r.textContent.includes('OR') && r.textContent.includes('tag'))"));

    // box score
    await ev("document.querySelector('[data-tab=box]').click()");
    const box = await ev("[...document.querySelectorAll('#tab-box tbody tr')].map(r=>[...r.cells].map(c=>c.textContent))");
    check("box score 19 players", box.length === 19, box.length);
    const runcie = box.find(r => r[0] === "33");
    check("Runcie 23 pts, 3/3 FT, 7/7 2P, 2/2 3P, 1 PF", runcie && runcie[3] === "23" && runcie[4] === "3/3" && runcie[5] === "7/7" && runcie[6] === "2/2" && runcie[14] === "1", JSON.stringify(runcie));
    check("tagged OR counted for tagged player", box.some(r => r[7] === "1"), JSON.stringify(box.map(r => r[7])));

    // filters
    await ev("document.querySelector('[data-tab=events]').click(); document.getElementById('f-kind').value='foul'; renderEvents();");
    check("foul filter", await ev("[...document.querySelectorAll('#ev-body tr')].every(r => /foul/i.test(r.cells[3].textContent))") && await ev("document.querySelectorAll('#ev-body tr').length") === 23);

    // remove tag and anchors
    await ev("document.querySelector('[data-tab=tags]').click(); document.querySelector('#tag-body button[data-del]').click()"); await sleep(500);
    check("tag removed", await ev("S.data.tags.length") === 0);
    await ev("document.querySelectorAll('#sync-list button').forEach(b=>b.click())"); await sleep(800);
    check("anchors removed", await waitFor("document.getElementById('sync-list').textContent.includes('not synced') && !document.getElementById('sync-list').textContent.includes('↔')"));

    check("no uncaught JS errors", errors.length === 0, errors.join(" | "));
    console.log(failures ? `\n${failures} FAILED` : "\nALL PASSED");
  } finally { chrome.kill(); }
  process.exit(failures ? 1 : 0);
})();
