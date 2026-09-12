from __future__ import annotations

import copy
import hashlib
import json
import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable, Any, Callable

APP_VERSION = "0.1.0-CTS"
PROJECT_FORMAT = 2
REF_SHA = "5e4e0521cd5feae8635913e645bbc8aaaa4f165a03b12aaa270f735fda6ab411"
SCRIPT_FILE_ID = 66
HOUSE_RAM = 0x0218BCF4
HOUSE_VARIANTS = {74:(0,0x0218BA9C,12,"Casa inicial"),75:(1,0x0218BB38,16,"Casa — 1º upgrade"),76:(2,0x0218BCF4,20,"Casa — final")}
BASE_SCRIPT_COUNT = 1295
BASE_POINTER_COUNT = 1296
BASE_LAST_SCRIPT_ID = 1294
# Legacy aliases kept for third-party scripts importing older names. They are
# base-layout facts only; runtime editing uses model.script_count/last_script_id.
REAL_SCRIPT_LAST = BASE_LAST_SCRIPT_ID
POINTER_COUNT_EXPECTED = BASE_POINTER_COUNT

KNOWN_NAMES = {
    521: "Banheiro",
    522: "Banheiro — retorno",
    523: "Cama do jogador",
    524: "Cama do cônjuge",
    527: "Calendário",
    528: "Mesa",
    529: "Relógio / visor",
    530: "Lixeira",
    531: "Cozinha",
    532: "Mobília — identificação pendente",
    533: "Toca-discos",
    534: "Mobília — identificação pendente",
    535: "Mobília — identificação pendente",
    536: "Porta-meia",
    537: "Cama da criança",
    538: "Telefone",
    539: "TV (provável)",
    540: "Lavabo",
    541: "Lavabo — retorno",
    542: "Mobília — identificação pendente",
}
CONFIRMED_GROUP_SEEDS = [
    {"name": "Banheiro", "scripts": [521, 522], "kind": "confirmed"},
    {"name": "Lavabo", "scripts": [540, 541], "kind": "confirmed"},
]
CALL_NAMES = {
    45: ("Mostrar Texto", "confirmed"),
    54: ("Choice binário", "confirmed"),
    57: ("Helper pós-Choice", "structural"),
    0x134: ("SET_INTERACTION_COUNTER_4", "confirmed"),
    0x136: ("BATHROOM_ACTION", "confirmed"),
    0x137: ("LAVABO_ACTION", "confirmed"),
}
BRANCHES = {"b", "blt", "ble", "beq", "bne", "bge", "bgt"}
OPS = [
    "nop", "equ", "addequ", "subequ", "mulequ", "divequ", "modequ",
    "add", "sub", "mul", "div", "mod", "and", "or", "inc", "dec",
    "neg", "not", "cmp", "pushm", "popm", "dup", "pop", "push", "b",
    "blt", "ble", "beq", "bne", "bge", "bgt", "bi", "end", "call",
    "push16", "push8", "switch",
]
SIZES = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,4,4,0,0,4,4,4,4,4,4,4,4,0,0,4,2,1,4]
OP_INDEX = {name: i for i, name in enumerate(OPS)}
WESTERN = {
    "À":0xA6,"Á":0xA7,"Â":0xA8,"Ã":0xA9,"Ç":0xAA,"È":0xAB,"É":0xAC,"Ê":0xAD,
    "Ì":0xAE,"Í":0xAF,"Î":0xB0,"Ï":0xB1,"Ñ":0xB2,"Ò":0xB3,"Ó":0xB4,"Ô":0xB5,
    "Õ":0xB6,"Ù":0xB7,"Ú":0xB8,"Û":0xB9,"ß":0xBB,"à":0xBC,"á":0xBD,"â":0xBE,
    "ã":0xBF,"ç":0xC0,"è":0xC1,"é":0xC2,"ê":0xC3,"ì":0xC4,"í":0xC5,"î":0xC6,
    "ï":0xC7,"ñ":0xC8,"ò":0xC9,"ó":0xCA,"ô":0xCB,"õ":0xCC,"ù":0xCD,"ú":0xCE,
    "û":0xCF,"Œ":0xD1,"œ":0xD2,"¿":0xD3,"¡":0xD4,"ª":0xD5,"º":0xD6,
}
# Alias kept because older research modules import PTBR.
PTBR = WESTERN
REV = {v:k for k,v in PTBR.items()}

ZONE_SAFE_VISUAL = "SAFE_VISUAL"
ZONE_SAFE_LOGIC = "SAFE_LOGIC"
ZONE_ENGINE_TRANSITION = "ENGINE_TRANSITION"
ZONE_UNKNOWN = "UNKNOWN"
ZONE_TYPES = (ZONE_SAFE_VISUAL, ZONE_SAFE_LOGIC, ZONE_ENGINE_TRANSITION)

class HMDSStudioError(Exception):
    pass

@dataclass
class Instruction:
    at: int
    op: str
    arg: Optional[int]
    size: int
    end: int
    truncated: bool = False

@dataclass
class JumpCase:
    value: int
    target: int

@dataclass
class JumpTable:
    index: int
    offset: int = 0
    default_target: int = -1
    cases: List[JumpCase] = field(default_factory=list)
    error: str = ""

@dataclass
class Chunk:
    tag: str
    payload: bytes

@dataclass
class ScriptRecord:
    sid: int
    st: int
    en: int
    rec: bytes
    valid: bool = False
    strings: List[str] = field(default_factory=list)
    raw_slots: List[bytes] = field(default_factory=list)
    chunks: List[Chunk] = field(default_factory=list)
    chunk_offsets: Dict[str, int] = field(default_factory=dict)
    code: Optional[bytes] = None
    jump_count: int = 0
    jump_tables: List[JumpTable] = field(default_factory=list)
    riff_length: int = 0
    effective_riff_length: int = 0
    tolerated_riff_overflow: bool = False
    error: str = ""

@dataclass
class Hotspot:
    left: int
    top: int
    right: int
    bottom: int
    sid: int

@dataclass
class Zone:
    start: int
    end: int
    type: str
    source: str = "user"

@dataclass
class Insertion:
    id: int
    at: int
    text: str
    order: float

@dataclass
class ChoiceCollapse:
    start: int
    end: int
    target: int
    option: int

@dataclass
class CodeEdit:
    insertions: List[Insertion] = field(default_factory=list)
    collapses: Dict[int, ChoiceCollapse] = field(default_factory=dict)

@dataclass
class LogicalGroup:
    id: int
    name: str
    scripts: List[int]
    kind: str = "user"
    confirmed_scripts: List[int] = field(default_factory=list)

@dataclass
class SemanticBlock:
    at: float
    end: int
    kind: str
    title: str
    detail: List[str]
    confidence: str
    unknown: bool = False
    str_index: Optional[int] = None
    str_indices: Optional[List[int]] = None
    custom: bool = False
    custom_id: Optional[int] = None
    zone: str = ZONE_UNKNOWN
    zone_source: str = "generic"
    choice_targets: Optional[List[int]] = None
    choice_end: Optional[int] = None

@dataclass
class ChoiceInfo:
    start: int
    end: Optional[int]
    safe: bool
    targets: Optional[List[int]] = None
    switch_at: Optional[int] = None
    jump_index: Optional[int] = None


def u32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)

def i32(x: int) -> bytes:
    return struct.pack("<i", int(x))

def read_u16(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]

def read_u32(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]

def read_i32(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]

def align(x: int, a: int = 4) -> int:
    return (x + a - 1) & ~(a - 1)

def cap_bytes(v: int) -> int:
    return 128 * 1024 * (2 ** v)

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

def decode_text(raw: bytes) -> str:
    out: List[str] = []
    i = 0
    while i < len(raw):
        b = raw[i]
        i += 1
        if b == 0:
            break
        if b == 0x05 and i < len(raw) and raw[i] == 0x0C:
            out.append("{BOX}")
            i += 1
            continue
        if b == 0x0A:
            out.append("\n")
            continue
        if 0x20 <= b <= 0x7E:
            out.append(chr(b))
            continue
        if b in REV:
            out.append(REV[b])
            continue
        if b in (0xFF, 0x81) and i < len(raw):
            out.append(f"{{{b:02X} {raw[i]:02X}}}")
            i += 1
            continue
        out.append(f"{{{b:02X}}}")
    return "".join(out)

