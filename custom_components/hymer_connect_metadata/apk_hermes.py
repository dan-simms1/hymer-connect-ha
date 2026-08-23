"""Reconstruct object/array literals from a HYMER Hermes APK, using only stdlib.

The HYMER Android app is a React-Native/Hermes build. Its runtime metadata
(sensor slots, scenarios, component and vehicle catalogs) lives in JS object
literals that Hermes compiles to bytecode. This module rebuilds those literals
WITHOUT decompiling the app: it parses the Hermes function table, walks each
function's bytecode straight-line, and tracks registers so that objects built
by ``New{Object,Array}WithBuffer`` + ``LoadConst*`` + ``Put*`` chains -- including
nested objects and arrays -- come back as plain Python dicts/lists.

Validated against Hermes bytecode version 96 (the version HYMER ships). No
``hermes-dec``, no decompilation, no third-party dependencies.
"""

from __future__ import annotations

import struct
import zipfile
from typing import Any

from .apk_oauth import (
    OAuthExtractionError,
    _read_bundle,
    _StringTable,
)

BUNDLE_ASSET = "assets/index.android.bundle"

# --- Hermes v96 opcode table (facebook/hermes main BytecodeList.def) ----------
# Only DEFINE_OPCODE_* lines get sequential opcode numbers; DEFINE_RET_TARGET and
# DEFINE_JUMP_LONG_VARIANT are annotations and are excluded.
_OPS_DEF = """
Unreachable
NewObjectWithBuffer Reg8 UInt16 UInt16 UInt16 UInt16
NewObjectWithBufferLong Reg8 UInt16 UInt16 UInt32 UInt32
NewObject Reg8
NewObjectWithParent Reg8 Reg8
NewArrayWithBuffer Reg8 UInt16 UInt16 UInt16
NewArrayWithBufferLong Reg8 UInt16 UInt16 UInt32
NewArray Reg8 UInt16
Mov Reg8 Reg8
MovLong Reg32 Reg32
Negate Reg8 Reg8
Not Reg8 Reg8
BitNot Reg8 Reg8
TypeOf Reg8 Reg8
Eq Reg8 Reg8 Reg8
StrictEq Reg8 Reg8 Reg8
Neq Reg8 Reg8 Reg8
StrictNeq Reg8 Reg8 Reg8
Less Reg8 Reg8 Reg8
LessEq Reg8 Reg8 Reg8
Greater Reg8 Reg8 Reg8
GreaterEq Reg8 Reg8 Reg8
Add Reg8 Reg8 Reg8
AddN Reg8 Reg8 Reg8
Mul Reg8 Reg8 Reg8
MulN Reg8 Reg8 Reg8
Div Reg8 Reg8 Reg8
DivN Reg8 Reg8 Reg8
Mod Reg8 Reg8 Reg8
Sub Reg8 Reg8 Reg8
SubN Reg8 Reg8 Reg8
LShift Reg8 Reg8 Reg8
RShift Reg8 Reg8 Reg8
URshift Reg8 Reg8 Reg8
BitAnd Reg8 Reg8 Reg8
BitXor Reg8 Reg8 Reg8
BitOr Reg8 Reg8 Reg8
Inc Reg8 Reg8
Dec Reg8 Reg8
InstanceOf Reg8 Reg8 Reg8
IsIn Reg8 Reg8 Reg8
GetEnvironment Reg8 UInt8
StoreToEnvironment Reg8 UInt8 Reg8
StoreToEnvironmentL Reg8 UInt16 Reg8
StoreNPToEnvironment Reg8 UInt8 Reg8
StoreNPToEnvironmentL Reg8 UInt16 Reg8
LoadFromEnvironment Reg8 Reg8 UInt8
LoadFromEnvironmentL Reg8 Reg8 UInt16
GetGlobalObject Reg8
GetNewTarget Reg8
CreateEnvironment Reg8
CreateInnerEnvironment Reg8 Reg8 UInt32
DeclareGlobalVar UInt32
ThrowIfHasRestrictedGlobalProperty UInt32
GetByIdShort Reg8 Reg8 UInt8 UInt8
GetById Reg8 Reg8 UInt8 UInt16
GetByIdLong Reg8 Reg8 UInt8 UInt32
TryGetById Reg8 Reg8 UInt8 UInt16
TryGetByIdLong Reg8 Reg8 UInt8 UInt32
PutById Reg8 Reg8 UInt8 UInt16
PutByIdLong Reg8 Reg8 UInt8 UInt32
TryPutById Reg8 Reg8 UInt8 UInt16
TryPutByIdLong Reg8 Reg8 UInt8 UInt32
PutNewOwnByIdShort Reg8 Reg8 UInt8
PutNewOwnById Reg8 Reg8 UInt16
PutNewOwnByIdLong Reg8 Reg8 UInt32
PutNewOwnNEById Reg8 Reg8 UInt16
PutNewOwnNEByIdLong Reg8 Reg8 UInt32
PutOwnByIndex Reg8 Reg8 UInt8
PutOwnByIndexL Reg8 Reg8 UInt32
PutOwnByVal Reg8 Reg8 Reg8 UInt8
DelById Reg8 Reg8 UInt16
DelByIdLong Reg8 Reg8 UInt32
GetByVal Reg8 Reg8 Reg8
PutByVal Reg8 Reg8 Reg8
DelByVal Reg8 Reg8 Reg8
PutOwnGetterSetterByVal Reg8 Reg8 Reg8 Reg8 UInt8
GetPNameList Reg8 Reg8 Reg8 Reg8
GetNextPName Reg8 Reg8 Reg8 Reg8 Reg8
Call Reg8 Reg8 UInt8
Construct Reg8 Reg8 UInt8
Call1 Reg8 Reg8 Reg8
CallDirect Reg8 UInt8 UInt16
Call2 Reg8 Reg8 Reg8 Reg8
Call3 Reg8 Reg8 Reg8 Reg8 Reg8
Call4 Reg8 Reg8 Reg8 Reg8 Reg8 Reg8
CallLong Reg8 Reg8 UInt32
ConstructLong Reg8 Reg8 UInt32
CallDirectLongIndex Reg8 UInt8 UInt32
CallBuiltin Reg8 UInt8 UInt8
CallBuiltinLong Reg8 UInt8 UInt32
GetBuiltinClosure Reg8 UInt8
Ret Reg8
Catch Reg8
DirectEval Reg8 Reg8 UInt8
Throw Reg8
ThrowIfEmpty Reg8 Reg8
Debugger
AsyncBreakCheck
ProfilePoint UInt16
CreateClosure Reg8 Reg8 UInt16
CreateClosureLongIndex Reg8 Reg8 UInt32
CreateGeneratorClosure Reg8 Reg8 UInt16
CreateGeneratorClosureLongIndex Reg8 Reg8 UInt32
CreateAsyncClosure Reg8 Reg8 UInt16
CreateAsyncClosureLongIndex Reg8 Reg8 UInt32
CreateThis Reg8 Reg8 Reg8
SelectObject Reg8 Reg8 Reg8
LoadParam Reg8 UInt8
LoadParamLong Reg8 UInt32
LoadConstUInt8 Reg8 UInt8
LoadConstInt Reg8 Imm32
LoadConstDouble Reg8 Double
LoadConstBigInt Reg8 UInt16
LoadConstBigIntLongIndex Reg8 UInt32
LoadConstString Reg8 UInt16
LoadConstStringLongIndex Reg8 UInt32
LoadConstEmpty Reg8
LoadConstUndefined Reg8
LoadConstNull Reg8
LoadConstTrue Reg8
LoadConstFalse Reg8
LoadConstZero Reg8
CoerceThisNS Reg8 Reg8
LoadThisNS Reg8
ToNumber Reg8 Reg8
ToNumeric Reg8 Reg8
ToInt32 Reg8 Reg8
AddEmptyString Reg8 Reg8
GetArgumentsPropByVal Reg8 Reg8 Reg8
GetArgumentsLength Reg8 Reg8
ReifyArguments Reg8
CreateRegExp Reg8 UInt32 UInt32 UInt32
SwitchImm Reg8 UInt32 Addr32 UInt32 UInt32
StartGenerator
ResumeGenerator Reg8 Reg8
CompleteGenerator
CreateGenerator Reg8 Reg8 UInt16
CreateGeneratorLongIndex Reg8 Reg8 UInt32
IteratorBegin Reg8 Reg8
IteratorNext Reg8 Reg8 Reg8
IteratorClose Reg8 UInt8
Jmp Addr8
JmpLong Addr32
JmpTrue Addr8 Reg8
JmpTrueLong Addr32 Reg8
JmpFalse Addr8 Reg8
JmpFalseLong Addr32 Reg8
JmpUndefined Addr8 Reg8
JmpUndefinedLong Addr32 Reg8
SaveGenerator Addr8
SaveGeneratorLong Addr32
JLess Addr8 Reg8 Reg8
JLessLong Addr32 Reg8 Reg8
JNotLess Addr8 Reg8 Reg8
JNotLessLong Addr32 Reg8 Reg8
JLessN Addr8 Reg8 Reg8
JLessNLong Addr32 Reg8 Reg8
JNotLessN Addr8 Reg8 Reg8
JNotLessNLong Addr32 Reg8 Reg8
JLessEqual Addr8 Reg8 Reg8
JLessEqualLong Addr32 Reg8 Reg8
JNotLessEqual Addr8 Reg8 Reg8
JNotLessEqualLong Addr32 Reg8 Reg8
JLessEqualN Addr8 Reg8 Reg8
JLessEqualNLong Addr32 Reg8 Reg8
JNotLessEqualN Addr8 Reg8 Reg8
JNotLessEqualNLong Addr32 Reg8 Reg8
JGreater Addr8 Reg8 Reg8
JGreaterLong Addr32 Reg8 Reg8
JNotGreater Addr8 Reg8 Reg8
JNotGreaterLong Addr32 Reg8 Reg8
JGreaterN Addr8 Reg8 Reg8
JGreaterNLong Addr32 Reg8 Reg8
JNotGreaterN Addr8 Reg8 Reg8
JNotGreaterNLong Addr32 Reg8 Reg8
JGreaterEqual Addr8 Reg8 Reg8
JGreaterEqualLong Addr32 Reg8 Reg8
JNotGreaterEqual Addr8 Reg8 Reg8
JNotGreaterEqualLong Addr32 Reg8 Reg8
JGreaterEqualN Addr8 Reg8 Reg8
JGreaterEqualNLong Addr32 Reg8 Reg8
JNotGreaterEqualN Addr8 Reg8 Reg8
JNotGreaterEqualNLong Addr32 Reg8 Reg8
JEqual Addr8 Reg8 Reg8
JEqualLong Addr32 Reg8 Reg8
JNotEqual Addr8 Reg8 Reg8
JNotEqualLong Addr32 Reg8 Reg8
JStrictEqual Addr8 Reg8 Reg8
JStrictEqualLong Addr32 Reg8 Reg8
JStrictNotEqual Addr8 Reg8 Reg8
JStrictNotEqualLong Addr32 Reg8 Reg8
Add32 Reg8 Reg8 Reg8
Sub32 Reg8 Reg8 Reg8
Mul32 Reg8 Reg8 Reg8
Divi32 Reg8 Reg8 Reg8
Divu32 Reg8 Reg8 Reg8
Loadi8 Reg8 Reg8 Reg8
Loadu8 Reg8 Reg8 Reg8
Loadi16 Reg8 Reg8 Reg8
Loadu16 Reg8 Reg8 Reg8
Loadi32 Reg8 Reg8 Reg8
Loadu32 Reg8 Reg8 Reg8
Store8 Reg8 Reg8 Reg8
Store16 Reg8 Reg8 Reg8
Store32 Reg8 Reg8 Reg8
"""
_OPSZ = {"Reg8": 1, "Reg32": 4, "UInt8": 1, "UInt16": 2, "UInt32": 4,
         "Addr8": 1, "Addr32": 4, "Imm32": 4, "Double": 8}
