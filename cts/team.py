from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from . import __version__
from .validation import editor_to_raw, ensure_terminal_control, raw_to_editor

# Public exchange formats. Keep these constants stable so third-party tools can
# create/consume CTS files without importing the GUI.
DIALOGUE_FORMAT = "HMDS-CTS-DIALOGUE-1"
PACK_FORMAT = "HMDS-CTS-PACK-1"
DIALOGUE_EXTENSION = ".ctsdialogue"
PACK_EXTENSION = ".ctspack"
CSV_EXTENSION = ".csv"

# Older public formats remain readable for backwards compatibility.
EXCHANGE_FORMAT = "HMDS-CTS-EXCHANGE-1"
LEGACY_FORMAT = "HMDS-CTS-TEAM-1"

SUPPORTED_IMPORT_EXTENSIONS = (
    DIALOGUE_EXTENSION,
    PACK_EXTENSION,
    CSV_EXTENSION,
    ".ctsexport",
    ".ctsteam",
    ".json",
)


def text_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class TeamImportResult:
    applied: int = 0
    unchanged: int = 0
    incompatible: int = 0
    text_changes: int = 0
    status_changes: int = 0
    conflicts: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)

    def add_skip(self, row: dict, reason: str, *, category: str = "incompatible") -> None:
        self.incompatible += 1
        self.skipped.append({
            "script_id": row.get("script_id"),
            "string_index": row.get("string_index"),
            "entity_name": row.get("entity_name", ""),
            "reason": reason,
            "category": category,
        })


def make_entry(model, session, project, sid: int, idx: int, *, entity_id: str = "", entity_name: str = "") -> dict:
    """Create a self-contained snapshot for one physical Script/STR.

    Exchange files always include the current text, not only diffs. This makes
    review/import deterministic and lets external tools understand the file without
    needing the original project state.
    """
    sid = int(sid); idx = int(idx)
    script = model.parse_script(sid)
    if not script.valid or not (0 <= idx < len(script.strings)):
        raise ValueError(f"Script {sid} / STR {idx} não é uma fala válida.")
    original = script.strings[idx]
    current = model.current_text(session, sid, idx)
    edited = idx in session.string_edits.get(sid, {})
    return {
        "script_id": sid,
        "string_index": idx,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "original_sha256": text_fingerprint(original),
        "original": raw_to_editor(original),
        "translation": raw_to_editor(current),
        "has_translation": True,
        "modified": bool(edited or current != original),
        "status": project.status(sid, idx, session),
    }


def safe_make_entry(model, session, project, sid: int, idx: int, *, entity_id: str = "", entity_name: str = "") -> tuple[Optional[dict], Optional[str]]:
    """Non-throwing variant intended for bulk exports and third-party tools."""
    try:
        sid = int(sid); idx = int(idx)
        # The historical final-record anomaly is readable, but not safely writable.
        # Excluding it from collaboration exports prevents another translator from
        # receiving an entry that cannot be applied back to that ROM family.
        if hasattr(model, "is_script_write_protected") and model.is_script_write_protected(sid):
            return None, f"Script {sid} está protegido nesta ROM e foi omitido."
        return make_entry(model, session, project, sid, idx, entity_id=entity_id, entity_name=entity_name), None
    except Exception as exc:
        return None, str(exc)


def _base_payload(model, entries: Iterable[dict], *, scope: Optional[dict] = None, omitted: Optional[List[dict]] = None) -> dict:
    return {
        "app_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "author": "Atm",
        "game_code": model.game_code,
        "script_count": model.script_count,
        "base_sha256": model.sha256,
        "scope": scope or {"type": "custom"},
        "entries": list(entries),
        "omitted": list(omitted or []),
    }


def write_dialogue_file(path: str | Path, model, entry: dict, *, scope: Optional[dict] = None) -> Path:
    p = Path(path)
    payload = _base_payload(model, [entry], scope=scope or {
        "type": "dialogue",
        "script_id": int(entry.get("script_id", -1)),
        "string_index": int(entry.get("string_index", -1)),
    })
    payload["format"] = DIALOGUE_FORMAT
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_pack_file(path: str | Path, model, entries: Iterable[dict], *, scope: Optional[dict] = None, omitted: Optional[List[dict]] = None) -> Path:
    p = Path(path)
    payload = _base_payload(model, entries, scope=scope, omitted=omitted)
    payload["format"] = PACK_FORMAT
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_team_package(path: str | Path, model, entries: Iterable[dict], *, translator: str = "", scope: Optional[dict] = None) -> Path:
    """Backward-compatible alias retained for integrations built on older CTS."""
    rows = list(entries)
    if len(rows) == 1 and (scope or {}).get("type") == "dialogue":
        return write_dialogue_file(path, model, rows[0], scope=scope)
    return write_pack_file(path, model, rows, scope=scope)


