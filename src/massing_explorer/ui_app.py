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
  .drop-wrap { position: relative; }
  .file-clear {
    margin-top: 8px;
    width: 100%;
    border: 1px solid var(--line-strong);
    background: transparent;
    color: var(--muted);
    padding: 8px 12px;
    font-family: inherit;
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .file-clear:hover { color: var(--bad); border-color: var(--bad); }
  .file-clear[hidden] { display: none; }
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
  .run-progress {
    display: none;
    flex-direction: column;
    gap: 8px;
    margin-top: -8px;
  }
  .run-progress.show { display: flex; }
  .run-progress .bar {
    height: 3px;
    background: #1c1c1c;
    border: 1px solid var(--line);
    overflow: hidden;
    position: relative;
  }
  .run-progress .bar > span {
    position: absolute;
    inset: 0 auto 0 0;
    width: 40%;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    animation: run-progress-slide 1.1s ease-in-out infinite;
  }
  .run-progress.determinate .bar > span {
    animation: none;
    width: var(--pct, 8%);
    background: var(--accent);
    transition: width 0.35s ease;
  }
  @keyframes run-progress-slide {
    0% { left: -40%; }
    100% { left: 100%; }
  }
  .run-progress .phase {
    font-size: 11px;
    color: var(--faint);
    letter-spacing: 0.04em;
  }
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
  .pool-card .cell-id {
    font-family: "JetBrains Mono", monospace;
    font-size: 10px;
    color: var(--faint);
    word-break: break-all;
    line-height: 1.35;
    margin-top: 6px;
  }
  .cand-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 14px;
    margin-top: 10px;
  }
  .cand-grid .pool-card canvas {
    height: 130px;
  }
  .cand-radars {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    padding: 0 8px 10px;
    pointer-events: none;
  }
  .cand-radar .radar-title {
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--faint);
    text-align: center;
    margin-bottom: 2px;
  }
  .cand-radar svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .space-wrap {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .space-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    font-size: 11px;
    color: var(--faint);
  }
  .space-legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .space-swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
  }
  .space-swatch.illegal { background: #3a3a3a; }
  .space-swatch.legal { background: #6a8a9a; }
  .space-swatch.elite { background: #d4a84b; box-shadow: 0 0 0 2px rgba(212,168,75,0.35); }
  .space-block h4 {
    margin: 0 0 6px;
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .space-block .space-pca-meta {
    margin: 0 0 8px;
    font-size: 11px;
    color: var(--faint);
    line-height: 1.4;
  }
  .space-cloud {
    position: relative;
    width: 100%;
    height: 320px;
    background: #0a0a0a;
    border: 1px solid #1c1c1c;
    overflow: hidden;
  }
  .space-cloud canvas {
    width: 100% !important;
    height: 100% !important;
    display: block;
    cursor: grab;
  }
  .space-cloud canvas:active { cursor: grabbing; }
  .space-cloud-tip {
    position: absolute;
    left: 10px;
    bottom: 8px;
    right: 10px;
    font-size: 11px;
    color: var(--muted);
    pointer-events: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .space-elite-list {
    margin: 0;
    padding-left: 18px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  .space-elite-list li {
    margin-bottom: 4px;
  }
  .space-elite-list button {
    background: none;
    border: none;
    color: var(--warn);
    cursor: pointer;
    padding: 0;
    font: inherit;
    text-decoration: underline;
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
    left: 0; right: 0; bottom: 0;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 24px;
    padding: 18px 22px;
    background: linear-gradient(transparent, rgba(7,7,7,0.85));
    pointer-events: none;
    z-index: 6;
  }
  .unit-toggle {
    pointer-events: auto;
    display: flex;
    gap: 0;
    border: 1px solid var(--line-strong);
    width: max-content;
    margin-top: 4px;
  }
  .hud .unit-toggle {
    margin-bottom: 8px;
  }
  .unit-toggle button {
    background: transparent;
    border: none;
    color: var(--faint);
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 7px 14px;
    cursor: pointer;
  }
  .unit-toggle button.active {
    color: #0a0a0a;
    background: var(--accent);
  }
  .hud .meta {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.6;
    pointer-events: auto;
  }
  .hud .meta strong {
    color: var(--text);
    font-weight: 500;
  }
  .hud .meta .fail-link {
    color: #c48888;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 2px;
    background: none;
    border: none;
    padding: 0;
    font: inherit;
  }
  .hud .meta .fail-link:hover { color: #e0a0a0; }
  .hud .meta .ok-checks { color: #8faf9a; }
  .checks-panel {
    position: absolute;
    left: 18px;
    bottom: 72px;
    max-width: min(520px, 92vw);
    max-height: min(42vh, 360px);
    overflow: auto;
    padding: 12px 14px;
    background: rgba(10,10,10,0.96);
    border: 1px solid #3a3a3a;
    box-shadow: 0 8px 28px rgba(0,0,0,0.45);
    z-index: 8;
    pointer-events: auto;
    display: none;
  }
  .checks-panel.show { display: block; }
  .checks-panel h3 {
    margin: 0 0 8px;
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #c8c4bc;
    font-weight: 500;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .checks-panel h3 button {
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    padding: 0 2px;
  }
  .checks-panel .item {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #d8d4cc;
    line-height: 1.4;
    margin: 8px 0;
    padding-left: 10px;
    border-left: 2px solid #c48888;
  }
  .checks-panel .item .check-id {
    color: #c48888;
    display: block;
    margin-bottom: 2px;
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
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.72);
    display: none;
    place-items: center;
    z-index: 40;
    padding: 24px;
  }
  .modal-backdrop.show { display: grid; }
  .modal {
    width: min(560px, 100%);
    max-height: min(80vh, 720px);
    overflow: auto;
    background: #0e0e0e;
    border: 1px solid var(--line-strong);
    padding: 22px 22px 18px;
  }
  .modal h2 {
    margin: 0 0 8px;
    font-size: 13px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    font-weight: 500;
    color: var(--accent);
  }
  .modal .lead {
    margin: 0 0 16px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .modal-q {
    border-top: 1px solid var(--line);
    padding: 14px 0 10px;
  }
  .modal-q textarea.clause-edit {
    width: 100%;
    min-height: 64px;
    margin: 0 0 10px;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    line-height: 1.45;
    color: var(--text);
    background: #0c0c0c;
    border: 1px solid var(--line-strong);
    padding: 8px 10px;
    resize: vertical;
  }
  .modal-q .hint {
    color: var(--faint);
    font-size: 11px;
    margin-bottom: 10px;
  }
  .modal-q .choices {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .modal-q .choices label {
    margin: 0;
    letter-spacing: 0;
    text-transform: none;
    font-size: 12px;
    color: var(--muted);
    border: 1px solid var(--line-strong);
    padding: 8px 10px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .modal-q .choices label:has(input:checked) {
    border-color: var(--accent);
    color: var(--accent);
  }
  .modal-actions {
    display: flex;
    gap: 10px;
    margin-top: 16px;
  }
  .modal-actions button {
    flex: 1;
    border: 1px solid var(--accent);
    background: transparent;
    color: var(--accent);
    padding: 11px 14px;
    font-family: inherit;
    font-size: 11px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .modal-actions button.ghost {
    border-color: var(--line-strong);
    color: var(--muted);
  }
  .learn-modal { max-width: min(920px, 96vw); }
  .learn-pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 12px 0 4px;
  }
  .learn-card {
    border: 1px solid var(--line-strong);
    padding: 10px;
    background: rgba(255,255,255,0.02);
    cursor: pointer;
    transition: border-color 0.15s ease, background 0.15s ease;
    width: 100%;
    text-align: left;
    font: inherit;
    color: inherit;
    display: block;
  }
  .learn-card:hover,
  .learn-card:focus-visible {
    border-color: var(--accent, #d4a84b);
    background: rgba(212,168,75,0.06);
    outline: none;
  }
  .learn-card .hint-pick {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--faint);
    margin-top: 8px;
  }
  .learn-card canvas {
    width: 100%;
    height: 160px;
    display: block;
    background: #070707;
  }
  .learn-card .side {
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .learn-card .label {
    font-size: 13px;
    color: var(--text);
    margin: 8px 0 4px;
    line-height: 1.35;
  }
  .learn-card .axes {
    font-size: 11px;
    color: var(--faint);
    line-height: 1.45;
  }
  @media (max-width: 720px) {
    .learn-pair { grid-template-columns: 1fr; }
  }
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
      <div class="drop-wrap">
        <div class="drop" id="drop">
          <div class="name" id="fileName">Drop Excel / CSV</div>
          <div class="hint">.xlsx · .xls · .csv</div>
        </div>
        <button type="button" class="file-clear" id="fileClear" hidden>Remove file</button>
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
    <div class="run-progress" id="runProgress" aria-hidden="true">
      <div class="bar"><span id="runProgressFill"></span></div>
      <div class="phase" id="runProgressPhase">Working…</div>
    </div>
  </aside>

  <main>
    <div class="stage">
      <div class="empty" id="empty">Orbit · drag to rotate · scroll to zoom</div>
      <div id="viewport"></div>
      <div class="legend" id="legend"></div>
      <div class="inspect" id="inspect"></div>
      <div class="checks-panel" id="checksPanel" aria-hidden="true"></div>
      <div class="hud">
        <div>
          <div class="unit-toggle" data-unit-toggle>
            <button type="button" data-unit="ft" class="active">ft</button>
            <button type="button" data-unit="m">m</button>
          </div>
          <div class="meta" id="meta"></div>
        </div>
        <div class="mass-list" id="massList"></div>
      </div>
    </div>
    <section class="process" id="process">
      <div class="process-tabs" id="processTabs">
        <button type="button" data-tab="interpreted" class="active">Interpreted</button>
        <button type="button" data-tab="pool">Sample pool</button>
        <button type="button" data-tab="space">Space</button>
        <button type="button" data-tab="candidates">Top candidates</button>
        <button type="button" data-tab="process">Process / BO</button>
      </div>
      <div class="process-body" id="processBody">
        <div class="empty-msg">Generate to see requirements, limitations, preferences, and search steps.</div>
      </div>
    </section>
  </main>

  <div class="modal-backdrop" id="modalityModal" aria-hidden="true">
    <div class="modal" role="dialog" aria-labelledby="modalityTitle">
      <h2 id="modalityTitle">Classify wording</h2>
      <p class="lead">
        These clauses use wording we have not learned yet. Fix any typos
        in the box — we remember the corrected line, not the misspelling.
        Then pick requirement, limitation, or preference.
      </p>
      <div id="modalityQuestions"></div>
      <div class="modal-actions">
        <button type="button" class="ghost" id="modalitySkip">Skip for now</button>
        <button type="button" id="modalityApply">Save &amp; generate</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="learnModal" aria-hidden="true">
    <div class="modal learn-modal" role="dialog" aria-labelledby="learnTitle">
      <h2 id="learnTitle">Which scheme do you prefer?</h2>
      <p class="lead" id="learnLead">
        Click the scheme you prefer. Soft taste only — requirements and caps stay locked.
      </p>
      <div class="learn-pair" id="learnPair"></div>
      <div class="modal-actions">
        <button type="button" class="ghost" id="learnSkip">Skip for now</button>
      </div>
    </div>
  </div>

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
const fileClear = document.getElementById("fileClear");
const brief = document.getElementById("brief");
const explore = document.getElementById("explore");
const go = document.getElementById("go");
const status = document.getElementById("status");
const runProgress = document.getElementById("runProgress");
const runProgressFill = document.getElementById("runProgressFill");
const runProgressPhase = document.getElementById("runProgressPhase");
const empty = document.getElementById("empty");
const meta = document.getElementById("meta");
const massList = document.getElementById("massList");
const legend = document.getElementById("legend");
const inspect = document.getElementById("inspect");
const viewport = document.getElementById("viewport");
const processTabs = document.getElementById("processTabs");
const processBody = document.getElementById("processBody");

let hasFile = false;
let displayUnit = (localStorage.getItem("massing_unit") === "m") ? "m" : "ft";
const FT_TO_M = 0.3048;
let lastTransparency = null;
let lastMesh = null;
let activeTab = "interpreted";
let selectedSchemeRank = 0;
let studyId = null;
let selectedBoxId = null;
let pendingModality = null;

const modalityModal = document.getElementById("modalityModal");
const modalityQuestions = document.getElementById("modalityQuestions");
const modalityApply = document.getElementById("modalityApply");
const modalitySkip = document.getElementById("modalitySkip");
const learnModal = document.getElementById("learnModal");
const learnPairEl = document.getElementById("learnPair");
const learnLead = document.getElementById("learnLead");
const learnSkip = document.getElementById("learnSkip");
let pendingLearnPair = null;

function fmtAxis(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  if (n >= 0 && n <= 1.0001) return Math.round(n * 100) + "%";
  return n.toFixed(2);
}

function learnCardHtml(side, card) {
  const axes = card.axes || {};
  const axisLine = [
    ["coherence", axes.program_coherence],
    ["pref", axes.preference_alignment],
    ["perf", axes.performance_efficiency],
    ["robust", axes.robustness],
  ].map(([k, v]) => `${k} ${fmtAxis(v)}`).join(" · ");
  return `<button type="button" class="learn-card" data-side="${side}" aria-label="Prefer scheme ${side.toUpperCase()}">
    <div class="side">Scheme ${side.toUpperCase()}</div>
    <canvas aria-hidden="true"></canvas>
    <div class="label">${esc(card.label || card.cell_id || side)}</div>
    <div class="axes">${esc(axisLine)}</div>
    <div class="hint-pick">Click to prefer</div>
  </button>`;
}

function openLearnModal(pair) {
  pendingLearnPair = pair || null;
  if (!pendingLearnPair || !pendingLearnPair.a || !pendingLearnPair.b) return;
  const n = pendingLearnPair.comparisons || 0;
  const maxN = pendingLearnPair.max_comparisons || 5;
  const kind = pendingLearnPair.kind ? ` · ${pendingLearnPair.kind}` : "";
  if (learnLead) {
    const progress = `Question ${Math.min(n + 1, maxN)} of ${maxN}`;
    learnLead.textContent = `${progress}${kind}. `
      + (pendingLearnPair.note || "Click the scheme you prefer. Soft taste only — requirements and caps stay locked.");
  }
  learnPairEl.innerHTML = learnCardHtml("a", pendingLearnPair.a) + learnCardHtml("b", pendingLearnPair.b);
  learnModal.classList.add("show");
  learnModal.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => {
    for (const card of learnPairEl.querySelectorAll(".learn-card")) {
      const side = card.dataset.side;
      const mesh = pendingLearnPair[side]?.preview;
      const canvas = card.querySelector("canvas");
      if (canvas && mesh) paintSchemeThumb(canvas, mesh);
      card.addEventListener("click", () => submitLearnChoice(side));
    }
  });
}

function closeLearnModal() {
  learnModal.classList.remove("show");
  learnModal.setAttribute("aria-hidden", "true");
  pendingLearnPair = null;
}

function maybeOpenLearnPair(transparency) {
  const pair = transparency && transparency.learn_pair;
  if (pair && pair.a && pair.b) openLearnModal(pair);
}

async function submitLearnChoice(winner) {
  if (!studyId) {
    setStatus("No study loaded.", "warn");
    return;
  }
  if (learnPairEl) {
    for (const btn of learnPairEl.querySelectorAll(".learn-card")) btn.disabled = true;
  }
  if (learnSkip) learnSkip.disabled = true;
  const learnLabel = `Recording preference ${String(winner).toUpperCase()}…`;
  setStatus(learnLabel);
  startRunProgress(learnLabel);
  try {
    const res = await fetch("/api/learn_choice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ study_id: studyId, winner }),
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Could not record choice", "warn");
      return;
    }
    closeLearnModal();
    if (data.mesh) {
      renderMesh(data.mesh);
      renderLegend(data.mesh);
      lastMesh = data.mesh;
      clearInspect();
      updateHud(data.mesh);
    }
    lastTransparency = data.transparency || lastTransparency;
    renderProcess(lastTransparency);
    setStatus(data.note || "Preference recorded.", "ok");
    maybeOpenLearnPair(lastTransparency);
  } catch (err) {
    setStatus(String(err), "bad");
  } finally {
    stopRunProgress();
    if (learnSkip) learnSkip.disabled = false;
    if (learnPairEl) {
      for (const btn of learnPairEl.querySelectorAll(".learn-card")) btn.disabled = false;
    }
  }
}

learnSkip.addEventListener("click", () => {
  closeLearnModal();
  setStatus("Skipped A/B for now. Open Process → steps to see LEARN pending.", "warn");
});

function openModalityModal(questions) {
  pendingModality = questions || [];
  const unresolved = pendingModality.some((q) => q.reason === "unresolved");
  const title = document.getElementById("modalityTitle");
  const lead = modalityModal.querySelector(".lead");
  if (title) title.textContent = unresolved ? "Need a reading" : "Classify wording";
  if (lead) {
    lead.textContent = unresolved
      ? "Every number in these clauses must land on a lever. Fix typos in the box, then confirm requirement / limitation / preference. We remember the corrected wording."
      : "Fix any typos in the box, then pick requirement, limitation, or preference. We remember the corrected line, not the misspelling.";
  }
  modalityQuestions.innerHTML = pendingModality.map((q, i) => {
    const sug = q.suggested ? ` · model lean: ${q.suggested} (${Math.round((q.confidence||0)*100)}%)` : "";
    const unp = q.unplaced_numbers ? ` · unplaced: ${q.unplaced_numbers}` : "";
    const why = q.rationale ? ` — ${q.rationale}` : "";
    const pre = q.suggested || "";
    return `<div class="modal-q" data-idx="${i}">
      <textarea class="clause-edit" aria-label="Corrected clause">${esc(q.text)}</textarea>
      <div class="hint">${esc((q.source || "unknown") + sug + unp + why)}</div>
      <div class="choices">
        <label><input type="radio" name="mod${i}" value="requirement" ${pre==="requirement"?"checked":""}/> Requirement</label>
        <label><input type="radio" name="mod${i}" value="limitation" ${pre==="limitation"?"checked":""}/> Limitation</label>
        <label><input type="radio" name="mod${i}" value="preference" ${pre==="preference"?"checked":""}/> Preference</label>
      </div>
    </div>`;
  }).join("");
  modalityModal.classList.add("show");
  modalityModal.setAttribute("aria-hidden", "false");
}

function closeModalityModal() {
  modalityModal.classList.remove("show");
  modalityModal.setAttribute("aria-hidden", "true");
}

function collectModalityAnswers() {
  if (!pendingModality) return [];
  return pendingModality.map((q, i) => {
    const picked = modalityQuestions.querySelector(`input[name="mod${i}"]:checked`);
    const edited = (modalityQuestions.querySelector(`.modal-q[data-idx="${i}"] textarea.clause-edit`)?.value || q.text || "").trim();
    return {
      text: q.text,
      corrected: edited,
      kind: picked ? picked.value : (q.suggested || null),
      cue: q.cue || "",
      reason: q.reason || "modality",
    };
  }).filter(a => a.kind);
}

function patchBriefFromAnswers(answers) {
  let text = brief.value;
  for (const a of answers || []) {
    if (a.text && a.corrected && a.text !== a.corrected && text.includes(a.text)) {
      text = text.replace(a.text, a.corrected);
    }
  }
  brief.value = text;
}

modalityApply.addEventListener("click", async () => {
  const answers = collectModalityAnswers();
  patchBriefFromAnswers(answers);
  closeModalityModal();
  await runGenerate(answers, false);
});
modalitySkip.addEventListener("click", async () => {
  closeModalityModal();
  await runGenerate([], true);
});

processTabs.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  activeTab = btn.dataset.tab;
  for (const b of processTabs.querySelectorAll("button")) {
    b.classList.toggle("active", b === btn);
  }
  renderProcess(lastTransparency);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeChecksPanel();
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
  setFileLoaded(true, data.name, data.departments + " departments · " + data.rooms + " rooms");
}

go.addEventListener("click", async () => {
  if (!hasFile) { setStatus("Drop a program file first.", "warn"); return; }
  const text = brief.value.trim();
  if (!text) { setStatus("Type a brief.", "warn"); return; }
  await runGenerate([], false);
});

let runProgressTimer = null;
let runProgressStep = 0;
const RUN_PHASES = [
  "Reading brief…",
  "COVER sampling…",
  "REPAIR / planner…",
  "MCTS / BO…",
  "REFINE / archive…",
];

function startRunProgress(label) {
  if (!runProgress) return;
  stopRunProgress();
  runProgress.classList.add("show");
  runProgress.classList.remove("determinate");
  runProgress.setAttribute("aria-hidden", "false");
  runProgressStep = 0;
  if (runProgressPhase) runProgressPhase.textContent = label || RUN_PHASES[0];
  if (runProgressFill) runProgressFill.style.width = "";
  runProgressTimer = setInterval(() => {
    runProgressStep = Math.min(runProgressStep + 1, RUN_PHASES.length - 1);
    if (runProgressPhase) {
      runProgressPhase.textContent = RUN_PHASES[runProgressStep];
    }
    runProgress.classList.add("determinate");
    const pct = 12 + runProgressStep * 18;
    runProgress.style.setProperty("--pct", `${Math.min(pct, 88)}%`);
  }, 4500);
}

function stopRunProgress() {
  if (runProgressTimer) {
    clearInterval(runProgressTimer);
    runProgressTimer = null;
  }
  if (!runProgress) return;
  runProgress.classList.remove("show", "determinate");
  runProgress.setAttribute("aria-hidden", "true");
  runProgress.style.removeProperty("--pct");
}

async function runGenerate(modalityAnswers, skipModalityAsk) {
  const text = brief.value.trim();
  go.disabled = true;
  const kickoff = skipModalityAsk
    ? "COVER sampling…"
    : (modalityAnswers?.length
      ? "Applying your reading, then COVER sampling…"
      : "Reading brief, then COVER sampling…");
  setStatus(kickoff);
  startRunProgress(kickoff);
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief: text,
        explore: true,
        modality_answers: modalityAnswers || [],
        skip_modality_ask: !!skipModalityAsk,
      }),
    });
    const data = await res.json();
    if (data.needs_modality && (data.questions || []).length) {
      setStatus("Need your read on a few clauses…", "warn");
      openModalityModal(data.questions);
      return;
    }
    if (!data.ok) {
      setStatus(data.error || "Generate failed", "bad");
      if (/upload a program/i.test(data.error || "")) setFileLoaded(false);
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
    closeChecksPanel();
    const pass = data.mesh.all_checks_passed;
    const nFail = normalizeFailedChecks(data.mesh.failed_checks).length;
    setStatus(
      (pass
        ? "Checks passed. "
        : `Some checks failed (${nFail}) — click “failed check(s)” in the HUD. `) + (data.note || ""),
      pass ? "ok" : "warn"
    );
    updateHud(data.mesh);
    maybeOpenLearnPair(lastTransparency);
  } catch (err) {
    setStatus(String(err), "bad");
  } finally {
    stopRunProgress();
    go.disabled = false;
  }
}

function fmtLen(ft, digits) {
  if (ft == null || Number.isNaN(Number(ft))) return "—";
  const n = Number(ft);
  if (displayUnit === "m") return (n * FT_TO_M).toFixed(digits == null ? 1 : digits) + " m";
  return n.toFixed(digits == null ? 0 : digits) + " ft";
}
function fmtArea(sf, digits) {
  if (sf == null || Number.isNaN(Number(sf))) return "—";
  const n = Number(sf);
  if (displayUnit === "m") return (n * FT_TO_M * FT_TO_M).toFixed(digits == null ? 0 : digits) + " m²";
  return n.toFixed(digits == null ? 0 : digits) + " sf";
}
function syncUnitToggle() {
  document.querySelectorAll("[data-unit-toggle] button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.unit === displayUnit);
  });
}
function applyDisplayUnit(unit) {
  displayUnit = unit === "m" ? "m" : "ft";
  localStorage.setItem("massing_unit", displayUnit);
  syncUnitToggle();
  if (lastMesh) updateHud(lastMesh);
  if (selectedBoxId && lastMesh) {
    const box = (lastMesh.boxes || []).find(b => b.id === selectedBoxId);
    if (box) showInspect(box);
  }
  renderProcess(lastTransparency);
}

document.querySelectorAll("[data-unit-toggle]").forEach(el => {
  el.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-unit]");
    if (btn) applyDisplayUnit(btn.dataset.unit);
  });
});
syncUnitToggle();

function setFileLoaded(on, name, detail) {
  hasFile = !!on;
  fileName.textContent = on ? (name || "Program loaded") : "Drop Excel / CSV";
  if (fileClear) fileClear.hidden = !on;
  if (fileInput && !on) fileInput.value = "";
  if (detail) setStatus(detail, "ok");
}

fileClear?.addEventListener("click", async (e) => {
  e.preventDefault();
  e.stopPropagation();
  try {
    await fetch("/api/clear_program", { method: "POST" });
  } catch (_) {}
  setFileLoaded(false);
  setStatus("Program file removed.", "ok");
});

function setStatus(text, kind) {
  status.textContent = text;
  status.className = "status" + (kind ? " " + kind : "");
}

const checksPanel = document.getElementById("checksPanel");

function normalizeFailedChecks(raw) {
  return (raw || []).map((item) => {
    if (item && typeof item === "object") {
      return {
        check: String(item.check || item.id || "check"),
        message: String(item.message || item.msg || item.check || ""),
      };
    }
    return { check: "check", message: String(item ?? "") };
  }).filter((item) => item.message);
}

function closeChecksPanel() {
  if (!checksPanel) return;
  checksPanel.classList.remove("show");
  checksPanel.setAttribute("aria-hidden", "true");
  checksPanel.innerHTML = "";
}

function openChecksPanel(failed) {
  if (!checksPanel) return;
  const items = normalizeFailedChecks(failed);
  if (!items.length) {
    closeChecksPanel();
    return;
  }
  checksPanel.innerHTML = `
    <h3><span>${items.length} failed check${items.length === 1 ? "" : "s"}</span>
      <button type="button" id="checksClose" aria-label="Close">×</button></h3>
    ${items.map((item) => `
      <div class="item">
        <span class="check-id">${esc(item.check)}</span>
        ${esc(item.message)}
      </div>`).join("")}
  `;
  checksPanel.classList.add("show");
  checksPanel.setAttribute("aria-hidden", "false");
  document.getElementById("checksClose")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeChecksPanel();
  });
}

