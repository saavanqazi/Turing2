# ruff: noqa
# Vendored verbatim -- see the module docstring. Lint and autofix are off for
# this file on purpose: `ruff --fix` would rewrite `Dict[str, Any]` to `dict`,
# collapse the codec's explicit branches and reflow the tables, which is exactly
# the in-place "improvement" that would make re-vendoring a manual merge and
# put byte-identical parse output at risk. Re-vendor, do not edit.
"""Vendored ``.fig`` decode core (Kiwi codec + internal -> REST mapper).

Second-generation vendored copy: taken verbatim from
``zeta3-data-browser/verifier_generation/fig_decode.py``, which is itself the
verbatim decode core of ``connectors/figma/scripts/build_figma_seed.py``
(obi-rl-gym commit ``65b60bb345``). It lives here so a ``figma.*`` verifier
source can open the ``.fig`` design document the task names, in-process, with
no gym connector tree, gsutil, PIL or CLI.

Byte-identical parse output is the contract in both directions -- a design the
seed builder turned into a gym state and the same design read here must grade
the same -- so nothing in the parsing logic may be "improved" in place. Only
one thing differs from the zeta3 copy: ``_decompress_block``'s ImportError text
names this project's requirements file instead of that app's. The excisions are
inherited unchanged (no ``gsutil_pull``, no ``validate_with_figma_db``, no
disk-writing tail, no PIL sidecar, no self-test, no CLI), and
:func:`canvas_bytes_from_fig` / :func:`parse_fig_bytes` are the same two public
entry points added by that vendoring.

``zstandard`` is imported lazily inside :func:`_decompress_block` and nowhere
else, so this module imports cleanly wherever the dependency is absent; only a
zstd-compressed payload needs it. :mod:`rl_world_verifiers.sources.figma` turns
the resulting ``RuntimeError`` into a ``SourceAuthoringError``.

Keep this file in sync with the source; re-vendor rather than edit.
"""

from __future__ import annotations

import io
import math
import re
import struct
import zipfile
import zlib
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Kiwi binary codec (port of evanw/kiwi). Generic + schema-driven.
# --------------------------------------------------------------------------- #

# Built-in field types (negative ints in the compiled schema).
TYPE_BOOL = -1
TYPE_BYTE = -2
TYPE_INT = -3
TYPE_UINT = -4
TYPE_FLOAT = -5
TYPE_STRING = -6
TYPE_INT64 = -7
TYPE_UINT64 = -8

KIND_ENUM = 0
KIND_STRUCT = 1
KIND_MESSAGE = 2
_KIND_NAMES = {KIND_ENUM: "ENUM", KIND_STRUCT: "STRUCT", KIND_MESSAGE: "MESSAGE"}


class ByteBuffer:
    """Little reader/writer matching evanw/kiwi's wire encoding."""

    def __init__(self, data: bytes = b""):
        self._data = bytearray(data)
        self._index = 0

    # -- reading ----------------------------------------------------------- #
    def read_byte(self) -> int:
        if self._index >= len(self._data):
            raise EOFError("kiwi: read past end of buffer")
        b = self._data[self._index]
        self._index += 1
        return b

    def read_bytes(self, count: int) -> bytes:
        if self._index + count > len(self._data):
            raise EOFError("kiwi: read past end of buffer")
        out = bytes(self._data[self._index : self._index + count])
        self._index += count
        return out

    def read_var_uint(self) -> int:
        shift = 0
        result = 0
        while True:
            byte = self.read_byte()
            result |= (byte & 0x7F) << shift
            shift += 7
            if not (byte & 0x80):
                break
        return result & 0xFFFFFFFF

    def read_var_int(self) -> int:
        value = self.read_var_uint()
        # zigzag decode (32-bit)
        return (value >> 1) ^ -(value & 1)

    def read_var_uint64(self) -> int:
        shift = 0
        result = 0
        while True:
            byte = self.read_byte()
            result |= (byte & 0x7F) << shift
            shift += 7
            if not (byte & 0x80):
                break
        return result & 0xFFFFFFFFFFFFFFFF

    def read_var_int64(self) -> int:
        value = self.read_var_uint64()
        return (value >> 1) ^ -(value & 1)

    def read_var_float(self) -> float:
        first = self.read_byte()
        if first == 0:
            return 0.0
        bits = (
            first
            | (self.read_byte() << 8)
            | (self.read_byte() << 16)
            | (self.read_byte() << 24)
        )
        # un-rotate: exponent was moved to the low bits on write
        bits = ((bits << 23) | (bits >> 9)) & 0xFFFFFFFF
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    def read_string(self) -> str:
        # null-terminated UTF-8
        start = self._index
        data = self._data
        while True:
            if self._index >= len(data):
                raise EOFError("kiwi: unterminated string")
            if data[self._index] == 0:
                break
            self._index += 1
        out = bytes(data[start : self._index]).decode("utf-8", errors="replace")
        self._index += 1  # consume the null
        return out

    # -- writing ----------------------------------------------------------- #
    def write_byte(self, value: int) -> None:
        self._data.append(value & 0xFF)

    def write_var_uint(self, value: int) -> None:
        value &= 0xFFFFFFFF
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                self.write_byte(byte | 0x80)
            else:
                self.write_byte(byte)
                break

    def write_var_int(self, value: int) -> None:
        self.write_var_uint(((value << 1) ^ (value >> 31)) & 0xFFFFFFFF)

    def write_var_uint64(self, value: int) -> None:
        value &= 0xFFFFFFFFFFFFFFFF
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                self.write_byte(byte | 0x80)
            else:
                self.write_byte(byte)
                break

    def write_var_int64(self, value: int) -> None:
        self.write_var_uint64(((value << 1) ^ (value >> 63)) & 0xFFFFFFFFFFFFFFFF)

    def write_var_float(self, value: float) -> None:
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        # rotate the exponent to the low byte
        bits = ((bits >> 23) | (bits << 9)) & 0xFFFFFFFF
        if (bits & 0xFF) == 0:  # zero / denormal optimization
            self.write_byte(0)
            return
        self.write_byte(bits & 0xFF)
        self.write_byte((bits >> 8) & 0xFF)
        self.write_byte((bits >> 16) & 0xFF)
        self.write_byte((bits >> 24) & 0xFF)

    def write_string(self, value: str) -> None:
        self._data.extend(value.encode("utf-8"))
        self.write_byte(0)

    def to_bytes(self) -> bytes:
        return bytes(self._data)


def decode_binary_schema(data: bytes) -> List[Dict[str, Any]]:
    """Decode a compiled Kiwi schema into a list of definition dicts."""
    bb = ByteBuffer(data)
    definition_count = bb.read_var_uint()
    definitions: List[Dict[str, Any]] = []
    for _ in range(definition_count):
        name = bb.read_string()
        kind = bb.read_byte()
        field_count = bb.read_var_uint()
        fields = []
        for _ in range(field_count):
            field_name = bb.read_string()
            field_type = bb.read_var_int()
            is_array = bb.read_byte() != 0
            value = bb.read_var_uint()
            fields.append(
                {"name": field_name, "type": field_type, "is_array": is_array, "value": value}
            )
        definitions.append({"name": name, "kind": kind, "fields": fields})
    return definitions


def encode_binary_schema(definitions: List[Dict[str, Any]]) -> bytes:
    """Encode definition dicts back into a compiled Kiwi schema (for self-test)."""
    bb = ByteBuffer()
    bb.write_var_uint(len(definitions))
    for d in definitions:
        bb.write_string(d["name"])
        bb.write_byte(d["kind"])
        bb.write_var_uint(len(d["fields"]))
        for f in d["fields"]:
            bb.write_string(f["name"])
            bb.write_var_int(f["type"])
            bb.write_byte(1 if f["is_array"] else 0)
            bb.write_var_uint(f["value"])
    return bb.to_bytes()


