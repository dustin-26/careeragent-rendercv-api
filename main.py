from __future__ import annotations

import hmac
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="Campus Career Agent RenderCV API",
    version="1.0.0",
)


class RenderRequest(BaseModel):
    """Request body for generating a RenderCV PDF."""

    model_config = ConfigDict(extra="forbid")

    # Complete RenderCV document:
    # {
    #   "cv": {...},
    #   "design": {...},
    #   "locale": {...}
    # }
    document: dict[str, Any]

    file_name: str = Field(
        default="resume.pdf",
        min_length=1,
        max_length=120,
    )


def verify_api_key(received_key: str | None) -> None:
    """Validate the caller using a secret stored in App Service settings."""

    expected_key = os.getenv("RENDERCV_API_KEY", "")

    if not expected_key:
        raise HTTPException(
            status_code=500,
            detail="RENDERCV_API_KEY is not configured on the server.",
        )

    if received_key is None or not hmac.compare_digest(
        received_key,
        expected_key,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key.",
        )


def safe_pdf_name(file_name: str) -> str:
    """Remove unsafe characters and guarantee a PDF extension."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._")

    if not cleaned:
        cleaned = "resume.pdf"

    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"

    return cleaned


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Campus Career Agent RenderCV API",
        "status": "running",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
    }


@app.post("/render-cv")
def render_cv(
    request: RenderRequest,
    x_api_key: str | None = Header(default=None),
) -> Response:
    verify_api_key(x_api_key)

    if "cv" not in request.document:
        raise HTTPException(
            status_code=400,
            detail="The document must contain a top-level 'cv' object.",
        )

    output_file_name = safe_pdf_name(request.file_name)

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_directory:
            working_directory = Path(temp_directory)

            yaml_path = working_directory / "input.yaml"
            pdf_path = working_directory / "output.pdf"

            yaml_text = yaml.safe_dump(
                request.document,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )

            yaml_path.write_text(
                yaml_text,
                encoding="utf-8",
            )

            command = [
                "rendercv",
                "render",
                "input.yaml",
                "--pdf-path",
                "output.pdf",
                "--dont-generate-markdown",
                "--dont-generate-html",
                "--dont-generate-png",
            ]

            process = subprocess.run(
                command,
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            if process.returncode != 0:
                error_message = (
                    process.stderr.strip()
                    or process.stdout.strip()
                    or "RenderCV returned an unknown error."
                )

                raise HTTPException(
                    status_code=422,
                    detail=error_message[-5000:],
                )

            if not pdf_path.exists():
                raise HTTPException(
                    status_code=500,
                    detail="RenderCV completed but no PDF was generated.",
                )

            pdf_content = pdf_path.read_bytes()

    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail="PDF generation exceeded the 120-second limit.",
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected rendering error: {exc}",
        ) from exc

    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{output_file_name}"'
            ),
            "Cache-Control": "no-store",
        },
    )
