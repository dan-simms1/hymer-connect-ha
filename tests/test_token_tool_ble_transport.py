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


class RequestResponseMatchingTests(unittest.TestCase):
    """A control write is acknowledged by a matching request id, nothing else."""

    @staticmethod
    def _response_frame(request_id: int, status: int = 1) -> bytes:
        from hymer_token_tool import ble

        body = ble._encode_varint_field(
            ble._RESPONSE_REQUEST_ID_FIELD, request_id
        ) + ble._encode_varint_field(ble._RESPONSE_STATUS_FIELD, status)
        return ble.encode_ble_pia_frame(
            ble._encode_length_delimited_field(ble._BLE_PROTOCOL_RESPONSE_FIELD, body)
        )

    def _run_with_frames(self, frames: list[bytes], request_id: int):
        import asyncio
        from collections import deque

        async def run_test():
            session = scu.ScuBleSession("scu")
            session._loop = asyncio.get_running_loop()
            session._pending_frames = deque(frames)

            async def fake_send(_plaintext: bytes) -> None:
                return None

            session._send_application_data = fake_send
            return await session._send_request_and_await_response(
                b"request-frame", request_id, timeout=1.0
            )

        return asyncio.run(run_test())

    def test_response_for_a_different_request_is_ignored(self) -> None:
        """The SCU pushes unsolicited frames, so the first frame is not the answer."""
        response = self._run_with_frames(
            [self._response_frame(111), self._response_frame(222, status=1)],
            request_id=222,
        )
        self.assertEqual(response.request_id, 222)
        self.assertTrue(response.succeeded)

    def test_undecodable_frames_are_skipped_rather_than_fatal(self) -> None:
        from hymer_token_tool import ble

        subscription_push = ble.encode_ble_pia_frame(b"\x22\x02\x08\x01")
        response = self._run_with_frames(
            [subscription_push, self._response_frame(7)], request_id=7
        )
        self.assertEqual(response.request_id, 7)

    def test_an_error_status_is_returned_rather_than_waited_through(self) -> None:
        response = self._run_with_frames(
            [self._response_frame(9, status=15)], request_id=9
        )
        self.assertEqual(response.status, 15)
        self.assertFalse(response.succeeded)

    def test_no_matching_response_times_out(self) -> None:
        with self.assertRaises(scu.ScuBleSessionError):
            self._run_with_frames([self._response_frame(1)], request_id=999)


if __name__ == "__main__":
    unittest.main()
