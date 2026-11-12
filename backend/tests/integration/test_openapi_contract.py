"""Contract verification tests ensuring backend API and OpenAPI match frontend."""

from app.config import get_settings
from app.main import app


def test_openapi_schema_contains_all_core_routes():
    """Verify all 11 required endpoints are present in the OpenAPI schema."""
    openapi = app.openapi()
    paths = openapi.get("paths", {})

    expected_routes = [
        ("/api/health", "get"),
        ("/api/reviews", "post"),
        ("/api/reviews", "get"),
        ("/api/reviews/{id}", "get"),
        ("/api/reviews/{id}", "delete"),
        ("/api/reviews/{id}/files", "get"),
        ("/api/reviews/{id}/findings", "get"),
        ("/api/findings/{id}", "get"),
        ("/api/findings/{id}", "patch"),
        ("/api/settings", "get"),
        ("/api/settings", "put"),
    ]

    for path, method in expected_routes:
        assert path in paths, f"Missing route {path} in OpenAPI paths"
        assert method in paths[path], f"Missing {method.upper()} method for {path}"


def test_cors_configuration_allows_local_dev_origins_without_wildcard():
    """Verify CORS origins list includes local dev addresses and avoids wildcard."""
    settings = get_settings()
    origins = settings.cors_origins_list

    assert "*" not in origins, "Wildcard '*' must not be permitted in CORS origins"
    assert "http://localhost:5173" in origins, "http://localhost:5173 must be allowed"
    assert "http://127.0.0.1:5173" in origins, "http://127.0.0.1:5173 must be allowed"


def test_health_endpoint_contract(client):
    """Verify GET /api/health response matches HealthResponse schema."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"


def test_repeated_severity_query_param_schema():
    """Verify severity query parameter on findings endpoint is defined as an array."""
    openapi = app.openapi()
    params = openapi["paths"]["/api/reviews/{id}/findings"]["get"]["parameters"]
    sev_param = next((p for p in params if p["name"] == "severity"), None)

    assert sev_param is not None, "Severity query parameter must exist"
    assert sev_param["in"] == "query"

    # Schema allows array of Severity enums
    schema = sev_param.get("schema", {})
    any_of = schema.get("anyOf", [])
    array_type = next((s for s in any_of if s.get("type") == "array"), None)
    assert array_type is not None, (
        "Severity parameter schema must allow array for repeated query params"
    )
    assert "Severity" in array_type["items"].get("$ref", "")


def test_error_envelope_structure(client):
    """Verify 404 responses conform to PR Sentinel standard error envelope."""
    response = client.get("/api/reviews/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data, (
        "Error response must follow standard envelope with 'error' root key"
    )
    assert "code" in data["error"]
    assert "message" in data["error"]
