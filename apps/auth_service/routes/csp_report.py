"""CSP violation reporting endpoint.

Receives CSP violation reports from browsers and logs them
for security monitoring.

Security: Multi-layer defense (Nginx + FastAPI):
- Nginx-level: Rate limiting (10/min), payload limit (10KB)
- FastAPI-level: App-level payload check (defense-in-depth)
See nginx-oauth2.conf /csp-report location for details.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class CSPViolationReport(BaseModel):
    """CSP violation report from browser.

    Browsers send this JSON when CSP is violated.
    Schema defined by W3C CSP Level 2 spec.
    """

    document_uri: str = Field(..., alias="document-uri")
    violated_directive: str = Field(..., alias="violated-directive")
    effective_directive: str = Field(..., alias="effective-directive")
    original_policy: str = Field(..., alias="original-policy")
    blocked_uri: str = Field(..., alias="blocked-uri")
    status_code: int = Field(..., alias="status-code")
    referrer: str = ""
    source_file: str | None = Field(None, alias="source-file")
    line_number: int | None = Field(None, alias="line-number")
    column_number: int | None = Field(None, alias="column-number")
    sample: str | None = None

    # Pydantic V2: Use model_config instead of class Config (Gemini Code Review Iteration 5: Medium)
    # Codex Commit Review: MEDIUM - Forbid extra fields to prevent garbage data pollution
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CSPReportWrapper(BaseModel):
    """Wrapper for CSP violation report.

    Browsers send: {"csp-report": {...}}
    """

    csp_report: CSPViolationReport = Field(..., alias="csp-report")

    # Pydantic V2: Use model_config instead of class Config (Codex + Gemini Code Review Iteration 5: Low)
    # Codex Commit Review: MEDIUM - Forbid extra fields to prevent garbage data pollution
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


@router.post("/csp-report")
async def csp_report(request: Request) -> dict[str, str]:
    """Handle CSP violation reports.

    Logs CSP violations for security monitoring.
    This endpoint is public (no auth required) to allow browser CSP reports.

    Security measures (defense-in-depth):
    - Primary: Nginx client_max_body_size 10k (blocks large bodies BEFORE FastAPI parse)
    - Secondary: Pre-Pydantic size validation (Codex Fresh Review: HIGH - check before parsing)
    - Rate limiting: 10 requests/min per IP (enforced by Nginx)

    Args:
        request: FastAPI request object (for IP logging and body access)

    Returns:
        JSON response acknowledging receipt

    Raises:
        HTTPException: 413 if payload exceeds 10KB limit
        HTTPException: 400 if Content-Length malformed or body read error
        HTTPException: 422 if Pydantic validation fails (invalid CSP report format)
    """
    # App-level payload size guard (defense-in-depth)
    # CRITICAL (Codex Fresh Review: HIGH): Check size BEFORE Pydantic parsing
    # Removed report parameter to avoid automatic parsing

    # Step 1: Parse Content-Length header defensively (prevent ValueError crash)
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except (ValueError, TypeError) as err:
        # Malformed Content-Length header - reject request
        logger.warning(
            "CSP report rejected: malformed Content-Length header",
            extra={
                "content_length_raw": request.headers.get("content-length"),
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        raise HTTPException(
            status_code=400, detail="Malformed Content-Length header"
        ) from err

    # Step 2: Check Content-Length if present (fast path - reject before reading body)
    if content_length > 10240:  # 10KB limit
        logger.warning(
            "CSP report rejected: payload too large (Content-Length)",
            extra={
                "content_length": content_length,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        raise HTTPException(status_code=413, detail="Payload too large")

    # Step 3: Read raw body incrementally (Codex Fresh Review: HIGH - protect against chunked DoS)
    # CRITICAL: Read body in chunks to detect oversized payloads BEFORE buffering entire body
    # This protects against chunked transfer or missing Content-Length header attacks
    try:
        body_bytes = b""
        async for chunk in request.stream():
            body_bytes += chunk
            if len(body_bytes) > 10240:  # 10KB limit - abort immediately
                logger.warning(
                    "CSP report rejected: payload too large (streaming)",
                    extra={
                        "accumulated_size": len(body_bytes),
                        "content_length_header": content_length,
                        "client_ip": request.client.host if request.client else "unknown",
                    },
                )
                raise HTTPException(status_code=413, detail="Payload too large")
    except HTTPException:
        raise  # Re-raise 413 or 400 from above
    except Exception as e:
        logger.error(
            "CSP report rejected: body stream error",
            extra={
                "error": str(e),
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        raise HTTPException(status_code=400, detail="Invalid request body") from e

    # Step 4: Parse body with Pydantic AFTER size validation (Codex Fresh Review: HIGH)
    try:
        import json

        body_dict = json.loads(body_bytes.decode("utf-8"))
        report = CSPReportWrapper.model_validate(body_dict)
    except json.JSONDecodeError as err:
        logger.warning(
            "CSP report rejected: invalid JSON",
            extra={
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        logger.warning(
            "CSP report rejected: Pydantic validation failed",
            extra={
                "error": str(err),
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        # Codex Commit Review: HIGH - Use 422 for validation errors (API contract)
        raise HTTPException(status_code=422, detail="Invalid CSP report format") from err

    violation = report.csp_report

    # Extract client IP for logging
    client_ip = request.client.host if request.client else "unknown"

    # Log CSP violation with structured logging
    logger.warning(
        "CSP violation reported",
        extra={
            "client_ip": client_ip,
            "document_uri": violation.document_uri,
            "violated_directive": violation.violated_directive,
            "effective_directive": violation.effective_directive,
            "blocked_uri": violation.blocked_uri,
            "source_file": violation.source_file,
            "line_number": violation.line_number,
            "sample": violation.sample,
        },
    )

    # Check for common attack patterns
    if "javascript:" in violation.blocked_uri.lower():
        logger.error(
            "POSSIBLE XSS ATTACK: javascript: URI blocked by CSP",
            extra={
                "client_ip": client_ip,
                "blocked_uri": violation.blocked_uri,
                "document_uri": violation.document_uri,
            },
        )

    if "data:" in violation.blocked_uri.lower() and "script-src" in violation.violated_directive:
        logger.error(
            "POSSIBLE XSS ATTACK: data: URI script blocked by CSP",
            extra={
                "client_ip": client_ip,
                "blocked_uri": violation.blocked_uri,
                "document_uri": violation.document_uri,
            },
        )

    return {"status": "received", "message": "CSP violation logged"}
