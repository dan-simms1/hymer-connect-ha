from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1] / "tools" / "hymer_token_tool"
sys.path.insert(0, str(TOOL_ROOT))

from hymer_token_tool import tokens  # noqa: E402


def _b64url_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt(payload: dict[str, object]) -> str:
    return ".".join(
        [
            _b64url_json({"alg": "RS256", "typ": "JWT"}),
            _b64url_json(payload),
            "signaturepart",
        ]
    )


class TokenToolJwtTests(unittest.TestCase):
    def test_find_remote_access_refresh_token_in_larger_text(self) -> None:
        access = _jwt({"ett": "access", "sub": "account"})
        refresh = _jwt({"ett": "access-refresh", "urn": "urn:ehg:vehicle:test"})
        text = json.dumps({"one": access, "nested": {"token": refresh}})

        self.assertEqual(tokens.find_remote_access_refresh_token(text), refresh)

    def test_coerce_remote_access_refresh_token_keeps_unmatched_value(self) -> None:
        self.assertEqual(
            tokens.coerce_remote_access_refresh_token("not-a-token"),
            "not-a-token",
        )

    def test_decode_jwt_without_verification_handles_invalid_input(self) -> None:
        self.assertEqual(tokens.decode_jwt_without_verification("not-a-token"), {})


if __name__ == "__main__":
    unittest.main()