class KiwiSchema:
    """Schema-driven Kiwi message decoder/encoder."""

    def __init__(self, definitions: List[Dict[str, Any]]):
        self.definitions = definitions
        self.by_name = {d["name"]: i for i, d in enumerate(definitions)}

    # -- decode ------------------------------------------------------------ #
    def decode_value(self, bb: ByteBuffer, ftype: int, is_array: bool):
        if is_array:
            count = bb.read_var_uint()
            return [self._decode_single(bb, ftype) for _ in range(count)]
        return self._decode_single(bb, ftype)

    def _decode_single(self, bb: ByteBuffer, ftype: int):
        if ftype == TYPE_BOOL:
            return bb.read_byte() != 0
        if ftype == TYPE_BYTE:
            return bb.read_byte()
        if ftype == TYPE_INT:
            return bb.read_var_int()
        if ftype == TYPE_UINT:
            return bb.read_var_uint()
        if ftype == TYPE_FLOAT:
            return bb.read_var_float()
        if ftype == TYPE_STRING:
            return bb.read_string()
        if ftype == TYPE_INT64:
            return bb.read_var_int64()
        if ftype == TYPE_UINT64:
            return bb.read_var_uint64()
        if ftype < 0:
            # Unknown built-in: best-effort varint so decoding can continue.
            return bb.read_var_uint()
        return self._decode_definition(bb, ftype)

    def _decode_definition(self, bb: ByteBuffer, index: int):
        d = self.definitions[index]
        kind = d["kind"]
        if kind == KIND_ENUM:
            value = bb.read_var_uint()
            for f in d["fields"]:
                if f["value"] == value:
                    return f["name"]
            return value  # unknown enum member -> raw value
        if kind == KIND_STRUCT:
            obj = {}
            for f in d["fields"]:
                obj[f["name"]] = self.decode_value(bb, f["type"], f["is_array"])
            return obj
        # MESSAGE
        obj = {}
        field_by_value = {f["value"]: f for f in d["fields"]}
        while True:
            fid = bb.read_var_uint()
            if fid == 0:
                break
            f = field_by_value.get(fid)
            if f is None:
                raise ValueError(f"kiwi: unknown field id {fid} in message '{d['name']}'")
            obj[f["name"]] = self.decode_value(bb, f["type"], f["is_array"])
        return obj

    def decode_message(self, data: bytes, root: str) -> Dict[str, Any]:
        bb = ByteBuffer(data)
        return self._decode_definition(bb, self.by_name[root])

    # -- encode (used by --self-test) -------------------------------------- #
    def encode_value(self, bb: ByteBuffer, ftype: int, is_array: bool, value) -> None:
        if is_array:
            bb.write_var_uint(len(value))
            for item in value:
                self._encode_single(bb, ftype, item)
            return
        self._encode_single(bb, ftype, value)

    def _encode_single(self, bb: ByteBuffer, ftype: int, value) -> None:
        if ftype == TYPE_BOOL:
            bb.write_byte(1 if value else 0)
        elif ftype == TYPE_BYTE:
            bb.write_byte(int(value))
        elif ftype == TYPE_INT:
            bb.write_var_int(int(value))
        elif ftype == TYPE_UINT:
            bb.write_var_uint(int(value))
        elif ftype == TYPE_FLOAT:
            bb.write_var_float(float(value))
        elif ftype == TYPE_STRING:
            bb.write_string(str(value))
        elif ftype == TYPE_INT64:
            bb.write_var_int64(int(value))
        elif ftype == TYPE_UINT64:
            bb.write_var_uint64(int(value))
        else:
            self._encode_definition(bb, ftype, value)

    def _encode_definition(self, bb: ByteBuffer, index: int, value) -> None:
        d = self.definitions[index]
        kind = d["kind"]
        if kind == KIND_ENUM:
            for f in d["fields"]:
                if f["name"] == value:
                    bb.write_var_uint(f["value"])
                    return
            bb.write_var_uint(int(value))
            return
        if kind == KIND_STRUCT:
            for f in d["fields"]:
                self.encode_value(bb, f["type"], f["is_array"], value[f["name"]])
            return
        # MESSAGE
        for f in d["fields"]:
            if f["name"] in value and value[f["name"]] is not None:
                bb.write_var_uint(f["value"])
                self.encode_value(bb, f["type"], f["is_array"], value[f["name"]])
        bb.write_byte(0)

    def encode_message(self, message: Dict[str, Any], root: str) -> bytes:
        bb = ByteBuffer()
        self._encode_definition(bb, self.by_name[root], message)
        return bb.to_bytes()


# --------------------------------------------------------------------------- #
# fig-kiwi container handling
# --------------------------------------------------------------------------- #

FIG_KIWI_MAGIC = b"fig-kiwi"
# FigJam (.jam) uses the identical fig-kiwi container (8-byte magic + int32
# version + length-prefixed Kiwi blocks); only the magic string differs.
FIG_JAM_MAGIC = b"fig-jam."
_FIG_MAGICS = (FIG_KIWI_MAGIC, FIG_JAM_MAGIC)
ZIP_MAGIC = b"PK\x03\x04"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _decompress_block(comp: bytes) -> bytes:
    """Decompress one fig-kiwi block.

    Figma compresses each block independently and the codec has changed over
    time: the compiled schema block is raw DEFLATE, while newer files compress
    the document/message block with ZSTANDARD (magic ``28 b5 2f fd``). Detect
    zstd by magic, otherwise fall back to raw-deflate then zlib-wrapped deflate.
    """
    if comp[:4] == ZSTD_MAGIC:
        try:
            import zstandard  # local import: only needed at build time
        except ImportError as exc:  # vendored change: runtime, not build-time, dep
            raise RuntimeError(
                "zstandard is not installed; a zstd-compressed .fig cannot be "
                "parsed. Add zstandard to infra/task-harness/requirements.txt "
                "or pip install zstandard."
            ) from exc

        return zstandard.ZstdDecompressor().decompress(comp)
    try:
        return zlib.decompressobj(-15).decompress(comp)  # raw deflate
    except zlib.error:
        return zlib.decompressobj().decompress(comp)  # zlib-wrapped


def load_canvas_fig_bytes(path: str) -> bytes:
    """Return the raw ``canvas.fig`` bytes from a ``.fig``/``.jam`` file.

    Newer ``.fig`` files are ZIP archives containing a ``canvas.fig`` entry;
    some payloads are the fig-kiwi container directly.
    """
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            target = "canvas.fig" if "canvas.fig" in names else None
            if target is None:
                target = next((n for n in names if n.endswith("canvas.fig")), None)
            if target is None:
                raise ValueError(f"{path}: ZIP has no canvas.fig (entries: {names[:8]})")
            return zf.read(target)
    if head in _FIG_MAGICS:
        with open(path, "rb") as f:
            return f.read()
    raise ValueError(f"{path}: not a ZIP or fig-kiwi/fig-jam file (head={head!r})")


def split_fig_blocks(data: bytes) -> List[bytes]:
    """Split a fig-kiwi container into its decompressed blocks.

    Layout: ``b'fig-kiwi'`` / ``b'fig-jam.'`` (8) + ``int32`` version (4) +
    repeated [``uint32`` length + ``length`` raw-deflate bytes]. block[0] is the
    compiled Kiwi schema; block[1] is the document message.
    """
    if data[:8] not in _FIG_MAGICS:
        raise ValueError(f"not a fig-kiwi/fig-jam payload (head={data[:8]!r})")
    pos = 8
    (_version,) = struct.unpack("<i", data[pos : pos + 4])
    pos += 4
    blocks: List[bytes] = []
    while pos + 4 <= len(data):
        (size,) = struct.unpack("<I", data[pos : pos + 4])
        pos += 4
        comp = data[pos : pos + size]
        pos += size
        if len(comp) < size:
            raise ValueError("fig-kiwi: truncated block")
        blocks.append(_decompress_block(comp))
    if len(blocks) < 2:
        raise ValueError(f"fig-kiwi: expected >=2 blocks, got {len(blocks)}")
    return blocks


