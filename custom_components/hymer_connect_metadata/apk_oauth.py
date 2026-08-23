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


class OAuthExtractionError(RuntimeError):
    """Raised when the OAuth client cannot be recovered from the bundle."""


@dataclass
class _Bundle:
    raw: bytes
    string_count: int
    smallstr_off: int
    overflow_off: int
    storage_off: int
    objkey_off: int
    objkey_size: int
    objval_off: int
    objval_size: int


def _align4(x: int) -> int:
    return (x + 3) & ~3


def _read_bundle(raw: bytes) -> _Bundle:
    if raw[:4] != HERMES_MAGIC:
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

    off = _align4(128)
    off = _align4(off); off += function_count * 16       # function headers
    off = _align4(off); off += string_kind_count * 4     # string kinds
    off = _align4(off); off += identifier_count * 4      # identifier hashes
    off = _align4(off); smallstr_off = off; off += string_count * 4
    off = _align4(off); overflow_off = off; off += overflow_string_count * 8
    off = _align4(off); storage_off = off; off += string_storage_size
    off = _align4(off); off += array_buffer_size         # array buffer
    off = _align4(off); objkey_off = off; off += obj_key_size
    off = _align4(off); objval_off = off; off += obj_value_size
    return _Bundle(
        raw=raw, string_count=string_count,
        smallstr_off=smallstr_off, overflow_off=overflow_off, storage_off=storage_off,
        objkey_off=objkey_off, objkey_size=obj_key_size,
        objval_off=objval_off, objval_size=obj_value_size,
    )


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
            offset, length = struct.unpack_from(
                "<II", b.raw, b.overflow_off + offset * 8
            )
        if is_utf16:
            data = b.raw[b.storage_off + offset: b.storage_off + offset + length * 2]
            return data.decode("utf-16-le", "replace")
        data = b.raw[b.storage_off + offset: b.storage_off + offset + length]
        return data.decode("utf-8", "replace")


def _find_string_id(st: _StringTable, count: int, needle: str) -> int:
    for i in range(count):
        if st.get(i) == needle:
            return i
    raise OAuthExtractionError(f"string {needle!r} not present in the app bundle")


def _b64_decode(text: str) -> str | None:
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 - not valid base64/text
        return None


def extract_oauth_client(apk_bytes: bytes) -> dict[str, str]:
    """Return the ``oauth_client.json`` payload for a HYMER APK's bytes.

    Raises OAuthExtractionError if the config object cannot be located/validated.
    """
    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as z:
        try:
            raw = z.read(BUNDLE_ASSET)
        except KeyError as err:
            raise OAuthExtractionError(f"{BUNDLE_ASSET} not found in APK") from err

    b = _read_bundle(raw)
    st = _StringTable(b)
    cu_id = _find_string_id(st, b.string_count, "CLIENT_USERNAME")
    cp_id = _find_string_id(st, b.string_count, "CLIENT_PASSWORD")

    keybuf = b.raw[b.objkey_off: b.objkey_off + b.objkey_size]
    valbuf = b.raw[b.objval_off: b.objval_off + b.objval_size]

    # 1) Locate the config object's KEY run: CLIENT_USERNAME's id (uint16),
    #    immediately followed by CLIENT_PASSWORD's id, disambiguates it.
    want = struct.pack("<HH", cu_id, cp_id)
    kpos = keybuf.find(want)
    if kpos < 0:
        raise OAuthExtractionError("CLIENT_USERNAME/PASSWORD key pair not found")
    if keybuf.find(want, kpos + 1) != -1:
        raise OAuthExtractionError("ambiguous CLIENT_USERNAME/PASSWORD key run")

    # 2) The config object's ``CLOUD_..._URL`` keys sit contiguously right before
    #    CLIENT_USERNAME. Scan left over those ``*_URL`` keys to count them; they
    #    are the anchor. (Fails loudly if a future app reshuffles the layout.)
    def key_at(byte_off: int) -> int:
        return struct.unpack_from("<H", keybuf, byte_off)[0]

    n_url = 0
    while kpos - (n_url + 1) * 2 >= 0 and st.get(
        key_at(kpos - (n_url + 1) * 2)
    ).endswith("_URL"):
        n_url += 1
    if n_url == 0:
        raise OAuthExtractionError("unexpected config key layout (no URL anchor)")

    # 3) Find the VALUE run via that anchor: n_url contiguous string-ids whose
    #    base64 decodes to https URLs. The value at the position right after the
    #    URL run is CLIENT_USERNAME's value; the next is CLIENT_PASSWORD's. The
    #    run starts at an arbitrary byte offset (serialised literals are not
    #    width-aligned), so scan byte-by-byte. Precompute the https-URL string
    #    ids once so the scan is a cheap set lookup. Try uint16 (ShortString)
    #    then uint32 (LongString) literal widths.
    https_ids = {
        i for i in range(b.string_count)
        if (t := _b64_decode(st.get(i))) and t.startswith("https://")
    }

    def bind_at(width: int) -> tuple[str, str] | None:
        fmt = "<H" if width == 2 else "<I"
        limit = len(valbuf) - width * (n_url + 2)
        for pos in range(limit + 1):
            ids = struct.unpack_from("<" + ("H" if width == 2 else "I") * n_url, valbuf, pos)
            if all(i in https_ids for i in ids):
                user_id = struct.unpack_from(fmt, valbuf, pos + n_url * width)[0]
                pass_id = struct.unpack_from(fmt, valbuf, pos + (n_url + 1) * width)[0]
                user = _b64_decode(st.get(user_id))
                pw = _b64_decode(st.get(pass_id))
                if user and pw and not user.startswith("https://"):
                    return user, pw
        return None

    bound = bind_at(2) or bind_at(4)
    if not bound:
        raise OAuthExtractionError(
            "could not bind CLIENT_USERNAME/PASSWORD to their values"
        )
    username, password = bound
    basic = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {
        "_comment": (
            "Locally generated OAuth client auth derived from the user's own "
            "HYMER app artefact. Sensitive local file. Do not share or publish."
        ),
        "authorization_header": f"Basic {basic}",
    }


def extract_oauth_client_from_path(apk_path: str) -> dict[str, str]:
    with open(apk_path, "rb") as fh:
        return extract_oauth_client(fh.read())


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: extract_oauth_from_apk.py <path-to.apk>", file=sys.stderr)
        raise SystemExit(2)
    payload = extract_oauth_client_from_path(sys.argv[1])
    # Print the header length only, never the secret, on the CLI.
    hdr = payload["authorization_header"]
    print(f"authorization_header: Basic <{len(hdr) - 6} base64 chars>")
