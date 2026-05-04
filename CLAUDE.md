# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HomCenter Import Invoice System — a Hebrew RTL single-page application for processing import invoices and exporting to Infor M3 ERP (via APS450MI / ION API). Built for HomCenter (Israeli home improvement chain). No build system, no dependencies, no server required.

## Files

- **`ceo_pilot.html`** — the main application (open directly in a browser)
- **`index (3).html`** — a saved browser snapshot from Claude's design preview tool; not the working app

## Development

Open `ceo_pilot.html` directly in a browser. No build, no install, no server needed.

## Architecture

Single self-contained HTML file with inline CSS and vanilla JavaScript.

### State

All runtime data lives in a single `state` object (defined near the top of the `<script>` block):
- `state.suppliers` — list of freight forwarders/agents
- `state.prices` — price agreements per supplier per IMP code
- `state.imp` — regex-based IMP mapping rules (invoice line → IMP code)
- `state.audit` — activity log entries

State is in-memory only; nothing is persisted to localStorage or a backend.

### View Routing

Six views are defined as `<div class="view" id="view-{name}">`. Only the `.active` view is shown. Navigation is handled by `nav(id, el)`, which switches the active view and re-renders it.

Views: `pilot`, `suppliers`, `prices`, `imp`, `settings`, `audit`.

### IMP Mapping

IMP (Import) codes categorize invoice line items (DUTY, PORT, HANDLING, TRANSPORT, etc.). Each rule has a regex pattern (`pat`) matched case-insensitively against invoice line text. Rules are sorted by `priority` then `code`. The `testIMP()` function lets you test text against all rules live.

### Pilot Pipeline

The pilot view simulates a 5-step invoice processing pipeline (PDF parse → supplier ID → IMP mapping → price check → M3 export) using sequential `setTimeout` calls. It is a demo/simulation, not live processing.

### Excel Import/Export

Uses [SheetJS](https://sheetjs.com/) loaded from CDN (`xlsx.full.min.js`). Two import flows:
- IMP rules import (required columns: `imp_code`, `imp_description`, `keyword_pattern`)
- Price list import (required columns: `imp_code`, `unit_price`, `currency`, `valid_from`)

### M3 / Infor Integration

Settings view exposes Infor M3 ION API configuration (CONO, DIVI, VAT code, ION Base URL, Tenant ID, Client ID). The pilot generates `APS450MI AddHead` JSON output. In the current pilot, export is simulated (Dry Run mode).
