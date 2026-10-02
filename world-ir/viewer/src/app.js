// The viewer page: scene picker, camera picker, toggles, selection, and the repair replay player.
// Both the repo page (index.html) and the hosted artifact call start(); they differ only in where three.js
// and the models come from.

import { WorldView } from "./loader.js";

const COLORS = { error: "#ff5a52", fixing: "#ffa21f", fixed: "#3ad07a", ask: "#f2c94c", deliberate: "#b28cff" };
const PAUSE = { start: 1600, aim: 900, settle: 1300 };

const STYLE = `
  :root { --bg: #11161a; --panel: rgba(18, 24, 29, 0.9); --fg: #e1e8ec; --muted: #98a7b1; --line: #2a3740; --accent: #5fc4ce; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
  #view { position: fixed; inset: 0; }
  .wv-panel { position: fixed; top: 12px; width: min(300px, calc(100vw - 24px)); background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; display: grid; gap: 10px; }
  #panel { left: 12px; }
  #replay { right: 12px; width: min(360px, calc(100vw - 24px)); max-height: calc(100vh - 24px); overflow: auto; }
  h1, h2 { font-size: 16px; margin: 0; }
  h2 { font-size: 15px; }
  label { display: flex; align-items: center; gap: 8px; }
  select, button { background: #0d1215; color: var(--fg); border: 1px solid var(--line); border-radius: 4px; padding: 4px 8px; font: inherit; }
  select { width: 100%; }
  button { cursor: pointer; }
  button:hover { border-color: var(--accent); }
  button:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .row { display: grid; gap: 4px; }
  .muted { color: var(--muted); font-size: 12px; }
  .toggles { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; }
  #selected { font-family: ui-monospace, monospace; color: var(--accent); min-height: 1.4em; }
  #warnings { color: #e3a950; font-size: 12px; margin: 0; padding-left: 16px; }
  .score { display: flex; align-items: baseline; gap: 8px; font-variant-numeric: tabular-nums; }
  .score b { font-size: 26px; line-height: 1; }
  .controls { display: flex; gap: 8px; }
  ol.steps { margin: 0; padding-left: 20px; display: grid; gap: 6px; font-size: 13px; }
  ol.steps li { color: var(--muted); }
  ol.steps li.now { color: var(--fg); }
  ol.steps li.done { color: #b9e8cb; }
  ol.steps li.done::marker { color: ${COLORS.fixed}; }
  ol.steps li.now::marker { color: ${COLORS.fixing}; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--muted); }
  .legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; background: var(--c); }
  .kept { font-size: 12px; color: var(--muted); margin: 0; padding-left: 18px; }
  [hidden] { display: none !important; }
`;

const MARKUP = `
  <div id="view"></div>
  <div id="panel" class="wv-panel">
    <h1 id="title">World Viewer</h1>
    <div class="row"><span class="muted">Scene</span><select id="scene"></select></div>
    <div class="row"><span class="muted">Repair replay</span><select id="replay-pick"><option value="">none</option></select></div>
    <div class="row"><span class="muted">Camera</span><select id="camera"><option value="orbit">orbit</option></select></div>
    <div class="toggles">
      <label><input type="checkbox" id="cutaway" checked> Cutaway</label>
      <label><input type="checkbox" id="ceiling"> Ceiling</label>
      <label><input type="checkbox" id="zones"> Clear zones</label>
      <label><input type="checkbox" id="boxes"> Validator boxes</label>
    </div>
    <div class="row"><span class="muted">Click an object</span><span id="selected"></span></div>
    <div class="muted" id="status"></div>
    <ul id="warnings"></ul>
  </div>
  <div id="replay" class="wv-panel" hidden>
    <h2 id="replay-title"></h2>
    <div class="score"><b id="errors">0</b><span class="muted" id="score-note">errors</span></div>
    <div class="controls"><button id="play">Play</button><button id="restart">Restart</button><span class="muted" id="step-note"></span></div>
    <ol class="steps" id="steps"></ol>
    <div class="row" id="kept-row" hidden><span class="muted">Left alone on purpose</span><ul class="kept" id="kept"></ul></div>
    <div class="legend">
      <span style="--c:${COLORS.error}">error</span><span style="--c:${COLORS.fixing}">fixing</span>
      <span style="--c:${COLORS.fixed}">fixed</span><span style="--c:${COLORS.deliberate}">deliberate</span><span style="--c:${COLORS.ask}">ask the user</span>
    </div>
  </div>
`;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

