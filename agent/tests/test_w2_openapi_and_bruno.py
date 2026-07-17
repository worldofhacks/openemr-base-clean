"""Authoritative OpenAPI and runnable Week 2 grader-workflow synchronization."""

from __future__ import annotations

from pathlib import Path
import re

from fastapi import FastAPI
import yaml


_ROOT = Path(__file__).resolve().parents[1]
_SPEC_PATH = _ROOT / "ops" / "openapi.yaml"
_BRUNO = _ROOT / "bruno"
_EXPECTED_OPERATIONS = {
    "/health": {"get"},
    "/ready": {"get"},
    "/launch": {"get"},
    "/week2/launch": {"get"},
    "/callback": {"get"},
    "/week2": {"get"},
    "/documents": {"post"},
    "/documents/lab-trends": {"get"},
    "/documents/{document_id}/status": {"get"},
    "/documents/{document_id}/extraction-report": {"get"},
    "/documents/{document_id}/retry": {"post"},
    "/documents/{document_id}/pages/{page_number}": {"get"},
    "/documents/{document_id}/readback-verification": {"get"},
    "/evidence/search": {"post"},
    "/chat": {"post"},
}


def _spec() -> dict:
    loaded = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _generated_spec() -> dict:
    from app.routes.chat import router as chat_router
    from app.routes.documents import router as documents_router
    from app.routes.evidence import router as evidence_router
    from app.routes.health import router as health_router
    from app.routes.sessions import router as sessions_router
    from app.routes.week2_ui import router as week2_ui_router

    app = FastAPI()
    for router in (
        health_router,
        sessions_router,
        chat_router,
        documents_router,
        evidence_router,
        week2_ui_router,
    ):
        app.include_router(router)
    return app.openapi()


def _mounted_operations() -> dict[str, set[str]]:
    generated = _generated_spec()["paths"]
    return {
        path: {
            method
            for method in generated[path]
            if method in {"get", "post", "put", "patch", "delete"}
        }
        for path in _EXPECTED_OPERATIONS
        if path in generated
    }


def _resolve(spec: dict, value: dict) -> dict:
    ref = value.get("$ref")
    if ref is None:
        return value
    assert ref.startswith("#/"), f"external OpenAPI ref is forbidden: {ref}"
    current: object = spec
    for part in ref[2:].split("/"):
        assert isinstance(current, dict) and part in current, f"unresolved ref: {ref}"
        current = current[part]
    assert isinstance(current, dict)
    return current


def _walk_refs(spec: dict, value: object) -> None:
    if isinstance(value, dict):
        if "$ref" in value:
            _resolve(spec, value)
        for child in value.values():
            _walk_refs(spec, child)
    elif isinstance(value, list):
        for child in value:
            _walk_refs(spec, child)


