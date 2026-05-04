from __future__ import annotations

from pathlib import Path
import ssl
import sys
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1] / "tools" / "hymer_token_tool"
sys.path.insert(0, str(TOOL_ROOT))

from hymer_token_tool import tls  # noqa: E402


class TokenToolTlsTests(unittest.TestCase):
    def test_legacy_tls_context_lowers_openssl_security_level(self) -> None:
        self.assertTrue(tls.APP_TLS_CIPHERS.startswith("@SECLEVEL=0:"))

        context = tls.create_legacy_tls_context()

        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1)
        self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_1)
        for option_name in ("OP_NO_TLSv1", "OP_NO_TLSv1_1"):
            option = getattr(ssl, option_name, None)
            if isinstance(option, int):
                self.assertFalse(context.options & option)


if __name__ == "__main__":
    unittest.main()