export async function start({ assetData = null, scenes, defaultScene = "bedroom" } = {}) {
  document.head.insertAdjacentHTML("beforeend", `<style>${STYLE}</style>`);
  document.body.insertAdjacentHTML("afterbegin", MARKUP);
  const $ = (id) => document.getElementById(id);
  const view = new WorldView($("view"), { assetBase: "./", assetData });
  window.view = view;

  $("scene").innerHTML = scenes.map((s) => `<option value="${s}">${s}</option>`).join("");
  const replays = await fetch("./replays/index.json").then((r) => (r.ok ? r.json() : [])).catch(() => []);
  $("replay-pick").innerHTML += replays.map((r) => `<option value="${r.name}">${r.title}</option>`).join("");

  async function show(lowered, name) {
    await view.load(lowered);
    $("title").textContent = lowered.world_id.replace(/_/g, " ");
    $("camera").innerHTML = `<option value="orbit">orbit</option>` + [...view.cameras.values()].map((c) => `<option value="${c.id}">${c.id} (${c.purpose})</option>`).join("");
    const skipped = lowered.unsupported.map((u) => `${u.node} (${u.kind})`);
    $("status").textContent = `${lowered.items.length} items · three r${lowered.versions.three.split(".")[1]} · lowering ${lowered.versions.lowering}` + (skipped.length ? ` · not drawn yet: ${skipped.join(", ")}` : "");
    $("warnings").innerHTML = view.warnings.map((w) => `<li>${w}</li>`).join("");
    $("camera").value = "orbit";
    $("selected").textContent = "";
    view.highlight([]);
    document.body.dataset.ready = name;
  }

  function frameFrom(purposes) {
    const b = view.lowered.bounds;
    const extent = b ? Math.max(b[1][0] - b[0][0], b[1][2] - b[0][2]) : 0;
    for (const purpose of purposes) {
      const cam = [...view.cameras.values()].find((c) => c.projection === "perspective" && c.purpose === purpose);
      if (cam && (purpose !== "top_down" || extent > 30)) return view.orbitFrom(cam.id);
    }
  }

  async function openScene(name) {
    player.stop();
    $("replay").hidden = true;
    $("replay-pick").value = "";
    const lowered = await (await fetch(`./scenes/${name}.json`)).json();
    await show(lowered, name);
    frameFrom(["top_down"]); // large outdoor scenes start from their overview camera
  }

  // Replay player ------------------------------------------------------------------

  const player = {
    replay: null,
    run: 0,
    stop() {
      this.run++;
      $("play").textContent = "Play";
    },
    async open(name) {
      this.stop();
      const replay = await (await fetch(`./replays/${name}.json`)).json();
      this.replay = replay;
      const entry = replays.find((r) => r.name === name);
      if (entry?.scene && scenes.includes(entry.scene)) $("scene").value = entry.scene;
      await show(replay.start, name);
      const b = replay.start.bounds;
      const extent = b ? Math.max(b[1][0] - b[0][0], b[1][2] - b[0][2]) : 0;
      if (extent > 30) frameFrom(["hero", "top_down"]);
      else view.frameAll(); // a whole room at once, walls cut away
      $("replay").hidden = false;
      $("replay-title").textContent = replay.title;
      $("steps").innerHTML = replay.steps.map((s) => `<li>${escape(s.label)}</li>`).join("");
      const kept = replay.start_issues.filter((i) => i.waived_by || i.severity === "ask");
      $("kept-row").hidden = !kept.length;
      $("kept").innerHTML = kept.map((i) => `<li>${escape(i.node)}: ${escape(i.waived_by ?? "a question for the user")}</li>`).join("");
      this.render(replay.start_issues, null);
      this.count(replay.start_issues, 0);
    },
    count(issues, step) {
      const errors = issues.filter((i) => i.severity === "error").length;
      $("errors").textContent = errors;
      $("score-note").textContent = errors === 1 ? "error left" : "errors left";
      $("step-note").textContent = step ? `step ${step} of ${this.replay.steps.length}` : `${this.replay.steps.length} steps`;
      [...$("steps").children].forEach((li, i) => {
        li.className = i < step ? "done" : "";
      });
    },
    render(issues, focus) {
      const marks = [];
      const seen = new Set();
      for (const i of issues) {
        const key = i.part ?? i.node;
        if (seen.has(key)) continue;
        seen.add(key);
        if (i.severity === "error") marks.push({ node: key, color: COLORS.error, label: `${i.code.replace("relation:", "")} · ${i.node}` });
        else if (i.severity === "ask") marks.push({ node: key, color: COLORS.ask, label: `ask the user · ${i.node}` });
        else if (i.waived_by) marks.push({ node: key, color: COLORS.deliberate, label: `deliberate · ${i.waived_by.split(":")[0]}` });
      }
      if (focus) {
        const key = focus.issue?.part ?? focus.issue?.node ?? focus.actions[0].id;
        view.showMarks([{ node: key, color: focus.color, label: focus.label }, ...marks.filter((m) => m.node !== key)]);
        return;
      }
      view.showMarks(marks);
    },
    async play() {
      const name = $("replay-pick").value;
      if (!name) return;
      await this.open(name); // always plays from the broken start
      const mine = ++this.run;
      $("play").textContent = "Pause";
      const steps = this.replay.steps;
      await sleep(PAUSE.start);
      for (let k = 0; k < steps.length; k++) {
        if (this.run !== mine) return;
        const step = steps[k];
        const before = k ? steps[k - 1].issues : this.replay.start_issues;
        [...$("steps").children][k].className = "now";
        this.render(before, { ...step, color: COLORS.fixing, label: `fixing: ${step.label}` });
        await sleep(PAUSE.aim);
        if (this.run !== mine) return;
        await view.applyPatch(step.patch, { duration: reducedMotion ? 1 : 800 });
        this.render(step.issues, { ...step, color: COLORS.fixed, label: `fixed: ${step.actions[0].id}` });
        this.count(step.issues, k + 1);
        await sleep(PAUSE.settle);
      }
      if (this.run !== mine) return;
      this.render(steps.length ? steps[steps.length - 1].issues : this.replay.start_issues, null);
      $("play").textContent = "Play";
      document.body.dataset.replayDone = name;
    },
  };

  view.addEventListener("select", (e) => {
    const node = e.detail?.node ?? "";
    $("selected").textContent = node;
    view.highlight(node ? [node] : []);
  });
  $("scene").addEventListener("change", (e) => openScene(e.target.value));
  $("replay-pick").addEventListener("change", (e) => (e.target.value ? player.open(e.target.value) : openScene($("scene").value)));
  $("play").addEventListener("click", () => ($("play").textContent === "Pause" ? player.stop() : player.play()));
  $("restart").addEventListener("click", () => player.open($("replay-pick").value));
  $("camera").addEventListener("change", (e) => view.useCamera(e.target.value));
  for (const name of ["cutaway", "ceiling", "zones", "boxes"]) {
    $(name).addEventListener("change", (e) => view.setOption(name, e.target.checked));
  }

  const params = new URLSearchParams(location.search);
  const replay = params.get("replay");
  if (replay && replays.some((r) => r.name === replay)) {
    $("replay-pick").value = replay;
    await player.open(replay);
    if (params.has("autoplay")) player.play();
  } else {
    const first = params.get("scene") ?? defaultScene;
    $("scene").value = first;
    await openScene(first);
  }
  return { view, player };
}

function escape(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
