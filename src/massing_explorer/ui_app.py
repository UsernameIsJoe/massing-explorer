"""
Local Massing Explorer studio UI.

Drop a program Excel, type a brief, get a quick Three.js massing preview.
Stdlib HTTP only — no Flask. Launch with run_ui.bat or:

    python -m massing_explorer.ui_app
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "project.example.yaml"
DEFAULT_PORT = 8765
UPLOAD_DIR = Path(tempfile.gettempdir()) / "massing_explorer_ui"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory upload path by session token (one file at a time is fine).
_STATE: dict[str, Any] = {"program_path": None, "program_name": None}


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Massing Explorer</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root {
    --bg: #070707;
    --panel: #0e0e0e;
    --line: #1c1c1c;
    --line-strong: #2a2a2a;
    --text: #e8e6e1;
    --muted: #8a8780;
    --faint: #55524c;
    --accent: #d4cfc4;
    --warn: #c9a27a;
    --ok: #8faf9a;
    --bad: #c48888;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: "Instrument Sans", sans-serif;
    font-size: 14px;
    letter-spacing: 0.01em;
  }
  body {
    display: grid;
    grid-template-columns: minmax(300px, 360px) 1fr;
    grid-template-rows: 1fr;
    min-height: 100vh;
    height: 100vh;
  }
  aside {
    border-right: 1px solid var(--line);
    background: var(--panel);
    display: flex;
    flex-direction: column;
    padding: 28px 24px 24px;
    gap: 22px;
    min-height: 0;
    overflow: auto;
  }
  .brand {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .brand h1 {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .brand p {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  label {
    display: block;
    font-size: 10px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--faint);
    margin-bottom: 8px;
  }
  .drop {
    border: 1px dashed var(--line-strong);
    padding: 22px 16px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    background: #0a0a0a;
  }
  .drop:hover, .drop.drag {
    border-color: var(--accent);
    background: #121212;
  }
  .drop .name {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: var(--text);
    word-break: break-all;
  }
  .drop .hint {
    margin-top: 8px;
    color: var(--muted);
    font-size: 12px;
  }
  textarea {
    width: 100%;
    min-height: 140px;
    resize: vertical;
    background: #0a0a0a;
    border: 1px solid var(--line-strong);
    color: var(--text);
    padding: 12px 14px;
    font-family: inherit;
    font-size: 13px;
    line-height: 1.5;
    outline: none;
  }
  textarea:focus { border-color: #3a3a3a; }
  textarea::placeholder { color: var(--faint); }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
    cursor: pointer;
    user-select: none;
  }
  .toggle input { accent-color: var(--accent); }
  button.primary {
    width: 100%;
    border: 1px solid var(--accent);
    background: transparent;
    color: var(--accent);
    padding: 12px 16px;
    font-family: inherit;
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }
  button.primary:hover:not(:disabled) {
    background: var(--accent);
    color: #0a0a0a;
  }
  button.primary:disabled {
    opacity: 0.35;
    cursor: wait;
  }
  .status {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.55;
    min-height: 2.8em;
    white-space: pre-wrap;
  }
  .status.ok { color: var(--ok); }
  .status.bad { color: var(--bad); }
  .status.warn { color: var(--warn); }
  main {
    position: relative;
    display: grid;
    grid-template-rows: minmax(240px, 1fr) minmax(220px, 38vh);
    min-height: 0;
    height: 100vh;
    overflow: hidden;
    background: var(--bg);
  }
  .stage {
    position: relative;
    min-height: 0;
    background:
      radial-gradient(ellipse 80% 50% at 50% 0%, #121212 0%, transparent 55%),
      var(--bg);
    overflow: hidden;
  }
  #viewport {
    position: absolute;
    inset: 0;
  }
  .process {
    border-top: 1px solid var(--line);
    background: var(--panel);
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  .process-tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid var(--line);
    flex: 0 0 auto;
  }
  .process-tabs button {
    appearance: none;
    border: 0;
    background: transparent;
    color: var(--faint);
    font-family: inherit;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 12px 16px;
    cursor: pointer;
    border-bottom: 1px solid transparent;
    margin-bottom: -1px;
  }
  .process-tabs button.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  .process-body {
    flex: 1;
    overflow: auto;
    padding: 14px 18px 18px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.55;
  }
  .process-body .empty-msg {
    color: var(--faint);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-size: 10px;
    padding: 18px 0;
  }
  .clause-col {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }
  .clause-col h4 {
    margin: 0 0 8px;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 500;
  }
  .clause-col .req h4 { color: #c48888; }
  .clause-col .lim h4 { color: #c9a27a; }
  .clause-col .pref h4 { color: #8faf9a; }
  .clause-col ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .clause-col li {
    padding: 6px 0;
    border-bottom: 1px solid #1a1a1a;
    color: #c8c4bc;
  }
  .pool-table, .cand-table {
    width: 100%;
    border-collapse: collapse;
  }
  .pool-table th, .cand-table th, .pool-table td, .cand-table td {
    text-align: left;
    padding: 6px 8px 6px 0;
    border-bottom: 1px solid #1a1a1a;
    vertical-align: top;
  }
  .pool-table th, .cand-table th {
    color: var(--faint);
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-size: 10px;
  }
  .pool-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
    gap: 12px;
    margin-top: 10px;
  }
  .pool-card {
    border: 1px solid var(--line-strong);
    background: #0a0a0a;
    padding: 0;
    cursor: pointer;
    text-align: left;
    color: inherit;
    font: inherit;
    transition: border-color 0.15s, background 0.15s;
  }
  .pool-card:hover { border-color: #3a3a3a; }
  .pool-card.selected {
    border-color: var(--accent);
    background: #121210;
  }
  .pool-card canvas {
    display: block;
    width: 100%;
    height: 110px;
    background: #070707;
  }
  .pool-card .cap {
    padding: 8px 10px 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .pool-card .cap .title {
    color: var(--text);
    font-size: 11px;
  }
  .pool-card .cap .sub {
    color: var(--faint);
    font-size: 10px;
    line-height: 1.4;
  }
  .pill {
    display: inline-block;
    padding: 1px 6px;
    border: 1px solid #333;
    color: var(--accent);
    margin-right: 4px;
    font-size: 10px;
  }
  .pill.ok { border-color: #3a5a44; color: var(--ok); }
  .pill.bad { border-color: #5a3a3a; color: var(--bad); }
  .pill.kept { border-color: #6a5a2a; color: var(--warn); }
  .step {
    margin: 0 0 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid #1a1a1a;
  }
  .step .phase {
    color: var(--accent);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    font-size: 10px;
    margin-bottom: 4px;
  }
  .step .ops {
    margin-top: 6px;
    color: var(--faint);
  }
  .notes-line {
    margin-top: 12px;
    color: var(--faint);
  }
  @media (max-width: 980px) {
    .clause-col { grid-template-columns: 1fr; }
  }
  .hud {
    position: absolute;
    left: 24px;
    bottom: 24px;
    right: 24px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    pointer-events: none;
    z-index: 2;
  }
  .hud .meta {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.6;
  }
  .hud .meta strong {
    color: var(--text);
    font-weight: 500;
  }
  .mass-list {
    text-align: right;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.55;
  }
  .legend {
    position: absolute;
    top: 18px;
    left: 18px;
    max-width: min(360px, 46vw);
    max-height: min(58vh, 480px);
    overflow: auto;
    padding: 14px 16px;
    background: rgba(10,10,10,0.94);
    border: 1px solid #3a3a3a;
    box-shadow: 0 8px 28px rgba(0,0,0,0.45);
    z-index: 5;
    pointer-events: auto;
    display: none;
  }
  .legend.show { display: block; }
  .legend h3 {
    margin: 0 0 10px;
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #c8c4bc;
    font-weight: 500;
  }
  .legend .item {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 5px 0;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #d8d4cc;
    line-height: 1.35;
  }
  .legend .swatch {
    width: 14px;
    height: 14px;
    flex: 0 0 auto;
    border: 1px solid rgba(255,255,255,0.35);
  }
  .legend .swatch.dh {
    box-shadow: inset 0 0 0 2px rgba(232,230,225,0.55);
  }
  .legend .mass-block {
    margin-top: 0;
    padding-top: 0;
    border-top: none;
  }
  .legend .mass-title {
    color: var(--text);
    font-size: 11px;
    margin-bottom: 4px;
    margin-top: 8px;
  }
  .legend .mass-title:first-child { margin-top: 0; }
  .legend .hint {
    margin-top: 10px;
    font-size: 10px;
    color: var(--faint);
    line-height: 1.4;
  }
  .inspect {
    position: absolute;
    top: 18px;
    right: 18px;
    width: min(320px, 42vw);
    max-height: min(58vh, 480px);
    overflow: auto;
    padding: 14px 16px;
    background: rgba(10,10,10,0.94);
    border: 1px solid #3a3a3a;
    box-shadow: 0 8px 28px rgba(0,0,0,0.45);
    z-index: 5;
    display: none;
  }
  .inspect.show { display: block; }
  .inspect h3 {
    margin: 0 0 6px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
  }
  .inspect .sub {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
    margin-bottom: 10px;
  }
  .inspect .swatch {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 1px solid rgba(255,255,255,0.35);
    vertical-align: -1px;
    margin-right: 8px;
  }
  .inspect .section {
    margin-top: 10px;
  }
  .inspect .section h4 {
    margin: 0 0 6px;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 500;
  }
  .inspect .section.req h4 { color: #9bbf9a; }
  .inspect .section.lim h4 { color: #c9a27a; }
  .inspect .section.pref h4 { color: #8a9fc2; }
  .inspect .section ul {
    margin: 0;
    padding-left: 16px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #d8d4cc;
    line-height: 1.45;
  }
  .inspect .empty-msg {
    font-size: 11px;
    color: var(--faint);
  }
  .inspect .close {
    position: absolute;
    top: 10px;
    right: 12px;
    background: none;
    border: none;
    color: var(--faint);
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 2px 4px;
  }
  .inspect .close:hover { color: var(--text); }
  .empty {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: var(--faint);
    font-size: 12px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    pointer-events: none;
    z-index: 1;
  }
  .empty.hide { display: none; }
  @media (max-width: 860px) {
    body { grid-template-columns: 1fr; height: auto; }
    aside { min-height: auto; }
    main { height: auto; grid-template-rows: 55vh minmax(240px, auto); }
    .legend { max-width: calc(100% - 36px); }
  }
</style>
</head>
<body>
  <aside>
    <div class="brand">
      <h1>Massing Explorer</h1>
      <p>Program in. Brief in. Legal massing out — feet, not fiction.</p>
    </div>

    <div>
      <label>Program</label>
      <div class="drop" id="drop">
        <div class="name" id="fileName">Drop Excel / CSV</div>
        <div class="hint">.xlsx · .xls · .csv</div>
      </div>
      <input type="file" id="file" accept=".xlsx,.xls,.csv" hidden />
    </div>

    <div style="flex:1; display:flex; flex-direction:column;">
      <label>Brief</label>
      <textarea id="brief" placeholder="Four masses. Gym and dining together. Max 400 ft. Art on the ground floor. Max 3 stories."></textarea>
    </div>

    <div class="row">
      <label class="toggle" title="COVER / LEARN / REFINE archive loop (always on)">
        <input type="checkbox" id="explore" checked disabled />
        Full explore
      </label>
    </div>

    <button class="primary" id="go">Generate</button>
    <div class="status" id="status">Ready.</div>
  </aside>

  <main>
    <div class="stage">
      <div class="empty" id="empty">Orbit · drag to rotate · scroll to zoom</div>
      <div id="viewport"></div>
      <div class="legend" id="legend"></div>
      <div class="inspect" id="inspect"></div>
      <div class="hud">
        <div class="meta" id="meta"></div>
        <div class="mass-list" id="massList"></div>
      </div>
    </div>
    <section class="process" id="process">
      <div class="process-tabs" id="processTabs">
        <button type="button" data-tab="interpreted" class="active">Interpreted</button>
        <button type="button" data-tab="pool">Sample pool</button>
        <button type="button" data-tab="candidates">Top candidates</button>
        <button type="button" data-tab="process">Process / BO</button>
      </div>
      <div class="process-body" id="processBody">
        <div class="empty-msg">Generate to see requirements, limitations, preferences, and search steps.</div>
      </div>
    </section>
  </main>

<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
  }
}
</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const fileName = document.getElementById("fileName");
const brief = document.getElementById("brief");
const explore = document.getElementById("explore");
const go = document.getElementById("go");
const status = document.getElementById("status");
const empty = document.getElementById("empty");
const meta = document.getElementById("meta");
const massList = document.getElementById("massList");
const legend = document.getElementById("legend");
const inspect = document.getElementById("inspect");
const viewport = document.getElementById("viewport");
const processTabs = document.getElementById("processTabs");
const processBody = document.getElementById("processBody");

let hasFile = false;
let lastTransparency = null;
let lastMesh = null;
let activeTab = "interpreted";
let selectedSchemeRank = 0;
let studyId = null;
let selectedBoxId = null;

processTabs.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  activeTab = btn.dataset.tab;
  for (const b of processTabs.querySelectorAll("button")) {
    b.classList.toggle("active", b === btn);
  }
  renderProcess(lastTransparency);
});

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
drop.addEventListener("drop", async (e) => {
  e.preventDefault();
  drop.classList.remove("drag");
  if (e.dataTransfer.files?.[0]) await upload(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", async () => {
  if (fileInput.files?.[0]) await upload(fileInput.files[0]);
});

async function upload(file) {
  setStatus("Uploading " + file.name + "…");
  const body = new FormData();
  body.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body });
  const data = await res.json();
  if (!data.ok) {
    setStatus(data.error || "Upload failed", "bad");
    return;
  }
  hasFile = true;
  fileName.textContent = data.name;
  setStatus(data.departments + " departments · " + data.rooms + " rooms", "ok");
}

go.addEventListener("click", async () => {
  if (!hasFile) { setStatus("Drop a program file first.", "warn"); return; }
  const text = brief.value.trim();
  if (!text) { setStatus("Type a brief.", "warn"); return; }
  go.disabled = true;
  setStatus("Exploring archive…");
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief: text, explore: true }),
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Generate failed", "bad");
      return;
    }
    renderMesh(data.mesh);
    renderLegend(data.mesh);
    lastMesh = data.mesh;
    lastTransparency = data.transparency || null;
    studyId = data.study_id || null;
    selectedSchemeRank = (lastTransparency?.sample_pool?.selected_rank) ?? 0;
    renderProcess(lastTransparency);
    clearInspect();
    const pass = data.mesh.all_checks_passed;
    setStatus(
      (pass ? "Checks passed. " : "Some checks failed. ") + (data.note || ""),
      pass ? "ok" : "warn"
    );
    updateHud(data.mesh);
    setStatus(String(err), "bad");
  } finally {
    go.disabled = false;
  }
});

function setStatus(text, kind) {
  status.textContent = text;
  status.className = "status" + (kind ? " " + kind : "");
}

function updateHud(mesh) {
  if (!mesh) return;
  const nBoxes = (mesh.boxes || []).length;
  const nDept = (mesh.legend?.departments || []).length;
  meta.innerHTML = `<strong>${(mesh.masses || []).length}</strong> masses · ` +
    `<strong>${nBoxes}</strong> program boxes · ` +
    `<strong>${nDept}</strong> depts · ` +
    `story <strong>${mesh.story_height_ft} ft</strong>` +
    (mesh.envelope ? ` · <span style="color:#c9a27a">envelope</span>` : "") +
    (mesh.failed_checks?.length
      ? ` · <span style="color:#c48888">${mesh.failed_checks.length} failed check(s)</span>`
      : ` · <span style="color:#8faf9a">checks ok</span>`);
  const totalLen = (mesh.masses || []).reduce((s, m) => s + (m.length_ft || 0), 0);
  massList.innerHTML = (mesh.masses || []).map(m =>
    `${m.name} · ${m.length_ft}×${m.width_ft} · ${m.stories} fl`
  ).join("<br/>") + `<br/><br/>frontage ~ ${totalLen.toFixed(0)} ft`;
}

async function selectScheme(rank) {
  const pool = lastTransparency?.sample_pool || {};
  const schemes = pool.schemes || [];
  const scheme = schemes.find(s => s.rank === rank) || schemes[rank];
  if (!scheme) return;
  selectedSchemeRank = rank;
  if (lastTransparency?.sample_pool) {
    lastTransparency.sample_pool.selected_rank = rank;
  }
  // Immediate envelope in the main stage while the full solve loads.
  if (scheme.preview) {
    renderMesh(scheme.preview);
    renderLegend(scheme.preview);
    lastMesh = scheme.preview;
    updateHud(scheme.preview);
  }
  renderProcess(lastTransparency);
  if (studyId == null) return;
  setStatus(`Loading scheme #${rank}…`);
  try {
    const res = await fetch("/api/apply_scheme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ study_id: studyId, index: rank }),
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Could not apply scheme", "warn");
      return;
    }
    renderMesh(data.mesh);
    renderLegend(data.mesh);
    lastMesh = data.mesh;
    clearInspect();
    updateHud(data.mesh);
    const pass = data.mesh.all_checks_passed;
    setStatus(
      (pass ? "Scheme applied. " : "Scheme applied with failed checks. ") + (data.note || ""),
      pass ? "ok" : "warn"
    );
  } catch (err) {
    setStatus(String(err), "bad");
  }
}

const thumbScenes = new WeakMap();
let sharedThumbRenderer = null;

function getSharedThumbRenderer() {
  if (!sharedThumbRenderer) {
    sharedThumbRenderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      preserveDrawingBuffer: true,
      powerPreference: "low-power",
    });
    sharedThumbRenderer.setPixelRatio(1);
  }
  return sharedThumbRenderer;
}

function paintSchemeThumb(canvas, mesh) {
  if (!mesh || !canvas) return;
  const w = canvas.clientWidth || 168;
  const h = canvas.clientHeight || 110;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.floor(w * dpr));
  canvas.height = Math.max(1, Math.floor(h * dpr));

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x070707);
  const camera = new THREE.PerspectiveCamera(35, w / Math.max(h, 1), 1, 5000);
  const amb = new THREE.AmbientLight(0xffffff, 0.65);
  scene.add(amb);
  const key = new THREE.DirectionalLight(0xffffff, 0.7);
  key.position.set(120, 200, 90);
  scene.add(key);
  const group = new THREE.Group();
  scene.add(group);

  for (const b of mesh.boxes || []) {
    const geo = new THREE.BoxGeometry(b.dx, b.dz, b.dy);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(b.color),
      metalness: 0.05,
      roughness: 0.75,
    });
    const box = new THREE.Mesh(geo, mat);
    box.position.set(b.x, b.z, b.y);
    group.add(box);
    const edges = new THREE.EdgesGeometry(geo);
    const line = new THREE.LineSegments(
      edges,
      new THREE.LineBasicMaterial({ color: 0xe8e6e1, transparent: true, opacity: 0.25 })
    );
    line.position.copy(box.position);
    group.add(line);
  }
  const ext = mesh.extent || { x: 200, y: 80, z: 40 };
  const cx = ext.x / 2;
  const cy = ext.z / 2;
  const cz = ext.y / 2;
  const dist = Math.max(ext.x, ext.y, ext.z) * 1.55 + 40;
  camera.position.set(cx + dist * 0.75, cy + dist * 0.55, cz + dist * 0.7);
  camera.lookAt(cx, cy * 0.4, cz);

  const renderer = getSharedThumbRenderer();
  renderer.setSize(canvas.width, canvas.height, false);
  renderer.render(scene, camera);
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(renderer.domElement, 0, 0, canvas.width, canvas.height);
  }
  // Dispose temp GPU objects from this paint.
  group.traverse((obj) => {
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) {
      if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose());
      else obj.material.dispose();
    }
  });
}

function mountPoolThumbs() {
  const cards = processBody.querySelectorAll(".pool-card[data-rank]");
  for (const card of cards) {
    const rank = Number(card.dataset.rank);
    const canvas = card.querySelector("canvas");
    const scheme = (lastTransparency?.sample_pool?.schemes || [])
      .find(s => s.rank === rank);
    if (canvas && scheme?.preview) paintSchemeThumb(canvas, scheme.preview);
    card.addEventListener("click", () => selectScheme(rank));
  }
}

function renderLegend(mesh) {
  const leg = mesh.legend || {};
  const masses = leg.masses || [];
  if (!masses.length) {
    legend.classList.remove("show");
    legend.innerHTML = "";
    return;
  }
  const dhDepts = new Set(
    (mesh.boxes || []).filter(b => b.double_height).map(b => b.department)
  );
  let html = `<h3>By mass</h3>`;
  for (const m of masses) {
    html += `<div class="mass-title">${esc(m.name)}${m.double_height ? " · double-height" : ""}</div>`;
    const colors = m.colors || [];
    (m.departments || []).forEach((name, i) => {
      const dh = dhDepts.has(name);
      html += `<div class="item"><span class="swatch${dh ? " dh" : ""}" style="background:${colors[i] || "#888"}"></span>` +
        `<span>${esc(name)}${dh ? " · 2×H" : ""}</span></div>`;
    });
  }
  html += `<div class="hint">Click a program box for dimensions and brief clauses · inset = double-height</div>`;
  legend.innerHTML = html;
  legend.classList.add("show");
}

function clausesForDepartment(dept) {
  const i = (lastTransparency && lastTransparency.interpreted) || {};
  const match = (arr) => (arr || []).filter(c => {
    const depts = c.departments || [];
    if (!depts.length) return false;
    return depts.some(d => String(d).toLowerCase() === String(dept).toLowerCase());
  });
  return {
    requirements: match(i.requirements),
    limitations: match(i.limitations),
    preferences: match(i.preferences),
  };
}

function clearInspect() {
  selectedBoxId = null;
  inspect.classList.remove("show");
  inspect.innerHTML = "";
  for (const child of meshGroup.children) {
    if (child.isMesh && child.userData && child.userData.box) {
      child.material.emissive?.setHex(0x000000);
      child.material.opacity = child.userData.box.double_height ? 0.88 : 0.92;
    }
  }
}

function showInspect(box) {
  selectedBoxId = box.id;
  const mass = (lastMesh?.masses || []).find(m => m.id === box.mass_id || m.name === box.mass) || {};
  const related = clausesForDepartment(box.department);
  const list = (arr) => arr.length
    ? `<ul>${arr.map(c => `<li>${esc(c.label || c.lever)}</li>`).join("")}</ul>`
    : `<div class="empty-msg">None tied to this program</div>`;
  inspect.innerHTML = `
    <button type="button" class="close" id="inspectClose" aria-label="Close">×</button>
    <h3><span class="swatch" style="background:${esc(box.color)}"></span>${esc(box.department)}</h3>
    <div class="sub">
      Mass: ${esc(box.mass || "—")}<br/>
      Box: ${Number(box.dx).toFixed(1)} × ${Number(box.dy).toFixed(1)} × ${Number(box.dz).toFixed(1)} ft
      (L×W×H)${box.double_height ? " · double-height" : ""}<br/>
      Level ${box.level ?? "—"} · GSF ${box.gsf != null ? Number(box.gsf).toFixed(0) : "—"} sf<br/>
      Mass envelope: ${mass.length_ft != null ? Number(mass.length_ft).toFixed(1) : "—"} ×
      ${mass.width_ft != null ? Number(mass.width_ft).toFixed(1) : "—"} ft ·
      ${mass.stories != null ? mass.stories : "—"} fl
    </div>
    <div class="section req"><h4>Requirements</h4>${list(related.requirements)}</div>
    <div class="section lim"><h4>Limitations</h4>${list(related.limitations)}</div>
    <div class="section pref"><h4>Preferences</h4>${list(related.preferences)}</div>
  `;
  inspect.classList.add("show");
  document.getElementById("inspectClose")?.addEventListener("click", (e) => {
    e.stopPropagation();
    clearInspect();
  });
  for (const child of meshGroup.children) {
    if (!child.isMesh || !child.userData?.box) continue;
    const on = child.userData.box.id === box.id;
    if (child.material.emissive) child.material.emissive.setHex(on ? 0x334455 : 0x000000);
    child.material.opacity = on ? 1 : (child.userData.box.double_height ? 0.55 : 0.45);
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]
  ));
}

function renderProcess(t) {
  if (!t) {
    processBody.innerHTML = `<div class="empty-msg">Generate to see requirements, limitations, preferences, and search steps.</div>`;
    return;
  }
  if (activeTab === "interpreted") {
    const i = t.interpreted || {};
    const list = (arr) => (arr && arr.length)
      ? `<ul>${arr.map(c => `<li>${esc(c.label || c.lever)}</li>`).join("")}</ul>`
      : `<div class="empty-msg">None</div>`;
    const notes = (i.notes || []).slice(0, 10).map(n => esc(n)).join(" · ");
    const unk = (i.unknown_programs || []).map(esc).join(", ");
    processBody.innerHTML = `
      <div class="clause-col">
        <div class="req"><h4>Requirements</h4>${list(i.requirements)}</div>
        <div class="lim"><h4>Limitations</h4>${list(i.limitations)}</div>
        <div class="pref"><h4>Preferences</h4>${list(i.preferences)}</div>
      </div>
      ${notes ? `<div class="notes-line">Notes: ${notes}</div>` : ""}
      ${unk ? `<div class="notes-line">Unknown programs: ${unk}</div>` : ""}
      <div class="notes-line">Source: ${esc(i.source || "regex")}</div>`;
    return;
  }
  if (activeTab === "pool") {
    const pool = t.sample_pool || {};
    const schemes = pool.schemes || [];
    const arch = pool.archive_preview || [];
    if (!schemes.length && !arch.length) {
      processBody.innerHTML = `<div class="empty-msg">No sample-pool schemes yet — generate to run scheme search.</div>`;
      return;
    }
    if (schemes.length) {
      const selected = pool.selected_rank ?? selectedSchemeRank;
      processBody.innerHTML = `
        <div class="notes-line" style="margin-top:0">${pool.count} scheme(s) in pool · click a card to show it in the main 3D view</div>
        <div class="pool-grid">
          ${schemes.map(s => {
            const masses = (s.masses || []).map(m =>
              `${esc(m.mass_name || m.mass_id)} ${m.stories || "?"}fl`
            ).join(" · ");
            const sel = s.rank === selected ? "selected" : "";
            return `<button type="button" class="pool-card ${sel}" data-rank="${s.rank}">
              <canvas></canvas>
              <div class="cap">
                <div class="title">#${s.rank} · ${s.total_length_ft != null ? Number(s.total_length_ft).toFixed(0)+" ft" : "—"}
                  <span class="pill ${s.verified ? "ok" : "bad"}">${s.verified ? "ok" : "fail"}</span>
                </div>
                <div class="sub">${masses || "—"}</div>
              </div>
            </button>`;
          }).join("")}
        </div>`;
      requestAnimationFrame(() => mountPoolThumbs());
      return;
    }
    processBody.innerHTML = `
      <div class="notes-line" style="margin-top:0">${pool.count} legal cell(s) in COVER archive</div>
      <table class="cand-table">
        <thead><tr><th>Cell</th><th>Fit</th><th>Stories</th><th>Partition</th></tr></thead>
        <tbody>
          ${arch.map(c => {
            const stories = Object.entries(c.stories || {}).map(([k,v]) => `${esc(k)}:${v}`).join(" ");
            return `<tr>
              <td>${c.kept ? `<span class="pill kept">kept</span>` : ""} ${esc(c.label)}</td>
              <td><span class="pill ${c.fits ? "ok" : "bad"}">${c.fits ? "legal" : "over"}</span></td>
              <td>${esc(stories || "—")}</td>
              <td>${esc(c.partition || "—")}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>`;
    return;
  }
  if (activeTab === "candidates") {
    const cands = t.top_candidates || [];
    if (!cands.length) {
      processBody.innerHTML = `<div class="empty-msg">No archive elites yet — generate to build the COVER archive.</div>`;
      return;
    }
    processBody.innerHTML = `
      <table class="cand-table">
        <thead><tr><th>Candidate</th><th>Fit</th><th>Stories</th><th>Why</th></tr></thead>
        <tbody>
          ${cands.map(c => {
            const stories = Object.entries(c.stories || {}).map(([k,v]) => `${esc(k)}:${v}`).join(" ");
            return `<tr>
              <td>${c.kept ? `<span class="pill kept">kept</span>` : ""} ${esc(c.label)}</td>
              <td><span class="pill ${c.fits ? "ok" : "bad"}">${c.fits ? "legal" : "over"}</span></td>
              <td>${esc(stories || "—")}</td>
              <td>${esc(c.reason || "")}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>`;
    return;
  }
  // process / BO
  const p = t.process || {};
  const steps = p.steps || [];
  if (!steps.length) {
    processBody.innerHTML = `<div class="empty-msg">No process log.</div>`;
    return;
  }
  processBody.innerHTML = `
    <div class="notes-line" style="margin-top:0">Mode: ${esc(p.mode || "")}${p.full_explore ? " · full explore" : " · quick"}</div>
    ${steps.map(s => {
      const ops = (s.ops || []).map(o =>
        `${esc(o.op)} <span class="pill">${esc(o.kind)}</span>${o.ei != null ? " ei="+Number(o.ei).toFixed(3) : ""}${o.reward != null ? " r="+Number(o.reward).toFixed(3) : ""}`
      ).join("<br/>");
      const cands = (s.candidates || []).map(c =>
        `${esc(c.op)} ei=${c.ei != null ? Number(c.ei).toFixed(3) : "—"}`
      ).join("<br/>");
      const items = (s.items || []).map(it => `· ${esc(it.sentence || "")}`).join("<br/>");
      return `<div class="step">
        <div class="phase">${esc(s.phase)}</div>
        <div>${esc(s.summary || "")}</div>
        ${s.detail ? `<div class="ops">${esc(s.detail)}</div>` : ""}
        ${ops ? `<div class="ops">${ops}</div>` : ""}
        ${cands ? `<div class="ops">EI pool:<br/>${cands}</div>` : ""}
        ${items ? `<div class="ops">${items}</div>` : ""}
      </div>`;
    }).join("")}
    ${p.note ? `<div class="notes-line">${esc(p.note)}</div>` : ""}
    ${p.archive ? `<div class="notes-line">Archive: ${p.archive.legal || 0} legal / ${p.archive.attempts || 0} attempts / ${p.archive.cells || 0} cells</div>` : ""}
  `;
}

/* ——— Three.js ——— */
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x070707);
scene.fog = new THREE.Fog(0x070707, 800, 2200);

const camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
camera.position.set(280, 220, 320);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
viewport.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.target.set(100, 20, 40);

const amb = new THREE.AmbientLight(0xffffff, 0.55);
scene.add(amb);
const key = new THREE.DirectionalLight(0xffffff, 0.85);
key.position.set(200, 400, 180);
scene.add(key);
const fill = new THREE.DirectionalLight(0xa8b4c0, 0.25);
fill.position.set(-180, 80, -120);
scene.add(fill);

const grid = new THREE.GridHelper(1200, 40, 0x222222, 0x141414);
grid.position.y = 0;
scene.add(grid);

const meshGroup = new THREE.Group();
scene.add(meshGroup);

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerDown = null;

function onPointerDown(e) {
  pointerDown = { x: e.clientX, y: e.clientY };
}
function onPointerUp(e) {
  if (!pointerDown) return;
  const dx = e.clientX - pointerDown.x;
  const dy = e.clientY - pointerDown.y;
  pointerDown = null;
  if (dx * dx + dy * dy > 16) return; // drag = orbit, not pick
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(
    meshGroup.children.filter(c => c.isMesh && c.userData?.box),
    false
  );
  if (!hits.length) {
    clearInspect();
    return;
  }
  showInspect(hits[0].object.userData.box);
}
renderer.domElement.addEventListener("pointerdown", onPointerDown);
renderer.domElement.addEventListener("pointerup", onPointerUp);

function resize() {
  const w = viewport.clientWidth;
  const h = viewport.clientHeight;
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
}
window.addEventListener("resize", resize);
resize();

function renderMesh(mesh) {
  while (meshGroup.children.length) {
    const c = meshGroup.children.pop();
    c.geometry?.dispose();
    if (c.material) {
      if (Array.isArray(c.material)) c.material.forEach(m => m.dispose());
      else c.material.dispose();
    }
  }
  empty.classList.add("hide");

  for (const b of mesh.boxes || []) {
    const geo = new THREE.BoxGeometry(b.dx, b.dz, b.dy);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(b.color),
      metalness: 0.05,
      roughness: 0.72,
      transparent: true,
      opacity: b.double_height ? 0.88 : 0.92,
      emissive: new THREE.Color(0x000000),
    });
    const box = new THREE.Mesh(geo, mat);
    // Three Y-up: map our (x,y,z)=(length,width,up) → (x, z, y)
    box.position.set(b.x, b.z, b.y);
    box.userData.box = b;
    meshGroup.add(box);

    const edges = new THREE.EdgesGeometry(geo);
    const line = new THREE.LineSegments(
      edges,
      new THREE.LineBasicMaterial({
        color: b.double_height ? 0xffffff : 0xe8e6e1,
        transparent: true,
        opacity: b.double_height ? 0.55 : 0.28,
      })
    );
    line.position.copy(box.position);
    meshGroup.add(line);
  }

  const ext = mesh.extent || { x: 200, y: 100, z: 40 };
  const cx = ext.x / 2;
  const cy = ext.z / 2;
  const cz = ext.y / 2;
  controls.target.set(cx, cy, cz);
  const dist = Math.max(ext.x, ext.y, ext.z) * 1.35 + 80;
  camera.position.set(cx + dist * 0.7, cy + dist * 0.55, cz + dist * 0.75);
  controls.update();
}

(function loop() {
  requestAnimationFrame(loop);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "MassingExplorerUI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} {fmt % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            data = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                self._upload()
            elif path == "/api/generate":
                self._generate()
            elif path == "/api/apply_scheme":
                self._apply_scheme()
            else:
                self.send_error(404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, status=500)

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if "multipart/form-data" not in ctype:
            self._json({"ok": False, "error": "Expected multipart upload."}, 400)
            return
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
        if not boundary:
            self._json({"ok": False, "error": "Missing multipart boundary."}, 400)
            return
        filename, raw = _parse_multipart_file(body, boundary.encode("ascii"))
        if not filename or raw is None:
            self._json({"ok": False, "error": "No file in upload."}, 400)
            return
        suffix = Path(filename).suffix.lower()
        if suffix not in {".xlsx", ".xls", ".csv"}:
            self._json({"ok": False, "error": "Use Excel or CSV."}, 400)
            return
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(raw)
        from .load import load_program_file

        config = str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        program = load_program_file(str(dest), config_path=config)
        _STATE["program_path"] = str(dest)
        _STATE["program_name"] = filename
        _STATE["config_path"] = config
        self._json(
            {
                "ok": True,
                "name": filename,
                "departments": len(program.departments),
                "rooms": len(program.rooms),
            }
        )

    def _generate(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        brief = (payload.get("brief") or "").strip()
        do_explore = True  # Studio always runs the COVER / sample-pool path.
        path = _STATE.get("program_path")
        if not path:
            self._json({"ok": False, "error": "Upload a program first."}, 400)
            return
        if not brief:
            self._json({"ok": False, "error": "Brief is empty."}, 400)
            return

        from .brief import apply_brief, parse_brief, briefing_from_parsed
        from .config import load_project_config
        from .explore.ui_payload import transparency_payload
        from .load import load_program_file
        from .preview3d import preview_mesh
        from .session import StudySession, slugify_study_id
        from .solver import solve_massing_study

        config_path = _STATE.get("config_path") or (
            str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        )
        program = load_program_file(path, config_path=config_path)
        study_id = slugify_study_id(f"ui_{int(time.time())}")
        session = StudySession(
            study_id=study_id,
            program=program,
            config_path=config_path or "",
        )
        session.save()

        note = ""
        parsed = parse_brief(brief, session.department_names())
        out = apply_brief(session, brief)
        if out.get("skipped"):
            self._json(
                {
                    "ok": False,
                    "error": out.get("reason") or "Brief did not change grouping or site.",
                },
                400,
            )
            return
        note = (session.constraints.get("explore") or {}).get("note") or "COVER complete."
        # Scheme search also runs inside COVER; if the pool is still empty
        # (e.g. search pruned everything), try once more for the UI thumbs.
        if not (session.last_search or []):
            from .tools import search_site_schemes

            searched = search_site_schemes(
                session,
                max_total_length_ft=session.constraints.get("max_total_length_ft"),
                max_length_ft=session.constraints.get("max_building_length_ft"),
                max_width_ft=session.constraints.get("max_building_width_ft"),
                max_stories=int(
                    parsed.max_stories
                    or session.constraints.get("max_stories")
                    or 4
                ),
                preference=parsed.preference or "balanced",
                top_n=8,
            )
            if searched.get("found"):
                note = (note + " " if note else "") + (
                    f"Sample pool: {searched['found']} scheme(s)."
                )
            elif searched.get("notes"):
                note = (note + " " if note else "") + str(searched["notes"][0])

        if "briefing" not in session.constraints:
            session.constraints["briefing"] = briefing_from_parsed(parsed)

        result = solve_massing_study(session, config_path=config_path)
        try:
            config = load_project_config(config_path) if config_path else {}
        except FileNotFoundError:
            config = {}
        mesh = preview_mesh(result, config=config)
        # Confirm every spreadsheet department landed in the mesh.
        program_names = {d.name for d in program.departments}
        boxed = {b.get("department") for b in mesh.get("boxes") or []}
        missing = sorted(program_names - boxed)
        if missing:
            note = (note + " " if note else "") + (
                "Warning: not yet drawn in 3D: " + ", ".join(missing)
            )
        else:
            note = (note + " " if note else "") + (
                f"All {len(program_names)} spreadsheet programs drawn with split colors."
            )
        grouping = [
            {"id": m.id, "name": m.name, "departments": m.departments}
            for m in session.masses
        ]
        try:
            transparency = transparency_payload(
                session,
                parsed=parsed,
                briefing=session.constraints.get("briefing"),
                full_explore=do_explore,
            )
        except Exception as exc:
            traceback.print_exc()
            transparency = {
                "interpreted": {
                    "source": "error",
                    "requirements": [],
                    "limitations": [],
                    "preferences": [],
                    "notes": [f"Transparency panel failed: {exc}"],
                    "unknown_programs": [],
                    "unmatched": [],
                },
                "sample_pool": {"count": 0, "schemes": [], "archive_preview": [], "selected_rank": None},
                "top_candidates": [],
                "process": {
                    "mode": "error",
                    "full_explore": do_explore,
                    "steps": [{"phase": "ERROR", "summary": str(exc), "detail": ""}],
                    "archive": None,
                    "note": str(exc),
                },
            }
            note = (note + " " if note else "") + f"Transparency panel error: {exc}"
        session.save()
        _STATE["study_id"] = session.study_id
        _STATE["config_path"] = config_path
        self._json(
            {
                "ok": True,
                "note": note,
                "study_id": session.study_id,
                "grouping": grouping,
                "mesh": mesh,
                "explore": do_explore,
                "program_count": len(program_names),
                "boxed_departments": sorted(boxed),
                "transparency": transparency,
            }
        )

    def _apply_scheme(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        study_id = payload.get("study_id") or _STATE.get("study_id")
        index = int(payload.get("index", 0))
        if not study_id:
            self._json({"ok": False, "error": "No study loaded. Generate first."}, 400)
            return

        from .config import load_project_config
        from .preview3d import preview_mesh
        from .session import StudySession
        from .solver import solve_massing_study
        from .tools import apply_scheme

        try:
            session = StudySession.load(str(study_id))
        except FileNotFoundError:
            self._json({"ok": False, "error": f"Study '{study_id}' not found."}, 404)
            return

        if not session.last_search:
            self._json({"ok": False, "error": "No sample-pool schemes on this study."}, 400)
            return
        if index < 0 or index >= len(session.last_search):
            self._json(
                {
                    "ok": False,
                    "error": f"Scheme index must be 0..{len(session.last_search) - 1}",
                },
                400,
            )
            return

        applied = apply_scheme(session, index)
        if not applied.get("ok"):
            self._json({"ok": False, "error": applied.get("error") or "Apply failed."}, 400)
            return

        config_path = session.config_path or _STATE.get("config_path") or (
            str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        )
        result = solve_massing_study(session, config_path=config_path or None)
        try:
            config = load_project_config(config_path) if config_path else {}
        except FileNotFoundError:
            config = {}
        mesh = preview_mesh(result, config=config)
        session.save()
        _STATE["study_id"] = session.study_id
        scheme = session.last_search[index]
        note = (
            f"Showing scheme #{index}: {scheme.get('total_length_ft', '?')} ft total, "
            f"{len(scheme.get('masses') or [])} masses."
        )
        self._json(
            {
                "ok": True,
                "note": note,
                "study_id": session.study_id,
                "index": index,
                "mesh": mesh,
            }
        )


def _parse_multipart_file(body: bytes, boundary: bytes) -> tuple[str | None, bytes | None]:
    sep = b"--" + boundary
    for chunk in body.split(sep):
        if not chunk or chunk in (b"--", b"--\r\n", b"\r\n"):
            continue
        if chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if b"\r\n\r\n" not in chunk:
            continue
        head, data = chunk.split(b"\r\n\r\n", 1)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        header_text = head.decode("utf-8", errors="replace")
        if "filename=" not in header_text:
            continue
        name = None
        for line in header_text.split("\r\n"):
            if "filename=" in line:
                # Content-Disposition: form-data; name="file"; filename="x.xlsx"
                for token in line.split(";"):
                    token = token.strip()
                    if token.lower().startswith("filename="):
                        name = token.split("=", 1)[1].strip().strip('"')
        if name:
            return name, data
    return None, None


def main(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Massing Explorer UI -> {url}", flush=True)
    print("Drop a program Excel, type a brief, Generate. Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
