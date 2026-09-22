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
const fmtJs = s => { const m = Math.floor(s/60), r = s - m*60; return `${m}:${r < 10 ? "0" : ""}${r.toFixed(1)}`; };
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
    await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable"); await send("Network.setCacheDisabled", { cacheDisabled: true });
    await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    const ev = async expr => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(300); } return false; };

    await send("Page.navigate", { url: `${BASE}/#match=8439241` });
    check("match loads", await waitFor("document.getElementById('match-title').textContent.includes('116–70')"));
    check("events listed", await waitFor("document.querySelectorAll('#ev-body tr').length > 100"));
    check("events unsynced initially", await ev("[...document.querySelectorAll('#ev-body tr')].every(r => r.classList.contains('nosync'))"));
    check("player mounted (YouTube iframe or local <video>)", await waitFor("!!document.querySelector('#player iframe, #player video')", 15000));
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

    // "here" button: anchor an event at the current video time and re-time neighbours
    await ev("P.time = () => 500");
    await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1' && r.cells[1].textContent==='2:01').querySelector('button.mark').click()");
    check("event anchored via here-button", await waitFor("document.getElementById('sync-list').textContent.includes('2:01 ↔ 8:20.0')"));
    const t201 = await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1' && r.cells[1].textContent==='2:01')");
    check("anchored event shows check mark", await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[1].textContent==='2:01').querySelector('button.mark').textContent") === "✓");
    // 0:54 now interpolates between (0:00,300) and (2:01,500): 300 + 54*200/121
    const v054 = await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1' && r.cells[1].textContent==='0:54').cells[6].textContent");
    check("neighbour re-timed by new anchor", v054 === fmtJs(300 + 54 * 200 / 121), v054);
    // key M on a selected event
    await ev("[...document.querySelectorAll('#ev-body tr')].find(r=>r.cells[0].textContent==='Q1' && r.cells[1].textContent==='3:05').click()");
    await ev("P.time = () => 900; document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'m', bubbles:true}))");
    check("key M anchors selected event", await waitFor("document.getElementById('sync-list').textContent.includes('3:05 ↔ 15:00.0')"));
    await ev("document.querySelectorAll('#sync-list button').forEach(b => { if (b.dataset.c === '121' || b.dataset.c === '185') b.click() })"); await sleep(800);
    check("extra anchors removed", await waitFor("!document.getElementById('sync-list').textContent.includes('2:01') && !document.getElementById('sync-list').textContent.includes('3:05')"));
    await ev("P.time = () => 700"); await sleep(1200);

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

    // ---- Court tab: calibration via clicks on the frame + tracked positions on the 2D court
    await ev("document.querySelector('[data-tab=court]').click()");
    check("court canvas visible", await ev("document.getElementById('court2d').offsetHeight > 0"));
    check("tracking status shows data", await waitFor("/available|done/.test(document.getElementById('track-status').textContent)"));
    check("landmark list populated", await ev("document.getElementById('lm-select').options.length") >= 20);
    await ev("calibPts = []; document.getElementById('calib').open = true; P.time = () => 30; document.getElementById('btn-frame').click()");
    check("fullscreen calibrator opens with full-res frame", await waitFor("!document.getElementById('calib-full').classList.contains('hidden') && CF.img && CF.img.width === 1920", 15000));
    check("frame fitted to screen", await ev("Math.abs(CF.scale - CF.fit) < 1e-9 && CF.fit < 1"));
    // zoom in 3 steps around a point, then pan
    await ev("(()=>{const cv=document.getElementById('cf-canvas'); const r=cv.getBoundingClientRect(); for(let i=0;i<3;i++) cv.dispatchEvent(new WheelEvent('wheel',{deltaY:-100, clientX:r.left+r.width*0.3, clientY:r.top+r.height*0.5, bubbles:true, cancelable:true}));})()");
    check("wheel zooms in", Math.abs(await ev("CF.scale / CF.fit") - 1.25 ** 3) < 1e-6, await ev("CF.scale / CF.fit"));
    const before = await ev("[CF.ox, CF.oy]");
    await ev("(()=>{const cv=document.getElementById('cf-canvas'); const r=cv.getBoundingClientRect(); cv.dispatchEvent(new MouseEvent('mousedown',{clientX:r.left+200, clientY:r.top+200, bubbles:true})); cv.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+260, clientY:r.top+230, bubbles:true})); cv.dispatchEvent(new MouseEvent('mouseup',{clientX:r.left+260, clientY:r.top+230, bubbles:true}));})()");
    const after = await ev("[CF.ox, CF.oy]");
    check("drag pans", Math.round(after[0] - before[0]) === 60 && Math.round(after[1] - before[1]) === 30, JSON.stringify([before, after]));
    check("drag does not place a point", await ev("calibPts.length") === 0, await ev("JSON.stringify(calibPts)"));
    // click landmarks: choose image-space targets, convert to screen coords via the current view
    const clicks = [["corner_L_top", 470, 490], ["corner_R_top", 1560, 458], ["corner_L_bottom", 96, 918], ["corner_R_bottom", 1862, 950], ["centre", 1056, 670]];
    for (const [name, ix, iy] of clicks) {
      await ev(`(()=>{const cv=document.getElementById('cf-canvas'); const r=cv.getBoundingClientRect(); document.getElementById('lm-select').value='${name}';
        cfFit(); CF.scale = CF.fit*4; CF.ox = cv.width/2 - ${ix}*CF.scale; CF.oy = cv.height/2 - ${iy}*CF.scale; drawCalib();
        const x=r.left+CF.ox+${ix}*CF.scale, y=r.top+CF.oy+${iy}*CF.scale;
        cv.dispatchEvent(new MouseEvent('mousedown',{clientX:x, clientY:y, bubbles:true})); cv.dispatchEvent(new MouseEvent('mouseup',{clientX:x, clientY:y, bubbles:true}));})()`);
    }
    check("5 calibration points listed", await ev("calibPts.length") === 5, await ev("calibPts.length"));
    const cRB = await ev("calibPts.find(p=>p.name==='corner_R_bottom')");
    check("click at zoom maps to exact image pixel", Math.abs(cRB.px - 1862) < 0.6 && Math.abs(cRB.py - 950) < 0.6, JSON.stringify(cRB));
    check("points listed in fullscreen bar too", await ev("document.getElementById('cf-pts').textContent.includes('corner_R_bottom')"));
    await ev("document.getElementById('cf-save').click()");
    check("save from fullscreen works", await waitFor("S.data.calibration && S.data.calibration.H"));
    await waitFor("CF.outline && CF.outline.length === 7", 5000);
    check("court outline drawn after calibration (green pixels on canvas)", await ev("(()=>{drawCalib(); const c=document.getElementById('cf-canvas'); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for(let i=0;i<d.length;i+=4) if(d[i+1]>150 && d[i]<100 && d[i+2]<150) n++; return n;})()") > 100);
    await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape', bubbles:true}))");
    check("Esc closes calibrator", await ev("document.getElementById('calib-full').classList.contains('hidden')"));
    check("calibration status shown", await ev("document.getElementById('calib-status').textContent").then(t => /calibrated \(5 pts/.test(t)));
    await ev("P.time = () => 31.0"); await sleep(1500);
    await ev("TRK = { frames: [], t0: null, t1: null }; ensureTracks(31.0)"); await sleep(1000); await ev("drawCourt(31.0)");
    check("court view shows players", await waitFor("/\\d+ players on court/.test(document.getElementById('court-info').textContent)", 8000), await ev("document.getElementById('court-info').textContent"));
    await ev("drawCourt(31.0)");
    const px = await ev("(()=>{const c=document.getElementById('court2d'); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for(let i=0;i<d.length;i+=4){ if(Math.abs(d[i]-d[i+1])>60 || Math.abs(d[i+1]-d[i+2])>60) n++; } return n;})()");
    check("coloured dots drawn on court", px > 50, px);
    // calibration persists across reload of the match
    await ev("loadMatch(8439241)"); await sleep(1500);
    check("calibration persisted", await ev("S.data.calibration.points.length") === 5);

    check("no uncaught JS errors", errors.length === 0, errors.join(" | "));
    console.log(failures ? `\n${failures} FAILED` : "\nALL PASSED");
  } finally { chrome.kill(); }
  process.exit(failures ? 1 : 0);
})();
