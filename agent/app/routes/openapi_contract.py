"""Shared, behavior-free OpenAPI response annotations for mounted HTTP routes.

The response middleware adds ``x-copilot-request-id`` to every real response.  These
helpers document that invariant (and the closed error envelopes) without wrapping or
otherwise changing route execution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """The shape returned by FastAPI ``HTTPException`` handlers."""

    detail: Any


_CORRELATION_HEADER = {
    "description": "PHI-free request correlation identifier.",
    "required": True,
    "schema": {"type": "string", "minLength": 1},
}

_ERROR_DESCRIPTIONS = {
    400: "The request could not complete the closed workflow.",
    401: "The opaque session expired.",
    403: "Patient, encounter, scope, or route authorization failed closed.",
    404: "Session, document, or page was not found within the patient pin.",
    409: "The durable job is not in the required state.",
    413: "Request-body middleware rejected the body before route processing.",
    415: "File signature/media type is not permitted for the selected document type.",
    429: "A caller-scoped request or daily resource bound was reached.",
    503: "A required dependency or artifact integrity check failed closed.",
}


def correlation_headers(
    *,
    location: bool = False,
    retry_after: bool = False,
    private_no_store: bool = False,
) -> dict[str, dict[str, object]]:
    """Return fresh header metadata so FastAPI cannot mutate shared declarations."""

    headers: dict[str, dict[str, object]] = {
        "x-copilot-request-id": deepcopy(_CORRELATION_HEADER)
    }
    if location:
        headers["Location"] = {
            "description": "Trusted SMART authorization origin or server-owned UI.",
            "required": True,
            "schema": {"type": "string", "format": "uri-reference"},
        }
    if retry_after:
        headers["Retry-After"] = {
            "description": "Seconds before this caller should retry.",
            "required": True,
            "schema": {"type": "integer", "minimum": 1},
        }
    if private_no_store:
        headers["Cache-Control"] = {
            "description": "This patient-pinned response must not be stored.",
            "schema": {"type": "string", "enum": ["private, no-store"]},
        }
    return headers


def documented_response(
    description: str,
    *,
    model: type[BaseModel] | None = None,
    content: dict[str, object] | None = None,
    location: bool = False,
    private_no_store: bool = False,
) -> dict[str, object]:
    """Build a response annotation with the mandatory correlation header."""

    response: dict[str, object] = {
        "description": description,
        "headers": correlation_headers(
            location=location, private_no_store=private_no_store
        ),
    }
    if model is not None:
        response["model"] = model
    if content is not None:
        response["content"] = content
    return response


def documented_errors(
    *status_codes: int, schema_only: bool = False
) -> dict[int, dict[str, object]]:
    """Describe the mounted route's closed HTTP error surface."""

    responses: dict[int, dict[str, object]] = {}
    for status_code in status_codes:
        if status_code == 422:
            responses[status_code] = {
                "description": "Request validation or a closed input contract failed.",
                "headers": correlation_headers(),
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {
                                    "$ref": (
                                        "#/components/schemas/HTTPValidationError"
                                    )
                                },
                                {"$ref": "#/components/schemas/ErrorResponse"},
                            ]
                        }
                    }
                },
            }
            continue
        if status_code not in _ERROR_DESCRIPTIONS:
            raise ValueError(f"unsupported documented error status: {status_code}")
        response: dict[str, object] = {
            "description": _ERROR_DESCRIPTIONS[status_code],
            "headers": correlation_headers(retry_after=status_code == 429),
        }
        if schema_only:
            # A route-level HTML response class must not leak into exception docs;
            # FastAPI's HTTPException handler always returns this JSON envelope.
            response["content"] = {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                }
            }
        else:
            response["model"] = ErrorResponse
        responses[status_code] = response
    return responses
