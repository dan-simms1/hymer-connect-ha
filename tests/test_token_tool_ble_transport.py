from __future__ import annotations

from pathlib import Path
import sys
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1] / "tools" / "hymer_token_tool"
sys.path.insert(0, str(TOOL_ROOT))

from hymer_token_tool import scu  # noqa: E402


class TokenToolBleTransportTests(unittest.TestCase):
    def test_uart_rx_prefers_write_without_response_when_available(self) -> None:
        properties = {"write", "write-without-response"}

        self.assertTrue(
            scu._choose_write_mode(
                properties=properties,
                identifier="scu",
                description="control",
            )
        )
        self.assertFalse(
            scu._choose_write_mode(
                properties=properties,
                identifier="scu",
                description="UART RX",
                prefer_without_response=True,
            )
        )

    def test_tls_record_writes_are_chunked_without_response_and_paced(self) -> None:
        import asyncio

        class FakeBleakClient:
            def __init__(self) -> None:
                self.writes: list[tuple[bytes, bool]] = []

            async def write_gatt_char(
                self,
                _uuid: str,
                chunk: bytes,
                *,
                response: bool,
            ) -> None:
                self.writes.append((bytes(chunk), response))

        async def run_test() -> None:
            client = FakeBleakClient()
            session = scu.ScuBleSession("scu", write_chunk_size=3)
            session._client = client
            session._write_with_response = False
            session._write_chunk_size = 3
            sleeps: list[float] = []
            original_sleep = scu.asyncio.sleep

            async def fake_sleep(delay: float) -> None:
                sleeps.append(delay)

            scu.asyncio.sleep = fake_sleep
            try:
                await session._write_tls_records(b"abcdefg")
            finally:
                scu.asyncio.sleep = original_sleep

            self.assertEqual(
                client.writes,
                [(b"abc", False), (b"def", False), (b"g", False)],
            )
            self.assertEqual(
                sleeps,
                [scu.SCU_UART_WRITE_PACING_S, scu.SCU_UART_WRITE_PACING_S],
            )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
