"""
Invoice data processor — loads CSV/Excel files into Pandas DataFrames
and exposes clean, serialisable summaries for the Flask API.
"""
import io
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
CUSTOMS_RATE_USD_ILS = 3.70  # default fallback rate

# Required columns per entity
_REQUIRED = {
    "suppliers":  ["code", "name"],
    "rate_cards": ["vendor_code", "month", "imp_code", "unit_price", "currency"],
}


# ── loaders ──────────────────────────────────────────────────────────────────

def _load(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if path.suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def _save(df: pd.DataFrame, filename: str) -> None:
    (DATA_DIR / filename).write_text(df.to_csv(index=False), encoding="utf-8")


def load_all() -> dict[str, pd.DataFrame]:
    return {
        "invoices":      _load("invoices.csv"),
        "invoice_lines": _load("invoice_lines.csv"),
        "suppliers":     _load("suppliers.csv"),
        "imp_mapping":   _load("imp_mapping.csv"),
        "rate_cards":    _load("rate_cards.csv"),
        "audit_log":     _load("audit_log.csv"),
    }


# ── KPI summary ──────────────────────────────────────────────────────────────

def kpi_summary(dfs: dict) -> dict:
    inv   = dfs["invoices"].copy()
    lines = dfs["invoice_lines"].copy()

    total_invoices = len(inv)
    pending        = int((inv["status"] == "pending").sum())
    approved       = int((inv["status"] == "approved").sum())
    discrepancy    = int((inv["status"] == "discrepancy").sum())

    inv["total_ils"] = inv.apply(
        lambda r: r["total_amount"] * CUSTOMS_RATE_USD_ILS
        if r["currency"] == "USD" else r["total_amount"],
        axis=1,
    )
    total_value_ils = float(inv["total_ils"].sum())

    bad_lines = lines[lines["ok"].astype(str).str.lower() == "false"]
    return {
        "total_invoices":    total_invoices,
        "pending":           pending,
        "approved":          approved,
        "discrepancy":       discrepancy,
        "total_value_ils":   round(total_value_ils, 2),
        "discrepancy_lines": len(bad_lines),
    }


# ── invoice detail ────────────────────────────────────────────────────────────

def invoice_detail(dfs: dict, invoice_id: str) -> dict | None:
    row = dfs["invoices"][dfs["invoices"]["invoice_id"] == invoice_id]
    if row.empty:
        return None
    inv_dict = row.iloc[0].where(pd.notna(row.iloc[0]), None).to_dict()
    lines_rows = dfs["invoice_lines"][
        dfs["invoice_lines"]["invoice_id"] == invoice_id
    ].copy()
    inv_dict["lines"] = lines_rows.where(pd.notna(lines_rows), None).to_dict(orient="records")
    return inv_dict


# ── price-check ───────────────────────────────────────────────────────────────

def price_check(dfs: dict, invoice_id: str) -> list[dict]:
    detail = invoice_detail(dfs, invoice_id)
    if detail is None:
        return []

    rate_cards  = dfs["rate_cards"]
    vendor_code = _vendor_code(dfs["suppliers"], detail.get("vendor", ""))
    month       = detail.get("month", "")

    results = []
    for line in detail["lines"]:
        imp    = line.get("imp_code", "")
        billed = float(line.get("billed_amount") or 0)

        agreed_row = rate_cards[
            (rate_cards["vendor_code"] == vendor_code) &
            (rate_cards["month"]       == month) &
            (rate_cards["imp_code"]    == imp)
        ]
        if agreed_row.empty:
            raw = line.get("agreed_price")
            agreed = float(raw) if raw else None
        else:
            agreed = float(agreed_row.iloc[0]["unit_price"])

        variance_pct = None
        status = "unknown"
        if agreed is not None and agreed > 0:
            variance_pct = round((billed - agreed) / agreed * 100, 2)
            status = "ok" if abs(variance_pct) < 0.01 else "discrepancy"
        elif str(line.get("ok", "")).lower() == "true":
            status = "ok"

        results.append({
            "line_no":      line.get("line_no"),
            "desc_raw":     line.get("desc_raw"),
            "imp_code":     imp,
            "agreed":       agreed,
            "billed":       billed,
            "variance_pct": variance_pct,
            "status":       status,
            "note":         line.get("note"),
        })
    return results


# ── IMP text matcher ─────────────────────────────────────────────────────────

def match_imp(dfs: dict, text: str) -> dict | None:
    best, best_conf = None, 0
    for _, row in dfs["imp_mapping"].iterrows():
        pattern = str(row.get("keyword_pattern", ""))
        if not pattern:
            continue
        try:
            if re.search(pattern, text, re.IGNORECASE):
                conf = int(row.get("confidence", 80))
                if conf > best_conf:
                    best_conf = conf
                    best = row.where(pd.notna(row), None).to_dict()
        except re.error:
            pass
    return best


# ── SUPPLIERS CRUD ────────────────────────────────────────────────────────────

def add_supplier(dfs: dict, data: dict) -> tuple[dict, str | None]:
    """
    Add a new supplier.  Returns (record, error_message).
    """
    code = str(data.get("code", "")).strip().upper()
    name = str(data.get("name", "")).strip()
    if not code or not name:
        return {}, "code and name are required"

    df = dfs["suppliers"]
    if code in df["code"].values:
        return {}, f"Supplier code '{code}' already exists"

    currencies    = str(data.get("currencies", "ILS"))
    email_patterns = str(data.get("email_patterns", ""))
    sup_type      = str(data.get("type", "custom"))

    new_row = pd.DataFrame([{
        "code":           code,
        "name":           name,
        "currencies":     currencies,
        "email_patterns": email_patterns,
        "type":           sup_type,
    }])
    dfs["suppliers"] = pd.concat([df, new_row], ignore_index=True)
    _save(dfs["suppliers"], "suppliers.csv")
    return new_row.iloc[0].to_dict(), None


def remove_supplier(dfs: dict, code: str) -> tuple[bool, str | None]:
    """
    Remove a supplier by code (only custom ones).
    Returns (success, error_message).
    """
    df  = dfs["suppliers"]
    row = df[df["code"] == code]
    if row.empty:
        return False, f"Supplier '{code}' not found"
    if str(row.iloc[0].get("type", "")) == "builtin":
        return False, "Cannot delete built-in suppliers"

    dfs["suppliers"] = df[df["code"] != code].reset_index(drop=True)
    _save(dfs["suppliers"], "suppliers.csv")
    return True, None


def import_suppliers_excel(dfs: dict, file_bytes: bytes) -> dict:
    """
    Parse an Excel/CSV upload and merge into the suppliers DataFrame.
    Required columns: code, name
    Optional: currencies, email_patterns, type
    Returns {imported, skipped, errors}.
    """
    try:
        src = io.BytesIO(file_bytes)
        raw = pd.read_excel(src) if file_bytes[:4] in (b'PK\x03\x04', b'\xd0\xcf\x11\xe0') \
              else pd.read_csv(io.BytesIO(file_bytes))
    except Exception as e:
        return {"imported": 0, "skipped": 0, "errors": [str(e)]}

    raw.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]

    missing = [c for c in _REQUIRED["suppliers"] if c not in raw.columns]
    if missing:
        return {"imported": 0, "skipped": 0,
                "errors": [f"Missing columns: {', '.join(missing)}"]}

    imported, skipped, errors = 0, 0, []
    for i, row in raw.iterrows():
        _, err = add_supplier(dfs, row.where(pd.notna(row), "").to_dict())
        if err:
            skipped += 1
            errors.append(f"Row {i + 2}: {err}")
        else:
            imported += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── RATE CARDS CRUD ───────────────────────────────────────────────────────────