def decode_fig(canvas_bytes: bytes) -> Tuple[KiwiSchema, Dict[str, Any]]:
    """Decode ``canvas.fig`` bytes into (schema, message)."""
    blocks = split_fig_blocks(canvas_bytes)
    schema = KiwiSchema(decode_binary_schema(blocks[0]))
    # Figma's root message is conventionally named "Message"; fall back to the
    # first MESSAGE definition that carries a node-changes-like field.
    root = "Message" if "Message" in schema.by_name else _guess_root(schema)
    message = schema.decode_message(blocks[1], root)
    return schema, message


def _guess_root(schema: KiwiSchema) -> str:
    for d in schema.definitions:
        if d["kind"] == KIND_MESSAGE and any(
            "node" in f["name"].lower() for f in d["fields"]
        ):
            return d["name"]
    # last resort: the first message definition
    for d in schema.definitions:
        if d["kind"] == KIND_MESSAGE:
            return d["name"]
    raise ValueError("fig schema has no MESSAGE definition")

# --------------------------------------------------------------------------- #
# Internal Figma message -> REST JSON mapping
#
# Figma's internal model is a flat list of ``nodeChanges``; each carries a
# ``guid`` (sessionID/localID), a ``parentIndex`` (parent guid + fractional
# position), a ``type`` enum, a ``name`` and typed geometry/paint fields. The
# REST API (which the 27 tools consume) is the nested parent->children tree
# below. We emit only the tool-relevant fields; the rest are dropped.
# --------------------------------------------------------------------------- #


# The REST node-type vocabulary the tools/models accept (db_models.FigmaNode).
_VALID_REST_TYPES = {
    "DOCUMENT", "CANVAS", "FRAME", "GROUP", "RECTANGLE", "ELLIPSE", "TEXT",
    "VECTOR", "COMPONENT", "INSTANCE", "COMPONENT_SET", "SECTION", "CONNECTOR",
    "SLICE", "STAR", "REGULAR_POLYGON", "BOOLEAN_OPERATION",
}

# Figma's INTERNAL (.fig/Kiwi) type names differ from the public REST names.
# These are the known renames; anything already-valid passes through, and
# unmapped internal types fall back to a valid container/leaf so the node is
# still preserved (never dropped) and the tree stays model-valid.
_INTERNAL_TO_REST_TYPE = {
    "ROUNDED_RECTANGLE": "RECTANGLE",  # internal rounded-rect -> REST RECTANGLE
    "SYMBOL": "COMPONENT",             # internal main-component name
    "LINE": "VECTOR",                  # LINE isn't in the REST node enum
}

_UNMAPPED_TYPES: Dict[str, int] = {}


def _rest_node_type(internal: Optional[str], has_children: bool) -> str:
    """Translate an internal Figma node type to a valid REST node type."""
    if not internal:
        return "FRAME" if has_children else "VECTOR"
    mapped = _INTERNAL_TO_REST_TYPE.get(internal, internal)
    if mapped in _VALID_REST_TYPES:
        return mapped
    _UNMAPPED_TYPES[internal] = _UNMAPPED_TYPES.get(internal, 0) + 1
    return "FRAME" if has_children else "VECTOR"