_OPCODES: list[tuple[str, list[str], int]] = []  # (name, operands, total_size)
for _line in _OPS_DEF.strip().splitlines():
    _parts = _line.split()
    _ops = _parts[1:]
    _OPCODES.append((_parts[0], _ops, 1 + sum(_OPSZ[t] for t in _ops)))
_NAME2OP = {n: i for i, (n, _o, _s) in enumerate(_OPCODES)}

# SerializedLiteralParser tags (bits 6-4): 0 Null 1 True 2 False 3 Number(8)
# 4 LongString(4) 5 ShortString(2) 6 ByteString(1) 7 Integer(4).
_STR_W = {4: 4, 5: 2, 6: 1}
_UNKNOWN = object()

MIN_VERSION, MAX_VERSION = 90, 96


def _align4(x: int) -> int:
    return (x + 3) & ~3


def _parse_literals(buf: bytes, offset: int, count: int, st: _StringTable) -> list[Any]:
    out: list[Any] = []
    i, n = offset, len(buf)
    while len(out) < count and i < n:
        tb = buf[i]; i += 1
        tag = (tb >> 4) & 0x07
        if tb & 0x80:
            if i >= n:
                break
            length = ((tb & 0x0F) << 8) | buf[i]; i += 1
        else:
            length = tb & 0x0F
        for _ in range(length):
            if len(out) >= count:
                break
            if tag in _STR_W:
                w = _STR_W[tag]
                sid = struct.unpack_from({1: "<B", 2: "<H", 4: "<I"}[w], buf, i)[0]
                i += w
                out.append(st.get(sid))
            elif tag == 3:
                out.append(struct.unpack_from("<d", buf, i)[0]); i += 8
            elif tag == 7:
                out.append(struct.unpack_from("<i", buf, i)[0]); i += 4
            elif tag == 0:
                out.append(None)
            elif tag == 1:
                out.append(True)
            elif tag == 2:
                out.append(False)
    return out