def add_rate_card(dfs: dict, data: dict) -> tuple[dict, str | None]:
    """
    Add a price entry.  Returns (record, error_message).
    """
    required = _REQUIRED["rate_cards"]
    missing  = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return {}, f"Missing fields: {', '.join(missing)}"

    vendor_code = str(data["vendor_code"]).strip().upper()
    month       = str(data["month"]).strip()
    imp_code    = str(data["imp_code"]).strip().upper()

    # Validate vendor exists
    if vendor_code not in dfs["suppliers"]["code"].values:
        return {}, f"Vendor code '{vendor_code}' not found in suppliers"

    # Validate month format YYYY-MM
    if not re.match(r"^\d{4}-\d{2}$", month):
        return {}, "month must be YYYY-MM format"

    try:
        unit_price = float(data["unit_price"])
    except (ValueError, TypeError):
        return {}, "unit_price must be a number"

    df  = dfs["rate_cards"]
    dup = df[
        (df["vendor_code"] == vendor_code) &
        (df["month"]       == month) &
        (df["imp_code"]    == imp_code)
    ]
    if not dup.empty:
        # Update existing entry instead of duplicating
        idx = dup.index[0]
        dfs["rate_cards"].at[idx, "unit_price"]   = unit_price
        dfs["rate_cards"].at[idx, "currency"]     = str(data.get("currency", "ILS"))
        dfs["rate_cards"].at[idx, "service_name"] = str(data.get("service_name", ""))
        dfs["rate_cards"].at[idx, "unit"]         = str(data.get("unit", ""))
        _save(dfs["rate_cards"], "rate_cards.csv")
        return dfs["rate_cards"].iloc[idx].to_dict(), None

    new_row = pd.DataFrame([{
        "vendor_code":  vendor_code,
        "month":        month,
        "imp_code":     imp_code,
        "service_name": str(data.get("service_name", "")),
        "unit_price":   unit_price,
        "currency":     str(data.get("currency", "ILS")),
        "unit":         str(data.get("unit", "")),
    }])
    dfs["rate_cards"] = pd.concat([df, new_row], ignore_index=True)
    _save(dfs["rate_cards"], "rate_cards.csv")
    return new_row.iloc[0].to_dict(), None


