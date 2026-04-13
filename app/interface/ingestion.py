"""PDF Ingestion: validates and prepares PDF files for the pipeline."""

import hashlib
import shutil
from pathlib import Path
from typing import Any


class PDFIngestion:
    """Handles PDF file validation and preparation."""

    def __init__(self, storage_dir: Path):
        self._inputs_dir = storage_dir / "inputs"
        self._inputs_dir.mkdir(parents=True, exist_ok=True)

    def ingest(self, offer_pdf_path: str, cv_pdf_path: str) -> dict[str, Any]:
        """Validate and copy PDF files to the storage directory."""
        offer_result = self._validate_and_store(offer_pdf_path, "offer")
        cv_result = self._validate_and_store(cv_pdf_path, "cv")

        return {
            "offer": offer_result,
            "cv": cv_result,
            "valid": offer_result["valid"] and cv_result["valid"],
        }

    def _validate_and_store(self, pdf_path: str, doc_type: str) -> dict[str, Any]:
        """Validate a single PDF file."""
        path = Path(pdf_path)

        if not path.exists():
            return {"valid": False, "error": f"File not found: {pdf_path}", "path": pdf_path}

        if not path.suffix.lower() == ".pdf":
            return {"valid": False, "error": f"Not a PDF file: {pdf_path}", "path": pdf_path}

        file_size = path.stat().st_size
        if file_size == 0:
            return {"valid": False, "error": f"Empty file: {pdf_path}", "path": pdf_path}

        # Read first bytes to verify PDF magic number
        with open(path, "rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            return {"valid": False, "error": f"Invalid PDF header: {pdf_path}", "path": pdf_path}

        # Compute checksum
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        # Copy to storage
        dest = self._inputs_dir / f"{doc_type}_{path.name}"
        shutil.copy2(path, dest)

        return {
            "valid": True,
            "path": str(dest),
            "original_path": pdf_path,
            "file_name": path.name,
            "file_size_bytes": file_size,
            "checksum_sha256": checksum,
        }
