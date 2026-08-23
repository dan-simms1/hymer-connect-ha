"""Legacy TLS-over-BLE engine for the HYMER SCU.

The SCU speaks TLS 1.0/1.1 with RSA-AES-SHA ciphers over the Nordic UART GATT
channel. This drives that handshake through an ``ssl.MemoryBIO`` pair so the
records can be pumped over any transport. Ported from the standalone token tool,
proven against a real vehicle on 2026-08-22. No Bluetooth/HA dependency and no
loopback self-test helpers.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

APP_TLS_CIPHERS = "@SECLEVEL=0:AES128-SHA:AES256-SHA"

APP_TLS_MINIMUM_VERSION = ssl.TLSVersion.TLSv1

APP_TLS_MAXIMUM_VERSION = ssl.TLSVersion.TLSv1_1

_TLS_READ_CHUNK_SIZE = 16_384

class TlsSupportError(RuntimeError):
    """Raised when the local TLS stack cannot match the app's legacy profile."""

@dataclass
class TlsPumpResult:
    """State emitted after advancing the TLS state machine once."""

    outbound_tls_records: bytes
    plaintext_chunks: list[bytes]
    handshake_complete: bool
    negotiated_tls_version: str | None
    cipher_suite: str | None
    cipher_protocol: str | None
    cipher_bits: int | None
    peer_closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbound_tls_records_hex": self.outbound_tls_records.hex(),
            "plaintext_chunks_hex": [chunk.hex() for chunk in self.plaintext_chunks],
            "handshake_complete": self.handshake_complete,
            "negotiated_tls_version": self.negotiated_tls_version,
            "cipher_suite": self.cipher_suite,
            "cipher_protocol": self.cipher_protocol,
            "cipher_bits": self.cipher_bits,
            "peer_closed": self.peer_closed,
        }

def create_legacy_tls_context(
    *,
    minimum_version: ssl.TLSVersion = APP_TLS_MINIMUM_VERSION,
    maximum_version: ssl.TLSVersion = APP_TLS_MAXIMUM_VERSION,
    ciphers: str = APP_TLS_CIPHERS,
) -> ssl.SSLContext:
    """Create a client TLS context that matches the app's observable profile."""
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = minimum_version
        context.maximum_version = maximum_version
        _clear_legacy_tls_disable_options(context)
        context.set_ciphers(ciphers)
        return context
    except ssl.SSLError as err:
        raise TlsSupportError(
            f"Could not build legacy TLS context for {minimum_version.name}.."
            f"{maximum_version.name} with ciphers {ciphers!r}: {err}"
        ) from err

def _clear_legacy_tls_disable_options(context: ssl.SSLContext) -> None:
    """Allow TLS 1.0/1.1 when OpenSSL exposes legacy protocol-disable flags."""
    for option_name in ("OP_NO_TLSv1", "OP_NO_TLSv1_1"):
        option = getattr(ssl, option_name, None)
        if isinstance(option, int):
            context.options &= ~option

class LegacyTlsClient:
    """Drive TLS over an arbitrary byte-stream transport via MemoryBIO."""

    def __init__(
        self,
        *,
        minimum_version: ssl.TLSVersion = APP_TLS_MINIMUM_VERSION,
        maximum_version: ssl.TLSVersion = APP_TLS_MAXIMUM_VERSION,
        ciphers: str = APP_TLS_CIPHERS,
        server_hostname: str | None = None,
        session_id_hint: str | None = None,
    ) -> None:
        self._context = create_legacy_tls_context(
            minimum_version=minimum_version,
            maximum_version=maximum_version,
            ciphers=ciphers,
        )
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._sslobj = self._context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_hostname=server_hostname,
        )
        self._handshake_complete = False
        self._peer_closed = False
        self._session_id_hint = session_id_hint

    @property
    def handshake_complete(self) -> bool:
        return self._handshake_complete

    @property
    def peer_closed(self) -> bool:
        return self._peer_closed

    @property
    def session_id_hint(self) -> str | None:
        return self._session_id_hint

    def connection_info(self) -> dict[str, Any]:
        """Return the negotiated TLS session details once the handshake completes."""
        if not self._handshake_complete:
            raise TlsSupportError("TLS handshake is not complete yet")
        cipher = self._sslobj.cipher()
        return {
            "negotiated_tls_version": self._sslobj.version(),
            "cipher_suite": cipher[0],
            "cipher_protocol": cipher[1],
            "cipher_bits": cipher[2],
        }

    def begin_handshake(self) -> TlsPumpResult:
        """Kick off the client handshake and return outbound TLS records."""
        return self._advance_tls_state_machine()

    def feed_encrypted(self, data: bytes) -> TlsPumpResult:
        """Feed encrypted TLS records from the transport into the client."""
        if data:
            self._incoming.write(data)
        return self._advance_tls_state_machine()

    def encrypt_plaintext(self, data: bytes) -> TlsPumpResult:
        """Encode plaintext into outbound TLS application-data records."""
        if not self._handshake_complete:
            raise TlsSupportError("TLS handshake is not complete yet")
        if self._peer_closed:
            raise TlsSupportError("TLS peer has already closed the session")
        try:
            self._sslobj.write(data)
        except ssl.SSLError as err:
            raise TlsSupportError(f"Could not write plaintext into TLS session: {err}") from err
        return self._build_result([])

    def close(self) -> TlsPumpResult:
        """Start TLS shutdown and return any resulting close-notify records."""
        try:
            self._sslobj.unwrap()
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            pass
        except ssl.SSLError as err:
            raise TlsSupportError(f"TLS shutdown failed: {err}") from err
        return self._build_result([])

    def _advance_tls_state_machine(self) -> TlsPumpResult:
        plaintext_chunks: list[bytes] = []
        if not self._handshake_complete:
            try:
                self._sslobj.do_handshake()
                self._handshake_complete = True
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                pass
            except ssl.SSLError as err:
                raise TlsSupportError(f"TLS handshake failed: {err}") from err

        if self._handshake_complete:
            while True:
                try:
                    plaintext = self._sslobj.read(_TLS_READ_CHUNK_SIZE)
                except ssl.SSLWantReadError:
                    break
                except ssl.SSLWantWriteError:
                    break
                except ssl.SSLZeroReturnError:
                    self._peer_closed = True
                    break
                except ssl.SSLError as err:
                    raise TlsSupportError(f"TLS decrypt failed: {err}") from err
                if not plaintext:
                    break
                plaintext_chunks.append(plaintext)

        return self._build_result(plaintext_chunks)

    def _build_result(self, plaintext_chunks: list[bytes]) -> TlsPumpResult:
        cipher = self._sslobj.cipher() if self._handshake_complete else None
        return TlsPumpResult(
            outbound_tls_records=self._drain_outgoing_records(),
            plaintext_chunks=plaintext_chunks,
            handshake_complete=self._handshake_complete,
            negotiated_tls_version=self._sslobj.version() if self._handshake_complete else None,
            cipher_suite=cipher[0] if cipher else None,
            cipher_protocol=cipher[1] if cipher else None,
            cipher_bits=cipher[2] if cipher else None,
            peer_closed=self._peer_closed,
        )

    def _drain_outgoing_records(self) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = self._outgoing.read()
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