def remove_rate_card(dfs: dict, vendor_code: str, month: str, imp_code: str) -> tuple[bool, str | None]:
    """Remove a rate card entry. Returns (success, error_message)."""
    df  = dfs["rate_cards"]
    mask = (
        (df["vendor_code"] == vendor_code.upper()) &
        (df["month"]       == month) &
        (df["imp_code"]    == imp_code.upper())
    )
    if not mask.any():
        return False, "Rate card entry not found"

    dfs["rate_cards"] = df[~mask].reset_index(drop=True)
    _save(dfs["rate_cards"], "rate_cards.csv")
    return True, None


def import_rate_cards_excel(dfs: dict, file_bytes: bytes, vendor_code: str) -> dict:
    """
    Parse an Excel/CSV upload for rate cards.
    Required columns: imp_code, unit_price, currency, month (or taken from filename).
    Returns {imported, skipped, errors}.
    """
    try:
        src = io.BytesIO(file_bytes)
        raw = pd.read_excel(src) if file_bytes[:4] in (b'PK\x03\x04', b'\xd0\xcf\x11\xe0') \
              else pd.read_csv(io.BytesIO(file_bytes))
    except Exception as e:
        return {"imported": 0, "skipped": 0, "errors": [str(e)]}

    raw.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]

    required = ["imp_code", "unit_price", "currency"]
    missing  = [c for c in required if c not in raw.columns]
    if missing:
        return {"imported": 0, "skipped": 0,
                "errors": [f"Missing columns: {', '.join(missing)}"]}

    imported, skipped, errors = 0, 0, []
    for i, row in raw.iterrows():
        rec = row.where(pd.notna(row), "").to_dict()
        rec["vendor_code"] = vendor_code.upper()
        if "month" not in rec or not rec["month"]:
            from datetime import date
            rec["month"] = date.today().strftime("%Y-%m")
        _, err = add_rate_card(dfs, rec)
        if err:
            skipped += 1
            errors.append(f"Row {i + 2}: {err}")
        else:
            imported += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── AUDIT LOG ────────────────────────────────────────────────────────────────

def append_audit(dfs: dict, action: str, detail: str,
                 status: str = "ok", user: str = "system") -> dict:
    """Append one entry to the audit log and persist to CSV."""
    from datetime import datetime
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    new = pd.DataFrame([{"ts": ts, "action": action,
                          "detail": detail, "status": status, "user": user}])
    dfs["audit_log"] = pd.concat([new, dfs["audit_log"]], ignore_index=True)
    _save(dfs["audit_log"], "audit_log.csv")
    return {"ts": ts, "action": action, "detail": detail,
            "status": status, "user": user}


def get_audit(dfs: dict, limit: int = 100,
              action: str | None = None,
              status: str | None = None) -> list[dict]:
    df = dfs["audit_log"].copy()
    if action:
        df = df[df["action"] == action]
    if status:
        df = df[df["status"] == status]
    df = df.head(limit)
    return df.where(pd.notna(df), None).to_dict(orient="records")


# ── M3 EXPORT ────────────────────────────────────────────────────────────────

# M3 program and transaction used for AP invoice header + lines
_M3_PROGRAM     = "APS450MI"
_M3_TRANSACTION = "AddHead"
_M3_LINE_TX     = "AddLine"

# Default M3 company settings (overridden by env or settings endpoint)
_M3_DEFAULTS = {
    "CONO": "100",   # company
    "DIVI": "HC",    # division
    "VT01": "6",     # VAT code
}