def read_team_package(path: str | Path) -> dict:
    p = Path(path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    fmt = payload.get("format")
    if fmt not in {DIALOGUE_FORMAT, PACK_FORMAT, EXCHANGE_FORMAT, LEGACY_FORMAT}:
        raise ValueError("Pacote de exportação incompatível.")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Pacote sem lista de falas.")
    if fmt == DIALOGUE_FORMAT and len(entries) != 1:
        raise ValueError("Arquivo .ctsdialogue inválido: ele deve conter exatamente uma fala.")
    for row in entries:
        if "has_translation" not in row:
            row["has_translation"] = bool(str(row.get("translation") or ""))
    return payload


def write_csv(path: str | Path, entries: Iterable[dict], *, translator: str = "") -> Path:
    p = Path(path)
    fields = [
        "entity_name", "entity_id", "script_id", "string_index", "original_sha256",
        "original", "translation", "status",
    ]
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        for row0 in entries:
            wr.writerow({k: row0.get(k, "") for k in fields})
    return p


def read_csv(path: str | Path) -> List[dict]:
    p = Path(path)
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    required = {"script_id", "string_index", "original_sha256", "translation"}
    if not rows:
        return []
    if not required.issubset(set(rows[0].keys())):
        raise ValueError("CSV não possui as colunas obrigatórias do Character Translation Studio.")
    out = []
    for r in rows:
        try:
            sid = int(r.get("script_id", "")); idx = int(r.get("string_index", ""))
        except Exception:
            continue
        out.append({
            "script_id": sid,
            "string_index": idx,
            "entity_id": r.get("entity_id", ""),
            "entity_name": r.get("entity_name", ""),
            "original_sha256": r.get("original_sha256", ""),
            "original": r.get("original", ""),
            "translation": r.get("translation", ""),
            "has_translation": True,
            "status": r.get("status", ""),
        })
    return out


def read_exchange_file(path: str | Path) -> dict:
    """Read any supported CTS exchange file and normalize it to a payload dict."""
    p = Path(path)
    if p.suffix.lower() == CSV_EXTENSION:
        return {
            "format": "HMDS-CTS-CSV-1",
            "game_code": "",
            "scope": {"type": "csv"},
            "entries": read_csv(p),
            "omitted": [],
        }
    return read_team_package(p)


def import_entries(model, session, project, entries: Iterable[dict], *, overwrite_conflicts: bool = False) -> TeamImportResult:
    """Apply entries independently; one bad/protected row never aborts the batch."""
    result = TeamImportResult()
    valid_statuses = {"untranslated", "translated", "review", "reviewed", "ignored"}

    for row in entries:
        try:
            sid = int(row.get("script_id")); idx = int(row.get("string_index"))
            script = model.parse_script(sid)
            if not script.valid or not (0 <= idx < len(script.strings)):
                result.add_skip(row, "Script/STR não existe nesta ROM.")
                continue

            original = script.strings[idx]
            expected = str(row.get("original_sha256") or "")
            if expected and expected != text_fingerprint(original):
                result.add_skip(row, "O texto-base é diferente nesta ROM.")
                continue

            has_translation = bool(row.get("has_translation", "translation" in row))
            incoming_raw = None
            if has_translation:
                incoming_friendly = str(row.get("translation") or "")
                incoming_raw = ensure_terminal_control(original, editor_to_raw(incoming_friendly))

            local_raw = model.current_text(session, sid, idx)
            local_edit = session.string_edits.get(sid, {}).get(idx)
            local_status = project.status(sid, idx, session)
            incoming_status = str(row.get("status") or "").strip()

            # A protected final ScriptRecord can still be inspected. Only skip the row
            # when the incoming file would actually change its physical text.
            if (incoming_raw is not None and incoming_raw != local_raw and
                    hasattr(model, "is_script_write_protected") and model.is_script_write_protected(sid)):
                result.add_skip(row, f"Script {sid} é somente leitura nesta ROM por causa da anomalia do record final.", category="protected")
                continue

            row_changed = False

            if incoming_raw is not None:
                if local_edit is not None and local_raw != incoming_raw and not overwrite_conflicts:
                    result.conflicts.append({
                        "script_id": sid,
                        "string_index": idx,
                        "local": raw_to_editor(local_raw),
                        "incoming": raw_to_editor(incoming_raw),
                        "entity_name": row.get("entity_name", ""),
                    })
                    continue

                if incoming_raw != local_raw:
                    try:
                        if incoming_raw == original:
                            edits = session.string_edits.get(sid)
                            if edits and idx in edits:
                                edits.pop(idx, None)
                                if not edits:
                                    session.string_edits.pop(sid, None)
                        else:
                            model.set_string(session, sid, idx, incoming_raw)
                    except Exception as exc:
                        result.add_skip(row, f"Não foi possível aplicar o texto: {exc}", category="write_error")
                        continue
                    row_changed = True
                    result.text_changes += 1

            if incoming_status in valid_statuses and incoming_status != local_status:
                project.set_status(sid, idx, incoming_status)
                row_changed = True
                result.status_changes += 1

            if row_changed:
                result.applied += 1
            else:
                result.unchanged += 1
        except Exception as exc:
            # Corrupted/unsupported entries are isolated to this row. Bulk imports
            # continue and the UI can tell the user what was skipped.
            result.add_skip(row, str(exc), category="error")

    return result
