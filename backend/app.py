"""
HomCenter Invoice API — Phase 3
Flask server with full CRUD for suppliers and rate cards.

Run:  python app.py
      (or)  flask --app app run --port 5001 --debug
"""
import os
from flask import Flask, jsonify, request
from flask_cors import CORS

from flask import send_file
import io

from processor import (
    load_all,
    kpi_summary,
    invoice_detail,
    price_check,
    match_imp,
    df_to_records,
    # Phase 3 — CRUD
    add_supplier,
    remove_supplier,
    import_suppliers_excel,
    add_rate_card,
    remove_rate_card,
    import_rate_cards_excel,
    # Phase 4 — Audit + M3
    append_audit,
    get_audit,
    generate_m3_export,
    m3_export_to_excel,
    mark_invoices_exported,
)

app = Flask(__name__)
CORS(app)

_dfs = load_all()


def _reload_if_needed():
    if request.args.get("reload") == "1":
        global _dfs
        _dfs = load_all()


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    _reload_if_needed()
    return jsonify({
        "ok":          True,
        "message":     "HomCenter Invoice API is running",
        "version":     "3.0.0-phase3",
        "data_loaded": {k: len(v) for k, v in _dfs.items()},
        "kpi":         kpi_summary(_dfs),
    })


# ── invoices ──────────────────────────────────────────────────────────────────

@app.get("/api/invoices")
def list_invoices():
    _reload_if_needed()
    inv_df = _dfs["invoices"].copy()
    sf = request.args.get("status")
    if sf:
        inv_df = inv_df[inv_df["status"] == sf]
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
    discs = [r for r in results if r["status"] == "discrepancy"]
    return jsonify({
        "invoice_id":        invoice_id,
        "lines":             results,
        "discrepancy_count": len(discs),
        "all_ok":            len(discs) == 0,
    })


# ── suppliers ─────────────────────────────────────────────────────────────────

@app.get("/api/suppliers")
def list_suppliers():
    _reload_if_needed()
    return jsonify(df_to_records(_dfs["suppliers"]))


@app.post("/api/suppliers")
def create_supplier():
    data = request.get_json(force=True)
    record, err = add_supplier(_dfs, data)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "supplier": record}), 201


@app.delete("/api/suppliers/<code>")
def delete_supplier(code: str):
    ok, err = remove_supplier(_dfs, code)
    if not ok:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "deleted": code})


@app.post("/api/suppliers/import")
def import_suppliers():
    """
    Multipart file upload: field name = 'file'
    Accepts .xlsx / .xls / .csv
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (field name: 'file')"}), 400
    f = request.files["file"]
    result = import_suppliers_excel(_dfs, f.read())
    status_code = 200 if not result["errors"] else 207
    return jsonify(result), status_code


# ── IMP mapping ───────────────────────────────────────────────────────────────

@app.get("/api/imp")
def list_imp():
    _reload_if_needed()
    imp_df = _dfs["imp_mapping"].copy()
    cat = request.args.get("category")
    if cat:
        imp_df = imp_df[imp_df["category"] == cat]
    return jsonify(df_to_records(imp_df))


@app.post("/api/imp/match")
def imp_match():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    return jsonify({"text": text, "match": match_imp(_dfs, text)})


# ── rate cards ────────────────────────────────────────────────────────────────

@app.get("/api/rate-cards")
def list_rate_cards():
    _reload_if_needed()
    rc = _dfs["rate_cards"].copy()
    vendor = request.args.get("vendor")
    month  = request.args.get("month")
    if vendor:
        rc = rc[rc["vendor_code"] == vendor.upper()]
    if month:
        rc = rc[rc["month"] == month]
    return jsonify(df_to_records(rc))


@app.post("/api/rate-cards")
def create_rate_card():
    data = request.get_json(force=True)
    record, err = add_rate_card(_dfs, data)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "rate_card": record}), 201


@app.delete("/api/rate-cards")
def delete_rate_card():
    """
    Body: {"vendor_code": "DHL", "month": "2024-04", "imp_code": "IMP-002"}
    """
    data        = request.get_json(force=True)
    vendor_code = data.get("vendor_code", "")
    month       = data.get("month", "")
    imp_code    = data.get("imp_code", "")
    ok, err = remove_rate_card(_dfs, vendor_code, month, imp_code)
    if not ok:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True})


@app.post("/api/rate-cards/import")
def import_rate_cards():
    """
    Multipart: file = Excel/CSV, vendor_code = form field
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (field name: 'file')"}), 400
    vendor_code = request.form.get("vendor_code", "").strip().upper()
    if not vendor_code:
        return jsonify({"error": "vendor_code form field is required"}), 400
    f      = request.files["file"]
    result = import_rate_cards_excel(_dfs, f.read(), vendor_code)
    return jsonify(result), 200 if not result["errors"] else 207