def generate_m3_export(dfs: dict,
                       invoice_ids: list[str] | None = None,
                       cono: str = "100",
                       divi: str = "HC",
                       vat_code: str = "6") -> list[dict]:
    """
    Build APS450MI AddHead + AddLine commands for each ready invoice.
    Returns a list of M3 command dicts.
    """
    inv_df = dfs["invoices"].copy()
    if invoice_ids:
        inv_df = inv_df[inv_df["invoice_id"].isin(invoice_ids)]

    # Only export approved or discrepancy-that-was-acknowledged invoices
    # (exclude 'pending' — not yet reviewed)
    exportable = inv_df[inv_df["status"].isin(["approved", "discrepancy"])]

    commands = []
    for _, inv in exportable.iterrows():
        inv_id  = str(inv["invoice_id"])
        lines   = dfs["invoice_lines"][
            dfs["invoice_lines"]["invoice_id"] == inv_id
        ].copy()

        # ── Header record ──────────────────────────────────────────────
        total    = float(inv.get("total_amount", 0) or 0)
        ils_amt  = total * CUSTOMS_RATE_USD_ILS if inv.get("currency") == "USD" else total
        vat_amt  = float(inv.get("vat_amount", 0) or 0)

        commands.append({
            "program":     _M3_PROGRAM,
            "transaction": _M3_TRANSACTION,
            "record": {
                "CONO": cono,
                "DIVI": divi,
                "SINO": inv_id.split("-")[-1],      # invoice number
                "SUNO": _vendor_code(dfs["suppliers"], str(inv.get("vendor", ""))),
                "IVDT": str(inv.get("date", "")).replace("/", ""),
                "IVAM": round(ils_amt, 2),
                "CUAM": round(total, 2),
                "CUCD": str(inv.get("currency", "ILS")),
                "VTAM": round(vat_amt, 2),
                "VT01": vat_code,
                "TX40": str(inv.get("vendor", "")),
            },
        })

        # ── Line records ───────────────────────────────────────────────
        for _, line in lines.iterrows():
            billed  = float(line.get("billed_amount", 0) or 0)
            ils_line = billed * CUSTOMS_RATE_USD_ILS if inv.get("currency") == "USD" else billed
            commands.append({
                "program":     _M3_PROGRAM,
                "transaction": _M3_LINE_TX,
                "record": {
                    "CONO": cono,
                    "DIVI": divi,
                    "SINO": inv_id.split("-")[-1],
                    "PONR": str(line.get("line_no", "")),
                    "ITNO": str(line.get("imp_code", "")),
                    "IVQA": 1,
                    "LIMT": round(ils_line, 2),
                    "CUAM": round(billed, 2),
                    "TX40": str(line.get("desc_raw", "")),
                },
            })

    return commands


def m3_export_to_excel(commands: list[dict]) -> bytes:
    """Flatten M3 commands into an Excel workbook and return bytes."""
    rows = []
    for cmd in commands:
        row = {"program": cmd["program"], "transaction": cmd["transaction"]}
        row.update(cmd["record"])
        rows.append(row)

    df  = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="M3_Export")
    return buf.getvalue()


def mark_invoices_exported(dfs: dict, invoice_ids: list[str], user: str = "system") -> int:
    """Set status → 'approved' and erp_status → 'נשלח ל-M3' for given invoices."""
    mask = dfs["invoices"]["invoice_id"].isin(invoice_ids)
    dfs["invoices"].loc[mask, "status"]     = "approved"
    dfs["invoices"].loc[mask, "erp_status"] = "נשלח ל-M3"
    _save(dfs["invoices"], "invoices.csv")
    count = int(mask.sum())
    if count:
        append_audit(dfs, "M3_EXPORT",
                     f"{count} חשבוניות נשלחו ל-M3: {', '.join(invoice_ids[:3])}{'...' if len(invoice_ids)>3 else ''}",
                     "ok", user)
    return count


# ── helpers ───────────────────────────────────────────────────────────────────

def _vendor_code(suppliers_df: pd.DataFrame, vendor_name: str) -> str:
    match = suppliers_df[suppliers_df["name"] == vendor_name]
    if not match.empty:
        return str(match.iloc[0]["code"])
    return vendor_name.split()[0].upper()[:6] if vendor_name else ""


def df_to_records(df: pd.DataFrame) -> list[dict]:
    return df.where(pd.notna(df), None).to_dict(orient="records")
