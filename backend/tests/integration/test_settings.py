"""Integration tests for the /api/settings endpoints."""

from fastapi.testclient import TestClient

from app.db.models import Category


def test_get_settings_defaults(client: TestClient):
    """Verify GET /api/settings returns expected seed defaults."""
    response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["max_files"] == 50
    assert data["max_diff_bytes"] == 500000
    assert data["min_confidence"] == 0.5
    assert data["gemini_model"] == ""

    # All defined categories must be enabled by default
    expected_categories = [c.value for c in Category]
    assert sorted(data["enabled_categories"]) == sorted(expected_categories)


def test_update_settings_success(client: TestClient):
    """Verify PUT /api/settings updates values and persists changes."""
    update_payload = {
        "max_files": 120,
        "max_diff_bytes": 1000000,
        "min_confidence": 0.8,
        "enabled_categories": ["security", "performance"],
        "gemini_model": "gemini-2.5-pro",
    }
    put_response = client.put("/api/settings", json=update_payload)
    assert put_response.status_code == 200

    updated_data = put_response.json()
    assert updated_data["max_files"] == 120
    assert updated_data["max_diff_bytes"] == 1000000
    assert updated_data["min_confidence"] == 0.8
    assert updated_data["enabled_categories"] == ["security", "performance"]
    assert updated_data["gemini_model"] == "gemini-2.5-pro"

    # Confirm GET reflects the updated state
    get_response = client.get("/api/settings")
    assert get_response.status_code == 200
    assert get_response.json() == updated_data


def test_update_settings_validation_errors(client: TestClient):
    """Verify invalid setting values return 422 with standard envelope."""
    invalid_cases = [
        ({"max_files": 0}, "max_files must be >= 1"),
        ({"max_files": 250}, "max_files must be <= 200"),
        ({"max_diff_bytes": 5000}, "max_diff_bytes must be >= 10,000"),
        ({"max_diff_bytes": 6000000}, "max_diff_bytes must be <= 5,000,000"),
        ({"min_confidence": -0.1}, "min_confidence must be >= 0.0"),
        ({"min_confidence": 1.5}, "min_confidence must be <= 1.0"),
        (
            {"enabled_categories": ["invalid_category_name"]},
            "category must be valid enum",
        ),
    ]

    for payload, description in invalid_cases:
        response = client.put("/api/settings", json=payload)
        assert response.status_code == 422, f"Failed for {description}: {response.text}"
        data = response.json()

        # Check standard error envelope
        assert "error" in data, f"Envelope missing for {description}"
        assert data["error"]["code"] == "validation_error"
        assert "message" in data["error"]
        assert "request_id" in data["error"]
        assert data["error"]["request_id"] != ""
