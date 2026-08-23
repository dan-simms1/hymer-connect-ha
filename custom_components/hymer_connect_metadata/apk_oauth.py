#!/usr/bin/env python3
"""Extract the HYMER OAuth client from a Hermes APK bundle, using only stdlib.

The HYMER Android app is a React-Native/Hermes build: its JS is compiled to
Hermes *bytecode* in ``assets/index.android.bundle``. The app's config object
(``{BRAND, VERSION, CLOUD_API_BASE_URL, ..., CLIENT_USERNAME, CLIENT_PASSWORD,
...}``) is serialised into the bytecode's *object key/value buffers*, with the
string values interned (base64-encoded) in the *string table*.

This module reads just those three structures -- the string table and the
object key/value buffers -- to recover ``CLIENT_USERNAME`` / ``CLIENT_PASSWORD``
and emit the same ``oauth_client.json`` payload the offline decompiling
extractor produces. No ``hermes-dec``, no decompilation, no third-party deps.

It deliberately does NOT hard-code any credential: everything is read from the
APK the caller supplies. The recovered values are the caller's own app
artefact and must be treated as sensitive local data.
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
from dataclasses import dataclass

BUNDLE_ASSET = "assets/index.android.bundle"
HERMES_MAGIC = bytes.fromhex("c61fbc03")

# The APK and its Hermes bundle are untrusted input parsed inside Home
# Assistant, so every read is bounded. The real HYMER bundle is a few MB; these
# caps sit far above that but well below anything that could exhaust the host.
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_ZIP_RATIO = 200  # reject a member that inflates more than this (zip bomb)
_MAX_ZIP_ENTRIES = 200_000  # a real APK has a few thousand; reject a dir bomb
_MIN_CENTRAL_DIR_RECORD = 46  # fixed central-directory header size (bytes)


class OAuthExtractionError(RuntimeError):
    """Raised when the OAuth client cannot be recovered from the bundle."""


@dataclass
class _Bundle:
    raw: bytes
    version: int
    function_count: int
    string_count: int
    overflow_string_count: int
    smallstr_off: int
    overflow_off: int
    storage_off: int
    storage_size: int
    arraybuf_off: int
    arraybuf_size: int
    objkey_off: int
    objkey_size: int
    objval_off: int
    objval_size: int


def _align4(x: int) -> int:
    return (x + 3) & ~3


def _read_bundle(raw: bytes) -> _Bundle:
    if len(raw) < 128 or raw[:4] != HERMES_MAGIC:
        raise OAuthExtractionError("not a Hermes bytecode bundle (bad magic)")
    version = struct.unpack_from("<I", raw, 8)[0]
    if version < 90 or version > 96:
        # The section layout below is validated for Hermes v90-96 (the versions
        # HYMER has shipped). Refuse loudly rather than mis-parse a new layout.
        raise OAuthExtractionError(f"unsupported Hermes bytecode version {version}")
    (
        file_length, _global, function_count, string_kind_count, identifier_count,
        string_count, overflow_string_count, string_storage_size, _bic, _bis,
        _rec, _res, array_buffer_size, obj_key_size, obj_value_size,
        *_rest,
    ) = struct.unpack_from("<19I", raw, 32)
    if file_length != len(raw):
        raise OAuthExtractionError("Hermes header fileLength mismatch")

    # Every section offset below is derived from header-supplied counts. A
    # hostile header can make them run past the file; validate each computed end
    # against the (already length-checked) file so no later read is unbounded.
    n = len(raw)

    def _bound(end: int, what: str) -> int:
        if end < 0 or end > n:
            raise OAuthExtractionError(f"Hermes {what} section exceeds file length")
        return end

    off = _align4(128)
    off = _align4(off); _bound(off + function_count * 16, "function"); off += function_count * 16
    off = _align4(off); _bound(off + string_kind_count * 4, "string-kind"); off += string_kind_count * 4
    off = _align4(off); _bound(off + identifier_count * 4, "identifier"); off += identifier_count * 4
    off = _align4(off); smallstr_off = off; _bound(off + string_count * 4, "small-string"); off += string_count * 4
    off = _align4(off); overflow_off = off; _bound(off + overflow_string_count * 8, "overflow-string"); off += overflow_string_count * 8
    off = _align4(off); storage_off = off; _bound(off + string_storage_size, "string-storage"); off += string_storage_size
    off = _align4(off); arraybuf_off = off; _bound(off + array_buffer_size, "array-buffer"); off += array_buffer_size
    off = _align4(off); objkey_off = off; _bound(off + obj_key_size, "object-key"); off += obj_key_size
    off = _align4(off); objval_off = off; _bound(off + obj_value_size, "object-value"); off += obj_value_size
    return _Bundle(
        raw=raw, version=version, function_count=function_count, string_count=string_count,
        overflow_string_count=overflow_string_count,
        smallstr_off=smallstr_off, overflow_off=overflow_off, storage_off=storage_off,
        storage_size=string_storage_size,
        arraybuf_off=arraybuf_off, arraybuf_size=array_buffer_size,
        objkey_off=objkey_off, objkey_size=obj_key_size,
        objval_off=objval_off, objval_size=obj_value_size,
    )


def _check_zip_bounds(fh) -> None:
    """Reject a central-directory bomb before ``ZipFile`` parses the archive.

    ``ZipFile`` reads central-directory records across the declared directory
    *size* (not the declared entry count), so both are bounded: each record is at
    least ``_MIN_CENTRAL_DIR_RECORD`` bytes, so ``size_cd // 46`` is the true
    upper bound on the ZipInfo objects it will allocate. A malicious archive that
    under-declares its count is therefore still caught by the size bound.
    """
    fh.seek(0, io.SEEK_END)
    size = fh.tell()
    scan = min(size, 65557)  # 22-byte EOCD + up to a 65535-byte comment
    fh.seek(size - scan)
    tail = fh.read(scan)
    idx = tail.rfind(b"PK\x05\x06")
    if idx < 0:
        raise OAuthExtractionError("not a valid APK (no zip end-of-directory record)")
    total = struct.unpack_from("<H", tail, idx + 10)[0]
    size_cd = struct.unpack_from("<I", tail, idx + 12)[0]

    # ZipFile uses a ZIP64 record whenever a locator sits in the 20 bytes
    # IMMEDIATELY before the EOCD -- positionally, not gated on the legacy
    # 0xFFFF/0xFFFFFFFF sentinels -- so mirror that exactly, otherwise an archive
    # could declare tiny legacy values here yet make ZipFile read a large ZIP64
    # directory.
    eocd_abs = (size - scan) + idx
    loc_abs = eocd_abs - 20
    if loc_abs >= 0:
        fh.seek(loc_abs)
        loc = fh.read(20)
        if len(loc) == 20 and loc[:4] == b"PK\x06\x07":
            zip64_off = struct.unpack_from("<Q", loc, 8)[0]
            if zip64_off < 0 or zip64_off + 48 > size:
                raise OAuthExtractionError("invalid ZIP64 end-of-directory record")
            fh.seek(zip64_off)
            z64 = fh.read(56)
            if len(z64) < 48 or z64[:4] != b"PK\x06\x06":
                raise OAuthExtractionError("invalid ZIP64 end-of-directory record")
            total = struct.unpack_from("<Q", z64, 32)[0]
            size_cd = struct.unpack_from("<Q", z64, 40)[0]
    if (
        total > _MAX_ZIP_ENTRIES
        or size_cd // _MIN_CENTRAL_DIR_RECORD > _MAX_ZIP_ENTRIES
    ):
        raise OAuthExtractionError("APK central directory is too large")


def read_bundle_asset(source) -> bytes:
    """Return ``assets/index.android.bundle`` from an APK, bounded against zip bombs.

    ``source`` may be raw bytes or a seekable binary file object (so a large APK
    can be streamed to a temp file and never held twice in memory). The entry
    count, the member's declared/actual uncompressed size and the compression
    ratio are all bounded, so neither a central-directory bomb nor a lying member
    header can exhaust the host.
    """
    fh = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    try:
        _check_zip_bounds(fh)
        fh.seek(0)
        with zipfile.ZipFile(fh) as archive:  # fh not closed (we did not open it)
            try:
                info = archive.getinfo(BUNDLE_ASSET)
            except KeyError as err:
                raise OAuthExtractionError(
                    f"{BUNDLE_ASSET} not found in APK"
                ) from err
            if info.file_size > _MAX_BUNDLE_BYTES:
                raise OAuthExtractionError("Hermes bundle exceeds the size limit")
            if info.compress_size and (
                info.file_size / info.compress_size > _MAX_ZIP_RATIO
            ):
                raise OAuthExtractionError("Hermes bundle compression ratio too high")
            with archive.open(info) as member:
                data = member.read(_MAX_BUNDLE_BYTES + 1)
    except zipfile.BadZipFile as err:
        raise OAuthExtractionError("not a valid APK (bad zip)") from err
    if len(data) > _MAX_BUNDLE_BYTES:
        raise OAuthExtractionError("Hermes bundle exceeds the size limit")
    return data


class _StringTable:
    """Resolve a Hermes string id to its text (small + overflow entries)."""

    def __init__(self, b: _Bundle) -> None:
        self._b = b

    def get(self, idx: int) -> str:
        b = self._b
        if idx < 0 or idx >= b.string_count:
            return ""
        ent = struct.unpack_from("<I", b.raw, b.smallstr_off + idx * 4)[0]
        is_utf16 = ent & 1
        offset = (ent >> 1) & 0x7FFFFF
        length = (ent >> 24) & 0xFF
        if length == 0xFF:  # overflowed: real offset/length live in the overflow table
            if offset >= b.overflow_string_count:
                return ""
            offset, length = struct.unpack_from(
                "<II", b.raw, b.overflow_off + offset * 8
            )
        # Validate the (offset,length) against the string-storage section so a
        # hostile entry can't read another section (or a multi-GB slice).
        byte_len = length * 2 if is_utf16 else length
        if offset < 0 or byte_len < 0 or offset + byte_len > b.storage_size:
            return ""
        start = b.storage_off + offset
        data = b.raw[start: start + byte_len]
        return data.decode("utf-16-le" if is_utf16 else "utf-8", "replace")


def _b64_decode(text: str) -> str | None:
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 - not valid base64/text
        return None


def _client_payload(username: str, password: str) -> dict[str, str]:
    basic = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {
        "_comment": (
            "Locally generated OAuth client auth derived from the user's own "
            "HYMER app artefact. Sensitive local file. Do not share or publish."
        ),
        "authorization_header": f"Basic {basic}",
    }


def _extract_from_config_object(
    objects: list[dict[str, object]],
) -> tuple[str, str] | None:
    """Bind CLIENT_USERNAME/PASSWORD from a single reconstructed config object.

    This is the structurally-sound path: the username and password come from the
    *same* object literal (built by one ``NewObjectWithBuffer``), so there is no
    chance of pairing values that merely happen to sit next to each other in the
    value buffer. Requires exactly one such object.
    """
    matches: list[tuple[str, str]] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        user_b64 = obj.get("CLIENT_USERNAME")
        pass_b64 = obj.get("CLIENT_PASSWORD")
        if not isinstance(user_b64, str) or not isinstance(pass_b64, str):
            continue
        user = _b64_decode(user_b64)
        pw = _b64_decode(pass_b64)
        if user and pw and not user.startswith("https://"):
            matches.append((user, pw))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise OAuthExtractionError("multiple config objects define OAuth credentials")
    return None


def extract_oauth_client_from_objects(
    objects: list[dict[str, object]],
) -> dict[str, str]:
    """Build the OAuth payload from already-reconstructed object literals."""
    bound = _extract_from_config_object(objects)
    if bound is None:
        raise OAuthExtractionError("no OAuth config object found in the app bundle")
    return _client_payload(*bound)


def extract_oauth_client(apk_bytes: bytes) -> dict[str, str]:
    """Return the ``oauth_client.json`` payload for a HYMER APK's bytes.

    Fail-closed: the credentials are read from the single reconstructed config
    object that contains both keys (values bound to the same instruction). There
    is deliberately no value-buffer byte-scan fallback -- that could pair
    unrelated strings, and its full-table scan was itself an unbounded path.
    Reconstruction requires Hermes v96 (what HYMER ships); any parse/limit error
    propagates rather than being swallowed.
    """
    from .apk_hermes import reconstruct_object_literals_from_bundle

    objects = reconstruct_object_literals_from_bundle(read_bundle_asset(apk_bytes))
    return extract_oauth_client_from_objects(objects)


def extract_oauth_client_from_path(apk_path: str) -> dict[str, str]:
    from .apk_hermes import reconstruct_object_literals_from_bundle

    with open(apk_path, "rb") as fh:
        bundle = read_bundle_asset(fh)
    return extract_oauth_client_from_objects(
        reconstruct_object_literals_from_bundle(bundle)
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: extract_oauth_from_apk.py <path-to.apk>", file=sys.stderr)
        raise SystemExit(2)
    payload = extract_oauth_client_from_path(sys.argv[1])
    # Print the header length only, never the secret, on the CLI.
    hdr = payload["authorization_header"]
    print(f"authorization_header: Basic <{len(hdr) - 6} base64 chars>")