def _schema_ref_names(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.add(ref.rsplit("/", 1)[-1])
        else:
            for child in value.values():
                refs.update(_schema_ref_names(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_schema_ref_names(child))
    return refs


def _normalized_enum(value: dict) -> set[object]:
    if "enum" in value:
        return set(value["enum"])
    if "const" in value:
        return {value["const"]}
    return set()


def test_one_authoritative_spec_matches_every_mounted_w2_operation() -> None:
    spec = _spec()
    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["title"] == "Clinical Co-Pilot Week 2 API"
    assert "fragment" not in spec["info"]["description"].casefold()
    assert not (_ROOT / "openapi" / "evidence.yaml").exists()

    documented = {
        path: {method for method in item if method in {"get", "post", "put", "patch", "delete"}}
        for path, item in spec["paths"].items()
    }
    assert documented == _EXPECTED_OPERATIONS
    assert _mounted_operations() == _EXPECTED_OPERATIONS
    _walk_refs(spec, spec)


def test_spec_locks_enums_citation_v2_and_health_sha() -> None:
    from app.schemas.citations import CitationSourceType, CitationV2
    from app.schemas.documents import FailureReason, UploadRequest

    spec = _spec()
    schemas = spec["components"]["schemas"]
    assert schemas["DocumentType"]["enum"] == [
        "lab_pdf",
        "intake_form",
        "medication_list",
    ]
    assert set(schemas["DocumentType"]["enum"]) == set(
        UploadRequest.model_json_schema()["properties"]["doc_type"]["enum"]
    )
    assert set(schemas["FailureReason"]["enum"]) == {
        reason.value for reason in FailureReason
    }
    assert set(schemas["CitationSourceType"]["enum"]) == {
        source.value for source in CitationSourceType
    }
    assert set(schemas["CitationV2"]["required"]) == set(CitationV2.model_fields)
    assert schemas["ChatResponse"]["properties"]["citations"]["items"] == {
        "$ref": "#/components/schemas/CitationV2"
    }
    assert schemas["HealthResponse"]["required"] == ["status", "sha"]


def test_spec_locks_status_media_redirect_correlation_and_session_only_trends() -> None:
    spec = _spec()
    paths = spec["paths"]
    expected_statuses = {
        ("/health", "get"): {"200"},
        ("/ready", "get"): {"200", "503"},
        ("/launch", "get"): {"302", "422", "429"},
        ("/week2/launch", "get"): {"302", "422", "429", "503"},
        ("/callback", "get"): {"302", "400", "403", "422"},
        ("/documents", "post"): {
            "200",
            "202",
            "401",
            "403",
            "404",
            "413",
            "415",
            "422",
            "429",
            "503",
        },
        ("/documents/lab-trends", "get"): {"200", "401", "404", "422", "503"},
        ("/documents/{document_id}/extraction-report", "get"): {
            "200",
            "401",
            "403",
            "404",
            "409",
            "422",
            "503",
        },
        ("/documents/{document_id}/retry", "post"): {
            "202",
            "401",
            "403",
            "404",
            "409",
            "413",
            "422",
            "503",
        },
        ("/evidence/search", "post"): {
            "200",
            "401",
            "404",
            "413",
            "422",
            "429",
            "503",
        },
        ("/chat", "post"): {
            "200",
            "401",
            "403",
            "404",
            "413",
            "422",
            "503",
        },
    }
    for (path, method), statuses in expected_statuses.items():
        assert set(paths[path][method]["responses"]) == statuses

    upload_content = paths["/documents"]["post"]["requestBody"]["content"]
    assert set(upload_content) == {"multipart/form-data"}
    page_content = paths["/documents/{document_id}/pages/{page_number}"]["get"][
        "responses"
    ]["200"]["content"]
    assert set(page_content) == {"image/png"}
    chat_content = paths["/chat"]["post"]["responses"]["200"]["content"]
    assert set(chat_content) == {"application/json", "text/event-stream"}
    assert set(chat_content["text/event-stream"]["x-event-schemas"]) == {
        "claim_block",
        "done",
    }
    redirect = _resolve(spec, paths["/week2/launch"]["get"]["responses"]["302"])
    assert "Location" in redirect["headers"]

    trend_parameters = [
        _resolve(spec, value)
        for value in paths["/documents/lab-trends"]["get"]["parameters"]
    ]
    assert [(item["name"], item["in"]) for item in trend_parameters] == [
        ("session_id", "query")
    ]

    evidence_parameters = [
        _resolve(spec, value)
        for value in paths["/evidence/search"]["post"]["parameters"]
    ]
    assert [(item["name"].casefold(), item["in"]) for item in evidence_parameters] == [
        ("x-copilot-session-id", "header")
    ]
    assert evidence_parameters[0]["required"] is False
    evidence_request = spec["components"]["schemas"]["EvidenceSearchRequest"]
    assert "session_id" not in evidence_request["properties"]
    assert "patient_id" not in evidence_request["properties"]
    assert spec["components"]["schemas"]["ChatRequest"]["properties"]["message"][
        "maxLength"
    ] == 4000

    for item in paths.values():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = item.get(method)
            if operation is None:
                continue
            for response in operation["responses"].values():
                resolved = _resolve(spec, response)
                assert "x-copilot-request-id" in resolved.get("headers", {})


def test_generated_openapi_semantically_matches_the_authoritative_contract() -> None:
    """Prevent route annotations from silently drifting behind the committed 3.0 spec.

    FastAPI emits OpenAPI 3.1, so nullable/const encodings may differ.  Operation status,
    media, schema references, enums, and headers are compared at their semantic seams.
    """

    authoritative = _spec()
    generated = _generated_spec()
    assert generated["openapi"].startswith("3.1")

    for path, methods in _EXPECTED_OPERATIONS.items():
        for method in methods:
            expected_operation = authoritative["paths"][path][method]
            actual_operation = generated["paths"][path][method]
            assert set(actual_operation["responses"]) == set(
                expected_operation["responses"]
            ), (path, method, "statuses")

            expected_parameters = [
                _resolve(authoritative, parameter)
                for parameter in expected_operation.get("parameters", [])
            ]
            actual_parameters = actual_operation.get("parameters", [])
            assert [
                (
                    parameter["name"].casefold(),
                    parameter["in"],
                    bool(parameter.get("required")),
                )
                for parameter in actual_parameters
            ] == [
                (
                    parameter["name"].casefold(),
                    parameter["in"],
                    bool(parameter.get("required")),
                )
                for parameter in expected_parameters
            ], (path, method, "parameters")

            expected_request = expected_operation.get("requestBody")
            actual_request = actual_operation.get("requestBody")
            assert (actual_request is None) == (expected_request is None)
            if expected_request is not None and actual_request is not None:
                expected_content = expected_request["content"]
                actual_content = actual_request["content"]
                assert set(actual_content) == set(expected_content), (
                    path,
                    method,
                    "request media",
                )
                for media_type in expected_content:
                    expected_schema = expected_content[media_type]["schema"]
                    actual_schema = actual_content[media_type]["schema"]
                    if path == "/documents":
                        expected_upload = _resolve(authoritative, expected_schema)
                        actual_upload = _resolve(generated, actual_schema)
                        assert set(actual_upload["required"]) == set(
                            expected_upload["required"]
                        )
                        assert set(actual_upload["properties"]) == set(
                            expected_upload["properties"]
                        )
                        expected_type = _resolve(
                            authoritative,
                            expected_upload["properties"]["doc_type"],
                        )
                        actual_type = actual_upload["properties"]["doc_type"]
                        assert _normalized_enum(actual_type) == _normalized_enum(
                            expected_type
                        )
                    else:
                        assert _schema_ref_names(actual_schema) == _schema_ref_names(
                            expected_schema
                        ), (path, method, "request schema")

            for status, actual_response_value in actual_operation[
                "responses"
            ].items():
                expected_response = _resolve(
                    authoritative, expected_operation["responses"][status]
                )
                actual_response = _resolve(generated, actual_response_value)
                assert set(actual_response.get("content", {})) == set(
                    expected_response.get("content", {})
                ), (path, method, status, "response media")
                assert {
                    name.casefold()
                    for name in actual_response.get("headers", {})
                } == {
                    name.casefold()
                    for name in expected_response.get("headers", {})
                }, (path, method, status, "response headers")
                actual_headers = {
                    name.casefold(): value
                    for name, value in actual_response.get("headers", {}).items()
                }
                expected_headers = {
                    name.casefold(): value
                    for name, value in expected_response.get("headers", {}).items()
                }
                for name, expected_header_value in expected_headers.items():
                    expected_header = _resolve(
                        authoritative, expected_header_value
                    )
                    actual_header = _resolve(generated, actual_headers[name])
                    assert bool(actual_header.get("required")) == bool(
                        expected_header.get("required")
                    )
                    expected_header_schema = expected_header.get("schema", {})
                    actual_header_schema = actual_header.get("schema", {})
                    for keyword in (
                        "type",
                        "format",
                        "minimum",
                        "minLength",
                        "enum",
                    ):
                        assert actual_header_schema.get(keyword) == (
                            expected_header_schema.get(keyword)
                        ), (path, method, status, name, keyword)

                for media_type, expected_media in expected_response.get(
                    "content", {}
                ).items():
                    actual_media = actual_response["content"][media_type]
                    expected_schema = expected_media.get("schema", {})
                    actual_schema = actual_media.get("schema", {})
                    assert _schema_ref_names(actual_schema) == _schema_ref_names(
                        expected_schema
                    ), (path, method, status, media_type, "response schema refs")
                    if not _schema_ref_names(expected_schema):
                        assert actual_schema.get("type") == expected_schema.get("type")
                        assert actual_schema.get("format") == expected_schema.get(
                            "format"
                        )

    expected_events = authoritative["paths"]["/chat"]["post"]["responses"][
        "200"
    ]["content"]["text/event-stream"]["x-event-schemas"]
    actual_events = generated["paths"]["/chat"]["post"]["responses"]["200"][
        "content"
    ]["text/event-stream"]["x-event-schemas"]
    assert set(actual_events) == set(expected_events) == {"claim_block", "done"}
    for event_name in expected_events:
        expected_event = _resolve(authoritative, expected_events[event_name])
        actual_event = actual_events[event_name]
        assert set(actual_event["required"]) == set(expected_event["required"])
        assert set(actual_event["properties"]) == set(expected_event["properties"])
    assert _normalized_enum(actual_events["done"]["properties"]["source"]) == (
        _normalized_enum(
            _resolve(authoritative, expected_events["done"])["properties"]["source"]
        )
    )

    expected_schemas = authoritative["components"]["schemas"]
    actual_schemas = generated["components"]["schemas"]
    assert _normalized_enum(actual_schemas["HealthResponse"]["properties"]["status"]) == {
        "alive"
    }
    assert _normalized_enum(
        actual_schemas["ReadinessResponse"]["properties"]["status"]
    ) == set(expected_schemas["ReadinessResponse"]["properties"]["status"]["enum"])
    assert set(actual_schemas["CitationSourceType"]["enum"]) == set(
        expected_schemas["CitationSourceType"]["enum"]
    )


def _bru_meta(contents: str) -> tuple[str, int]:
    name = re.search(r"meta\s*\{.*?name:\s*(.+?)\n.*?seq:\s*(\d+)", contents, re.S)
    assert name is not None
    return name.group(1).strip(), int(name.group(2))


def test_bruno_has_all_ten_grader_flows_and_a_real_bounded_poll() -> None:
    request_files = sorted(
        path
        for path in _BRUNO.glob("*.bru")
        if path.name not in {"bruno.json"}
    )
    metadata = {
        path.name: _bru_meta(path.read_text(encoding="utf-8"))
        for path in request_files
    }
    assert set(metadata) == {
        "health.bru",
        "ready.bru",
        "lab-upload.bru",
        "lab-status.bru",
        "lab-extraction-report.bru",
        "lab-page-preview.bru",
        "lab-readback.bru",
        "evidence-search.bru",
        "chat.bru",
        "intake-upload.bru",
        "intake-duplicate.bru",
    }
    assert sorted(sequence for _name, sequence in metadata.values()) == list(range(1, 12))

    poll = (_BRUNO / "lab-status.bru").read_text(encoding="utf-8")
    assert 'bru.setNextRequest("Bounded lab status poll")' in poll
    assert "attempts < 30" in poll
    assert "await bru.sleep(1000)" in poll
    duplicate = (_BRUNO / "intake-duplicate.bru").read_text(encoding="utf-8")
    assert "expect(res.getStatus()).to.equal(200)" in duplicate
    assert 'bru.getVar("intake_document_id")' in duplicate
    evidence = (_BRUNO / "evidence-search.bru").read_text(encoding="utf-8")
    assert "x-copilot-session-id" not in evidence.casefold()
    assert "{{session_id}}" not in evidence

    combined = "\n".join(path.read_text(encoding="utf-8") for path in request_files)
    for forbidden in ("access_token", "bearer ", "authorization:", "patient_id:"):
        assert forbidden not in combined.casefold()
