"""Saneado de secretos: enlaces, textos y estructuras."""

from __future__ import annotations

from conftest import TEST_API_KEY

from multimedia_search_mcp.security import redact, redact_text, sanitize_url


class TestSanitizeUrl:
    def test_removes_api_key_parameter(self) -> None:
        cleaned = sanitize_url("http://127.0.0.1:9696/2/download?apikey=abc123&link=xyz")
        assert "abc123" not in cleaned
        assert "apikey" not in cleaned
        assert "link=xyz" in cleaned

    def test_removes_alternative_secret_parameters(self) -> None:
        for name in ("api_key", "api-key", "token", "passkey"):
            cleaned = sanitize_url(f"http://host/path?{name}=secret&ok=1")
            assert "secret" not in cleaned, name
            assert "ok=1" in cleaned

    def test_removes_userinfo(self) -> None:
        assert sanitize_url("http://user:pass@host/path").startswith("http://host/path")

    def test_keeps_magnet_intact(self) -> None:
        magnet = "magnet:?xt=urn:btih:ABC&dn=title&tr=udp%3A%2F%2Ftracker%3A80"
        assert sanitize_url(magnet) == magnet

    def test_non_url_is_untouched(self) -> None:
        assert sanitize_url("debian-13.0-amd64.iso") == "debian-13.0-amd64.iso"

    def test_empty_string(self) -> None:
        assert sanitize_url("") == ""


class TestRedaction:
    def test_text_secret_is_replaced(self) -> None:
        text = redact_text(f"error con clave {TEST_API_KEY} en la URL", [TEST_API_KEY])
        assert TEST_API_KEY not in text
        assert "***" in text

    def test_url_inside_text_is_cleaned(self) -> None:
        text = redact_text("fallo en http://host/x?apikey=abc&id=1", [])
        assert "apikey" not in text
        assert "id=1" in text

    def test_url_inside_a_longer_message_is_cleaned(self) -> None:
        text = redact_text(
            "error al llamar a https://tracker.test/api?apikey=secreto123 y reintentar",
            [],
        )
        assert "apikey" not in text
        assert "secreto123" not in text
        assert "reintentar" in text

    def test_short_secrets_are_not_replaced(self) -> None:
        # Evita destrozar textos por coincidencias triviales.
        assert redact_text("abc", ["ab"]) == "abc"

    def test_nested_structures_are_redacted(self) -> None:
        payload = {
            "results": [
                {"url": f"http://h/x?apikey={TEST_API_KEY}", "note": TEST_API_KEY},
            ],
            "count": 1,
            "none": None,
        }
        cleaned = redact(payload, [TEST_API_KEY])
        serialized = str(cleaned)
        assert TEST_API_KEY not in serialized
        assert cleaned["count"] == 1
        assert cleaned["none"] is None

    def test_redact_is_idempotent(self) -> None:
        payload = {"url": "http://h/x?apikey=secret123"}
        once = redact(payload, [])
        assert redact(once, []) == once