class _Reader:
    """Parses one Hermes bundle and reconstructs its object/array literals."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.b = _read_bundle(raw)
        version = struct.unpack_from("<I", raw, 8)[0]
        if not MIN_VERSION <= version <= MAX_VERSION:
            raise OAuthExtractionError(f"unsupported Hermes bytecode version {version}")
        # A lightly-cached string table (ids are resolved repeatedly).
        base_st = _StringTable(self.b)
        cache: dict[int, str] = {}

        class _CachedST:
            def get(self, idx: int) -> str:
                v = cache.get(idx, _UNKNOWN)
                if v is _UNKNOWN:
                    v = base_st.get(idx); cache[idx] = v
                return v

        self.st = _CachedST()
        self.keybuf = raw[self.b.objkey_off: self.b.objkey_off + self.b.objkey_size]
        self.valbuf = raw[self.b.objval_off: self.b.objval_off + self.b.objval_size]
        (_fl, _g, self.func_count, skc, idc, sc, osc, sss, _bic, _bis,
         _rc, _rs, array_buffer_size, _oks, _ovs, *_r) = struct.unpack_from("<19I", raw, 32)
        off = _align4(128)
        off = _align4(off) + self.func_count * 16
        off = _align4(off) + skc * 4
        off = _align4(off) + idc * 4
        off = _align4(off) + sc * 4
        off = _align4(off) + osc * 8
        off = _align4(off) + sss
        off = _align4(off)
        self.arraybuf = raw[off: off + array_buffer_size]

    def _functions(self):
        raw = self.raw
        for i in range(self.func_count):
            o = 128 + i * 16
            w0, w1, w2 = struct.unpack_from("<III", raw, o)
            flags = raw[o + 15]
            offset = w0 & 0x1FFFFFF
            size = w1 & 0x7FFF
            if (flags >> 5) & 1:  # overflowed -> read the large header
                large = ((w2 & 0x1FFFFFF) << 16) | offset
                offset, _pc, size = struct.unpack_from("<III", raw, large)[:3]
            yield offset, size

    def reconstruct(self) -> list[dict]:
        objects: list[dict] = []
        raw = self.raw
        st = self.st
        OP = _NAME2OP
        op_nowb = OP["NewObjectWithBuffer"]
        op_nowbl = OP["NewObjectWithBufferLong"]
        op_nawb = OP["NewArrayWithBuffer"]
        op_nawbl = OP["NewArrayWithBufferLong"]
        put_id = {OP["PutById"], OP["PutByIdLong"]}
        put_own = {OP["PutNewOwnByIdShort"], OP["PutNewOwnById"], OP["PutNewOwnByIdLong"],
                   OP["PutNewOwnNEById"], OP["PutNewOwnNEByIdLong"]}
        put_idx = {OP["PutOwnByIndex"], OP["PutOwnByIndexL"]}
        op_newobj, op_newarr = OP["NewObject"], OP["NewArray"]
        op_mov, op_movl = OP["Mov"], OP["MovLong"]
        lc_str = {OP["LoadConstString"], OP["LoadConstStringLongIndex"]}
        lc_int = {OP["LoadConstUInt8"], OP["LoadConstInt"], OP["LoadConstDouble"]}
        lc_true, lc_false, lc_zero = OP["LoadConstTrue"], OP["LoadConstFalse"], OP["LoadConstZero"]
        lc_null = {OP["LoadConstNull"], OP["LoadConstUndefined"], OP["LoadConstEmpty"]}

        for foff, fsize in self._functions():
            regs: dict[int, Any] = {}
            p, end = foff, foff + fsize
            while p < end:
                op = raw[p]
                if op >= len(_OPCODES):
                    break
                _name, operands, isize = _OPCODES[op]
                a = p + 1
                if op == op_nowb or op == op_nowbl:
                    dst = raw[a]
                    if op == op_nowb:
                        _sh, numLit, keyIdx, valIdx = struct.unpack_from("<HHHH", raw, a + 1)
                    else:
                        _sh, numLit = struct.unpack_from("<HH", raw, a + 1)
                        keyIdx, valIdx = struct.unpack_from("<II", raw, a + 5)
                    d = dict(zip(_parse_literals(self.keybuf, keyIdx, numLit, st),
                                 _parse_literals(self.valbuf, valIdx, numLit, st)))
                    regs[dst] = d
                    objects.append(d)
                elif op == op_nawb or op == op_nawbl:
                    dst = raw[a]
                    if op == op_nawb:
                        _sh, numElems, bufIdx = struct.unpack_from("<HHH", raw, a + 1)
                    else:
                        _sh, numElems = struct.unpack_from("<HH", raw, a + 1)
                        bufIdx = struct.unpack_from("<I", raw, a + 5)[0]
                    regs[dst] = _parse_literals(self.arraybuf, bufIdx, numElems, st)
                elif op == op_newobj:
                    d = {}; regs[raw[a]] = d; objects.append(d)
                elif op == op_newarr:
                    regs[raw[a]] = []
                elif op == op_mov:
                    regs[raw[a]] = regs.get(raw[a + 1], _UNKNOWN)
                elif op == op_movl:
                    s, dsrc = struct.unpack_from("<II", raw, a)
                    regs[s] = regs.get(dsrc, _UNKNOWN)
                elif op in lc_str:
                    sid = (struct.unpack_from("<H", raw, a + 1)[0] if operands[1] == "UInt16"
                           else struct.unpack_from("<I", raw, a + 1)[0])
                    regs[raw[a]] = st.get(sid)
                elif op in lc_int:
                    if operands[1] == "UInt8":
                        regs[raw[a]] = raw[a + 1]
                    elif operands[1] == "Imm32":
                        regs[raw[a]] = struct.unpack_from("<i", raw, a + 1)[0]
                    else:  # Double
                        regs[raw[a]] = struct.unpack_from("<d", raw, a + 1)[0]
                elif op == lc_true:
                    regs[raw[a]] = True
                elif op == lc_false:
                    regs[raw[a]] = False
                elif op == lc_zero:
                    regs[raw[a]] = 0
                elif op in lc_null:
                    regs[raw[a]] = None
                elif op in put_id:
                    obj, val, _cache = raw[a], raw[a + 1], raw[a + 2]
                    sid = (struct.unpack_from("<H", raw, a + 3)[0] if operands[3] == "UInt16"
                           else struct.unpack_from("<I", raw, a + 3)[0])
                    self._put(regs, obj, st.get(sid), val)
                elif op in put_own:
                    obj, val = raw[a], raw[a + 1]
                    sid = (raw[a + 2] if operands[2] == "UInt8"
                           else struct.unpack_from("<H" if operands[2] == "UInt16" else "<I",
                                                    raw, a + 2)[0])
                    self._put(regs, obj, st.get(sid), val)
                elif op in put_idx:
                    obj, val = raw[a], raw[a + 1]
                    idx = (raw[a + 2] if operands[2] == "UInt8"
                           else struct.unpack_from("<I", raw, a + 2)[0])
                    tgt = regs.get(obj)
                    if isinstance(tgt, list):
                        while len(tgt) <= idx:
                            tgt.append(None)
                        tgt[idx] = regs.get(val, _UNKNOWN)
                p += isize
        return objects

    def _put(self, regs, obj_reg, key, val_reg) -> None:
        tgt = regs.get(obj_reg)
        if isinstance(tgt, dict) and isinstance(key, str):
            val = regs.get(val_reg, _UNKNOWN)
            if val is not _UNKNOWN:
                tgt[key] = val


def _clean(obj, _path=frozenset()):
    """Strip untracked markers and break register-reuse cycles."""
    if isinstance(obj, (dict, list)):
        if id(obj) in _path:
            return None
        _path = _path | {id(obj)}
    if isinstance(obj, dict):
        return {k: _clean(v, _path) for k, v in obj.items() if v is not _UNKNOWN}
    if isinstance(obj, list):
        return [_clean(v, _path) for v in obj]
    return obj


def reconstruct_object_literals(apk_bytes: bytes) -> list[dict]:
    """Return every object literal reconstructed from the APK's Hermes bundle."""
    import io
    import sys

    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as z:
        try:
            raw = z.read(BUNDLE_ASSET)
        except KeyError as err:
            raise OAuthExtractionError(f"{BUNDLE_ASSET} not found in APK") from err
    reader = _Reader(raw)
    # Reconstructed literals can nest deeply; raise the limit for _clean's walk.
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 50000))
    try:
        return [_clean(o) for o in reader.reconstruct()]
    finally:
        sys.setrecursionlimit(old_limit)


def reconstruct_object_literals_from_path(apk_path: str) -> list[dict]:
    with open(apk_path, "rb") as fh:
        return reconstruct_object_literals(fh.read())
