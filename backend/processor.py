"""
Invoice data processor — loads CSV/Excel files into Pandas DataFrames
and exposes clean, serialisable summaries for the Flask API.
"""
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
CUSTOMS_RATE_USD_ILS = 3.70  # default fallback rate


# ── loaders ──────────────────────────────────────────────────────────────────

def _load(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if path.suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def load_all() -> dict[str, pd.DataFrame]:
    return {
        "invoices":      _load("invoices.csv"),
        "invoice_lines": _load("invoice_lines.csv"),
        "suppliers":     _load("suppliers.csv"),
        "imp_mapping":   _load("imp_mapping.csv"),
        "rate_cards":    _load("rate_cards.csv"),
    }


# ── KPI summary ──────────────────────────────────────────────────────────────

def kpi_summary(dfs: dict) -> dict:
    inv = dfs["invoices"].copy()
    lines = dfs["invoice_lines"].copy()

    total_invoices = len(inv)
    pending   = int((inv["status"] == "pending").sum())
    approved  = int((inv["status"] == "approved").sum())
    discrepancy = int((inv["status"] == "discrepancy").sum())

    # Convert all amounts to ILS for comparison
    inv["total_ils"] = inv.apply(
        lambda r: r["total_amount"] * CUSTOMS_RATE_USD_ILS
        if r["currency"] == "USD" else r["total_amount"],
        axis=1,
    )
    total_value_ils = float(inv["total_ils"].sum())

    # Count discrepancy lines
    bad_lines = lines[lines["ok"].astype(str).str.lower() == "false"]
    total_discrepancy_lines = len(bad_lines)

    return {
        "total_invoices": total_invoices,
        "pending": pending,
        "approved": approved,
        "discrepancy": discrepancy,
        "total_value_ils": round(total_value_ils, 2),
        "discrepancy_lines": total_discrepancy_lines,
    }


# ── invoice detail ────────────────────────────────────────────────────────────

def invoice_detail(dfs: dict, invoice_id: str) -> dict | None:
    inv = dfs["invoices"]
    row = inv[inv["invoice_id"] == invoice_id]
    if row.empty:
        return None

    inv_dict = row.iloc[0].where(pd.notna(row.iloc[0]), None).to_dict()

    lines = dfs["invoice_lines"]
    lines_rows = lines[lines["invoice_id"] == invoice_id].copy()
    lines_rows = lines_rows.where(pd.notna(lines_rows), None)
    inv_dict["lines"] = lines_rows.to_dict(orient="records")

    return inv_dict


# ── price-check ───────────────────────────────────────────────────────────────

def price_check(dfs: dict, invoice_id: str) -> list[dict]:
    """
    For each line in the invoice, look up the agreed price from rate_cards
    and compute the variance.  Returns a list of check results.
    """
    detail = invoice_detail(dfs, invoice_id)
    if detail is None:
        return []

    rate_cards = dfs["rate_cards"]
    vendor_code = _vendor_code(dfs["suppliers"], detail.get("vendor", ""))
    month = detail.get("month", "")

    results = []
    for line in detail["lines"]:
        imp = line.get("imp_code", "")
        billed = float(line.get("billed_amount") or 0)

        agreed_row = rate_cards[
            (rate_cards["vendor_code"] == vendor_code) &
            (rate_cards["month"] == month) &
            (rate_cards["imp_code"] == imp)
        ]

        if agreed_row.empty:
            agreed = line.get("agreed_price")
            agreed = float(agreed) if agreed else None
        else:
            agreed = float(agreed_row.iloc[0]["unit_price"])

        variance_pct = None
        status = "unknown"
        if agreed is not None and agreed > 0:
            variance_pct = round((billed - agreed) / agreed * 100, 2)
            status = "ok" if abs(variance_pct) < 0.01 else "discrepancy"
        elif line.get("ok") is True or str(line.get("ok", "")).lower() == "true":
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


def _vendor_code(suppliers_df: pd.DataFrame, vendor_name: str) -> str:
    match = suppliers_df[suppliers_df["name"] == vendor_name]
    if not match.empty:
        return str(match.iloc[0]["code"])
    # fallback: first token of name upper-cased
    return vendor_name.split()[0].upper()[:6] if vendor_name else ""


# ── IMP text matcher ─────────────────────────────────────────────────────────

def match_imp(dfs: dict, text: str) -> dict | None:
    """
    Given raw invoice line text, return the best-matching IMP rule
    using keyword_pattern (regex) with confidence score.
    Returns None if no match exceeds threshold.
    """
    imp_df = dfs["imp_mapping"].copy()
    best = None
    best_conf = 0

    for _, row in imp_df.iterrows():
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


# ── serialisers ──────────────────────────────────────────────────────────────

def df_to_records(df: pd.DataFrame) -> list[dict]:
    return df.where(pd.notna(df), None).to_dict(orient="records")
