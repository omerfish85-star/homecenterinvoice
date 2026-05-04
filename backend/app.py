"""
HomCenter Invoice API — Phase 1
Flask server that exposes invoice data processed by Pandas.

Run:  python app.py
      (or)  flask --app app run --port 5001 --debug
"""
import os
from flask import Flask, jsonify, request
from flask_cors import CORS

from processor import (
    load_all,
    kpi_summary,
    invoice_detail,
    price_check,
    match_imp,
    df_to_records,
)

app = Flask(__name__)
CORS(app)  # allow the local HTML file to call the API

# Load all DataFrames once at startup
_dfs = load_all()


def _reload_if_needed():
    """Hot-reload data during development when ?reload=1 is passed."""
    if request.args.get("reload") == "1":
        global _dfs
        _dfs = load_all()


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    _reload_if_needed()
    summary = kpi_summary(_dfs)
    return jsonify({
        "ok": True,
        "message": "HomCenter Invoice API is running",
        "version": "1.0.0-phase1",
        "data_loaded": {k: len(v) for k, v in _dfs.items()},
        "kpi": summary,
    })


# ── invoices ──────────────────────────────────────────────────────────────────

@app.get("/api/invoices")
def list_invoices():
    _reload_if_needed()
    status_filter = request.args.get("status")
    inv_df = _dfs["invoices"].copy()
    if status_filter:
        inv_df = inv_df[inv_df["status"] == status_filter]
    return jsonify(df_to_records(inv_df))


@app.get("/api/invoices/<invoice_id>")
def get_invoice(invoice_id: str):
    _reload_if_needed()
    detail = invoice_detail(_dfs, invoice_id)
    if detail is None:
        return jsonify({"error": f"Invoice {invoice_id!r} not found"}), 404
    return jsonify(detail)


@app.get("/api/invoices/<invoice_id>/price-check")
def invoice_price_check(invoice_id: str):
    _reload_if_needed()
    results = price_check(_dfs, invoice_id)
    if not results:
        return jsonify({"error": f"Invoice {invoice_id!r} not found"}), 404
    discrepancies = [r for r in results if r["status"] == "discrepancy"]
    return jsonify({
        "invoice_id": invoice_id,
        "lines": results,
        "discrepancy_count": len(discrepancies),
        "all_ok": len(discrepancies) == 0,
    })


# ── suppliers ─────────────────────────────────────────────────────────────────

@app.get("/api/suppliers")
def list_suppliers():
    _reload_if_needed()
    return jsonify(df_to_records(_dfs["suppliers"]))


# ── IMP mapping ───────────────────────────────────────────────────────────────

@app.get("/api/imp")
def list_imp():
    _reload_if_needed()
    category = request.args.get("category")
    imp_df = _dfs["imp_mapping"].copy()
    if category:
        imp_df = imp_df[imp_df["category"] == category]
    return jsonify(df_to_records(imp_df))


@app.post("/api/imp/match")
def imp_match():
    """POST {"text": "..."} → best matching IMP rule"""
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    result = match_imp(_dfs, text)
    return jsonify({"text": text, "match": result})


# ── rate cards ────────────────────────────────────────────────────────────────

@app.get("/api/rate-cards")
def list_rate_cards():
    _reload_if_needed()
    vendor = request.args.get("vendor")
    month  = request.args.get("month")
    rc = _dfs["rate_cards"].copy()
    if vendor:
        rc = rc[rc["vendor_code"] == vendor]
    if month:
        rc = rc[rc["month"] == month]
    return jsonify(df_to_records(rc))


# ── KPI summary ───────────────────────────────────────────────────────────────

@app.get("/api/kpi")
def kpi():
    _reload_if_needed()
    return jsonify(kpi_summary(_dfs))


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
