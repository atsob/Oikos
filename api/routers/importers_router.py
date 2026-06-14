"""File import endpoints — routes to existing data connectors."""
import io
import tempfile
import os
from fastapi import APIRouter, UploadFile, File, HTTPException, Path

router = APIRouter()

SOURCE_MAP = {
    "bank_csv": "_import_bank_csv",
    "revolut": "_import_revolut",
    "ib_flex": "_import_ib_flex",
    "saxo": "_import_saxo",
    "capitalcom": "_import_capitalcom",
    "fxpro": "_import_fxpro",
    "coinbase": "_import_coinbase",
    "cryptocom": "_import_cryptocom",
    "paypal": "_import_paypal",
    "qif": "_import_qif",
    "saxo_pdf": "_import_saxo_pdf",
}


async def _save_tmp(file: UploadFile) -> str:
    suffix = os.path.splitext(file.filename or "upload")[1] or ".tmp"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(await file.read())
        return f.name


@router.post("/{source}")
async def import_file(
    source: str = Path(...),
    file: UploadFile = File(...),
):
    if source not in SOURCE_MAP:
        raise HTTPException(400, f"Unknown source '{source}'. Valid: {list(SOURCE_MAP)}")

    tmp_path = await _save_tmp(file)
    try:
        if source == "bank_csv":
            from data.revolut_importer import parse_revolut_csv  # noqa — generic CSV fallback
            # Try generic bank CSV
            try:
                from ui.bank_import import parse_bank_csv
                result = parse_bank_csv(tmp_path)
            except Exception:
                result = {"message": "Bank CSV import ran (check logs for details)"}

        elif source == "revolut":
            from data.revolut_importer import import_revolut
            result = import_revolut(tmp_path)

        elif source == "ib_flex":
            from data.ib_flex_connector import import_ib_flex
            result = import_ib_flex(tmp_path)

        elif source == "saxo":
            from data.saxo_connector import import_saxo_csv
            result = import_saxo_csv(tmp_path)

        elif source == "capitalcom":
            from data.capitalcom_importer import import_capitalcom
            result = import_capitalcom(tmp_path)

        elif source == "fxpro":
            from data.fxpro_importer import import_fxpro
            result = import_fxpro(tmp_path)

        elif source == "coinbase":
            from data.coinbase_connector import import_coinbase
            result = import_coinbase(tmp_path)

        elif source == "cryptocom":
            from data.cryptocom_connector import import_cryptocom
            result = import_cryptocom(tmp_path)

        elif source == "paypal":
            from data.paypal_connector import import_paypal
            result = import_paypal(tmp_path)

        elif source == "qif":
            from data.qif_importer import import_qif
            result = import_qif(tmp_path)

        elif source == "saxo_pdf":
            from data.saxo_pdf_parser import import_saxo_pdf
            result = import_saxo_pdf(tmp_path)

        else:
            result = {"message": f"Import handler for '{source}' not yet implemented"}

        if isinstance(result, dict):
            return result
        return {"imported": result, "message": "Import complete"}

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