def _guid_to_id(guid: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(guid, dict):
        return None
    return f"{guid.get('sessionID', 0)}:{guid.get('localID', 0)}"


def _color_dict(c: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if not isinstance(c, dict):
        return None
    return {
        "r": float(c.get("r", 0.0)),
        "g": float(c.get("g", 0.0)),
        "b": float(c.get("b", 0.0)),
        "a": float(c.get("a", 1.0)),
    }


# --------------------------------------------------------------------------- #
# Absolute-coordinate composition.
#
# Figma's internal per-node ``transform`` is RELATIVE to the parent. The REST
# ``absoluteBoundingBox`` is defined as the node's ABSOLUTE (canvas) position,
# and the connector's own create/clone logic treats it as the global origin
# (``node_creation.py``: "their absoluteBoundingBox gives the global origin").
# Emitting the bare local ``m02/m12`` therefore collapses nested content near
# the parent origin (measured: 84% of a real file's nodes at |x|<50) and
# contradicts both the contract and the runtime logic. We compose each node's
# transform with its ancestors' to recover the true absolute box.
# --------------------------------------------------------------------------- #
_Affine = Tuple[float, float, float, float, float, float]  # (m00,m01,m02,m10,m11,m12)
_IDENTITY: _Affine = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _mat_from_transform(t: Any) -> _Affine:
    if not isinstance(t, dict):
        return _IDENTITY
    return (
        float(t.get("m00", 1.0)), float(t.get("m01", 0.0)), float(t.get("m02", 0.0)),
        float(t.get("m10", 0.0)), float(t.get("m11", 1.0)), float(t.get("m12", 0.0)),
    )


def _matmul(a: _Affine, b: _Affine) -> _Affine:
    a00, a01, a02, a10, a11, a12 = a
    b00, b01, b02, b10, b11, b12 = b
    return (
        a00 * b00 + a01 * b10, a00 * b01 + a01 * b11, a00 * b02 + a01 * b12 + a02,
        a10 * b00 + a11 * b10, a10 * b01 + a11 * b11, a10 * b02 + a11 * b12 + a12,
    )


def _abs_bbox(m: _Affine, w: float, h: float) -> Dict[str, float]:
    """Axis-aligned bounding box of the ``[0,0,w,h]`` rect transformed by ``m``."""
    m00, m01, m02, m10, m11, m12 = m
    xs = (m02, m00 * w + m02, m01 * h + m02, m00 * w + m01 * h + m02)
    ys = (m12, m10 * w + m12, m11 * h + m12, m10 * w + m11 * h + m12)
    x0, y0 = min(xs), min(ys)
    box = {"x": x0, "y": y0, "width": max(xs) - x0, "height": max(ys) - y0}
    # Some source nodes carry degenerate transforms/sizes (zero-scale masks,
    # empty lines) that yield NaN/Inf here. NaN/Inf is invalid JSON and breaks
    # strict parsers (browsers, json.loads(strict=True)), so collapse such a box
    # to a finite zero box rather than emit a poison value.
    if not all(math.isfinite(v) for v in box.values()):
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    return box


def _compose_abs_transforms(node_changes: List[Dict[str, Any]]) -> Dict[str, _Affine]:
    """Map every node id to its ABSOLUTE affine transform (composed from roots)."""
    local: Dict[str, _Affine] = {}
    parent: Dict[str, Optional[str]] = {}
    for nc in node_changes:
        if not isinstance(nc, dict):
            continue
        nid = _guid_to_id(nc.get("guid"))
        if not nid:
            continue
        local[nid] = _mat_from_transform(nc.get("transform"))
        parent[nid] = _guid_to_id((nc.get("parentIndex") or {}).get("guid"))
    children: Dict[str, List[str]] = {}
    roots: List[str] = []
    for nid in local:
        pid = parent.get(nid)
        if pid and pid in local and pid != nid:
            children.setdefault(pid, []).append(nid)
        else:
            roots.append(nid)
    abs_m: Dict[str, _Affine] = {}
    stack: List[Tuple[str, _Affine]] = [(r, _IDENTITY) for r in roots]
    while stack:
        nid, pm = stack.pop()
        m = _matmul(pm, local[nid])
        abs_m[nid] = m
        for cid in children.get(nid, []):
            stack.append((cid, m))
    return abs_m


# --- Vector path geometry (optional, --emit-geometry) -----------------------
# Figma stores each vector/boolean shape's rendered outline in a "commands" blob
# referenced by fillGeometry[].commandsBlob / strokeGeometry[].commandsBlob. The
# blob is a stream of [uint8 verb][float32-LE operands]; verbs and operand counts
# below were reverse-engineered and verified to parse 100% of Banking_Demo's
# 3,402 geometry blobs. Coordinates are in the node's local ``size`` space, so the
# emitted SVG path is relative to the node box (a viewer scales it to the node's
# absoluteBoundingBox). This populates the *existing* Path model field
# (db_models.Path.path) — no new schema types.
_PATH_VERB_FLOATS = {0: 0, 1: 2, 2: 2, 3: 4, 4: 6}  # CLOSE, MOVE, LINE, QUAD, CUBIC


def _blob_raw_bytes(blob: Any) -> bytes:
    data = blob.get("bytes") if isinstance(blob, dict) else blob
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    try:
        return bytes(data)  # kiwi decodes blob bytes as a list[int]
    except (TypeError, ValueError):
        return b""


def _commands_blob_to_svg(raw: bytes) -> Optional[str]:
    """Decode one Figma vector 'commands' blob to an SVG path-data string."""
    n = len(raw)
    if n == 0:
        return None
    pos = 0
    out: List[str] = []
    while pos < n:
        verb = raw[pos]
        pos += 1
        nf = _PATH_VERB_FLOATS.get(verb)
        # Unknown verb or truncated operands → abandon (never emit a partial path).
        if nf is None or pos + 4 * nf > n:
            return None
        v = struct.unpack_from("<" + "f" * nf, raw, pos)
        pos += 4 * nf
        if verb == 1:
            out.append(f"M{v[0]:.3f} {v[1]:.3f}")
        elif verb == 2:
            out.append(f"L{v[0]:.3f} {v[1]:.3f}")
        elif verb == 3:
            out.append(f"Q{v[0]:.3f} {v[1]:.3f} {v[2]:.3f} {v[3]:.3f}")
        elif verb == 4:
            out.append(f"C{v[0]:.3f} {v[1]:.3f} {v[2]:.3f} {v[3]:.3f} {v[4]:.3f} {v[5]:.3f}")
        elif verb == 0:
            out.append("Z")
    return " ".join(out) if out else None


def _map_geometry(segs: Any, blobs: List[Any]) -> List[Dict[str, Any]]:
    """Map internal fill/stroke geometry segments to REST Path dicts (SVG paths)."""
    out: List[Dict[str, Any]] = []
    if not isinstance(segs, list):
        return out
    for s in segs:
        if not isinstance(s, dict):
            continue
        bi = s.get("commandsBlob")
        if not isinstance(bi, int) or bi < 0 or bi >= len(blobs):
            continue
        path = _commands_blob_to_svg(_blob_raw_bytes(blobs[bi]))
        if not path:
            continue
        item: Dict[str, Any] = {"path": path}
        wr = s.get("windingRule")
        if isinstance(wr, str):
            item["windingRule"] = wr
        out.append(item)
    return out


# ScaleMode enum members the storage model accepts (common_models.ScaleMode).
# Internal ``imageScaleMode`` uses the identical spelling, so we forward as-is.
_VALID_SCALE_MODES = {"FILL", "FIT", "TILE", "STRETCH", "CROP"}


def _image_ref_hex(image_obj: Any) -> Optional[str]:
    """Hex-encode an internal image paint's ``image.hash`` (20-byte SHA1 list).

    The hex string is exactly the filename Figma uses inside the ``.fig`` ZIP's
    ``images/`` folder, so it doubles as the REST ``imageRef`` *and* the key for
    the extracted image sidecar (see :func:`extract_and_downscale_images`).
    """
    if not isinstance(image_obj, dict):
        return None
    h = image_obj.get("hash")
    if isinstance(h, (list, tuple)) and h:
        try:
            return "".join(f"{int(b) & 0xFF:02x}" for b in h)
        except (TypeError, ValueError):
            return None
    if isinstance(h, str) and h:
        return h
    return None


def _map_gradient_stops(stops: Any) -> Optional[List[Dict[str, Any]]]:
    """Map internal gradient ``stops`` to REST ``gradientStops`` ({color, position})."""
    if not isinstance(stops, list) or not stops:
        return None
    out: List[Dict[str, Any]] = []
    for s in stops:
        if not isinstance(s, dict):
            continue
        entry: Dict[str, Any] = {}
        color = _color_dict(s.get("color"))
        if color is not None:
            entry["color"] = color
        pos = s.get("position")
        if isinstance(pos, (int, float)):
            entry["position"] = float(pos)
        if entry:
            out.append(entry)
    return out or None


def _map_paints(paints: Any, for_stroke: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Map an internal paint list (fillPaints/strokePaints) to REST paint dicts.

    Emits the tool-visible fields ``get_node_info``/``get_figma_data`` surface
    (``type``, ``color``, ``visible``, ``opacity``) **plus** the visual-fidelity
    fields the storage model carries for renderers / VLM review, which the read
    tools intentionally do not surface:

    - **Image paints:** ``imageRef`` (hex of ``image.hash``; keys the extracted
      image sidecar), ``scaleMode`` (from ``imageScaleMode``) and ``imageTransform``.
    - **Gradient paints:** ``gradientStops`` ({color, position}).

    ``for_stroke`` matters because the schema splits fills and strokes: only
    ``common_models.FillItem`` (fills) declares the image fields
    ``imageRef``/``scaleMode``/``imageTransform``, while ``db_models.Stroke`` is a
    stricter ``BasePaintItem`` that **forbids** them (it only adds gradient stops
    + handles). So for strokes we must NOT emit the image fields — an image-painted
    stroke keeps just the shared paint fields — or ``FigmaDB`` validation rejects
    the seed (real failure hit on ``Automation+ lunch lady flow.jam``).
    ``gradientStops`` is legal on both. See ``DATA_FIDELITY_AND_COMPROMISES.md``.
    """
    if not isinstance(paints, list) or not paints:
        return None
    mapped: List[Dict[str, Any]] = []
    for p in paints:
        if not isinstance(p, dict):
            continue
        ptype = p.get("type", "SOLID")
        entry: Dict[str, Any] = {"type": ptype}
        color = _color_dict(p.get("color"))
        if color is not None:
            entry["color"] = color
        if "visible" in p:
            entry["visible"] = bool(p["visible"])
        opacity = p.get("opacity")
        if isinstance(opacity, (int, float)):
            entry["opacity"] = float(opacity)

        # Image-fill positioning fields exist only on the fills (FillItem) model;
        # the Stroke model forbids them, so skip them for strokes.
        if ptype == "IMAGE" and not for_stroke:
            image_ref = _image_ref_hex(p.get("image"))
            if image_ref:
                entry["imageRef"] = image_ref
            scale_mode = p.get("imageScaleMode")
            if scale_mode in _VALID_SCALE_MODES:
                entry["scaleMode"] = scale_mode
            t = p.get("transform")
            if isinstance(t, dict):
                entry["imageTransform"] = [
                    [float(t.get("m00", 1.0)), float(t.get("m01", 0.0)), float(t.get("m02", 0.0))],
                    [float(t.get("m10", 0.0)), float(t.get("m11", 1.0)), float(t.get("m12", 0.0))],
                ]
        elif isinstance(ptype, str) and ptype.startswith("GRADIENT"):
            stops = _map_gradient_stops(p.get("stops"))
            if stops:
                entry["gradientStops"] = stops

        mapped.append(entry)
    return mapped or None


def _map_effects(effects: Any) -> Optional[List[Dict[str, Any]]]:
    """Map an internal ``effects`` list to REST effect dicts.

    Keeps the fields ``get_node_info`` surfaces per effect: ``type``,
    ``visible``, ``radius``, ``color`` (RGBA) and ``offset`` ({x, y}). Shadow /
    blur effects are otherwise lost entirely.
    """
    if not isinstance(effects, list) or not effects:
        return None
    mapped: List[Dict[str, Any]] = []
    for e in effects:
        if not isinstance(e, dict):
            continue
        entry: Dict[str, Any] = {"type": e.get("type", "DROP_SHADOW")}
        if "visible" in e:
            entry["visible"] = bool(e["visible"])
        radius = e.get("radius")
        if isinstance(radius, (int, float)):
            entry["radius"] = float(radius)
        color = _color_dict(e.get("color"))
        if color is not None:
            entry["color"] = color
        offset = e.get("offset")
        if isinstance(offset, dict):
            entry["offset"] = {
                "x": float(offset.get("x", 0.0)),
                "y": float(offset.get("y", 0.0)),
            }
        mapped.append(entry)
    return mapped or None


# Internal auto-layout ("stack*") align tokens -> REST axis-align enums. Only
# the values the REST model accepts are forwarded; anything else is dropped.
_PRIMARY_AXIS_ALIGN = {"MIN", "MAX", "CENTER", "SPACE_BETWEEN"}
_COUNTER_AXIS_ALIGN = {"MIN", "MAX", "CENTER", "BASELINE"}


def _map_auto_layout(nc: Dict[str, Any], node: Dict[str, Any]) -> None:
    """Translate internal ``stack*`` auto-layout fields to REST layout fields.

    Figma stores auto-layout under ``stackMode`` / ``stackSpacing`` /
    ``stack*Padding`` / ``stack*AlignItems``; the REST node (and ``get_node_info``)
    expose ``layoutMode`` / ``itemSpacing`` / ``padding*`` / ``*AxisAlignItems``.
    """
    stack_mode = nc.get("stackMode")
    if stack_mode not in ("HORIZONTAL", "VERTICAL"):
        return  # not an auto-layout frame (absent or NONE)
    node["layoutMode"] = stack_mode

    spacing = nc.get("stackSpacing")
    if isinstance(spacing, (int, float)):
        node["itemSpacing"] = float(spacing)

    # Per-side padding, falling back to the symmetric horizontal/vertical values.
    horiz = nc.get("stackHorizontalPadding")
    vert = nc.get("stackVerticalPadding")
    sides = {
        "paddingLeft": nc.get("stackPaddingLeft", horiz),
        "paddingRight": nc.get("stackPaddingRight", horiz),
        "paddingTop": nc.get("stackPaddingTop", vert),
        "paddingBottom": nc.get("stackPaddingBottom", vert),
    }
    for key, val in sides.items():
        if isinstance(val, (int, float)):
            node[key] = float(val)

    primary = nc.get("stackPrimaryAlignItems")
    if primary in _PRIMARY_AXIS_ALIGN:
        node["primaryAxisAlignItems"] = primary
    counter = nc.get("stackCounterAlignItems")
    if counter in _COUNTER_AXIS_ALIGN:
        node["counterAxisAlignItems"] = counter


# Blend modes get_figma_data surfaces (common_models.BlendMode). NORMAL /
# PASS_THROUGH are no-op defaults we skip to avoid per-node noise.
_VALID_BLEND_MODES = {
    "PASS_THROUGH", "NORMAL", "DARKEN", "MULTIPLY", "LINEAR_BURN", "COLOR_BURN",
    "LIGHTEN", "SCREEN", "LINEAR_DODGE", "COLOR_DODGE", "OVERLAY", "SOFT_LIGHT",
    "HARD_LIGHT", "DIFFERENCE", "EXCLUSION", "HUE", "SATURATION", "COLOR",
    "LUMINOSITY",
}


def _map_text_style_extras(nc: Dict[str, Any], style: Dict[str, Any]) -> None:
    """Add the BaseTextStyle typography ``get_figma_data`` surfaces beyond font/size.

    Alignment enums pass through as-is. Internal ``letterSpacing`` / ``lineHeight``
    are ``{value, units}`` dicts; only the PIXELS form maps cleanly to the REST
    numeric ``letterSpacing`` / ``lineHeightPx`` fields (percent/auto forms are left
    out rather than emitting an ambiguous number).
    """
    tah = nc.get("textAlignHorizontal")
    if tah in ("LEFT", "RIGHT", "CENTER", "JUSTIFIED"):
        style["textAlignHorizontal"] = tah
    tav = nc.get("textAlignVertical")
    if tav in ("TOP", "CENTER", "BOTTOM"):
        style["textAlignVertical"] = tav
    ls = nc.get("letterSpacing")
    if isinstance(ls, dict) and ls.get("units") == "PIXELS" and isinstance(ls.get("value"), (int, float)):
        style["letterSpacing"] = float(ls["value"])
    lh = nc.get("lineHeight")
    if isinstance(lh, dict) and lh.get("units") == "PIXELS" and isinstance(lh.get("value"), (int, float)):
        style["lineHeightPx"] = float(lh["value"])


# --------------------------------------------------------------------------- #
# Migration coverage tracking. Every internal key is either MAPPED (-> a REST
# field a tool reads/writes) or dropped; report_coverage() lists the dropped
# keys with frequencies at emit time so nothing is *silently* discarded and new
# unexpected keys stand out. Rationale for each drop lives in
# docs/review-claude/DATA_FIDELITY_AND_COMPROMISES.md.
# --------------------------------------------------------------------------- #
_MAPPED_KEYS = {
    "guid", "name", "type", "parentIndex", "size", "transform", "visible",
    "locked", "opacity", "blendMode", "fillPaints", "strokePaints", "strokeWeight",
    "strokeAlign", "cornerRadius", "effects", "stackMode", "stackSpacing",
    "stackHorizontalPadding", "stackVerticalPadding", "stackPaddingLeft",
    "stackPaddingRight", "stackPaddingTop", "stackPaddingBottom",
    "stackPrimaryAlignItems", "stackCounterAlignItems", "symbolData", "textData",
    "fontSize", "fontName", "textAlignHorizontal", "textAlignVertical",
    "letterSpacing", "lineHeight",
    # frame clipping -> REST clipsContent (always consumed by _node_change_to_rest)
    "frameMaskDisabled",
}
# Consumed only under --emit-geometry (decoded to REST Path geometry). Tracked
# separately so the coverage report stays truthful for both flag states rather
# than mislabelling them as always-dropped.
_GEOMETRY_KEYS = {"fillGeometry", "strokeGeometry"}
_DROPPED_KEYS: Dict[str, int] = {}
_NODES_SEEN = 0


def _track_coverage(nc: Dict[str, Any], emit_geometry: bool = False) -> None:
    """Record which internal keys the mapper did not consume for this node.

    ``emit_geometry`` reflects the active run: when set, vector geometry keys are
    consumed (decoded to Path), so they are not counted as dropped.
    """
    global _NODES_SEEN
    _NODES_SEEN += 1
    consumed = _MAPPED_KEYS | _GEOMETRY_KEYS if emit_geometry else _MAPPED_KEYS
    for k in nc.keys():
        if k not in consumed:
            _DROPPED_KEYS[k] = _DROPPED_KEYS.get(k, 0) + 1


def report_coverage() -> str:
    """Human-readable summary of unmapped types and dropped internal keys."""
    lines: List[str] = []
    if _UNMAPPED_TYPES:
        lines.append("Unmapped node types (preserved via FRAME/VECTOR fallback):")
        for t, c in sorted(_UNMAPPED_TYPES.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {c:8d}  {t}")
    else:
        lines.append("Unmapped node types: none (every internal type recognized).")
    lines.append(f"Dropped internal keys (not read/written by any tool) over {_NODES_SEEN} nodes:")
    if _DROPPED_KEYS:
        for k, c in sorted(_DROPPED_KEYS.items(), key=lambda kv: -kv[1]):
            pct = 100.0 * c / _NODES_SEEN if _NODES_SEEN else 0.0
            lines.append(f"  {c:8d} ({pct:5.1f}%)  {k}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _node_name(raw_name: Optional[str], rest_type: str) -> str:
    """Return a non-empty layer name for a REST node.

    Figma's REST API always returns a non-empty ``name`` (unnamed layers show as
    "Vector", "Frame", ...), and the connector's ``raw_node_to_curated`` (used by
    ``get_figma_data``) rejects empty names. Internal nodes can legitimately have
    ``name == ""`` (e.g. vectors inside components), so fall back to a friendly
    default derived from the type, mirroring Figma's own default layer names.
    """
    if raw_name:
        return raw_name
    return (rest_type or "NODE").replace("_", " ").title() or "Node"


def _node_change_to_rest(
    nc: Dict[str, Any],
    has_children: bool = False,
    abs_transform: Optional[_Affine] = None,
    blobs: Optional[List[Any]] = None,
    emit_geometry: bool = False,
) -> Dict[str, Any]:
    """Map one internal nodeChange to a REST node dict (tool-relevant fields).

    ``abs_transform`` is the node's composed ABSOLUTE affine (see
    :func:`_compose_abs_transforms`); when provided, ``absoluteBoundingBox`` is
    the true canvas-space box. Without it we fall back to the bare local
    translation (kept only for the synthetic ``--self-test`` path).
    """
    _track_coverage(nc, emit_geometry)
    rest_type = _rest_node_type(nc.get("type"), has_children)
    node: Dict[str, Any] = {
        "id": _guid_to_id(nc.get("guid")) or "0:0",
        "name": _node_name(nc.get("name"), rest_type),
        "type": rest_type,
    }

    size = nc.get("size")
    if isinstance(size, dict):
        w = float(size.get("x", 0.0))
        h = float(size.get("y", 0.0))
        if abs_transform is not None:
            node["absoluteBoundingBox"] = _abs_bbox(abs_transform, w, h)
        else:
            transform = nc.get("transform") or {}
            node["absoluteBoundingBox"] = {
                "x": float(transform.get("m02", 0.0)),
                "y": float(transform.get("m12", 0.0)),
                "width": w,
                "height": h,
            }

    # Optional vector path geometry (opt-in). Populates the existing Path model
    # field so a viewer can draw real shapes instead of a bounding box; kept off
    # by default because it adds bytes and read tools rarely need pixel shapes.
    if emit_geometry and blobs is not None:
        fg = _map_geometry(nc.get("fillGeometry"), blobs)
        if fg:
            node["fillGeometry"] = fg
        sg = _map_geometry(nc.get("strokeGeometry"), blobs)
        if sg:
            node["strokeGeometry"] = sg

    # Frame clipping: internal ``frameMaskDisabled`` is the inverse of the REST
    # ``clipsContent`` (documented on get_figma_data). Emitting it lets a viewer
    # clip children to their frame (stops decorative vectors overflowing).
    if "frameMaskDisabled" in nc:
        node["clipsContent"] = not bool(nc["frameMaskDisabled"])

    if "visible" in nc:
        node["visible"] = bool(nc["visible"])
    if "locked" in nc:
        node["locked"] = bool(nc["locked"])
    if "opacity" in nc:
        node["opacity"] = float(nc["opacity"])

    # Compositing blend mode (get_figma_data surfaces it); skip the no-op defaults.
    blend_mode = nc.get("blendMode")
    if blend_mode in _VALID_BLEND_MODES and blend_mode not in ("NORMAL", "PASS_THROUGH"):
        node["blendMode"] = blend_mode

    fills = _map_paints(nc.get("fillPaints"))
    if fills:
        node["fills"] = fills

    # Strokes (outline) + weight; only emit weight alongside an actual stroke.
    strokes = _map_paints(nc.get("strokePaints"), for_stroke=True)
    if strokes:
        node["strokes"] = strokes
        stroke_weight = nc.get("strokeWeight")
        if isinstance(stroke_weight, (int, float)):
            node["strokeWeight"] = float(stroke_weight)

    # strokeAlign is present on every internal node and is a required field in
    # the get_node_info output (otherwise silently defaulted to INSIDE), so keep
    # the real value even when there is no stroke paint.
    stroke_align = nc.get("strokeAlign")
    if stroke_align in ("INSIDE", "OUTSIDE", "CENTER"):
        node["strokeAlign"] = stroke_align

    corner_radius = nc.get("cornerRadius")
    if isinstance(corner_radius, (int, float)):
        node["cornerRadius"] = float(corner_radius)

    # Visual effects (drop/inner shadow, blur) -> REST effects list.
    effects = _map_effects(nc.get("effects"))
    if effects:
        node["effects"] = effects

    # Auto-layout (stack* -> layoutMode / itemSpacing / padding* / *AxisAlign).
    _map_auto_layout(nc, node)

    # Component instance -> main-component reference (get_node_info.componentId).
    symbol_data = nc.get("symbolData")
    if isinstance(symbol_data, dict):
        component_id = _guid_to_id(symbol_data.get("symbolID"))
        if component_id:
            node["componentId"] = component_id

    text_data = nc.get("textData")
    if isinstance(text_data, dict) and text_data.get("characters") is not None:
        node["characters"] = text_data.get("characters")

    # Text typography (font size / family / style) -> REST node style.
    style: Dict[str, Any] = {}
    font_size = nc.get("fontSize")
    if isinstance(font_size, (int, float)):
        style["fontSize"] = float(font_size)
    font_name = nc.get("fontName")
    if isinstance(font_name, dict):
        if font_name.get("family"):
            style["fontFamily"] = font_name["family"]
        # get_node_info resolves the font weight/style from fontPostScriptName.
        if font_name.get("style"):
            style["fontPostScriptName"] = font_name["style"]
    _map_text_style_extras(nc, style)
    if style:
        node["style"] = style

    return node


# Populated by message_to_files when subsetting trims pages; reported by main().
_LAST_SUBSET_INFO: Optional[Dict[str, Any]] = None

# Populated by message_to_files when hidden/internal CANVAS pages are dropped.
_LAST_DROPPED_INTERNAL: Optional[Dict[str, Any]] = None


def _count_tree(node: Dict[str, Any]) -> int:
    """Count a node and all its descendants."""
    n = 1
    for c in node.get("children") or []:
        if isinstance(c, dict):
            n += _count_tree(c)
    return n


# Page names that mark low-value / non-live content. When a file exceeds the node
# ceiling we shed these *first* (largest first) so a live page is never dropped in
# favour of an archive/sandbox page. Matched on whole lowercase tokens (so "gold"
# won't match "old") plus a few multi-word phrases and archive-ish emoji.
_ARCHIVE_PAGE_TOKENS = {
    "archive", "archived", "archives", "old", "older", "oldest", "backup", "backups",
    "bak", "wip", "draft", "drafts", "scratch", "sandbox", "sandboxes", "legacy",
    "unused", "obsolete", "deprecated", "temp", "temporary", "tmp", "trash", "bin",
    "junk", "dump", "dnu", "graveyard", "dustbin",
}
_ARCHIVE_PAGE_PHRASES = (
    "do not use", "don't use", "not in use", "no longer used", "to delete",
    "old version", "old versions", "not used", "for reference",
)
# Only unambiguous "archive" emoji: wastebasket (with/without VS16 -> matched by
# the base codepoint) and file cabinet. Deliberately NOT construction (\U0001f6a7),
# beach, or package: those decorate live pages like "🚧 Ready for dev" (a false
# positive we hit on Automation+). Archive-word tokens still cover "sandbox" etc.
_ARCHIVE_PAGE_EMOJI = ("\U0001f5d1", "\U0001f5c4")


def _is_archive_page_name(name: Optional[str]) -> bool:
    """True if a page name looks like archive / sandbox / deprecated content."""
    if not name:
        return False
    if any(e in name for e in _ARCHIVE_PAGE_EMOJI):
        return True
    low = name.lower()
    if any(p in low for p in _ARCHIVE_PAGE_PHRASES):
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", low) if t]
    return any(t in _ARCHIVE_PAGE_TOKENS for t in tokens)


def _subset_document_pages(document: Dict[str, Any], max_nodes: int) -> Optional[Dict[str, Any]]:
    """Shrink an oversized document to fit ``max_nodes`` by dropping whole subtrees.

    Policy (name-aware; keeps *entire* subtrees so every retained node stays
    schema-valid -- we never trim fields or split a node):

    1. **Archive pages first** -- if over budget, drop *every* ``CANVAS`` page whose
       name looks like archive / sandbox / deprecated content (never the current
       page). These are low-value by definition, so we shed all of them (not just
       enough to fit) to keep the seed lean and never sacrifice a live page.
    2. **Frame-trim, keeping every live page** -- if still over budget with only
       live pages left, do NOT drop a whole live page (a website's "Homepage" must
       survive). Instead shed trailing top-level frames from the largest page(s),
       reading-order preserving (keep the first frames), until it fits. Every live
       page stays present with >=1 frame. Recorded in ``trimmed_children``.
    3. **Whole live page (absolute last resort)** -- only if a page is a single
       un-trimmable giant frame does dropping a whole live page happen (reason
       ``"size"``). This should never trigger for real files.

    Each dropped page is recorded as ``(name, node_count, reason)`` (``"archive"``
    or ``"size"``); trimmed frames as ``(page, frame, node_count)``. Returns a
    summary, or None if the tree already fit.
    """
    total = _count_tree(document)
    if total <= max_nodes:
        return None
    children = document.get("children") or []
    pages = [c for c in children if isinstance(c, dict) and c.get("type") == "CANVAS"]
    others = [c for c in children if not (isinstance(c, dict) and c.get("type") == "CANVAS")]
    if not pages:
        return None  # nothing page-shaped to trim safely

    cur_id = document.get("currentPageId")
    base = 1 + sum(_count_tree(o) for o in others)  # document root + non-page roots
    counts: Dict[int, int] = {id(p): _count_tree(p) for p in pages}
    keep: List[Dict[str, Any]] = list(pages)
    dropped: List[Tuple[str, int, str]] = []

    def _running() -> int:
        return base + sum(counts[id(p)] for p in keep)

    def _shed(candidates: List[Dict[str, Any]], reason: str, stop_when_fit: bool) -> None:
        # Largest first. Archive pages: shed all (stop_when_fit=False) since they
        # are low value. Real pages: shed only the fewest needed to fit.
        for p in sorted(candidates, key=lambda q: counts[id(q)], reverse=True):
            if len(keep) <= 1:
                break
            if stop_when_fit and _running() <= max_nodes:
                break
            keep.remove(p)
            dropped.append((p.get("name") or p.get("id") or "?", counts[id(p)], reason))

    # Shed every archive/sandbox page (never the current page).
    _shed(
        [p for p in keep if p.get("id") != cur_id and _is_archive_page_name(p.get("name"))],
        "archive",
        stop_when_fit=False,
    )

    # Frame-trim, keeping EVERY live page present. Shed trailing top-level frames
    # from the largest kept page until we fit, then re-pick the largest, etc. This
    # preserves all live pages (a website's "Homepage" is never dropped) -- only
    # trailing frames within oversized pages are shed. counts[] is kept in sync so
    # _running() stays cheap.
    trimmed: List[Tuple[str, str, int]] = []

    def _top_frames(p: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [k for k in (p.get("children") or []) if isinstance(k, dict)]

    guard = 0
    while _running() > max_nodes and guard < 1_000_000:
        guard += 1
        cand = [p for p in keep if len(_top_frames(p)) > 1]
        if not cand:
            break  # nothing left to trim without corrupting a single-frame page
        page = max(cand, key=lambda q: counts[id(q)])
        kids = _top_frames(page)
        non_dict = [k for k in (page.get("children") or []) if not isinstance(k, dict)]
        over = _running() - max_nodes
        shed = 0
        keepn = len(kids)
        # Drop trailing frames (keep reading order / leading frames) until this
        # page covers the overage or is down to its first frame.
        while keepn > 1 and shed < over:
            keepn -= 1
            dk = kids[keepn]
            dc = _count_tree(dk)
            shed += dc
            trimmed.append((page.get("name") or page.get("id") or "?",
                            dk.get("name") or dk.get("id") or "?", dc))
        page["children"] = kids[:keepn] + non_dict
        counts[id(page)] -= shed

    # Absolute last resort: a page that is one giant frame can't be frame-trimmed
    # (would corrupt the node); only then drop a whole live page.
    if _running() > max_nodes:
        _shed([p for p in keep if p.get("id") != cur_id], "size", stop_when_fit=True)

    keep_ids = {id(p) for p in keep}
    kept_pages = [p for p in pages if id(p) in keep_ids]

    if not dropped and not trimmed:
        return None

    document["children"] = others + kept_pages
    if cur_id not in {p.get("id") for p in kept_pages}:
        document["currentPageId"] = kept_pages[0].get("id") if kept_pages else None
    return {
        "nodes_before": total,
        "nodes_after": base + sum(_count_tree(p) for p in kept_pages),
        "pages_before": len(pages),
        "pages_after": len(kept_pages),
        "dropped_pages": dropped,
        "trimmed_children": trimmed,
    }


def message_to_files(
    message: Dict[str, Any],
    file_key: str,
    file_name: str,
    max_nodes: Optional[int] = None,
    drop_internal_pages: bool = True,
    emit_geometry: bool = False,
) -> Dict[str, Any]:
    """Build a REST file dict (with a nested ``document`` tree) from a message.

    When ``max_nodes`` is set and the tree is larger, whole pages are dropped to
    fit the budget (see :func:`_subset_document_pages`) before the current-page
    pointer and component maps are computed, so they reflect the retained pages.

    When ``drop_internal_pages`` is true (default) any CANVAS flagged
    ``internalOnly`` (or ``visible: false``) is removed with its whole subtree.
    Figma itself hides these pages (they hold the file's off-canvas component
    library), so keeping them would (a) show a page a user never sees in Figma
    and (b) often double the node count. Recorded in ``_LAST_DROPPED_INTERNAL``.
    """
    node_changes = message.get("nodeChanges") or message.get("node_changes") or []
    if not isinstance(node_changes, list):
        node_changes = []

    rest_nodes: Dict[str, Dict[str, Any]] = {}
    parent_of: Dict[str, Optional[str]] = {}
    order: List[str] = []

    # First pass: learn the parent of every node so we know which ids are
    # containers (have children) before we resolve node types.
    pre_parent: Dict[str, Optional[str]] = {}
    for nc in node_changes:
        if not isinstance(nc, dict):
            continue
        nid = _guid_to_id(nc.get("guid")) or "0:0"
        pre_parent[nid] = _guid_to_id((nc.get("parentIndex") or {}).get("guid"))
    parent_ids = {pid for pid in pre_parent.values() if pid}

    # Compose absolute transforms up-front so every node's absoluteBoundingBox
    # is the true canvas-space box (not the parent-relative local translation).
    abs_transforms = _compose_abs_transforms(node_changes)

    blobs = message.get("blobs") if emit_geometry else None
    internal_page_ids: set = set()
    for nc in node_changes:
        if not isinstance(nc, dict):
            continue
        nid_pre = _guid_to_id(nc.get("guid")) or "0:0"
        rest = _node_change_to_rest(
            nc,
            has_children=nid_pre in parent_ids,
            abs_transform=abs_transforms.get(nid_pre),
            blobs=blobs,
            emit_geometry=emit_geometry,
        )
        nid = rest["id"]
        rest_nodes[nid] = rest
        parent_of[nid] = _guid_to_id((nc.get("parentIndex") or {}).get("guid"))
        order.append(nid)
        # A CANVAS the file marks internal/hidden is Figma's off-canvas component
        # library, not a page the user sees; flag it for optional removal below.
        if nc.get("type") == "CANVAS" and (nc.get("internalOnly") is True or nc.get("visible") is False):
            internal_page_ids.add(nid)

    # Attach children to parents; nodes without a known parent are roots.
    roots: List[str] = []
    for nid in order:
        pid = parent_of.get(nid)
        if pid and pid in rest_nodes and pid != nid:
            rest_nodes[pid].setdefault("children", []).append(rest_nodes[nid])
        else:
            roots.append(nid)

    # The first DOCUMENT-typed root (or a synthetic document) anchors the tree.
    document = None
    for nid in roots:
        if rest_nodes[nid].get("type") == "DOCUMENT":
            document = rest_nodes[nid]
            break
    if document is None:
        document = {
            "id": "0:0",
            "name": "Document",
            "type": "DOCUMENT",
            "children": [rest_nodes[nid] for nid in roots],
        }

    # Drop hidden/internal CANVAS pages (Figma's off-canvas component library)
    # before anything downstream, so page lists, currentPageId, component maps
    # and any subsetting all reflect only the pages a user actually sees.
    global _LAST_DROPPED_INTERNAL
    _LAST_DROPPED_INTERNAL = None
    if drop_internal_pages and internal_page_ids:
        kids = document.get("children") or []
        kept = [c for c in kids if c.get("id") not in internal_page_ids]
        dropped = len(kids) - len(kept)
        if dropped:
            document["children"] = kept
            _LAST_DROPPED_INTERNAL = {
                "pages": dropped,
                "names": [c.get("name") for c in kids if c.get("id") in internal_page_ids],
            }

    # Optional subsetting for genuine node-count monsters: drop whole pages to a
    # budget *before* deriving currentPageId + component maps so they stay in sync
    # with the retained pages. Recorded in _LAST_SUBSET_INFO for main() to report.
    global _LAST_SUBSET_INFO
    _LAST_SUBSET_INFO = None
    if max_nodes:
        _LAST_SUBSET_INFO = _subset_document_pages(document, max_nodes)

    # Tools operate on a "current page" (create_frame/create_text default
    # parenting, get_selection, ...). Point currentPageId at the first CANVAS
    # page so a migrated file is immediately writable, mirroring the seed DB.
    doc_children = document.get("children") or []
    pages = [c for c in doc_children if c.get("type") == "CANVAS"]
    if pages:
        document["currentPageId"] = pages[0]["id"]
    elif doc_children:
        document["currentPageId"] = doc_children[0].get("id")

    # File-level component / component-set metadata maps. get_local_components
    # resolves each COMPONENT node's publish "key" from here (the .fig node
    # stream carries no publish key for local components), and REST FileData
    # carries these maps. Keys are synthesized deterministically from node ids.
    components: Dict[str, Dict[str, Any]] = {}
    component_sets: Dict[str, Dict[str, Any]] = {}

    def _index_components(n: Dict[str, Any]) -> None:
        nid = n.get("id")
        ntype = n.get("type")
        if nid:
            if ntype == "COMPONENT":
                components[nid] = {"key": nid, "name": n.get("name") or ""}
            elif ntype == "COMPONENT_SET":
                component_sets[nid] = {"key": nid, "name": n.get("name") or ""}
        for c in n.get("children") or []:
            _index_components(c)

    _index_components(document)

    file_dict: Dict[str, Any] = {
        "fileKey": file_key,
        "name": file_name,
        # lastModified / thumbnailUrl are required by FileData; .fig archives
        # don't carry them, so emit deterministic placeholders.
        "lastModified": "2024-01-01T00:00:00Z",
        "thumbnailUrl": f"https://example.com/thumbnails/{file_key}.png",
        "version": "1",
        "role": "owner",
        "editorType": "figma",
        "schemaVersion": 0,
        "document": document,
    }
    if components:
        file_dict["components"] = components
    if component_sets:
        file_dict["componentSets"] = component_sets
    return file_dict

_FILE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def safe_file_key(raw: str) -> str:
    """Sanitize a raw name into the tool-accepted file_key charset.

    Tools that take a ``file_key`` (``get_figma_data``, ``set_current_file``,
    ``list_files`` ...) only accept ``[A-Za-z0-9_-]``. The seed loader also maps
    ``file_key`` -> ``<file_key>.json``, so the key doubles as the filename stem.
    Any other character (space, ``#``, ``.`` ...) is collapsed to ``_``.

    ``+`` is spelled out as ``plus`` *before* collapsing punctuation, so a "+"
    variant stays distinct from its base name: ``Cards+`` -> ``Cardsplus`` (not
    ``Cards``, which would collide with ``Cards.fig`` and silently overwrite one
    seed with the other). Matches the pre-existing ``Cardsplus`` seed name.
    """
    key = re.sub(r"[^A-Za-z0-9_-]+", "_", (raw or "").replace("+", "plus")).strip("_")
    return key or "figma_file"


def _assert_tool_readable(document: Dict[str, Any]) -> None:
    """Assert every node satisfies the read-tool contract (non-empty id/name/type).

    ``FigmaDB`` validation permits empty ``id``/``name``/``type``, but
    ``raw_node_to_curated`` (used by ``get_figma_data``) requires all three to be
    non-empty on every node. Enforcing the stricter tool contract at emit time
    keeps schema-valid seeds from failing a read tool at runtime.
    """
    stack: List[Dict[str, Any]] = [document]
    while stack:
        n = stack.pop()
        if not n.get("id"):
            raise ValueError(f"node missing non-empty 'id' (name={n.get('name')!r}, type={n.get('type')!r})")
        if not n.get("name"):
            raise ValueError(f"node {n.get('id')!r} missing non-empty 'name'")
        if not n.get("type"):
            raise ValueError(f"node {n.get('id')!r} missing non-empty 'type'")
        for c in n.get("children") or []:
            if isinstance(c, dict):
                stack.append(c)


def _scrub_non_finite(obj: Any) -> int:
    """Replace NaN/Infinity floats with 0.0 in-place; return how many were fixed.

    ``NaN``/``Infinity`` are **invalid JSON** — Python's ``json.dump`` emits them
    happily (``allow_nan=True`` by default) and Pydantic accepts them, but any
    strict consumer (a browser's ``JSON.parse``, ``json.loads(strict=True)``, most
    other languages) rejects the file. Source ``.fig`` files carry them on
    degenerate nodes; :func:`_abs_bbox` already zeroes the common bbox case, so
    this is the belt-and-suspenders guarantee that *no* emitted seed is poisoned.
    """
    count = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, float) and not math.isfinite(v):
                obj[k] = 0.0
                count += 1
            else:
                count += _scrub_non_finite(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, float) and not math.isfinite(v):
                obj[i] = 0.0
                count += 1
            else:
                count += _scrub_non_finite(v)
    return count

# --------------------------------------------------------------------------- #
# Public entry point (added by the vendoring; not in the source script).
#
# ``canvas_bytes_from_fig`` is the in-memory twin of ``load_canvas_fig_bytes``
# above -- same ZIP-container detection, same fig-kiwi/fig-jam magic check, on
# bytes we already hold instead of a path. ``parse_fig_bytes`` then replays what
# ``main(--from-local)`` does between decode and emit, plus the dict assembly and
# the scrub/assert order of ``emit_seed``, and returns the DB dict instead of
# writing it to disk.
# --------------------------------------------------------------------------- #


def canvas_bytes_from_fig(data: bytes) -> bytes:
    """Return the raw ``canvas.fig`` bytes from in-memory ``.fig``/``.jam`` bytes.

    Byte-for-byte the behaviour of :func:`load_canvas_fig_bytes`, for a payload
    that is already in memory (an upload) rather than on disk.
    """
    head = data[:8]
    if head.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            target = "canvas.fig" if "canvas.fig" in names else None
            if target is None:
                target = next((n for n in names if n.endswith("canvas.fig")), None)
            if target is None:
                raise ValueError(f"ZIP has no canvas.fig (entries: {names[:8]})")
            return zf.read(target)
    if head in _FIG_MAGICS:
        return bytes(data)
    raise ValueError(f"not a ZIP or fig-kiwi/fig-jam payload (head={head!r})")


def parse_fig_bytes(
    data: bytes,
    *,
    file_key: str,
    file_name: str,
    keep_internal_pages: bool = False,
) -> Dict[str, Any]:
    """Parse ``.fig``/``.jam`` bytes into the Figma gym-state (``FigmaDB``) dict.

    The same pipeline the seed builder runs for ``--from-local``: container ->
    Kiwi blocks -> decoded message -> REST file dict -> DB envelope, with the
    seed defaults (no node ceiling, no vector geometry). ``keep_internal_pages``
    mirrors the CLI flag of the same name: off by default, so hidden/internal
    CANVAS pages are dropped exactly as the seeds were built.

    Raises ``ValueError`` for a payload that is not a ``.fig``, and
    ``RuntimeError`` for a zstd-compressed one when ``zstandard`` is missing.
    """
    canvas_bytes = canvas_bytes_from_fig(data)
    _schema, message = decode_fig(canvas_bytes)
    file_json = message_to_files(
        message,
        file_key,
        file_name,
        max_nodes=None,
        drop_internal_pages=not keep_internal_pages,
        emit_geometry=False,
    )
    db_dict: Dict[str, Any] = {
        "files": [file_json],
        "current_selection_node_ids": [],
        "projects": [],
        "current_file_key": file_key,
        "current_figma_channel": None,
    }
    # emit_seed's order: scrub the whole envelope, then assert the read-tool
    # contract on the document. FigmaDB validation sits between them there; it
    # is excised here (it imports the gym app) and never mutates the dict.
    _scrub_non_finite(db_dict)
    _assert_tool_readable(file_json["document"])
    return db_dict
