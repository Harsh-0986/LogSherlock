"""Redaction and privacy tests (SPEC §15.5)."""

from __future__ import annotations

from fixtures.log_samples import SECRETS

from app.logengine.redaction import (
    PLACEHOLDER_CONNSTR,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_IP,
    PLACEHOLDER_SECRET,
    PLACEHOLDER_TOKEN,
    Redactor,
)


class TestRedactor:
    def test_redacts_bearer_token(self) -> None:
        result = Redactor().redact("Authorization: Bearer abcdef1234567890XYZ")
        assert "abcdef1234567890XYZ" not in result.text
        assert PLACEHOLDER_TOKEN in result.text

    def test_redacts_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMDQ0MzIifQ.dBjftJeZ4CVP"
        assert jwt not in Redactor().redact(f"Invalid bearer {jwt} for refresh").text

    def test_redacts_aws_key(self) -> None:
        assert "AKIAIOSFODNN7EXAMPLE" not in Redactor().redact("aws_key=AKIAIOSFODNN7EXAMPLE").text

    def test_redacts_connection_string(self) -> None:
        out = Redactor().redact("dsn=postgresql://appuser:hunter2secret@db:5432/shop").text
        assert "hunter2secret" not in out
        assert PLACEHOLDER_CONNSTR in out

    def test_redacts_password_assignment(self) -> None:
        assert "s3cr3t" not in Redactor().redact("password=s3cr3t").text

    def test_redacts_email(self) -> None:
        out = Redactor().redact("reset for ops@shopstream.io").text
        assert "ops@shopstream.io" not in out
        assert PLACEHOLDER_EMAIL in out

    def test_redacts_ip_addresses(self) -> None:
        out = Redactor().redact("connect ECONNREFUSED 10.42.2.15:5432").text
        assert "10.42.2.15" not in out
        assert PLACEHOLDER_IP in out

    def test_ip_redaction_can_be_disabled(self) -> None:
        redactor = Redactor(redact_ip_addresses=False)
        assert "10.42.2.15" in redactor.redact("host 10.42.2.15").text

    def test_redaction_can_be_disabled_entirely(self) -> None:
        redactor = Redactor(enabled=False)
        text = "Authorization: Bearer abcdef1234567890XYZ"
        assert redactor.redact(text).text == text

    def test_sample_secrets_are_fully_redacted(self) -> None:
        out = Redactor().redact(SECRETS).text
        for secret in (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "a3f9c1e77b2d4056",
            "hunter2secret",
            "AKIAIOSFODNN7EXAMPLE",
            "abcdef1234567890XYZ",
            "ops@shopstream.io",
        ):
            assert secret not in out, f"leaked: {secret}"

    def test_counts_replacements(self) -> None:
        result = Redactor().redact(SECRETS)
        assert result.total_redactions > 0
        assert PLACEHOLDER_SECRET in result.counts or PLACEHOLDER_TOKEN in result.counts

    def test_non_secret_text_is_untouched(self) -> None:
        text = "Database connection failed at Database.connect"
        assert Redactor().redact(text).text == text

    def test_preserves_diagnostic_values(self) -> None:
        out = Redactor().redact("HTTP 503 upstream down, SQLSTATE 08006, ORA-01722").text
        assert "503" in out and "08006" in out and "ORA-01722" in out

    def test_does_not_leak_secret_type(self) -> None:
        # Placeholders are generic; the real secret type is not disclosed.
        out = Redactor().redact("aws_key=AKIAIOSFODNN7EXAMPLE").text
        assert "AKIA" not in out
