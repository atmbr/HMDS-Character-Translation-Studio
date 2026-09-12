from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from . import __version__
from .engine import EditSession, HMDSStudioError

PROJECT_FORMAT = "HMDS-CTS-PROJECT-1"


class TranslationProject:
    def __init__(self) -> None:
        self.statuses: Dict[str, str] = {}
        self.target_language = "pt-BR"
        self.path: Optional[Path] = None

    @staticmethod
    def key(sid: int, idx: int) -> str:
        return f"{int(sid)}:{int(idx)}"

    def status(self, sid: int, idx: int, session: EditSession) -> str:
        key = self.key(sid, idx)
        if key in self.statuses:
            return self.statuses[key]
        if idx in session.string_edits.get(sid, {}):
            return "translated"
        return "untranslated"

    def set_status(self, sid: int, idx: int, status: str) -> None:
        self.statuses[self.key(sid, idx)] = status

    # v0.3 compatibility: callers/importers may ask for notes, but v0.4 does not expose them.
    def note(self, sid: int, idx: int) -> str:
        return ""

    def set_note(self, sid: int, idx: int, note: str) -> None:
        return None

    def save(self, model, session: EditSession, path: str | Path) -> None:
        p = Path(path)
        payload = {
            "format": PROJECT_FORMAT,
            "app_version": __version__,
            "base_sha256": model.sha256,
            "game_code": model.game_code,
            "source_rom": str(model.source_path or ""),
            "target_language": self.target_language,
            "statuses": self.statuses,
            "session": session.snapshot(),
            "author": "Atm",
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.path = p

    def load(self, model, session: EditSession, path: str | Path) -> None:
        p = Path(path)
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("format") != PROJECT_FORMAT:
            raise HMDSStudioError("Projeto incompatível com o Character Translation Studio.")
        if payload.get("base_sha256") != model.sha256:
            raise HMDSStudioError("Este projeto foi criado para outra ROM base.")
        session.restore(payload.get("session", {}))
        session.hotspot_edits = {}
        session.code_edits = {}
        session.zone_overrides = {}
        self.statuses = dict(payload.get("statuses", {}))
        self.target_language = str(payload.get("target_language", "pt-BR"))
        self.path = p
