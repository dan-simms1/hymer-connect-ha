"""Hardening tests for parsing untrusted APK/Hermes bytes.

Unlike ``test_hermes_apk_extract`` these use tiny *synthetic* fixtures, so they
run in CI without the gitignored ``reference/base.apk``. They cover the security
surface Codex flagged: zip-bomb / size caps, header-count validation, the v96
reconstruction gate, structural OAuth binding, and the bounded, non-recursive
``_clean`` walk.
"""

from __future__ import annotations

import importlib
import io
import struct
import unittest
import zipfile

from tests.hymer_test_support import ensure_package_paths

ensure_package_paths()

apk_oauth = importlib.import_module("custom_components.hymer_connect_metadata.apk_oauth")
apk_hermes = importlib.import_module("custom_components.hymer_connect_metadata.apk_hermes")

BUNDLE_ASSET = apk_oauth.BUNDLE_ASSET
OAuthExtractionError = apk_oauth.OAuthExtractionError


def _hermes_header(version: int = 96, file_length: int = 128, **counts: int) -> bytes:
    """A minimal but structurally valid 128-byte Hermes header (empty sections)."""
    raw = bytearray(128)
    raw[0:4] = apk_oauth.HERMES_MAGIC
    struct.pack_into("<I", raw, 8, version)
    fields = [
        file_length,
        0,                                   # global function index
        counts.get("function_count", 0),
        counts.get("string_kind_count", 0),
        counts.get("identifier_count", 0),
        counts.get("string_count", 0),
        counts.get("overflow_string_count", 0),
        counts.get("string_storage_size", 0),
        0, 0, 0, 0,                          # bigint / regexp counts + sizes
        counts.get("array_buffer_size", 0),
        counts.get("obj_key_size", 0),
        counts.get("obj_value_size", 0),
        0, 0, 0, 0,                          # trailing header words (pad to 19)
    ]
    struct.pack_into("<19I", raw, 32, *fields)
    return bytes(raw)


def _apk_with_bundle(bundle: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BUNDLE_ASSET, bundle)
    return buf.getvalue()


class ReadBundleAssetTests(unittest.TestCase):
    def test_missing_bundle_member_is_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("assets/other.txt", b"nope")
        with self.assertRaises(OAuthExtractionError):
            apk_oauth.read_bundle_asset(buf.getvalue())

    def test_non_zip_input_is_rejected(self) -> None:
        with self.assertRaises(OAuthExtractionError):
            apk_oauth.read_bundle_asset(b"not a zip at all")

    def test_oversized_member_is_capped(self) -> None:
        # A highly compressible member that inflates past a lowered cap must be
        # refused rather than read into memory (zip-bomb defence).
        original = apk_oauth._MAX_BUNDLE_BYTES
        apk_oauth._MAX_BUNDLE_BYTES = 1024
        try:
            apk = _apk_with_bundle(b"\x00" * 200_000)
            with self.assertRaises(OAuthExtractionError):
                apk_oauth.read_bundle_asset(apk)
        finally:
            apk_oauth._MAX_BUNDLE_BYTES = original


class ReadBundleHeaderTests(unittest.TestCase):
    def test_bad_magic_is_rejected(self) -> None:
        with self.assertRaises(OAuthExtractionError):
            apk_oauth._read_bundle(b"\x00" * 200)

    def test_file_length_mismatch_is_rejected(self) -> None:
        raw = _hermes_header(file_length=999)
        with self.assertRaises(OAuthExtractionError):
            apk_oauth._read_bundle(raw)

    def test_unsupported_version_is_rejected(self) -> None:
        raw = _hermes_header(version=42)
        with self.assertRaises(OAuthExtractionError):
            apk_oauth._read_bundle(raw)

    def test_section_count_past_file_length_is_rejected(self) -> None:
        # A hostile function_count that would run the section table past EOF.
        raw = _hermes_header(function_count=1_000_000)
        with self.assertRaises(OAuthExtractionError):
            apk_oauth._read_bundle(raw)

    def test_empty_v96_header_parses(self) -> None:
        bundle = apk_oauth._read_bundle(_hermes_header(version=96))
        self.assertEqual(bundle.version, 96)
        self.assertEqual(bundle.function_count, 0)


class ReconstructionGateTests(unittest.TestCase):
    def test_reconstruction_requires_v96(self) -> None:
        # v95 is accepted by the header reader but not by the interpreter.
        with self.assertRaises(OAuthExtractionError):
            apk_hermes.reconstruct_object_literals_from_bundle(_hermes_header(version=95))

    def test_empty_v96_bundle_reconstructs_to_no_objects(self) -> None:
        self.assertEqual(
            apk_hermes.reconstruct_object_literals(_apk_with_bundle(_hermes_header(96))),
            [],
        )


class OAuthBindingTests(unittest.TestCase):
    def test_single_config_object_binds(self) -> None:
        import base64

        user = base64.b64encode(b"client-id").decode()
        pw = base64.b64encode(b"secret").decode()
        objs = [
            {"BRAND": "hymer"},
            {"CLIENT_USERNAME": user, "CLIENT_PASSWORD": pw, "CLOUD_API_BASE_URL": "x"},
        ]
        self.assertEqual(apk_oauth._extract_from_config_object(objs), ("client-id", "secret"))

    def test_no_config_object_returns_none(self) -> None:
        self.assertIsNone(apk_oauth._extract_from_config_object([{"BRAND": "hymer"}]))

    def test_multiple_config_objects_are_rejected(self) -> None:
        import base64

        a = {"CLIENT_USERNAME": base64.b64encode(b"a").decode(),
             "CLIENT_PASSWORD": base64.b64encode(b"b").decode()}
        b = {"CLIENT_USERNAME": base64.b64encode(b"c").decode(),
             "CLIENT_PASSWORD": base64.b64encode(b"d").decode()}
        with self.assertRaises(OAuthExtractionError):
            apk_oauth._extract_from_config_object([a, b])