# ── KPI ───────────────────────────────────────────────────────────────────────

@app.get("/api/kpi")
def kpi():
    _reload_if_needed()
    return jsonify(kpi_summary(_dfs))


# ── AUDIT LOG (Phase 4) ───────────────────────────────────────────────────────

@app.get("/api/audit")
def list_audit():
    _reload_if_needed()
    limit  = int(request.args.get("limit", 100))
    action = request.args.get("action")
    status = request.args.get("status")
    return jsonify(get_audit(_dfs, limit=limit, action=action, status=status))


@app.post("/api/audit")
def create_audit():
    data   = request.get_json(force=True)
    action = data.get("action", "").strip()
    detail = data.get("detail", "").strip()
    if not action or not detail:
        return jsonify({"error": "action and detail are required"}), 400
    entry = append_audit(
        _dfs,
        action=action,
        detail=detail,
        status=data.get("status", "ok"),
        user=data.get("user", "frontend"),
    )
    return jsonify({"ok": True, "entry": entry}), 201


# ── M3 EXPORT (Phase 4) ───────────────────────────────────────────────────────

@app.get("/api/m3/export")
def m3_export_json():
    """
    Returns M3 APS450MI commands as JSON.
    Optional QS: invoice_ids=ID1,ID2  cono=100  divi=HC  vat=6
    """
    _reload_if_needed()
    ids      = request.args.get("invoice_ids", "")
    id_list  = [i.strip() for i in ids.split(",") if i.strip()] or None
    cono     = request.args.get("cono", "100")
    divi     = request.args.get("divi", "HC")
    vat_code = request.args.get("vat", "6")

    commands = generate_m3_export(_dfs, id_list, cono=cono, divi=divi, vat_code=vat_code)
    invoices_included = list({c["record"].get("SUNO", "") for c in commands
                               if c["transaction"] == "AddHead"})
    return jsonify({
        "program":          "APS450MI",
        "command_count":    len(commands),
        "invoices_included": invoices_included,
        "commands":         commands,
    })


@app.get("/api/m3/export/excel")
def m3_export_excel():
    """Download M3 export as Excel file."""
    _reload_if_needed()
    ids     = request.args.get("invoice_ids", "")
    id_list = [i.strip() for i in ids.split(",") if i.strip()] or None
    commands = generate_m3_export(_dfs, id_list)
    xlsx     = m3_export_to_excel(commands)
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="M3_APS450MI_Export.xlsx",
    )


@app.post("/api/m3/send")
def m3_send():
    """
    Simulate sending invoices to M3.
    Body: {"invoice_ids": ["ID1", "ID2"], "user": "dana.levi"}
    Marks invoices as exported and appends audit entry.
    """
    data        = request.get_json(force=True)
    invoice_ids = data.get("invoice_ids", [])
    user        = data.get("user", "frontend")

    if not invoice_ids:
        return jsonify({"error": "invoice_ids list is required"}), 400

    count = mark_invoices_exported(_dfs, invoice_ids, user=user)
    return jsonify({
        "ok":       True,
        "exported": count,
        "message":  f"{count} חשבוניות סומנו כ'נשלח ל-M3'",
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