def encode_text(text: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(text):
        if text.startswith("{BOX}", i):
            out += b"\x05\x0C"
            i += 5
            continue
        if text[i] == "{":
            j = text.find("}", i + 1)
            if j > i:
                token = text[i+1:j].strip()
                parts = token.split()
                if 1 <= len(parts) <= 2 and all(len(q) == 2 and all(c in "0123456789abcdefABCDEF" for c in q) for q in parts):
                    out.extend(int(q, 16) for q in parts)
                    i = j + 1
                    continue
        ch = text[i]
        i += 1
        cp = ord(ch)
        if ch == "\n":
            out.append(0x0A)
        elif 0x20 <= cp <= 0x7E:
            out.append(cp)
        elif ch in PTBR:
            out.append(PTBR[ch])
        else:
            raise HMDSStudioError(f"Caractere não suportado pela fonte atual: {ch!r}")
    return bytes(out)

def parse_instructions(code: Optional[bytes]) -> List[Instruction]:
    if not code:
        return []
    out: List[Instruction] = []
    p = 0
    while p < len(code):
        at = p
        opbyte = code[p]
        p += 1
        if opbyte > 0x24:
            out.append(Instruction(at, "db", opbyte, 1, p))
            continue
        sz = SIZES[opbyte]
        if p + sz > len(code):
            out.append(Instruction(at, OPS[opbyte], None, 1 + sz, len(code), True))
            break
        arg: Optional[int] = None
        if sz == 1:
            arg = code[p]
        elif sz == 2:
            arg = struct.unpack_from("<H", code, p)[0]
        elif sz == 4:
            arg = struct.unpack_from("<I", code, p)[0]
        p += sz
        out.append(Instruction(at, OPS[opbyte], arg, 1 + sz, p))
    return out

def is_imm_push(ins: Optional[Instruction]) -> bool:
    return bool(ins and ins.op in ("push", "push16", "push8") and ins.arg is not None)

def parse_jump_tables(payload: bytes) -> List[JumpTable]:
    if len(payload) < 4:
        return []
    n = read_u32(payload, 0)
    if 4 + n * 4 > len(payload):
        return []
    offs = [read_u32(payload, 4 + i * 4) for i in range(n)]
    base = 4
    tables: List[JumpTable] = []
    for ti, off in enumerate(offs):
        p = base + off
        if p + 8 > len(payload):
            tables.append(JumpTable(ti, off, error="offset inválido"))
            continue
        count = read_u32(payload, p)
        default = read_i32(payload, p + 4)
        q = p + 8
        if q + count * 8 > len(payload):
            tables.append(JumpTable(ti, off, default, error="tabela truncada"))
            continue
        cases = []
        for _ in range(count):
            cases.append(JumpCase(read_u32(payload, q), read_u32(payload, q + 4)))
            q += 8
        tables.append(JumpTable(ti, off, default, cases))
    return tables

def build_jump_payload(tabs: List[JumpTable]) -> bytes:
    parts = []
    for t in tabs:
        part = bytearray()
        part += u32(len(t.cases))
        part += i32(t.default_target)
        for c in t.cases:
            part += u32(c.value)
            part += u32(c.target)
        parts.append(bytes(part))
    offs = []
    p = len(parts) * 4
    for part in parts:
        offs.append(p)
        p += len(part)
    return b"".join([u32(len(parts)), *(u32(o) for o in offs), *parts])

def push_imm(v: int) -> bytes:
    if v <= 0xFF:
        return bytes((0x23, v))
    if v <= 0xFFFF:
        return bytes((0x22,)) + struct.pack("<H", v)
    return bytes((0x17,)) + u32(v)

def show_text_code(idx: int) -> bytes:
    return push_imm(idx) + bytes((0x21,)) + u32(45)

def message_slot(text: str) -> bytes:
    return encode_text(text) + b"\x05\x00"

class EditSession:
    def __init__(self) -> None:
        self.string_edits: Dict[int, Dict[int, str]] = {}
        self.hotspot_edits: Dict[str, Hotspot] = {}
        self.code_edits: Dict[int, CodeEdit] = {}
        self.zone_overrides: Dict[int, List[Zone]] = {}
        self.groups: List[LogicalGroup] = []
        self.group_seq = 1
        self.edit_seq = 1
        self.clipboard: Optional[dict] = None
        self.reset_groups()

    def reset_groups(self) -> None:
        self.groups = []
        self.group_seq = 1
        for seed in CONFIRMED_GROUP_SEEDS:
            scripts = list(seed["scripts"])
            self.groups.append(LogicalGroup(self.group_seq, seed["name"], scripts, seed["kind"], list(scripts)))
            self.group_seq += 1

    def snapshot(self) -> dict:
        return {
            "string_edits": copy.deepcopy(self.string_edits),
            "hotspot_edits": {str(k): asdict(v) for k, v in self.hotspot_edits.items()},
            "code_edits": {
                sid: {
                    "insertions": [asdict(z) for z in e.insertions],
                    "collapses": {str(k): asdict(v) for k, v in e.collapses.items()},
                }
                for sid, e in self.code_edits.items()
            },
            "zone_overrides": {sid: [asdict(z) for z in zs] for sid, zs in self.zone_overrides.items()},
            "groups": [asdict(g) for g in self.groups],
            "group_seq": self.group_seq,
            "edit_seq": self.edit_seq,
            "clipboard": copy.deepcopy(self.clipboard),
        }

    def restore(self, data: dict) -> None:
        self.string_edits = {int(sid): {int(k): v for k, v in vals.items()} for sid, vals in data.get("string_edits", {}).items()}
        self.hotspot_edits = {str(k): Hotspot(**v) for k, v in data.get("hotspot_edits", {}).items()}
        self.code_edits = {}
        for sid, raw in data.get("code_edits", {}).items():
            ed = CodeEdit()
            ed.insertions = [Insertion(**z) for z in raw.get("insertions", [])]
            ed.collapses = {int(k): ChoiceCollapse(**v) for k, v in raw.get("collapses", {}).items()}
            self.code_edits[int(sid)] = ed
        self.zone_overrides = {int(sid): [Zone(**z) for z in zs] for sid, zs in data.get("zone_overrides", {}).items()}
        self.groups = [LogicalGroup(**g) for g in data.get("groups", [])]
        self.group_seq = int(data.get("group_seq", max([g.id for g in self.groups], default=0) + 1))
        self.edit_seq = int(data.get("edit_seq", 1))
        self.clipboard = copy.deepcopy(data.get("clipboard"))

    def changed_sids(self) -> set[int]:
        s = set(self.string_edits) | set(self.code_edits)
        return s



def _riff_magic_distance(magic: bytes) -> int:
    if len(magic) != 4:
        return 4
    return sum(a != b for a, b in zip(magic, b"RIFF"))

def _record_header_is_compatible(buf: bytes, off: int, limit: int) -> tuple[bool, bool]:
    """Validate a ScriptRecord header.

    Some real translated HMDS ROMs contain an isolated one-byte corruption in the
    literal RIFF magic while the pointer, RIFF length and SCR form remain valid.
    Treat exactly one differing RIFF byte as a tolerated header anomaly; stronger
    structural checks remain mandatory. Returns (compatible, tolerated_magic).
    """
    if off < 0 or off + 12 > len(buf) or limit <= off or limit > len(buf):
        return False, False
    magic = buf[off:off+4]
    tolerated = magic != b"RIFF"
    if tolerated and _riff_magic_distance(magic) != 1:
        return False, False
    riff_len = read_u32(buf, off + 4)
    if riff_len < 12 or off + riff_len > limit:
        return False, False
    if buf[off+8:off+12] != b"SCR ":
        return False, False
    return True, tolerated

def analyze_script_pointer_layout(script_s: bytes) -> dict:
    """Recognize the HMDS ScriptS pointer layout without assuming 1296 pointers.

    A compatible ScriptS has N ScriptRecords plus one extra pointer. Records are
    validated from the pointer table itself instead of stopping at the first byte
    sequence that is not exactly ``RIFF``. This matters for real PT-BR builds where
    an otherwise valid record may carry a one-byte RIFF-magic anomaly.
    """
    if len(script_s) < 12:
        raise HMDSStudioError("ScriptS pequeno demais.")
    pc = read_u32(script_s, 0)
    if pc < 2 or 4 + pc * 4 > len(script_s):
        raise HMDSStudioError(f"pointer_count inválido: {pc}")
    ptr = [read_u32(script_s, 4 + i * 4) for i in range(pc)]
    header = 4 + pc * 4
    if ptr[0] != header:
        raise HMDSStudioError("Header ScriptS inesperado.")

    # In this format the final pointer is the extra/tail pointer, therefore the
    # number of ScriptRecords is structurally pc-1. Validate every candidate.
    script_count = pc - 1
    if script_count < BASE_SCRIPT_COUNT:
        raise HMDSStudioError(
            f"ScriptS não pertence à família validada: somente {script_count} ScriptRecords declarados."
        )

    header_anomalies: List[int] = []
    for i in range(script_count):
        off = ptr[i]
        if off < header or off >= len(script_s):
            raise HMDSStudioError(f"Pointer do ScriptRecord {i} fora do ScriptS.")
        if i and off <= ptr[i-1]:
            raise HMDSStudioError("Pointer table ScriptS não é crescente.")
        if off & 3:
            raise HMDSStudioError(f"ScriptRecord {i} desalinhado em 4 bytes.")
        # For every record except the last, the next record pointer is a hard
        # upper bound. The last record may extend past an anomalous tail pointer.
        limit = ptr[i+1] if i < script_count - 1 else len(script_s)
        ok, tolerated = _record_header_is_compatible(script_s, off, limit)
        if not ok:
            raise HMDSStudioError(
                f"ScriptRecord {i} inválido: cabeçalho/RIFF/SCR não corresponde à família HMDS reconhecida."
            )
        if tolerated:
            header_anomalies.append(i)

    last = script_count - 1
    tail = ptr[-1]
    last_st = ptr[last]
    riff_len = read_u32(script_s, last_st + 4)
    riff_end = last_st + riff_len
    if riff_len < 12 or riff_end > len(script_s):
        raise HMDSStudioError("RIFF final inválido.")

    # The extra pointer is not uniform across all real/legacy HMDS builds.
    # We recognize three safe relations:
    #   1) exact EOF sentinel;
    #   2) the historical HMDS anomaly where the pointer lands inside the
    #      logical final RIFF;
    #   3) a normal record-end sentinel followed by preserved trailing bytes.
    # The RIFF length does not include up-to-3 alignment bytes, so never require
    # riff_end == len(script_s) exactly.
    aligned_riff_end = align(riff_end, 4)
    if aligned_riff_end > len(script_s):
        aligned_riff_end = len(script_s)

    if tail == len(script_s):
        tail_kind = "eof_sentinel"
        final_record_end = tail
        trailing_bytes = b""
    elif last_st < tail < riff_end:
        # Confirmed legacy anomaly: the extra pointer lands inside bytes that
        # still belong to the final RIFF/STR. The actual final record therefore
        # extends to the end of the ScriptS file.
        tail_kind = "final_record_anomaly"
        final_record_end = len(script_s)
        trailing_bytes = b""
    elif riff_end <= tail < len(script_s):
        # Some compatible/legacy builds use the extra pointer as the real end of
        # the final record but keep opaque bytes after it inside the FAT file.
        # They are not ScriptRecords; preserve them byte-exactly on rebuild.
        tail_kind = "record_end_with_trailing_data"
        final_record_end = tail
        trailing_bytes = script_s[tail:]
    else:
        raise HMDSStudioError(
            "Pointer extra do ScriptS está fora de uma relação estrutural segura com o último ScriptRecord."
        )

    return {
        "pointer_count": pc,
        "pointers": ptr,
        "script_count": script_count,
        "last_script_id": last,
        "tail_pointer": tail,
        "tail_kind": tail_kind,
        "final_riff_end": riff_end,
        "final_record_end": final_record_end,
        "trailing_bytes": trailing_bytes,
        "header_anomalies": header_anomalies,
    }

class RomModel:
    def __init__(self, data: bytes, source_path: Optional[Path] = None, strict_hash: bool = False, progress: Optional[Callable[[int,str],None]] = None, localization_only: bool = True) -> None:
        self.data = bytes(data)
        self.source_path = Path(source_path) if source_path else None
        self.localization_only = bool(localization_only)
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.is_reference_rom = self.sha256 == REF_SHA
        if strict_hash and not self.is_reference_rom:
            raise HMDSStudioError(
                "O modo estrito aceita somente a ROM PT-BR base validada do projeto.\n\n"
                f"SHA-256 esperado:\n{REF_SHA}\n\nSHA-256 recebido:\n{self.sha256}"
            )
        self.parsed: Dict[int, ScriptRecord] = {}
        self._progress_cb = progress
        self._map_info_cache: Dict[int, dict] = {}
        self._map_lz_cache: Dict[int, bytes] = {}
        self._report_progress(2, "Validando estrutura da ROM…")
        self._parse_rom()
        self._report_progress(40, "Estruturas principais carregadas.")

    def _report_progress(self, percent:int, message:str) -> None:
        cb = self._progress_cb
        if cb is not None:
            try:
                cb(max(0,min(100,int(percent))), message)
            except Exception:
                pass

    @classmethod
    def from_file(cls, path: str | Path, strict_hash: bool = False, progress: Optional[Callable[[int,str],None]] = None, localization_only: bool = True) -> "RomModel":
        p = Path(path)
        if progress:
            progress(0, "Lendo arquivo da ROM…")
        data = p.read_bytes()
        if progress:
            progress(2, f"ROM lida ({len(data)//(1024*1024)} MiB). Validando…")
        return cls(data, p, strict_hash=strict_hash, progress=progress, localization_only=localization_only)

    def _parse_rom(self) -> None:
        b = self.data
        if len(b) < 0x200:
            raise HMDSStudioError("Arquivo pequeno demais para ser uma ROM NDS válida.")
        self.game_code = b[0x0C:0x10].decode("ascii", "replace")
        self.arm_off = read_u32(b, 0x20)
        self.arm_ram = read_u32(b, 0x28)
        self.delta = self.arm_ram - self.arm_off
        self.fat = read_u32(b, 0x48)
        if self.fat <= 0 or self.fat + (SCRIPT_FILE_ID + 1) * 8 > len(b):
            raise HMDSStudioError("FAT NDS inválida ou incompleta.")
        fat_entry = self.fat + SCRIPT_FILE_ID * 8
        self.script_start = read_u32(b, fat_entry)
        self.script_end = read_u32(b, fat_entry + 4)
        if not (0 <= self.script_start < self.script_end <= len(b)):
            raise HMDSStudioError("FAT do ScriptS inválida.")
        self.script_s = b[self.script_start:self.script_end]

        layout = analyze_script_pointer_layout(self.script_s)
        self.pointer_count = layout["pointer_count"]
        self.ptr = layout["pointers"]
        self.script_count = layout["script_count"]
        self.last_script_id = layout["last_script_id"]
        self.tail_pointer = layout["tail_pointer"]
        self.tail_kind = layout["tail_kind"]
        self.final_riff_end = layout["final_riff_end"]
        self.final_record_end = layout.get("final_record_end", len(self.script_s))
        self.script_trailing_bytes = bytes(layout.get("trailing_bytes", b""))
        self.script_header_anomalies = list(layout.get("header_anomalies", []))

        if self.is_reference_rom:
            self.compatibility_level = "reference"
            self.compatibility_label = "ROM base PT-BR validada"
        elif self.script_count > BASE_SCRIPT_COUNT:
            self.compatibility_level = "expanded"
            self.compatibility_label = "HMDS modificado / ScriptS expandido reconhecido"
        else:
            self.compatibility_level = "structural"
            self.compatibility_label = "HMDS estruturalmente compatível"
        if self.script_header_anomalies:
            self.compatibility_label += f" · {len(self.script_header_anomalies)} cabeçalho RIFF tolerado" + ("" if len(self.script_header_anomalies) == 1 else "s")

        # Character Translation Studio profile: text/character editing deliberately
        # ignores maps, hotspots and scene objects. This allows localization work on
        # safe ScriptS-compatible descendants without coupling the translator to the
        # experimental RPG workspace.
        if self.localization_only:
            self.house_hotspots = {}
            self.house_table_offsets = {}
            self.hotspots = []
            self.physical_map_ids = []
            self.code_bearing = 0
            self.aligned_code_count = 0
            total_scripts = self.script_count
            self.tolerated_script_riff_overflows = []
            for sid in range(total_scripts):
                sc = self.parse_script(sid)
                if not sc.valid:
                    raise HMDSStudioError(f"Script {sid} inválido: {sc.error}")
                if sc.tolerated_riff_overflow:
                    self.tolerated_script_riff_overflows.append(sid)
                if sc.code is not None:
                    self.code_bearing += 1
                    if len(sc.code) % 4 != 0:
                        raise HMDSStudioError(f"Invariante CODE/4 violada no Script {sid}.")
                    self.aligned_code_count += 1
                if sid % 32 == 0 or sid == total_scripts - 1:
                    pct = 12 + int(28 * (sid + 1) / max(1, total_scripts))
                    self._report_progress(pct, f"Lendo textos/scripts… {sid+1}/{total_scripts}")
            if self.tolerated_script_riff_overflows:
                self.compatibility_label += f" · {len(self.tolerated_script_riff_overflows)} RIFF antigo tolerado" + ("" if len(self.tolerated_script_riff_overflows) == 1 else "s")
            self._report_progress(40, f"Banco de tradução carregado · {total_scripts} scripts.")
            return

        # The current compatibility profile intentionally keeps the official House-2
        # InteractionRect table in place. Legacy prototypes relocated it elsewhere;
        # those builds need a dedicated profile before write support is safe.
        for ref_off in (0x000EF7CC, 0x000EFA84):
            if ref_off + 4 > len(b) or read_u32(b, ref_off) != HOUSE_RAM:
                raise HMDSStudioError(
                    "ROM HMDS reconhecida parcialmente, mas a tabela de áreas de interação foi realocada. "
                    "Esta build ainda não possui um perfil de escrita seguro para essa variante."
                )

        self.house_off = HOUSE_RAM - self.delta
        self.house_hotspots: Dict[int, List[Hotspot]] = {}
        self.house_table_offsets: Dict[int, int] = {}
        for map_id, (state, ramaddr, expected_count, label) in HOUSE_VARIANTS.items():
            off = ramaddr - self.delta
            if off < 0 or off + 12 > len(b):
                raise HMDSStudioError(f"Tabela de interação Physical {map_id} fora da ROM.")
            self.house_table_offsets[map_id] = off
            rows=[]
            for i in range(64):
                p = off + i*12
                if p + 12 > len(b):
                    raise HMDSStudioError(f"Tabela de interação Physical {map_id} truncada.")
                sid = read_u32(b,p+8)
                if sid == 0xFFFFFFFF: break
                if sid >= self.script_count:
                    raise HMDSStudioError(
                        f"Área de interação Physical {map_id} referencia Script {sid}, fora da faixa 0..{self.last_script_id}."
                    )
                rows.append(Hotspot(read_u16(b,p),read_u16(b,p+2),read_u16(b,p+4),read_u16(b,p+6),sid))
            if len(rows) != expected_count:
                raise HMDSStudioError(f"Tabela de interação Physical {map_id} inesperada: {len(rows)}")
            self.house_hotspots[map_id]=rows
        self.hotspots = self.house_hotspots[76]  # compatibilidade interna
        self._report_progress(8, "Áreas de interação principais localizadas.")
        self._parse_nitrofs_and_maps()
        self._report_progress(12, f"NitroFS indexado · {len(self.physical_map_ids)} mapas físicos encontrados.")

        # Structural invariants confirmed for the compatible HMDS family.
        code_bearing = 0
        aligned = 0
        total_scripts = self.script_count
        for sid in range(total_scripts):
            sc = self.parse_script(sid)
            if sc.valid and sc.code is not None:
                code_bearing += 1
                if len(sc.code) % 4 == 0:
                    aligned += 1
                else:
                    raise HMDSStudioError(f"Invariante CODE/4 violada no Script {sid}.")
            if sid % 32 == 0 or sid == total_scripts-1:
                pct = 12 + int(28 * (sid + 1) / total_scripts)
                self._report_progress(pct, f"Lendo eventos/scripts… {sid+1}/{total_scripts}")
        self.code_bearing = code_bearing
        self.aligned_code_count = aligned

    def script_record_end(self, sid: int) -> int:
        if not (0 <= sid <= self.last_script_id):
            raise HMDSStudioError(f"Script ID fora da faixa: {sid}")
        if sid < self.last_script_id:
            return self.ptr[sid + 1]
        if self.tail_kind in ("eof_sentinel", "record_end_with_trailing_data"):
            return self.ptr[sid + 1]
        return len(self.script_s)

    def is_script_write_protected(self, sid: int) -> bool:
        return self.tail_kind == "final_record_anomaly" and sid == self.last_script_id

    def parse_script(self, sid: int) -> ScriptRecord:
        if sid in self.parsed:
            return self.parsed[sid]
        if not (0 <= sid <= self.last_script_id):
            raise HMDSStudioError(f"Script ID fora da faixa: {sid}")
        st = self.ptr[sid]
        en = self.script_record_end(sid)
        rec = self.script_s[st:en]
        o = ScriptRecord(sid, st, en, rec)
        try:
            if len(rec) < 12:
                raise HMDSStudioError("record curto")
            if rec[:4] != b"RIFF" and _riff_magic_distance(rec[:4]) != 1:
                raise HMDSStudioError("sem RIFF compatível")
            if rec[8:12] != b"SCR ":
                raise HMDSStudioError("sem SCR")
            riff = read_u32(rec, 4)
            o.riff_length = riff
            if riff < 12 or riff > len(rec):
                raise HMDSStudioError("RIFF length inválido")
            # Algumas traduções HMDS antigas mantêm a pointer table correta, mas
            # deixaram o tamanho RIFF alguns bytes curto. Para localização, o limite
            # físico seguro do ScriptRecord é o próximo ponteiro. Se um chunk começa
            # dentro do RIFF declarado e termina depois dele, mas ainda dentro do
            # próprio record, toleramos a divergência e usamos o record como limite.
            parse_limit = riff
            p = 12
            while p + 8 <= parse_limit:
                tag_raw = rec[p:p+4]
                tag = tag_raw.decode("latin1")
                ln = read_u32(rec, p + 4)
                chunk_end = p + 8 + ln
                if chunk_end > parse_limit:
                    if chunk_end <= len(rec):
                        o.tolerated_riff_overflow = True
                        parse_limit = len(rec)
                    else:
                        raise HMDSStudioError("chunk excede ScriptRecord")
                pay = rec[p+8:chunk_end]
                o.chunk_offsets[tag] = p
                o.chunks.append(Chunk(tag, pay))
                if tag == "CODE":
                    if len(pay) < 4:
                        raise HMDSStudioError("CODE curto")
                    cl = read_u32(pay, 0)
                    if 4 + cl > len(pay):
                        raise HMDSStudioError("CODE length inválido")
                    o.code = pay[4:4+cl]
                elif tag == "JUMP":
                    if len(pay) >= 4:
                        o.jump_count = read_u32(pay, 0)
                        o.jump_tables = parse_jump_tables(pay)
                elif tag == "STR ":
                    if len(pay) < 4:
                        raise HMDSStudioError("STR curto")
                    sc = read_u32(pay, 0)
                    if 4 + sc * 4 > len(pay):
                        raise HMDSStudioError("STR header inválido")
                    offs = [read_u32(pay, 4 + i * 4) for i in range(sc)]
                    bs = 4 + 4 * sc
                    blob = pay[bs:]
                    for i, off in enumerate(offs):
                        e = offs[i+1] if i+1 < sc else len(blob)
                        raw = blob[off:e]
                        o.raw_slots.append(raw)
                        o.strings.append(decode_text(raw))
                p = chunk_end
                # Pare em padding/cauda opaca depois dos chunks conhecidos. O parser
                # de tradução não precisa interpretar bytes não estruturais.
                if p < parse_limit and parse_limit == len(rec):
                    remaining = parse_limit - p
                    if remaining < 8:
                        break
                    nxt = rec[p:p+4]
                    if nxt not in (b"CODE", b"JUMP", b"STR "):
                        break
            o.effective_riff_length = parse_limit
            o.valid = True
        except Exception as exc:
            o.error = str(exc)
        self.parsed[sid] = o
        return o

    def _parse_nitrofs_and_maps(self) -> None:
        b=self.data
        self.fnt_off=read_u32(b,0x40); self.fnt_size=read_u32(b,0x44)
        fnt=b[self.fnt_off:self.fnt_off+self.fnt_size]
        root_sub=read_u32(fnt,0); dir_count=read_u16(fnt,6)
        dirs=[(read_u32(fnt,i*8),read_u16(fnt,i*8+4),read_u16(fnt,i*8+6)) for i in range(dir_count)]
        files={}
        def walk(did,path):
            sub,first,_=dirs[did-0xF000]; pos=sub; fid=first
            while True:
                ln=fnt[pos]; pos+=1
                if ln==0: break
                isdir=bool(ln&0x80); n=ln&0x7F; name=fnt[pos:pos+n].decode('ascii'); pos+=n
                if isdir:
                    child=read_u16(fnt,pos); pos+=2; walk(child,path+'/'+name if path else '/'+name)
                else:
                    files[path+'/'+name if path else '/'+name]=fid; fid+=1
        walk(0xF000,'')
        self.nitro_files=files
        self.nitro_file_ranges={}
        for n,fid0 in files.items():
            fp=self.fat+fid0*8
            if fp+8<=len(b):
                st0=read_u32(b,fp); en0=read_u32(b,fp+4)
                if 0<=st0<=en0<=len(b): self.nitro_file_ranges[n]=(st0,en0)
        fid=files.get('/map/map.bin')
        if fid is None: raise HMDSStudioError('map/map.bin não encontrado no NitroFS.')
        st=read_u32(b,self.fat+fid*8); en=read_u32(b,self.fat+fid*8+4)
        self.map_bin=b[st:en]
        first=read_u32(self.map_bin,0); self.map_ptr=[read_u32(self.map_bin,i*4) for i in range(first//4)]
        self.physical_map_ids=[i for i,p in enumerate(self.map_ptr) if p and p < len(self.map_bin)]

    def nitro_file_bytes(self, path:str)->bytes:
        key=path if path.startswith('/') else '/'+path
        rng=getattr(self,'nitro_file_ranges',{}).get(key)
        if rng is None:
            fid=getattr(self,'nitro_files',{}).get(key)
            if fid is None: raise HMDSStudioError(f'Arquivo NitroFS não encontrado: {key}')
            st=read_u32(self.data,self.fat+fid*8); en=read_u32(self.data,self.fat+fid*8+4)
        else: st,en=rng
        if not (0<=st<=en<=len(self.data)): raise HMDSStudioError(f'Faixa NitroFS inválida: {key}')
        return self.data[st:en]

    @classmethod
    def lz10_bytes(cls, data:bytes, start:int=0)->bytes:
        return cls._lz10(data,start)

    @staticmethod
    def _lz10(data:bytes, start:int)->bytes:
        if data[start] != 0x10: raise HMDSStudioError('Stream de mapa não é LZ10.')
        size=data[start+1]|(data[start+2]<<8)|(data[start+3]<<16); src=start+4; out=bytearray()
        while len(out)<size:
            flags=data[src]; src+=1
            for bit in range(7,-1,-1):
                if len(out)>=size: break
                if not flags&(1<<bit): out.append(data[src]); src+=1
                else:
                    a,b=data[src],data[src+1]; src+=2; ln=(a>>4)+3; disp=((a&15)<<8)|b; pos=len(out)-disp-1
                    for _ in range(ln): out.append(out[pos]); pos+=1
        return bytes(out)

    @staticmethod
    def _rgb555(v:int)->Tuple[int,int,int]:
        return ((v&31)*255//31, ((v>>5)&31)*255//31, ((v>>10)&31)*255//31)

    def map_info(self, map_id:int)->dict:
        if map_id in self._map_info_cache:
            return self._map_info_cache[map_id]
        if map_id not in self.physical_map_ids: raise HMDSStudioError(f'Physical Map {map_id} inexistente.')
        o=self.map_ptr[map_id]; m=self.map_bin
        w,h=m[o],m[o+1]; vals=struct.unpack_from('<7I',m,o+4)
        info=dict(id=map_id,width=w,height=h,gfx=vals[0],palette=vals[1],tilemap=vals[2],attr=vals[3],extra_start=vals[4],extra_end=vals[5],aux_count=vals[6])
        self._map_info_cache[map_id]=info
        return info

    def _map_lz_stream(self, start:int)->bytes:
        if start not in self._map_lz_cache:
            self._map_lz_cache[start]=self._lz10(self.map_bin,start)
        return self._map_lz_cache[start]

    def render_physical_map_ppm(self, map_id:int, frame:int=0)->bytes:
        info=self.map_info(map_id); m=self.map_bin; w,h=info['width'],info['height']
        gfx=self._map_lz_stream(info['gfx']); tmap=self._map_lz_stream(info['tilemap'])
        palraw=m[info['palette']:info['tilemap']]; frames=max(1,len(palraw)//0x3C0); frame=max(0,min(frame,frames-1)); pal=palraw[frame*0x3C0:(frame+1)*0x3C0]
        if len(pal)<0x3C0: pal=(pal+b'\x00'*0x3C0)[:0x3C0]
        normal=[self._rgb555(read_u16(pal,i)) for i in range(0,0x1C0,2)]
        ext=[self._rgb555(read_u16(pal,i)) for i in range(0x1C0,0x3C0,2)]
        entries=[read_u16(tmap,i*2) for i in range(len(tmap)//2)]
        W,H=w*8,h*8; rgb=bytearray(W*H*3)
        def put(x,y,c):
            q=(y*W+x)*3; rgb[q:q+3]=bytes(c)
        for layer in range(3):
            for ty in range(h):
                for tx in range(w):
                    e=entries[layer*w*h+ty*w+tx]; tid=e&0x3FF; xf=bool(e&0x400); yf=bool(e&0x800); p=(e>>12)&15
                    if layer==0:
                        td=gfx[tid*32:(tid+1)*32]; pix=[]
                        for z in td: pix.extend((z&15,z>>4))
                    else: pix=gfx[0x8000+tid*64:0x8000+(tid+1)*64]
                    for yy in range(8):
                        for xx in range(8):
                            sx=7-xx if xf else xx; sy=7-yy if yf else yy; pi=pix[sy*8+sx]
                            if layer>0 and pi==0: continue
                            if layer==0:
                                if p<2: continue
                                ci=(p-2)*16+pi
                                if ci>=len(normal): continue
                                col=normal[ci]
                            else:
                                ci=p*16+pi
                                if ci>=len(ext): continue
                                col=ext[ci]
                            put(tx*8+xx,ty*8+yy,col)
        return b'P6\n%d %d\n255\n'%(W,H)+bytes(rgb)

    def preload_physical_maps(self, map_ids:Optional[Iterable[int]]=None, progress:Optional[Callable[[int,int,int],None]]=None) -> Dict[int,bytes]:
        ids=list(self.physical_map_ids if map_ids is None else map_ids)
        cache:Dict[int,bytes]={}
        total=len(ids)
        for pos,mid in enumerate(ids,1):
            cache[mid]=self.render_physical_map_ppm(mid,0)
            if progress:
                try: progress(pos,total,mid)
                except Exception: pass
        return cache

    def move_custom_to(self, session:EditSession, source_sid:int, custom_id:int, target_sid:int, at:int, text_only:bool=True)->Insertion:
        e=session.code_edits.get(source_sid); z=next((x for x in (e.insertions if e else []) if x.id==custom_id),None)
        if not z: raise HMDSStudioError('Comando novo não encontrado.')
        self.ensure_visual_point(session,target_sid,at,allow_unknown=True)
        text=z.text; self.delete_custom(session,source_sid,custom_id)
        return self.add_text_command(session,target_sid,at,text,allow_unknown=True)

    def current_text(self, session: EditSession, sid: int, idx: int) -> str:
        return session.string_edits.get(sid, {}).get(idx, self.parse_script(sid).strings[idx])

    def set_string(self, session: EditSession, sid: int, idx: int, text: str) -> None:
        if self.is_script_write_protected(sid):
            raise HMDSStudioError(
                f"Script {sid} está protegido: o record final usa a anomalia de pointer ainda não fechada."
            )
        s = self.parse_script(sid)
        if not (0 <= idx < len(s.strings)):
            raise HMDSStudioError("Índice STR inválido.")
        encode_text(text)
        session.string_edits.setdefault(sid, {})[idx] = text

    def label(self, sid: int) -> str:
        return KNOWN_NAMES.get(sid, f"Script {sid}")

    @staticmethod
    def hotspot_key(map_id:int, index:int)->str:
        return f"{map_id}:{index}"

    def hotspot_rows(self, session: EditSession, map_id:int) -> List[Tuple[int,Hotspot]]:
        rows=self.house_hotspots.get(map_id,[])
        return [(i, session.hotspot_edits.get(self.hotspot_key(map_id,i), r)) for i,r in enumerate(rows)]

    def recs_for_sid(self, session: EditSession, sid: int) -> List[Tuple[str, Hotspot]]:
        out=[]
        for map_id, rows in self.house_hotspots.items():
            for i,r in enumerate(rows):
                rr=session.hotspot_edits.get(self.hotspot_key(map_id,i),r)
                if rr.sid==sid: out.append((self.hotspot_key(map_id,i),rr))
        return out

    def is_changed(self, session: EditSession, sid: int) -> bool:
        if sid in session.string_edits or sid in session.code_edits: return True
        for key, rr in session.hotspot_edits.items():
            try:
                map_id_s, idx_s = str(key).split(":",1); map_id=int(map_id_s); idx=int(idx_s)
            except Exception:
                map_id,idx=76,int(key)
            orig=self.house_hotspots.get(map_id,[])
            if idx < len(orig) and (orig[idx].sid==sid or rr.sid==sid): return True
        return False

    def str_preview(self, session: EditSession, sid: int, idx: int, limit: int = 90) -> str:
        s = self.parse_script(sid)
        if idx < 0 or idx >= len(s.strings):
            return f"STR {idx} (?)"
        v = self.current_text(session, sid, idx).replace("\n", " ↵ ")
        if len(v) > limit:
            v = v[:limit-3] + "…"
        return f"STR {idx}: “{v}”"

    def choice_info(self, sid: int) -> Dict[int, ChoiceInfo]:
        o = self.parse_script(sid)
        ins = parse_instructions(o.code)
        out: Dict[int, ChoiceInfo] = {}
        for i in range(4, len(ins)):
            x = ins[i]
            if not (x.op == "call" and x.arg == 54 and all(is_imm_push(ins[i-k]) for k in (1,2,3,4))):
                continue
            start = ins[i-4].at
            helper = ins[i+1] if i+1 < len(ins) else None
            after = ins[i+2] if i+2 < len(ins) else None
            info = ChoiceInfo(start, None, False)
            if helper and after and helper.op == "call" and helper.arg == 57 and after.op == "b":
                sw = next((z for z in ins if z.at == after.arg and z.op == "switch"), None)
                if sw and sw.arg is not None and 0 <= sw.arg < len(o.jump_tables):
                    jt = o.jump_tables[sw.arg]
                    c0 = next((c for c in jt.cases if c.value == 0), None)
                    c1 = next((c for c in jt.cases if c.value == 1), None)
                    if c0 and c1:
                        info = ChoiceInfo(start, after.end, True, [c0.target, c1.target], sw.at, sw.arg)
            out[start] = info
        return out

    def runtime_zones(self, sid: int) -> List[Zone]:
        o = self.parse_script(sid)
        zones: List[Zone] = []
        if sid in (522, 541):
            e = self.last_end_at(sid)
            zones.append(Zone(e, e+1, ZONE_SAFE_VISUAL, "runtime"))
        if sid in (521, 540):
            action = 0x136 if sid == 521 else 0x137
            ins = parse_instructions(o.code)
            ai = next((i for i, x in enumerate(ins) if x.op == "call" and x.arg == action), -1)
            if ai >= 0:
                b1 = None
                for i in range(ai - 1, -1, -1):
                    if ins[i].op == "call" and ins[i].arg == 0xB1:
                        b1 = ins[i]
                        break
                end = next((x for x in ins[ai:] if x.op == "end"), None)
                if b1 and end:
                    zones.append(Zone(b1.at, end.end, ZONE_ENGINE_TRANSITION, "runtime"))
        return zones

    def zone_at(self, session: EditSession, sid: int, at: int) -> Zone:
        all_zones = self.runtime_zones(sid) + session.zone_overrides.get(sid, [])
        hits = [z for z in all_zones if z.start <= at < z.end]
        if not hits:
            return Zone(at, at+1, ZONE_UNKNOWN, "generic")
        rank = {ZONE_ENGINE_TRANSITION: 3, ZONE_SAFE_LOGIC: 2, ZONE_SAFE_VISUAL: 1, ZONE_UNKNOWN: 0}
        return sorted(hits, key=lambda z: rank.get(z.type, 0), reverse=True)[0]

    def add_zone(self, session: EditSession, sid: int, start: int, end: int, zone_type: str) -> None:
        if zone_type not in ZONE_TYPES:
            raise HMDSStudioError("Tipo de zona inválido.")
        code_len = len(self.parse_script(sid).code or b"")
        if start < 0 or end <= start or end > code_len + 1:
            raise HMDSStudioError("Faixa de zona inválida.")
        session.zone_overrides.setdefault(sid, []).append(Zone(start, end, zone_type, "user"))

    def semantic_blocks(self, session: EditSession, sid: int) -> List[SemanticBlock]:
        o = self.parse_script(sid)
        ins = parse_instructions(o.code)
        blocks: List[SemanticBlock] = []
        consumed: set[int] = set()
        labels: set[int] = set()
        for x in ins:
            if x.op in BRANCHES and x.arg is not None:
                labels.add(x.arg)
        for jt in o.jump_tables:
            for c in jt.cases:
                labels.add(c.target)

        choices = self.choice_info(sid)
        for i in range(4, len(ins)):
            x = ins[i]
            if x.op == "call" and x.arg == 54 and all(is_imm_push(ins[i-k]) for k in (1,2,3,4)):
                a = [ins[i-4].arg, ins[i-3].arg, ins[i-2].arg, ins[i-1].arg]
                assert all(v is not None for v in a)
                detail = [
                    self.str_preview(session, sid, int(a[0])),
                    self.str_preview(session, sid, int(a[1])),
                    self.str_preview(session, sid, int(a[2])),
                    f"modo = {a[3]}",
                ]
                ci = choices.get(ins[i-4].at)
                blocks.append(SemanticBlock(
                    float(ins[i-4].at), x.end, "choice", "Escolha (2 opções)", detail, "confirmed",
                    str_indices=[int(a[0]), int(a[1]), int(a[2])],
                    choice_targets=list(ci.targets) if ci and ci.targets else None,
                    choice_end=ci.end if ci else None,
                ))
                consumed.update(range(i-4, i+1))

        for i in range(1, len(ins)):
            x = ins[i]
            if i in consumed or i-1 in consumed:
                continue
            if x.op == "call" and x.arg == 45 and is_imm_push(ins[i-1]):
                si = int(ins[i-1].arg or 0)
                blocks.append(SemanticBlock(
                    float(ins[i-1].at), x.end, "text", "Mostrar Texto",
                    [self.str_preview(session, sid, si)], "confirmed", str_index=si
                ))
                consumed.update((i-1, i))

        for i, x in enumerate(ins):
            if x.at in labels:
                blocks.append(SemanticBlock(x.at - 0.01, x.at, "label", f"LABEL_{x.at:04X}", [f"Destino CODE 0x{x.at:04X}"], "structural"))
            if i in consumed:
                continue
            if x.op == "call":
                if x.arg in CALL_NAMES:
                    name, conf = CALL_NAMES[int(x.arg)]
                    blocks.append(SemanticBlock(float(x.at), x.end, "native", name, [f"call 0x{int(x.arg):04X}"], conf))
                else:
                    blocks.append(SemanticBlock(float(x.at), x.end, "native", f"Native Call 0x{int(x.arg or 0):04X}", ["Sem nome semântico confirmado; ID físico preservado."], "structural", unknown=True))
            elif x.op == "switch":
                det = [f"JUMP table {x.arg}"]
                if x.arg is not None and 0 <= x.arg < len(o.jump_tables):
                    jt = o.jump_tables[x.arg]
                    if not jt.error:
                        det = [f"JUMP table {x.arg}: {len(jt.cases)} case(s)"] + [f"case {c.value} → 0x{c.target:04X}" for c in jt.cases[:12]]
                blocks.append(SemanticBlock(float(x.at), x.end, "ctrl", "Switch / seleção de ramo", det, "structural"))
            elif x.op in BRANCHES:
                blocks.append(SemanticBlock(float(x.at), x.end, "ctrl", f"Branch {x.op.upper()}", [f"destino → 0x{int(x.arg or 0):04X}"], "structural"))
            elif x.op == "cmp":
                blocks.append(SemanticBlock(float(x.at), x.end, "ctrl", "Comparação", ["cmp"], "structural"))
            elif x.op == "end":
                blocks.append(SemanticBlock(float(x.at), x.end, "end", "Encerrar fluxo", ["Fim deste fluxo da VM."], "confirmed"))

        ce = session.code_edits.get(sid)
        if ce:
            for z in ce.insertions:
                blocks.append(SemanticBlock(
                    z.at - 0.001 + ((z.order + 1) / 100000.0), z.at, "text", "Mostrar Texto (novo)",
                    [z.text], "confirmed", custom=True, custom_id=z.id
                ))

        for b in blocks:
            at = max(0, int(b.at))
            z = self.zone_at(session, sid, at)
            b.zone = z.type
            b.zone_source = z.source
        blocks.sort(key=lambda b: b.at)
        return blocks

    def branch_ranges(self, sid: int, choice: SemanticBlock) -> Optional[List[Tuple[int,int,int]]]:
        """Return [(case_value,start,end), ...] for a recognized binary choice.

        This is deliberately conservative and used for UI grouping only. The compiler
        itself relies on the JUMP targets/relocator, not these ranges.
        """
        if not choice.choice_targets or len(choice.choice_targets) != 2:
            return None
        o = self.parse_script(sid)
        ins = parse_instructions(o.code)
        t0, t1 = choice.choice_targets
        starts = sorted([(0, t0), (1, t1)], key=lambda x: x[1])
        first_val, first_start = starts[0]
        second_val, second_start = starts[1]
        first_end = second_start
        # Find a plausible common join from an unconditional branch in the first branch.
        join = None
        for x in ins:
            if first_start <= x.at < second_start and x.op == "b" and x.arg is not None and x.arg >= second_start:
                join = x.arg
        if join is None:
            # Otherwise stop the second branch at the first END at/after it.
            end_ins = next((x for x in ins if x.at >= second_start and x.op == "end"), None)
            join = end_ins.end if end_ins else len(o.code or b"")
        second_end = int(join)
        ranges = {
            first_val: (first_start, first_end),
            second_val: (second_start, second_end),
        }
        return [(0, *ranges[0]), (1, *ranges[1])]

    def last_end_at(self, sid: int) -> int:
        o = self.parse_script(sid)
        ins = parse_instructions(o.code)
        ends = [x for x in ins if x.op == "end"]
        if not ends:
            return len(o.code or b"")
        cur = ends[-1]
        for p in reversed(ends[:-1]):
            if p.end == cur.at:
                cur = p
            else:
                break
        return cur.at

    def ensure_visual_point(self, session: EditSession, sid: int, at: int, allow_unknown: bool = False) -> Zone:
        z = self.zone_at(session, sid, at)
        if z.type == ZONE_ENGINE_TRANSITION:
            raise HMDSStudioError("Ponto bloqueado: faz parte de uma transição interna confirmada da engine.")
        if z.type == ZONE_SAFE_LOGIC:
            raise HMDSStudioError("Ponto classificado como SAFE_LOGIC; diálogo/UI não deve ser inserido aqui.")
        if z.type == ZONE_UNKNOWN and not allow_unknown:
            raise HMDSStudioError("UNKNOWN_CONFIRM_REQUIRED")
        return z

    def _get_code_edit(self, session: EditSession, sid: int, create: bool = False) -> Optional[CodeEdit]:
        e = session.code_edits.get(sid)
        if e is None and create:
            e = CodeEdit()
            session.code_edits[sid] = e
        return e

    def _normalize_orders(self, e: CodeEdit, at: int) -> None:
        a = sorted([z for z in e.insertions if z.at == at], key=lambda z: (z.order, z.id))
        for i, z in enumerate(a):
            z.order = float(i)

    def add_text_command(self, session: EditSession, sid: int, at: int, text: str, *, allow_unknown: bool = False, before_id: Optional[int] = None, after_id: Optional[int] = None, skip_safety: bool = False) -> Insertion:
        if self.is_script_write_protected(sid):
            raise HMDSStudioError(
                f"Script {sid} está protegido: o record final possui estrutura anômala ainda não fechada."
            )
        encode_text(text)
        if not skip_safety:
            self.ensure_visual_point(session, sid, at, allow_unknown=allow_unknown)
        e = self._get_code_edit(session, sid, True)
        assert e is not None
        same = sorted([z for z in e.insertions if z.at == at], key=lambda z: (z.order, z.id))
        order = float(len(same))
        if before_id is not None:
            t = next((z for z in same if z.id == before_id), None)
            if t:
                order = t.order - 0.5
        elif after_id is not None:
            t = next((z for z in same if z.id == after_id), None)
            if t:
                order = t.order + 0.5
        ins = Insertion(session.edit_seq, at, text, order)
        session.edit_seq += 1
        e.insertions.append(ins)
        self._normalize_orders(e, at)
        return ins

    def edit_custom_text(self, session: EditSession, sid: int, custom_id: int, text: str) -> None:
        encode_text(text)
        e = session.code_edits.get(sid)
        if not e:
            raise HMDSStudioError("Comando novo não encontrado.")
        z = next((x for x in e.insertions if x.id == custom_id), None)
        if not z:
            raise HMDSStudioError("Comando novo não encontrado.")
        z.text = text

    def delete_custom(self, session: EditSession, sid: int, custom_id: int) -> None:
        e = session.code_edits.get(sid)
        if not e:
            return
        e.insertions = [z for z in e.insertions if z.id != custom_id]
        if not e.insertions and not e.collapses:
            session.code_edits.pop(sid, None)

    def move_custom(self, session: EditSession, sid: int, custom_id: int, delta: int) -> None:
        e = session.code_edits.get(sid)
        if not e:
            return
        z = next((x for x in e.insertions if x.id == custom_id), None)
        if not z:
            return
        same = sorted([x for x in e.insertions if x.at == z.at], key=lambda x: (x.order, x.id))
        i = same.index(z)
        j = i + delta
        if j < 0 or j >= len(same):
            return
        same[i].order, same[j].order = same[j].order, same[i].order
        self._normalize_orders(e, z.at)

    def duplicate_custom(self, session: EditSession, sid: int, custom_id: int) -> Optional[Insertion]:
        e = session.code_edits.get(sid)
        z = next((x for x in (e.insertions if e else []) if x.id == custom_id), None)
        if not z:
            return None
        return self.add_text_command(session, sid, z.at, z.text, after_id=z.id, skip_safety=True)

    def copy_custom(self, session: EditSession, sid: int, custom_id: int) -> None:
        e = session.code_edits.get(sid)
        z = next((x for x in (e.insertions if e else []) if x.id == custom_id), None)
        if z:
            session.clipboard = {"kind": "text", "text": z.text}

    def paste_text(self, session: EditSession, sid: int, at: int, allow_unknown: bool = False) -> Optional[Insertion]:
        if not session.clipboard or session.clipboard.get("kind") != "text":
            return None
        return self.add_text_command(session, sid, at, session.clipboard["text"], allow_unknown=allow_unknown)

    def collapse_choice(self, session: EditSession, sid: int, choice_start: int, option: int) -> None:
        ci = self.choice_info(sid).get(choice_start)
        if not ci or not ci.safe or not ci.targets or option not in (0,1):
            raise HMDSStudioError("Choice não pode ser fixada com segurança.")
        e = self._get_code_edit(session, sid, True)
        assert e is not None and ci.end is not None
        e.collapses[choice_start] = ChoiceCollapse(choice_start, ci.end, ci.targets[option], option)

    def restore_choice(self, session: EditSession, sid: int, choice_start: int) -> None:
        e = session.code_edits.get(sid)
        if not e:
            return
        e.collapses.pop(choice_start, None)
        if not e.insertions and not e.collapses:
            session.code_edits.pop(sid, None)

    def group_for_sid(self, session: EditSession, sid: int) -> Optional[LogicalGroup]:
        return next((g for g in session.groups if sid in g.scripts), None)

    def ensure_group(self, session: EditSession, sid: int) -> LogicalGroup:
        g = self.group_for_sid(session, sid)
        if g:
            return g
        g = LogicalGroup(session.group_seq, self.label(sid), [sid], "user", [])
        session.group_seq += 1
        session.groups.append(g)
        return g

    def link_continuation(self, session: EditSession, primary: int, target: int) -> LogicalGroup:
        if not (0 <= target <= self.last_script_id):
            raise HMDSStudioError("Script ID inválido.")
        if not self.parse_script(target).valid:
            raise HMDSStudioError(f"Script {target} não é parseável.")
        g = self.ensure_group(session, primary)
        old = self.group_for_sid(session, target)
        if old and old is not g:
            if old.kind == "confirmed":
                raise HMDSStudioError(f"Script {target} pertence a um evento runtime confirmado.")
            old.scripts = [x for x in old.scripts if x != target]
            if not old.scripts:
                session.groups.remove(old)
        if target not in g.scripts:
            g.scripts.append(target)
        return g

    def unlink_phase(self, session: EditSession, sid: int) -> None:
        g = self.group_for_sid(session, sid)
        if not g or g.scripts[0] == sid or sid in g.confirmed_scripts:
            return
        g.scripts.remove(sid)
        session.groups.append(LogicalGroup(session.group_seq, self.label(sid), [sid], "user", []))
        session.group_seq += 1

    def continuation_candidates(self, sid: int) -> List[dict]:
        out = []
        for d in range(1, 4):
            cand = sid + d
            if cand > self.last_script_id:
                break
            o = self.parse_script(cand)
            if not o.valid or not o.code:
                continue
            ins = parse_instructions(o.code)
            last = next((x for x in reversed(ins) if x.op != "nop"), None)
            score = 0
            reasons = []
            if len(o.code) <= 0x200:
                score += 2; reasons.append("CODE curto")
            if last and last.op == "end":
                score += 2; reasons.append("termina em END")
            if o.jump_count == 0:
                score += 1; reasons.append("sem JUMP")
            if score >= 3:
                out.append({"sid": cand, "score": score, "reasons": reasons})
        return sorted(out, key=lambda x: x["score"], reverse=True)

    def search_scripts(self, session: EditSession, query: str, mode: str = "hotspots") -> List[int]:
        q = query.strip().lower()
        if mode == "hotspots":
            ids = sorted(set(r.sid for rows in self.house_hotspots.values() for r in rows))
            # Include confirmed phases even if they have no direct hotspot.
            for g in session.groups:
                if g.kind == "confirmed":
                    ids.extend(g.scripts)
            ids = sorted(set(ids))
        elif mode == "semantic":
            ids = []
            for sid in range(self.script_count):
                o = self.parse_script(sid)
                if not o.valid:
                    continue
                blocks = self.semantic_blocks(session, sid)
                if any(b.kind in ("text", "choice") for b in blocks):
                    ids.append(sid)
        else:
            ids = list(range(self.script_count))
        if not q:
            return ids
        out = []
        for sid in ids:
            label = self.label(sid).lower()
            if q in str(sid) or q in label:
                out.append(sid); continue
            o = self.parse_script(sid)
            if any(q in self.current_text(session, sid, i).lower() for i in range(len(o.strings))):
                out.append(sid)
        return out

    def disassemble(self, sid: int) -> str:
        code = self.parse_script(sid).code
        if not code:
            return "Sem CODE parseável."
        lines = []
        for x in parse_instructions(code):
            s = f"{x.at:04X}: {x.op}"
            if x.arg is not None:
                width = max(2, (x.size - 1) * 2)
                s += f" 0x{x.arg:0{width}X}"
            lines.append(s)
        return "\n".join(lines)

    # ---------- rebuild ----------
    @staticmethod
    def _parse_chunks_raw(rec: bytes) -> Optional[dict]:
        if len(rec) < 12:
            return None
        if rec[:4] != b"RIFF" and _riff_magic_distance(rec[:4]) != 1:
            return None
        if rec[8:12] != b"SCR ":
            return None
        riff = read_u32(rec, 4)
        if riff < 12 or riff > len(rec):
            return None
        p = 12
        limit = riff
        m: Dict[str, Any] = {"_offsets": {}, "_riff": riff, "_tolerated_riff_overflow": False}
        while p + 8 <= limit:
            tag_raw = rec[p:p+4]
            tag = tag_raw.decode("latin1")
            ln = read_u32(rec, p+4)
            chunk_end = p + 8 + ln
            if chunk_end > limit:
                if chunk_end <= len(rec):
                    limit = len(rec)
                    m["_tolerated_riff_overflow"] = True
                else:
                    return None
            m["_offsets"][tag] = p
            m[tag] = rec[p+8:chunk_end]
            p = chunk_end
            if p < limit and limit == len(rec):
                if limit - p < 8:
                    break
                if rec[p:p+4] not in (b"CODE", b"JUMP", b"STR "):
                    break
        m["_effective_riff"] = limit
        return m

    @staticmethod
    def _make_aligned_record(chunks: List[Chunk]) -> bytes:
        chunks = [Chunk(c.tag, bytes(c.payload)) for c in chunks]
        str_idx = next((i for i, c in enumerate(chunks) if c.tag == "STR "), -1)
        body_len = 12 + sum(8 + len(c.payload) for c in chunks)
        pad = (4 - (body_len % 4)) % 4
        if pad:
            if str_idx < 0:
                raise HMDSStudioError("Não há STR para padding RIFF seguro.")
            chunks[str_idx].payload += bytes(pad)
        body = bytearray(b"RIFF" + b"\x00\x00\x00\x00" + b"SCR ")
        for c in chunks:
            body += c.tag.encode("latin1")
            body += u32(len(c.payload))
            body += c.payload
        struct.pack_into("<I", body, 4, len(body))
        if len(body) % 4:
            raise HMDSStudioError("Falha de alinhamento RIFF.")
        return bytes(body)

    @staticmethod
    def _edit_code_reloc(code: bytes, jump_tabs: List[JumpTable], insertions: List[dict], collapses: Dict[int, ChoiceCollapse]) -> Tuple[bytes, List[JumpTable], Dict[int,int]]:
        ins = parse_instructions(code)
        bounds = {x.at for x in ins} | {len(code)}
        for z in insertions:
            if z["at"] not in bounds:
                raise HMDSStudioError(f"Inserção em boundary inválido 0x{z['at']:04X}")
        reps = []
        for st, c in collapses.items():
            if st not in bounds or c.end not in bounds or c.end <= st:
                raise HMDSStudioError(f"Choice collapse inválido em 0x{st:04X}")
            reps.append((st, c.end, c.target))
        reps.sort()
        for i in range(1, len(reps)):
            if reps[i][0] < reps[i-1][1]:
                raise HMDSStudioError("Choices colapsados sobrepostos.")

        def inside(p: int) -> bool:
            return any(st < p < en for st, en, _ in reps)
        def rep_at(p: int):
            return next((r for r in reps if r[0] == p), None)

        groups: Dict[int, List[dict]] = {}
        for z in insertions:
            groups.setdefault(z["at"], []).append(z)
        for a in groups.values():
            a.sort(key=lambda z: (z.get("order", z["id"]), z["id"]))

        newpos = 0
        target_map: Dict[int, int] = {}
        segments = []
        i = 0
        while i < len(ins):
            x = ins[i]
            p = x.at
            if inside(p):
                i += 1
                continue
            target_map[p] = newpos
            for z in groups.get(p, []):
                segments.append(("insert", z))
                newpos += len(z["raw"])
            r = rep_at(p)
            if r:
                segments.append(("rep", r))
                newpos += 5
                while i < len(ins) and ins[i].at < r[1]:
                    i += 1
                continue
            segments.append(("inst", x))
            newpos += x.end - x.at
            i += 1
        target_map[len(code)] = newpos
        for z in groups.get(len(code), []):
            segments.append(("insert", z))
            newpos += len(z["raw"])

        targets = [int(x.arg) for x in ins if x.op in BRANCHES and x.arg is not None]
        for t in jump_tabs:
            if t.default_target >= 0:
                targets.append(t.default_target)
            targets.extend(c.target for c in t.cases)
        for t in targets:
            if t not in target_map:
                if inside(t):
                    raise HMDSStudioError(f"Safety Guard: destino 0x{t:04X} ficaria dentro de trecho removido.")
                raise HMDSStudioError(f"Safety Guard: destino desconhecido 0x{t:04X}")

        out = bytearray(newpos)
        p = 0
        for typ, seg in segments:
            if typ == "insert":
                raw = seg["raw"]
                out[p:p+len(raw)] = raw
                p += len(raw)
            elif typ == "rep":
                st, en, target = seg
                out[p] = OP_INDEX["b"]
                struct.pack_into("<I", out, p+1, target_map[target])
                p += 5
            else:
                x: Instruction = seg
                if x.op in BRANCHES:
                    out[p] = OP_INDEX[x.op]
                    struct.pack_into("<I", out, p+1, target_map[int(x.arg)])
                    p += 5
                else:
                    raw = code[x.at:x.end]
                    out[p:p+len(raw)] = raw
                    p += len(raw)
        nt = []
        for t in jump_tabs:
            nt.append(JumpTable(
                t.index, t.offset,
                target_map[t.default_target] if t.default_target >= 0 else t.default_target,
                [JumpCase(c.value, target_map[c.target]) for c in t.cases],
                t.error,
            ))
        return bytes(out), nt, target_map

    def _build_record(self, session: EditSession, sid: int) -> bytes:
        o = self.parse_script(sid)
        string_changes = session.string_edits.get(sid)
        ce = session.code_edits.get(sid)
        if not string_changes and ce is None:
            return o.rec
        if not o.valid:
            raise HMDSStudioError(f"Script {sid} não pode ser reconstruído: {o.error}")
        chunks = [Chunk(c.tag, c.payload) for c in o.chunks]
        si = next((i for i,c in enumerate(chunks) if c.tag == "STR "), -1)
        ci = next((i for i,c in enumerate(chunks) if c.tag == "CODE"), -1)
        ji = next((i for i,c in enumerate(chunks) if c.tag == "JUMP"), -1)
        if si < 0:
            raise HMDSStudioError(f"Script {sid} não possui STR.")
        if ci < 0 and ce is not None:
            raise HMDSStudioError(f"Script {sid} não possui CODE.")

        slots = list(o.raw_slots)
        if string_changes:
            for idx, text in string_changes.items():
                if idx < 0 or idx >= len(slots):
                    raise HMDSStudioError(f"String {idx} inválida no Script {sid}")
                slots[idx] = encode_text(text) + b"\x00"

        if ce is not None:
            sorted_ins = sorted(ce.insertions, key=lambda z: (z.at, z.order, z.id))
            compiled = []
            for z in sorted_ins:
                idx = len(slots)
                slots.append(message_slot(z.text))
                compiled.append({"id": z.id, "at": z.at, "order": z.order, "raw": show_text_code(idx), "str_index": idx})
            res_code, res_jumps, _ = self._edit_code_reloc(o.code or b"", o.jump_tables, compiled, ce.collapses)
            aligned_code = res_code + bytes(align(len(res_code), 4) - len(res_code))
            chunks[ci].payload = u32(len(aligned_code)) + aligned_code
            if ji >= 0:
                chunks[ji].payload = build_jump_payload(res_jumps)

        offs = []
        pos = 0
        for x in slots:
            offs.append(pos)
            pos += len(x)
        chunks[si].payload = b"".join([u32(len(slots)), *(u32(x) for x in offs), *slots])
        return self._make_aligned_record(chunks)

    @staticmethod
    def _bytes_eq(a: bytes, b: bytes) -> bool:
        return a == b

    def _validate_edited_record(self, rec: bytes, sid: int) -> None:
        c = self._parse_chunks_raw(rec)
        if not c or "CODE" not in c:
            raise HMDSStudioError(f"Safety Guard: Script {sid} sem CODE após rebuild.")
        pay = c["CODE"]
        if len(pay) < 4:
            raise HMDSStudioError("CODE curto")
        n = read_u32(pay, 0)
        if n % 4:
            raise HMDSStudioError(f"Safety Guard: CODE do Script {sid} não está alinhado em 4 bytes.")
        if "JUMP" in c["_offsets"] and c["_offsets"]["JUMP"] % 4:
            raise HMDSStudioError(f"Safety Guard: JUMP do Script {sid} desalinhado.")
        if c["_riff"] % 4 or len(rec) % 4:
            raise HMDSStudioError(f"Safety Guard: RIFF/record desalinhado no Script {sid}")
        code = pay[4:4+n]
        ins = parse_instructions(code)
        bounds = {x.at for x in ins} | {len(code)}
        for x in ins:
            if x.op in BRANCHES and x.arg not in bounds:
                raise HMDSStudioError(f"Safety Guard: branch inválido no Script {sid} @ 0x{x.at:04X}")
        if "JUMP" in c:
            for t in parse_jump_tables(c["JUMP"]):
                if t.error:
                    raise HMDSStudioError(f"JUMP inválido no Script {sid}")
                if t.default_target >= 0 and t.default_target not in bounds:
                    raise HMDSStudioError("JUMP default inválido")
                for q in t.cases:
                    if q.target not in bounds:
                        raise HMDSStudioError("JUMP target inválido")

    def _validate_records(self, session: EditSession, records: List[bytes]) -> dict:
        untouched = text_only = code_changed = 0
        for sid in range(self.script_count):
            orig = self.script_s[self.ptr[sid]:self.script_record_end(sid)]
            now = records[sid]
            ce = sid in session.code_edits
            se = sid in session.string_edits
            if not ce and not se:
                if orig != now:
                    raise HMDSStudioError(f"Safety Guard: Script não editado {sid} mudou.")
                untouched += 1
            elif not ce:
                a = self._parse_chunks_raw(orig) or {}
                b = self._parse_chunks_raw(now) or {}
                for tag in ("CODE", "JUMP"):
                    if a.get(tag, b"") != b.get(tag, b""):
                        raise HMDSStudioError(f"Safety Guard: {tag} mudou em edição só de texto no Script {sid}")
                text_only += 1
            else:
                self._validate_edited_record(now, sid)
                code_changed += 1
        return {"untouched": untouched, "textOnly": text_only, "codeChanged": code_changed}

    def build_rom(self, session: EditSession) -> Tuple[bytes, dict]:
        records = []
        for sid in range(self.script_count):
            if sid in session.string_edits or sid in session.code_edits:
                records.append(self._build_record(session, sid))
            else:
                records.append(self.script_s[self.ptr[sid]:self.script_record_end(sid)])
        guard = self._validate_records(session, records)

        header = 4 + self.pointer_count * 4
        new_ptr = []
        cur = header
        for sid in range(self.script_count):
            new_ptr.append(cur)
            cur += len(records[sid])
        if self.tail_kind in ("eof_sentinel", "record_end_with_trailing_data"):
            new_ptr.append(cur)
            tail_relation = "EOF" if self.tail_kind == "eof_sentinel" else "RECORD_END+TRAILING"
        else:
            rel_tail = self.tail_pointer - self.ptr[self.last_script_id]
            new_ptr.append(new_ptr[self.last_script_id] + rel_tail)
            tail_relation = rel_tail
        if len(new_ptr) != self.pointer_count:
            raise HMDSStudioError("Safety Guard: pointer_count mudou durante rebuild sem criação de Script.")
        new_ss = b"".join([u32(self.pointer_count), *(u32(x) for x in new_ptr), *records])
        if self.tail_kind == "record_end_with_trailing_data":
            new_ss += self.script_trailing_bytes

        # If ScriptS is already the trailing file (typical after a Studio build),
        # replace that trailing copy instead of appending another ~3 MiB orphan on
        # every open/edit/save cycle. Otherwise keep the conservative relocation.
        reuse_trailing_scripts = self.script_end == len(self.data)
        if reuse_trailing_scripts:
            stage = bytearray(self.data[:self.script_start])
            reloc = self.script_start
        else:
            stage = bytearray(self.data)
            reloc = align(len(stage), 0x200)

        for key, r in session.hotspot_edits.items():
            try: map_id_s, idx_s = str(key).split(":",1); map_id=int(map_id_s); idx=int(idx_s)
            except Exception: map_id,idx=76,int(key)
            if map_id not in self.house_table_offsets: raise HMDSStudioError(f"Área map {map_id} não suportada")
            p = self.house_table_offsets[map_id] + idx*12
            if p + 12 > len(stage):
                raise HMDSStudioError("Área de interação fica fora da região preservada da ROM.")
            struct.pack_into("<HHHHI", stage, p, r.left,r.top,r.right,r.bottom,r.sid)

        final = bytearray(reloc + len(new_ss))
        final[:len(stage)] = stage
        if len(stage) < reloc:
            final[len(stage):reloc] = b"\xFF" * (reloc - len(stage))
        final[reloc:] = new_ss
        fat_entry = self.fat + SCRIPT_FILE_ID * 8
        struct.pack_into("<II", final, fat_entry, reloc, reloc + len(new_ss))
        struct.pack_into("<I", final, 0x80, len(final))
        cap = final[0x14]
        while cap_bytes(cap) < len(final):
            cap += 1
        final[0x14] = cap
        struct.pack_into("<H", final, 0x15E, crc16(bytes(final[:0x15E])))

        # Extra invariant: preserve whichever tail convention this compatible ROM uses.
        guard["tailKind"] = self.tail_kind
        guard["tailRelation"] = tail_relation
        guard["pointerCount"] = self.pointer_count
        guard["scriptCount"] = self.script_count
        guard["newScriptSSize"] = len(new_ss)
        guard["reusedTrailingScriptS"] = reuse_trailing_scripts
        return bytes(final), guard

    # ---------- project ----------
    def save_project(self, session: EditSession, path: str | Path) -> None:
        payload = {
            "format": PROJECT_FORMAT,
            "app_version": APP_VERSION,
            "base_sha256": self.sha256,
            "source_rom": str(self.source_path) if self.source_path else "",
            "session": session.snapshot(),
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_project(self, session: EditSession, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        fmt=payload.get("format")
        if fmt not in (1, PROJECT_FORMAT): raise HMDSStudioError("Formato de projeto incompatível.")
        if fmt == 1:
            hs=payload.get("session",{}).get("hotspot_edits",{})
            payload["session"]["hotspot_edits"]={f"76:{k}":v for k,v in hs.items()}
        if payload.get("base_sha256") != self.sha256:
            raise HMDSStudioError("Este projeto pertence a outra ROM base.")
        session.restore(payload.get("session", {}))

    def project_summary(self, session: EditSession) -> str:
        return (
            f"{len(session.string_edits)} script(s) com texto físico editado · "
            f"{len(session.code_edits)} script(s) com comandos · "
            f"{len(session.hotspot_edits)} hotspot(s) · "
            f"{sum(1 for g in session.groups if len(g.scripts) > 1)} evento(s) composto(s)"
        )
