"""Public, UI-free API for building tools on top of HMDS CTS.

This module is the recommended integration surface for scripts and third-party tools.
UI internals may change between releases; the names exported here are intended to stay
small and predictable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .engine import EditSession, HMDSStudioError, RomModel
from .entities import EntityIndex, EntityRef
from .project import TranslationProject
from .team import (
    DIALOGUE_EXTENSION,
    PACK_EXTENSION,
    TeamImportResult,
    import_entries,
    make_entry,
    read_exchange_file,
    safe_make_entry,
    write_dialogue_file,
    write_pack_file,
)
from .validation import editor_to_raw, ensure_terminal_control, raw_to_editor, validate_translation

SUPPORTED_GAME_CODES = {"ABCP"}


@dataclass(frozen=True)
class DialogueSnapshot:
    script_id: int
    string_index: int
    original_raw: str
    current_raw: str
    original_text: str
    current_text: str
    status: str
    write_protected: bool


class Workspace:
    """A ROM + edit session + project metadata, without any Tkinter dependency."""

    def __init__(self, model: RomModel, *, entities: Optional[EntityIndex] = None):
        self.model = model
        self.session = EditSession()
        self.project = TranslationProject()
        self.entities = entities or EntityIndex()

    @classmethod
    def open(cls, path: str | Path, *, progress: Optional[Callable[[int, str], None]] = None,
             require_supported_game: bool = True) -> "Workspace":
        model = RomModel.from_file(path, localization_only=True, progress=progress)
        if require_supported_game and model.game_code not in SUPPORTED_GAME_CODES:
            raise HMDSStudioError(f"Game Code não suportado pelo perfil público atual: {model.game_code}")
        return cls(model)

    def characters(self, *, with_dialogues_only: bool = False) -> list[EntityRef]:
        return self.entities.entities(with_dialogues_only=with_dialogues_only)

    def dialogue_rows(self, entity_id: str, *, unique: bool = True) -> list[dict]:
        return self.entities.dialogues(entity_id, unique=unique)

    def get_dialogue(self, script_id: int, string_index: int) -> DialogueSnapshot:
        sid = int(script_id); idx = int(string_index)
        script = self.model.parse_script(sid)
        if not script.valid or not (0 <= idx < len(script.strings)):
            raise HMDSStudioError(f"Script {sid} / STR {idx} não existe.")
        original = script.strings[idx]
        current = self.model.current_text(self.session, sid, idx)
        return DialogueSnapshot(
            script_id=sid,
            string_index=idx,
            original_raw=original,
            current_raw=current,
            original_text=raw_to_editor(original),
            current_text=raw_to_editor(current),
            status=self.project.status(sid, idx, self.session),
            write_protected=bool(self.model.is_script_write_protected(sid)),
        )

    def set_dialogue(self, script_id: int, string_index: int, text: str, *, status: Optional[str] = None,
                     friendly_text: bool = True, protect_controls: bool = True) -> DialogueSnapshot:
        sid = int(script_id); idx = int(string_index)
        snap = self.get_dialogue(sid, idx)
        raw = editor_to_raw(text) if friendly_text else str(text)
        raw = ensure_terminal_control(snap.original_raw, raw)
        validation = validate_translation(snap.original_raw, raw, protect_controls=protect_controls)
        errors = [m.message for m in validation.messages if m.level == "error"]
        if errors:
            raise HMDSStudioError("\n".join(errors))
        if raw == snap.original_raw:
            edits = self.session.string_edits.get(sid)
            if edits and idx in edits:
                edits.pop(idx, None)
                if not edits:
                    self.session.string_edits.pop(sid, None)
        elif raw != snap.current_raw:
            self.model.set_string(self.session, sid, idx, raw)
        if status:
            self.project.set_status(sid, idx, status)
        return self.get_dialogue(sid, idx)

    def export_dialogue(self, path: str | Path, script_id: int, string_index: int,
                        *, entity_id: str = "", entity_name: str = "") -> Path:
        entry, reason = safe_make_entry(
            self.model, self.session, self.project, script_id, string_index,
            entity_id=entity_id, entity_name=entity_name,
        )
        if entry is None:
            raise HMDSStudioError(reason or "A fala não pode ser exportada com segurança.")
        return write_dialogue_file(path, self.model, entry)

    def export_character(self, path: str | Path, entity_id: str) -> tuple[Path, list[dict]]:
        entity = next((e for e in self.entities.entities(with_dialogues_only=False) if e.entity_id == entity_id), None)
        if entity is None:
            raise HMDSStudioError(f"Entidade não encontrada: {entity_id}")
        entries = []
        omitted = []
        seen = set()
        for row in self.entities.dialogues(entity_id, unique=True):
            sid, idx = int(row["script_id"]), int(row["string_index"])
            if (sid, idx) in seen:
                continue
            seen.add((sid, idx))
            entry, reason = safe_make_entry(
                self.model, self.session, self.project, sid, idx,
                entity_id=entity.entity_id, entity_name=entity.display_name,
            )
            if entry is None:
                omitted.append({"script_id": sid, "string_index": idx, "reason": reason or "omitida"})
            else:
                entries.append(entry)
        return write_pack_file(path, self.model, entries, scope={"type": "character", "entity_id": entity_id}, omitted=omitted), omitted

    def import_file(self, path: str | Path, *, overwrite_conflicts: bool = True) -> TeamImportResult:
        payload = read_exchange_file(path)
        game_code = str(payload.get("game_code") or "")
        if game_code and game_code != self.model.game_code:
            raise HMDSStudioError(f"Arquivo para outro Game Code: {game_code}")
        return import_entries(
            self.model, self.session, self.project,
            payload.get("entries", []), overwrite_conflicts=overwrite_conflicts,
        )

    def build_rom(self, path: str | Path) -> Path:
        out = Path(path)
        if self.model.source_path and out.resolve() == self.model.source_path.resolve():
            raise HMDSStudioError("A ROM original não pode ser sobrescrita.")
        data, _guard = self.model.build_rom(self.session)
        out.write_bytes(data)
        # Re-open before reporting success.
        verify = RomModel(data, out, localization_only=True)
        if verify.script_count != self.model.script_count:
            raise HMDSStudioError("A ROM gerada não preservou a contagem de scripts.")
        return out


__all__ = [
    "Workspace",
    "DialogueSnapshot",
    "SUPPORTED_GAME_CODES",
    "DIALOGUE_EXTENSION",
    "PACK_EXTENSION",
    "TeamImportResult",
]