class CleanWalkTests(unittest.TestCase):
    def test_cycles_are_broken(self) -> None:
        node: dict = {"name": "loop"}
        node["self"] = node
        cleaned = apk_hermes._clean(node)
        self.assertEqual(cleaned["name"], "loop")
        self.assertIsNone(cleaned["self"])  # cycle cut, not infinite recursion

    def test_unknown_markers_are_stripped(self) -> None:
        obj = {"keep": 1, "drop": apk_hermes._UNKNOWN}
        self.assertEqual(apk_hermes._clean(obj), {"keep": 1})

    def test_depth_budget_truncates_instead_of_crashing(self) -> None:
        original = apk_hermes._MAX_CLEAN_DEPTH
        apk_hermes._MAX_CLEAN_DEPTH = 5
        try:
            deep: dict = {}
            cur = deep
            for _ in range(50):
                nxt: dict = {}
                cur["n"] = nxt
                cur = nxt
            cleaned = apk_hermes._clean(deep)  # must not raise RecursionError
        finally:
            apk_hermes._MAX_CLEAN_DEPTH = original
        # Walk down: it bottoms out in a None at the depth cap.
        depth = 0
        cur = cleaned
        while isinstance(cur, dict) and "n" in cur:
            cur = cur["n"]
            depth += 1
        self.assertLessEqual(depth, 6)

    def test_node_budget_is_enforced(self) -> None:
        original = apk_hermes._MAX_CLEAN_NODES
        apk_hermes._MAX_CLEAN_NODES = 3
        try:
            with self.assertRaises(apk_hermes.HermesLimitError):
                apk_hermes._clean([{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}])
        finally:
            apk_hermes._MAX_CLEAN_NODES = original


class ZipEntryCountTests(unittest.TestCase):
    def test_entry_count_matches_a_normal_archive(self) -> None:
        apk = _apk_with_bundle(_hermes_header(96))
        self.assertEqual(apk_oauth._zip_entry_count(io.BytesIO(apk)), 1)

    def test_too_many_entries_is_rejected(self) -> None:
        apk = _apk_with_bundle(_hermes_header(96))
        original = apk_oauth._MAX_ZIP_ENTRIES
        apk_oauth._MAX_ZIP_ENTRIES = 0  # any archive now exceeds the cap
        try:
            with self.assertRaises(OAuthExtractionError):
                apk_oauth.read_bundle_asset(apk)
        finally:
            apk_oauth._MAX_ZIP_ENTRIES = original


class StringRangeTests(unittest.TestCase):
    def test_out_of_range_string_offset_returns_empty(self) -> None:
        raw = bytearray(200)
        # One small-string entry whose (offset,length) runs past the storage
        # section: offset 100000, length 5, utf-8.
        ent = ((100000 & 0x7FFFFF) << 1) | (5 << 24)
        struct.pack_into("<I", raw, 0, ent)
        bundle = apk_oauth._Bundle(
            raw=bytes(raw), version=96, function_count=0, string_count=1,
            overflow_string_count=0, smallstr_off=0, overflow_off=50,
            storage_off=60, storage_size=10, arraybuf_off=0, arraybuf_size=0,
            objkey_off=0, objkey_size=0, objval_off=0, objval_size=0,
        )
        self.assertEqual(apk_oauth._StringTable(bundle).get(0), "")


class CleanAmplificationTests(unittest.TestCase):
    def test_shared_subgraph_is_cleaned_once_and_reused(self) -> None:
        shared = {"x": 1}
        out = apk_hermes._clean_all([{"a": shared}, {"b": shared}])
        # Memoized: both parents point at the SAME cleaned child, so a shared
        # subgraph cannot amplify the output.
        self.assertIs(out[0]["a"], out[1]["b"])

    def test_node_budget_spans_all_roots(self) -> None:
        original = apk_hermes._MAX_CLEAN_NODES
        apk_hermes._MAX_CLEAN_NODES = 3
        try:
            with self.assertRaises(apk_hermes.HermesLimitError):
                apk_hermes._clean_all([{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}])
        finally:
            apk_hermes._MAX_CLEAN_NODES = original


class OpcodeClassificationTests(unittest.TestCase):
    def test_producers_are_destinations_and_puts_are_not(self) -> None:
        name2op = apk_hermes._NAME2OP
        dest = apk_hermes._OP0_DEST
        # Value producers: operand 0 is a written destination -> invalidated.
        for producer in ("GetById", "GetByIdShort", "Call", "Add"):
            self.assertTrue(dest[name2op[producer]], producer)
        # Object mutators / terminators: operand 0 is a source -> preserved.
        for source in ("PutById", "PutNewOwnById", "PutOwnByIndexL", "Ret", "Throw"):
            self.assertFalse(dest[name2op[source]], source)


if __name__ == "__main__":
    unittest.main()