function toggleChecksPanel(failed) {
  if (checksPanel?.classList.contains("show")) closeChecksPanel();
  else openChecksPanel(failed);
}

function updateHud(mesh) {
  if (!mesh) return;
  const nBoxes = (mesh.boxes || []).length;
  const nDept = (mesh.legend?.departments || []).length;
  const failed = normalizeFailedChecks(mesh.failed_checks);
  meta.innerHTML = `<strong>${(mesh.masses || []).length}</strong> masses · ` +
    `<strong>${nBoxes}</strong> program boxes · ` +
    `<strong>${nDept}</strong> depts · ` +
    `story <strong>${fmtLen(mesh.story_height_ft, 1)}</strong>` +
    (mesh.envelope ? ` · <span style="color:#c9a27a">envelope</span>` : "") +
    (failed.length
      ? ` · <button type="button" class="fail-link" id="failChecksBtn">${failed.length} failed check(s)</button>`
      : ` · <span class="ok-checks">checks ok</span>`);
  const failBtn = document.getElementById("failChecksBtn");
  if (failBtn) {
    failBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleChecksPanel(failed);
    });
  } else {
    closeChecksPanel();
  }
  const totalLen = (mesh.masses || []).reduce((s, m) => s + (m.length_ft || 0), 0);
  const gsf = (mesh.boxes || []).reduce((s, b) => s + (Number(b.gsf) || 0), 0)
    || (mesh.masses || []).reduce((s, m) => s + (Number(m.length_ft) || 0) * (Number(m.width_ft) || 0) * (Number(m.stories) || 1), 0);
  massList.innerHTML = (mesh.masses || []).map(m =>
    `${m.name} · ${fmtLen(m.length_ft, 1)}×${fmtLen(m.width_ft, 1)} · ${m.stories} fl`
  ).join("<br/>") + `<br/><br/>frontage ~ ${fmtLen(totalLen, 0)}` +
    (gsf ? `<br/>area ~ ${fmtArea(gsf, 0)}` : "");
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
  if (scheme.preview) {
    renderMesh(scheme.preview);
    renderLegend(scheme.preview);
    lastMesh = scheme.preview;
    updateHud(scheme.preview);
  }
  renderProcess(lastTransparency);
  if (studyId == null) return;
  // Persist selection in the background; card preview already updated the view.
  if (scheme.source === "archive" && scheme.cell_id) {
    // Envelope preview is interim; always swap in the full program mesh when ready.
    applyArchiveCell(scheme.cell_id, { quiet: true, skipMesh: false });
    return;
  }
  if (scheme.preview) {
    setStatus(`Previewing scheme #${rank}.`, "ok");
    return;
  }
  setStatus(`Loading scheme #${rank}…`);
  try {
    const res = await fetch("/api/apply_scheme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ study_id: studyId, index: scheme.index ?? rank }),
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

async function applyArchiveCell(cellId, opts = {}) {
  const quiet = Boolean(opts.quiet);
  const skipMesh = Boolean(opts.skipMesh);
  if (!quiet) setStatus("Loading COVER candidate…");
  try {
    const res = await fetch("/api/apply_cell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ study_id: studyId, cell_id: cellId }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (!quiet) setStatus(data.error || "Could not open that cell", "warn");
      return;
    }
    if (!skipMesh && data.mesh) {
      renderMesh(data.mesh);
      renderLegend(data.mesh);
      lastMesh = data.mesh;
      clearInspect();
      updateHud(data.mesh);
    }
    if (!quiet) {
      const pass = data.mesh && data.mesh.all_checks_passed;
      setStatus(
        (pass ? "Candidate applied. " : "Candidate applied with failed checks. ") + (data.note || ""),
        pass ? "ok" : "warn"
      );
    }
  } catch (err) {
    if (!quiet) setStatus(String(err), "bad");
  }
}

async function selectCandidate(cellId) {
  const cands = lastTransparency?.top_candidates || [];
  const cand = cands.find(c => c.cell_id === cellId);
  if (cand?.preview) {
    renderMesh(cand.preview);
    renderLegend(cand.preview);
    lastMesh = cand.preview;
    updateHud(cand.preview);
  }
  if (lastTransparency) {
    for (const c of lastTransparency.top_candidates || []) {
      c.selected = c.cell_id === cellId;
    }
  }
  renderProcess(lastTransparency);
  if (studyId == null || !cellId) return;
  // Envelope preview is interim; always swap in the full program mesh when ready.
  applyArchiveCell(cellId, { quiet: true, skipMesh: false });
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
  const cards = processBody.querySelectorAll(".pool-card");
  for (const card of cards) {
    const canvas = card.querySelector("canvas");
    let mesh = null;
    if (card.dataset.rank != null && card.dataset.rank !== "") {
      const rank = Number(card.dataset.rank);
      const scheme = (lastTransparency?.sample_pool?.schemes || []).find(s => s.rank === rank);
      mesh = scheme?.preview;
      card.addEventListener("click", () => selectScheme(rank));
    } else if (card.dataset.cell) {
      const cand = (lastTransparency?.top_candidates || []).find(c => c.cell_id === card.dataset.cell);
      mesh = cand?.preview;
      card.addEventListener("click", () => selectCandidate(card.dataset.cell));
    }
    if (canvas && mesh) paintSchemeThumb(canvas, mesh);
  }
}

function renderLegend(mesh) {
  const leg = mesh.legend || {};
  const masses = leg.masses || [];
  const hasDepts = masses.some((m) => (m.departments || []).length);
  // Envelope thumbnails have no program split — keep the study legend stable.
  if (!hasDepts) {
    if (legend.classList.contains("show") && legend.innerHTML) return;
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
      Box: ${fmtLen(box.dx, 1)} × ${fmtLen(box.dy, 1)} × ${fmtLen(box.dz, 1)}
      (L×W×H)${box.double_height ? " · double-height" : ""}<br/>
      Level ${box.level ?? "—"} · GSF ${fmtArea(box.gsf, 0)}<br/>
      Mass envelope: ${mass.length_ft != null ? fmtLen(mass.length_ft, 1) : "—"} ×
      ${mass.width_ft != null ? fmtLen(mass.width_ft, 1) : "—"} ·
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

const PROBE_AXIS_ORDER = [
  "program_organization",
  "mass_count",
  "distribution_balance",
  "topology",
  "loading",
  "mean_height",
  "height_articulation",
  "vertical_organization",
  "geometric_character",
];
const PROBE_AXIS_LABELS = {
  program_organization: "program organization",
  mass_count: "mass count",
  distribution_balance: "distribution balance",
  topology: "topology",
  loading: "loading",
  mean_height: "mean height",
  height_articulation: "height articulation",
  vertical_organization: "vertical organization",
  geometric_character: "geometric character",
};
const EVAL_AXIS_ORDER = [
  "program_coherence",
  "preference_alignment",
  "performance_efficiency",
  "robustness",
];
const EVAL_AXIS_LABELS = {
  program_coherence: "program coherence",
  preference_alignment: "preference alignment",
  performance_efficiency: "performance efficiency",
  robustness: "robustness",
};

function radarLabelLines(text) {
  const words = String(text || "").split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [text || ""];
  if (text.length <= 14) return [text];
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")];
}

function radarSvg(axisMap, order, labels, fill) {
  const size = 260;
  const cx = size / 2;
  const cy = size / 2;
  const r = 72;
  const n = order.length;
  if (!n) return "";
  const vals = order.map((k) => {
    const v = Number((axisMap || {})[k]);
    if (Number.isNaN(v)) return 0;
    return Math.max(0, Math.min(1, v));
  });
  const angleAt = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pt = (i, scale) => {
    const a = angleAt(i);
    return [cx + r * scale * Math.cos(a), cy + r * scale * Math.sin(a)];
  };
  const poly = (scale) => order.map((_, i) => pt(i, scale).map((x) => x.toFixed(1)).join(",")).join(" ");
  const dataPoly = vals.map((v, i) => pt(i, v).map((x) => x.toFixed(1)).join(",")).join(" ");
  const rings = [0.25, 0.5, 0.75, 1].map((s) =>
    `<polygon points="${poly(s)}" fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`
  ).join("");
  const spokes = order.map((_, i) => {
    const [x, y] = pt(i, 1);
    return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,0.14)" stroke-width="1"/>`;
  }).join("");
  const labelEls = order.map((k, i) => {
    const [x, y] = pt(i, 1.38);
    const lines = radarLabelLines(labels[k] || k.replace(/_/g, " "));
    const startDy = lines.length === 1 ? 0 : -5;
    const tspans = lines.map((line, li) =>
      `<tspan x="${x.toFixed(1)}" dy="${li === 0 ? startDy : 10}">${esc(line)}</tspan>`
    ).join("");
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" fill="#c8c4bc" font-size="8.5" font-family="IBM Plex Sans, sans-serif">${tspans}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${size} ${size}" role="img" aria-label="radar">
    ${rings}${spokes}
    <polygon points="${dataPoly}" fill="${fill}" fill-opacity="0.28" stroke="${fill}" stroke-width="1.5"/>
    ${labelEls}
  </svg>`;
}

function candidateRadarsHtml(c) {
  const probe = radarSvg(c.probe_axes || {}, PROBE_AXIS_ORDER, PROBE_AXIS_LABELS, "#5a8cc8");
  const evalm = radarSvg(c.eval_axes || c.traits || {}, EVAL_AXIS_ORDER, EVAL_AXIS_LABELS, "#c8a05a");
  return `<div class="cand-radars">
    <div class="cand-radar"><div class="radar-title">9 probe</div>${probe}</div>
    <div class="cand-radar"><div class="radar-title">4 eval</div>${evalm}</div>
  </div>`;
}

function pcaCaption(meta, dimLabel) {
  if (!meta || !meta.loadings || !meta.loadings.length) {
    return `${dimLabel} → 3 PCA axes (drag to orbit · click a dot to select).`;
  }
  const bits = meta.loadings.map((pc) => {
    const pct = Math.round(100 * (pc.explained || 0));
    const tops = (pc.top || []).slice(0, 2).map(t => (PROBE_AXIS_LABELS[t.axis] || EVAL_AXIS_LABELS[t.axis] || t.axis).replace(/_/g, " "));
    return `PC${pc.pc} ${pct}% (${tops.join(", ")})`;
  });
  return `${dimLabel} → 3 PCA axes · ${bits.join(" · ")}. Drag to orbit; click a gold/teal dot to select.`;
}

let spaceCloudHandles = [];

function disposeSpaceClouds() {
  for (const h of spaceCloudHandles) {
    try {
      if (h.raf) cancelAnimationFrame(h.raf);
      if (h.ro) h.ro.disconnect();
      if (h.controls) h.controls.dispose();
      if (h.renderer) {
        h.renderer.dispose();
        if (h.renderer.domElement && h.renderer.domElement.parentNode) {
          h.renderer.domElement.parentNode.removeChild(h.renderer.domElement);
        }
      }
      if (h.points) {
        h.points.geometry?.dispose();
        h.points.material?.dispose();
      }
      if (h.elites) {
        h.elites.geometry?.dispose();
        h.elites.material?.dispose();
      }
    } catch (_) { /* ignore */ }
  }
  spaceCloudHandles = [];
}

function mountSpaceCloud(host, points, xyzKey, tipEl) {
  if (!host || !points.length) return;
  const w0 = host.clientWidth || 640;
  const h0 = host.clientHeight || 320;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0a0a);
  const camera = new THREE.PerspectiveCamera(42, w0 / Math.max(h0, 1), 0.1, 2000);
  camera.position.set(70, 55, 90);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w0, h0, false);
  host.insertBefore(renderer.domElement, tipEl || null);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0, 0);

  const axis = new THREE.AxesHelper(36);
  scene.add(axis);
  const grid = new THREE.GridHelper(120, 12, 0x222222, 0x161616);
  grid.position.y = -40;
  scene.add(grid);

  const positions = [];
  const colors = [];
  const elitePos = [];
  const metaByIndex = [];
  const eliteMeta = [];
  for (const p of points) {
    const xyz = p[xyzKey] || [0, 0, 0];
    const x = Number(xyz[0]) || 0;
    const y = Number(xyz[1]) || 0;
    const z = Number(xyz[2]) || 0;
    positions.push(x, y, z);
    metaByIndex.push(p);
    if (p.elite) {
      colors.push(0.83, 0.66, 0.29);
      elitePos.push(x, y, z);
      eliteMeta.push(p);
    } else if (p.fits) {
      colors.push(0.42, 0.54, 0.60);
    } else {
      colors.push(0.22, 0.22, 0.22);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({
    size: 4.2,
    vertexColors: true,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.9,
  });
  const cloud = new THREE.Points(geo, mat);
  cloud.userData.meta = metaByIndex;
  scene.add(cloud);

  let eliteCloud = null;
  if (elitePos.length) {
    const egeo = new THREE.BufferGeometry();
    egeo.setAttribute("position", new THREE.Float32BufferAttribute(elitePos, 3));
    const emat = new THREE.PointsMaterial({
      size: 9.5,
      color: 0xd4a84b,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.95,
    });
    eliteCloud = new THREE.Points(egeo, emat);
    eliteCloud.userData.meta = eliteMeta;
    scene.add(eliteCloud);
  }

  const raycaster = new THREE.Raycaster();
  raycaster.params.Points = { threshold: 3.5 };
  const pointer = new THREE.Vector2();

  function pick(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const targets = eliteCloud ? [eliteCloud, cloud] : [cloud];
    const hits = raycaster.intersectObjects(targets, false);
    if (!hits.length) return null;
    const hit = hits[0];
    const list = hit.object.userData.meta || [];
    return list[hit.index] || null;
  }

  function onMove(ev) {
    const p = pick(ev);
    if (tipEl) {
      tipEl.textContent = p
        ? [p.label, p.envelope, p.loading, p.plate].filter(Boolean).join(" · ")
        : "Hover a sample · drag to orbit";
    }
  }
  function onClick(ev) {
    const p = pick(ev);
    if (p && p.cell_id) selectCandidate(p.cell_id);
  }
  renderer.domElement.addEventListener("pointermove", onMove);
  renderer.domElement.addEventListener("click", onClick);

  const handle = {
    renderer,
    controls,
    points: cloud,
    elites: eliteCloud,
    raf: 0,
    ro: null,
  };
  function frame() {
    handle.raf = requestAnimationFrame(frame);
    controls.update();
    renderer.render(scene, camera);
  }
  frame();

  handle.ro = new ResizeObserver(() => {
    const w = host.clientWidth || w0;
    const h = host.clientHeight || h0;
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  });
  handle.ro.observe(host);
  spaceCloudHandles.push(handle);
}

function renderSpaceTab(t) {
  disposeSpaceClouds();
  const space = t.space || {};
  const points = space.points || [];
  if (!points.length) {
    processBody.innerHTML = `<div class="empty-msg">No COVER archive points yet — generate to map the sample space.</div>`;
    return;
  }
  const pca = space.pca || {};
  const elites = points.filter(p => p.elite);
  const eliteList = elites.length
    ? `<ol class="space-elite-list">${elites.map(p => {
        const why = p.elite_why ? ` — ${esc(p.elite_why)}` : "";
        return `<li><button type="button" data-space-cell="${esc(p.cell_id)}">${esc(p.label || p.cell_id)}</button>${why}</li>`;
      }).join("")}</ol>`
    : `<div class="empty-msg">No top candidates highlighted.</div>`;
  processBody.innerHTML = `
    <div class="space-wrap">
      <div class="notes-line" style="margin-top:0">
        ${points.length} archive cell(s) · ${space.legal || 0} legal · ${space.illegal || 0} illegal ·
        ${space.elite_count || 0} top candidate(s) as gold dots.
        Each sample is one point; 9 probe / 4 eval axes are compressed to 3 PCA coordinates.
      </div>
      ${space.note ? `<div class="notes-line">${esc(space.note)}</div>` : ""}
      <div class="space-legend">
        <span><i class="space-swatch illegal"></i> illegal</span>
        <span><i class="space-swatch legal"></i> legal</span>
        <span><i class="space-swatch elite"></i> top candidate</span>
      </div>
      <div class="space-block">
        <h4>9 probe axes — COVER strategy space</h4>
        <div class="space-pca-meta">${esc(pcaCaption(pca.probe, "9D"))}</div>
        <div class="space-cloud" id="spaceCloudProbe"><div class="space-cloud-tip">Hover a sample · drag to orbit</div></div>
      </div>
      <div class="space-block">
        <h4>4 eval axes — soft quality</h4>
        <div class="space-pca-meta">${esc(pcaCaption(pca.eval, "4D"))}</div>
        <div class="space-cloud" id="spaceCloudEval"><div class="space-cloud-tip">Hover a sample · drag to orbit</div></div>
      </div>
      <div class="space-block">
        <h4>Top candidates</h4>
        ${eliteList}
      </div>
    </div>`;
  const probeHost = document.getElementById("spaceCloudProbe");
  const evalHost = document.getElementById("spaceCloudEval");
  if (probeHost) mountSpaceCloud(probeHost, points, "xyz_probe", probeHost.querySelector(".space-cloud-tip"));
  if (evalHost) mountSpaceCloud(evalHost, points, "xyz_eval", evalHost.querySelector(".space-cloud-tip"));
  processBody.querySelectorAll("[data-space-cell]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-space-cell");
      if (id) selectCandidate(id);
    });
  });
}

function renderProcess(t) {
  if (activeTab !== "space") disposeSpaceClouds();
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
  if (activeTab === "space") {
    renderSpaceTab(t);
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
      const fromArchive = schemes.some(s => s.source === "archive");
      const legalN = schemes.filter(s => s.verified || s.fits).length;
      const coverBits = [];
      if (pool.cover_attempts != null && pool.cover_max != null) {
        coverBits.push(`COVER ${pool.cover_attempts}/${pool.cover_max} (start ${pool.cover_start ?? 40})`);
      }
      if (pool.cover_pool) coverBits.push(`joint pool ${pool.cover_pool}`);
      if (pool.typology_cells && pool.typology_cells !== pool.count) {
        coverBits.push(`${pool.typology_cells} typology cells`);
      }
      if (pool.drawings_collapsed) coverBits.push(`hid ${pool.drawings_collapsed} near-twin drawing(s)`);
      if (pool.cover_incomplete) coverBits.push("map incomplete");
      const coverLine = coverBits.length ? `${coverBits.join(" · ")} · ` : "";
      processBody.innerHTML = `
        <div class="notes-line" style="margin-top:0">${coverLine}${pool.count} unique drawing(s) · ${legalN} under limits · click a card to show it in the main 3D view</div>
        <div class="pool-grid">
          ${schemes.map(s => {
            const masses = (s.masses || []).map(m =>
              `${esc(m.mass_name || m.mass_id)} ${m.stories || "?"}fl`
            ).join(" · ");
            const kind = [s.topology, s.envelope, s.loading].filter(Boolean).join(" · ");
            const sel = s.rank === selected ? "selected" : "";
            const cellAttr = s.cell_id ? ` data-cell="${esc(s.cell_id)}"` : "";
            return `<button type="button" class="pool-card ${sel}" data-rank="${s.rank}"${cellAttr}>
              <canvas></canvas>
              <div class="cap">
                <div class="title">#${s.rank} · ${s.total_length_ft != null ? fmtLen(s.total_length_ft, 0) : "—"}
                  <span class="pill ${s.verified ? "ok" : "bad"}">${s.verified ? "ok" : "fail"}</span>
                </div>
                <div class="sub">${kind ? esc(kind) + (masses ? " · " : "") : ""}${masses || "—"}</div>
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
      <div class="notes-line" style="margin-top:0">${cands.length} candidate(s) · elites first (best fit / contrast), then other legal cells. Near-identical drawings may still appear so you can inspect twins. Radars: 9 COVER probe axes and 4 soft eval axes (LEARN).</div>
      <div class="cand-grid">
        ${cands.map(c => {
          const masses = (c.masses || []).map(m =>
            `${esc(m.mass_name || m.mass_id)} ${m.stories || "?"}fl`
          ).join(" · ");
          const sel = c.selected || c.kept ? "selected" : "";
          const idLine = c.cell_id
            ? `<div class="cell-id" title="${esc(c.cell_id)}">id ${esc(c.cell_id)}</div>`
            : "";
          return `<button type="button" class="pool-card ${sel}" data-cell="${esc(c.cell_id || "")}">
            <canvas></canvas>
            <div class="cap">
              <div class="title">${c.kept ? `<span class="pill kept">kept</span> ` : ""}${esc(c.label)}
                <span class="pill ${c.fits ? "ok" : "bad"}">${c.fits ? "legal" : "over"}</span>
              </div>
              ${idLine}
              <div class="sub">${masses || esc(c.reason || "—")}${c.why ? `<br/>${esc(c.why)}` : ""}</div>
            </div>
            ${candidateRadarsHtml(c)}
          </button>`;
        }).join("")}
      </div>`;
    requestAnimationFrame(() => mountPoolThumbs());
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
      const probes = (s.probes || []).map(p =>
        `· ${esc(p.label || "")}${p.unlocks ? " — " + esc(p.unlocks) : ""} <span class="pill">ask architect</span>`
      ).join("<br/>");
      const knowledge = (s.knowledge || []).map(k =>
        `· ${esc(k.region || "")}: ${esc(k.impossible_because || "")}`
      ).join("<br/>");
      return `<div class="step">
        <div class="phase">${esc(s.phase)}</div>
        <div>${esc(s.summary || "")}</div>
        ${s.detail ? `<div class="ops">${esc(s.detail)}</div>` : ""}
        ${ops ? `<div class="ops">${ops}</div>` : ""}
        ${cands ? `<div class="ops">EI pool:<br/>${cands}</div>` : ""}
        ${items ? `<div class="ops">${items}</div>` : ""}
        ${probes ? `<div class="ops">Relaxation probes (not applied):<br/>${probes}</div>` : ""}
        ${knowledge ? `<div class="ops">Why empty:<br/>${knowledge}</div>` : ""}
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


def _study_department_colors(session: Any) -> dict[str, str]:
    """Stable department → hex map for one study (same legend across candidates)."""
    from .preview3d import stable_department_colors

    store = dict(session.constraints.get("explore") or {})
    cached_raw = store.get("department_colors")
    if isinstance(cached_raw, dict) and cached_raw:
        return {str(k): str(v) for k, v in cached_raw.items() if k and v}
    names: list[str] = []
    try:
        names = [str(n) for n in (session.department_names() or []) if n]
    except Exception:
        names = []
    if not names:
        program = getattr(session, "program", None)
        if program is not None:
            names = [str(d.name) for d in (program.departments or []) if getattr(d, "name", None)]
    colors = stable_department_colors(names)
    store["department_colors"] = colors
    session.constraints["explore"] = store
    return colors


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
            elif path == "/api/apply_cell":
                self._apply_cell()
            elif path == "/api/learn_choice":
                self._learn_choice()
            elif path == "/api/modality_resolve":
                self._modality_resolve()
            elif path == "/api/clear_program":
                self._clear_program()
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

    def _clear_program(self) -> None:
        _STATE["program_path"] = None
        _STATE["program_name"] = None
        self._json({"ok": True})

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

        from .brief import apply_parsed_brief, briefing_from_parsed, should_apply_brief
        from .config import load_project_config
        from .explore.ui_payload import transparency_payload
        from .load import load_program_file
        from .modality import apply_answer_corrections, find_brief_questions
        from .preview3d import preview_mesh
        from .session import StudySession, slugify_study_id
        from .solver import solve_massing_study

        modality_answers = list(payload.get("modality_answers") or [])
        skip_ask = bool(payload.get("skip_modality_ask"))
        brief = apply_answer_corrections(brief, modality_answers)

        config_path = _STATE.get("config_path") or (
            str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        )
        program = load_program_file(path, config_path=config_path)
        dept_names = sorted({d.name for d in program.departments})

        questions, parsed = find_brief_questions(
            brief,
            answers=modality_answers,
            # First pause: ask immediately. After they classify, use the LLM
            # only to place remaining numbers — do not re-guess modality.
            use_llm=bool(modality_answers),
            department_names=dept_names,
        )
        if questions and not skip_ask:
            self._json(
                {
                    "ok": True,
                    "needs_modality": True,
                    "questions": questions,
                    "brief": brief,
                }
            )
            return

        study_id = slugify_study_id(f"ui_{int(time.time())}")
        session = StudySession(
            study_id=study_id,
            program=program,
            config_path=config_path or "",
        )
        # Lock program → color map for this study so every candidate shares legends.
        _study_department_colors(session)
        session.save()

        note = ""
        if not should_apply_brief(session, parsed) and session.masses:
            self._json(
                {
                    "ok": False,
                    "error": "Brief did not change grouping or site.",
                },
                400,
            )
            return
        # Same orchestration as terminal chat: optional LLM planner into search.
        client = None
        try:
            from .ollama_client import OllamaClient

            client = OllamaClient()
        except Exception:
            client = None
        out = apply_parsed_brief(session, parsed, client=client)
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
        # Scheme search also runs inside COVER; retry only if COVER stored no cells.
        archive_now = (session.constraints.get("explore") or {}).get("archive") or {}
        has_cells = bool(archive_now.get("cells"))
        if not (session.last_search or []) and not has_cells:
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
        mesh = preview_mesh(
            result, config=config, department_colors=_study_department_colors(session)
        )
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
                "space": {"probe_axes": [], "eval_axes": [], "points": [], "pca": {}, "note": ""},
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

    def _modality_resolve(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        answers = list(payload.get("answers") or payload.get("modality_answers") or [])
        from .modality_memory import KINDS, remember

        saved = []
        for ans in answers:
            stored = str(ans.get("corrected") or ans.get("text") or "").strip()
            kind = str(ans.get("kind") or "").strip().lower()
            if not stored or kind not in KINDS:
                continue
            remember(stored, kind, cue=str(ans.get("cue") or "") or None)
            saved.append({"text": stored, "kind": kind})
        self._json({"ok": True, "saved": saved, "count": len(saved)})

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
        mesh = preview_mesh(
            result, config=config, department_colors=_study_department_colors(session)
        )
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

    def _apply_cell(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        study_id = payload.get("study_id") or _STATE.get("study_id")
        cell_id = str(payload.get("cell_id") or "").strip()
        if not study_id:
            self._json({"ok": False, "error": "No study loaded. Generate first."}, 400)
            return
        if not cell_id:
            self._json({"ok": False, "error": "Need a cell_id."}, 400)
            return

        from .config import load_project_config
        from .explore.archive import restore_entry
        from .preview3d import preview_mesh
        from .session import StudySession
        from .solver import solve_massing_study

        try:
            session = StudySession.load(str(study_id))
        except FileNotFoundError:
            self._json({"ok": False, "error": f"Study '{study_id}' not found."}, 404)
            return
        archive = (session.constraints.get("explore") or {}).get("archive") or {}
        entry = (archive.get("cells") or {}).get(cell_id)
        if not entry:
            self._json({"ok": False, "error": f"Cell '{cell_id}' is not in the archive."}, 404)
            return
        restore_entry(session, entry)
        store = dict(session.constraints.get("explore") or {})
        store["kept_cell"] = cell_id
        session.constraints["explore"] = store
        config_path = session.config_path or _STATE.get("config_path") or (
            str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        )
        result = solve_massing_study(session, config_path=config_path or None)
        try:
            config = load_project_config(config_path) if config_path else {}
        except FileNotFoundError:
            config = {}
        mesh = preview_mesh(
            result, config=config, department_colors=_study_department_colors(session)
        )
        session.save()
        _STATE["study_id"] = session.study_id
        self._json(
            {
                "ok": True,
                "note": f"Showing COVER cell {entry.get('reason') or cell_id}.",
                "study_id": session.study_id,
                "cell_id": cell_id,
                "mesh": mesh,
            }
        )

    def _learn_choice(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        study_id = payload.get("study_id") or _STATE.get("study_id")
        winner = str(payload.get("winner") or "").strip().lower()
        if not study_id:
            self._json({"ok": False, "error": "No study loaded. Generate first."}, 400)
            return
        if winner not in {"a", "b"}:
            self._json({"ok": False, "error": "winner must be 'a' or 'b'."}, 400)
            return

        from .config import load_project_config
        from .explore.preference import apply_choice
        from .explore.ui_payload import transparency_payload
        from .preview3d import preview_mesh
        from .session import StudySession
        from .solver import solve_massing_study

        try:
            session = StudySession.load(str(study_id))
        except FileNotFoundError:
            self._json({"ok": False, "error": f"Study '{study_id}' not found."}, 404)
            return

        applied = apply_choice(session, winner)
        if not applied:
            self._json(
                {"ok": False, "error": "No pending A/B pair on this study (or pair cells missing)."},
                400,
            )
            return
        # Keep A/B snappy: choice already restored the winner and queued the next
        # pair. Full REFINE runs only when LEARN completes (inside apply_choice).

        config_path = session.config_path or _STATE.get("config_path") or (
            str(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        )
        result = solve_massing_study(session, config_path=config_path or None)
        try:
            config = load_project_config(config_path) if config_path else {}
        except FileNotFoundError:
            config = {}
        mesh = preview_mesh(
            result, config=config, department_colors=_study_department_colors(session)
        )
        session.save()
        _STATE["study_id"] = session.study_id
        transparency = transparency_payload(session, full_explore=True)
        self._json(
            {
                "ok": True,
                "note": applied.get("reply") or f"Recorded preference {winner.upper()}.",
                "study_id": session.study_id,
                "winner": winner,
                "kept_cell": applied.get("kept_cell"),
                "mesh": mesh,
                "transparency": transparency,
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
