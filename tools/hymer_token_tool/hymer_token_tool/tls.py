"""Legacy TLS helpers for the SCU BLE pairing transport."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from typing import Any

APP_TLS_CIPHERS = "@SECLEVEL=0:AES128-SHA:AES256-SHA"
APP_TLS_MINIMUM_VERSION = ssl.TLSVersion.TLSv1
APP_TLS_MAXIMUM_VERSION = ssl.TLSVersion.TLSv1_1
_TLS_READ_CHUNK_SIZE = 16_384
_TLS_SELF_TEST_PING = b"ping"
_TLS_SELF_TEST_PONG = b"pong"


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


@dataclass
class TlsLoopbackSelfTestResult:
    """One local loopback result for a requested TLS version."""

    requested_tls_version: str
    negotiated_tls_version: str
    cipher_suite: str
    cipher_protocol: str
    cipher_bits: int
    server_received_hex: str
    client_received_hex: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def run_tls_loopback_self_test() -> list[TlsLoopbackSelfTestResult]:
    """Verify locally that MemoryBIO can speak TLS 1.0 and 1.1 with app-like ciphers."""
    with tempfile.TemporaryDirectory(prefix="hymer-token-tool-tls-") as tempdir:
        cert_path, key_path = _generate_self_signed_cert(Path(tempdir))
        return [
            _run_one_loopback_self_test(ssl.TLSVersion.TLSv1, cert_path, key_path),
            _run_one_loopback_self_test(ssl.TLSVersion.TLSv1_1, cert_path, key_path),
        ]


def _generate_self_signed_cert(tempdir: Path) -> tuple[Path, Path]:
    openssl_binary = shutil.which("openssl")
    if not openssl_binary:
        raise TlsSupportError("openssl is required for tls-self-test but was not found")
    cert_path = tempdir / "cert.pem"
    key_path = tempdir / "key.pem"
    command = [
        openssl_binary,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-days",
        "1",
        "-subj",
        "/CN=localhost",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as err:
        raise TlsSupportError(f"Could not generate a local self-signed cert: {err}") from err
    return cert_path, key_path


def _create_legacy_server_context(
    tls_version: ssl.TLSVersion,
    cert_path: Path,
    key_path: Path,
) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = tls_version
        context.maximum_version = tls_version
        context.set_ciphers(APP_TLS_CIPHERS)
        context.load_cert_chain(str(cert_path), str(key_path))
        return context
    except ssl.SSLError as err:
        raise TlsSupportError(
            f"Could not create local TLS server context for {tls_version.name}: {err}"
        ) from err


def _run_one_loopback_self_test(
    tls_version: ssl.TLSVersion,
    cert_path: Path,
    key_path: Path,
) -> TlsLoopbackSelfTestResult:
    server_context = _create_legacy_server_context(tls_version, cert_path, key_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    server_state: dict[str, Any] = {}

    def run_server() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                with server_context.wrap_socket(connection, server_side=True) as tls_socket:
                    server_state["negotiated_tls_version"] = tls_socket.version()
                    cipher = tls_socket.cipher()
                    server_state["cipher_suite"] = cipher[0]
                    server_state["cipher_protocol"] = cipher[1]
                    server_state["cipher_bits"] = cipher[2]
                    server_state["received"] = tls_socket.recv(len(_TLS_SELF_TEST_PING))
                    tls_socket.sendall(_TLS_SELF_TEST_PONG)
        except Exception as err:  # pragma: no cover - depends on local TLS stack
            server_state["error"] = repr(err)
        finally:
            listener.close()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    client = LegacyTlsClient(
        minimum_version=tls_version,
        maximum_version=tls_version,
    )
    reply = b""
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock:
        sock.settimeout(5.0)
        exchange = client.begin_handshake()
        _send_outbound_records(sock, exchange.outbound_tls_records)
        while not exchange.handshake_complete:
            incoming = sock.recv(65_536)
            if not incoming:
                raise TlsSupportError("Local TLS self-test server closed during handshake")
            exchange = client.feed_encrypted(incoming)
            _send_outbound_records(sock, exchange.outbound_tls_records)

        exchange = client.encrypt_plaintext(_TLS_SELF_TEST_PING)
        _send_outbound_records(sock, exchange.outbound_tls_records)
        while not reply:
            incoming = sock.recv(65_536)
            if not incoming:
                raise TlsSupportError("Local TLS self-test server closed before reply")
            exchange = client.feed_encrypted(incoming)
            _send_outbound_records(sock, exchange.outbound_tls_records)
            if exchange.plaintext_chunks:
                reply = b"".join(exchange.plaintext_chunks)

    thread.join(timeout=5.0)
    if thread.is_alive():
        raise TlsSupportError("Local TLS self-test server did not exit cleanly")
    if "error" in server_state:
        raise TlsSupportError(
            f"Local TLS self-test server failed for {tls_version.name}: {server_state['error']}"
        )
    if server_state.get("received") != _TLS_SELF_TEST_PING:
        raise TlsSupportError(
            f"Local TLS self-test server saw unexpected plaintext for {tls_version.name}: "
            f"{server_state.get('received')!r}"
        )
    if reply != _TLS_SELF_TEST_PONG:
        raise TlsSupportError(
            f"Local TLS self-test client saw unexpected reply for {tls_version.name}: {reply!r}"
        )

    return TlsLoopbackSelfTestResult(
        requested_tls_version=tls_version.name,
        negotiated_tls_version=str(server_state["negotiated_tls_version"]),
        cipher_suite=str(server_state["cipher_suite"]),
        cipher_protocol=str(server_state["cipher_protocol"]),
        cipher_bits=int(server_state["cipher_bits"]),
        server_received_hex=server_state["received"].hex(),
        client_received_hex=reply.hex(),
    )


def _send_outbound_records(sock: socket.socket, records: bytes) -> None:
    if records:
        sock.sendall(records)
