from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class EntityRef:
    entity_id: str
    entity_type: str
    display_name: str
    character_id: Optional[int] = None
    dialogue_count: int = 0
    script_count: int = 0
    preferred_thumbnail: Optional[Path] = None
    name_source: str = ""


class EntityIndex:
    """Read-only character/dialogue metadata with hot-path caches.

    The entity metadata comes from the validated CallGraph research.  ROM strings are
    still read from the currently opened ROM.  v0.6 builds lookup tables once at
    startup so selecting a dialogue no longer scans every character/row.
    """

    SCHEMA_PREFIX = "0.16.2"

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or Path(__file__).with_name("data")
        self.registry_raw = self._load("entity_registry.json")
        self.dialogue_raw = self._load("entity_dialogue_index.json")
        self.relations_raw = self._load("entity_relations.json")
        self.summary_raw = self._load("entity_summary.json")
        self._entities: Dict[str, dict] = {e["entity_id"]: e for e in self.registry_raw.get("entities", [])}
        self._dialogues: Dict[str, List[dict]] = dict(self.dialogue_raw.get("entities", {}))

        self._scripts_to_entities: Dict[int, List[str]] = {}
        self._unique_dialogues: Dict[str, List[dict]] = {}
        self._contexts_by_dialogue: Dict[tuple[int, int], List[dict]] = {}
        self._entity_refs_all: List[EntityRef] = []
        self._build_indexes()

    def _load(self, name: str) -> dict:
        p = self.data_dir / name
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def _build_indexes(self) -> None:
        # Unique physical STR rows per entity.  This used to be recomputed every
        # refresh and was noticeable on characters with hundreds of dialogues.
        for entity_id, e in self._entities.items():
            seen: Dict[tuple[int, int], dict] = {}
            for row in self._dialogues.get(entity_id, []):
                key = (int(row.get("script_id", -1)), int(row.get("string_index", -1)))
                if key not in seen:
                    seen[key] = dict(row)
                else:
                    dst = seen[key]
                    for field in ("face_ids", "expressions", "sides"):
                        vals = list(dst.get(field) or [])
                        for value in (row.get(field) or []):
                            if value not in vals:
                                vals.append(value)
                        dst[field] = vals
            self._unique_dialogues[entity_id] = sorted(
                seen.values(), key=lambda x: (int(x.get("script_id", 0)), int(x.get("string_index", 0)))
            )

            for sid in e.get("dialogue_usage", {}).get("scripts", []):
                self._scripts_to_entities.setdefault(int(sid), []).append(entity_id)

            use = e.get("dialogue_usage", {})
            cid = e.get("ids", {}).get("character_id")
            name = e.get("display_name", e["entity_id"])
            thumb = self.data_dir / "entity_thumbs" / f"cid_{int(cid):03d}.png" if cid is not None else None
            if thumb is not None and not thumb.exists():
                thumb = None
            self._entity_refs_all.append(EntityRef(
                entity_id=e["entity_id"],
                entity_type=e.get("entity_type", "unknown"),
                display_name=name,
                character_id=int(cid) if cid is not None else None,
                dialogue_count=len(self._unique_dialogues[entity_id]),
                script_count=int(use.get("script_count", 0)),
                preferred_thumbnail=thumb,
                name_source=e.get("name_source", ""),
            ))

        self._entity_refs_all.sort(
            key=lambda z: (0 if not z.display_name.startswith("Character CID") else 1, z.display_name.casefold(), z.character_id or -1)
        )

        # Direct physical dialogue -> visual contexts index.  This removes an O(all
        # dialogue rows) scan from every click / every live preview refresh.
        temp: Dict[tuple[int, int], List[dict]] = {}
        for entity_id, rows in self._dialogues.items():
            e = self._entities.get(entity_id, {})
            for d in rows:
                key = (int(d.get("script_id", -1)), int(d.get("string_index", -1)))
                row = dict(d)
                row["entity_id"] = entity_id
                row["display_name"] = e.get("display_name", entity_id)
                row["character_id"] = e.get("ids", {}).get("character_id")
                row["name_source"] = e.get("name_source", "")
                temp.setdefault(key, []).append(row)

        for key, rows in temp.items():
            unique: List[dict] = []
            seen = set()
            for r in rows:
                sig = (
                    r.get("entity_id"),
                    tuple(r.get("face_ids") or []),
                    tuple(r.get("expressions") or []),
                    tuple(r.get("sides") or []),
                )
                if sig in seen:
                    continue
                seen.add(sig)
                unique.append(r)
            unique.sort(key=self._context_sort_key)
            self._contexts_by_dialogue[key] = unique

    @staticmethod
    def _context_sort_key(r: dict):
        sides = r.get("sides") or []
        side = sides[0] if sides else ""
        return (0 if side == "left" else 1 if side == "right" else 2, int(r.get("character_id") or 9999))

    @property
    def available(self) -> bool:
        return bool(self._entities)

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    def compatible_with_model(self, model) -> bool:
        if not self.available or model is None:
            return False
        max_sid = -1
        for e in self._entities.values():
            scripts = e.get("dialogue_usage", {}).get("scripts", [])
            if scripts:
                max_sid = max(max_sid, max(int(x) for x in scripts))
        return getattr(model, "last_script_id", -1) >= max_sid

    def entities(self, query: str = "", with_dialogues_only: bool = True) -> List[EntityRef]:
        q = query.strip().casefold()
        out: List[EntityRef] = []
        for ref in self._entity_refs_all:
            if with_dialogues_only and ref.dialogue_count <= 0:
                continue
            if q:
                hay = f"{ref.entity_id} {ref.display_name} CID {ref.character_id}".casefold()
                if q not in hay:
                    continue
            out.append(ref)
        return out

    def entity_ref(self, entity_id: str) -> Optional[EntityRef]:
        return next((r for r in self._entity_refs_all if r.entity_id == entity_id), None)

    def entity(self, entity_id: str) -> Optional[dict]:
        return self._entities.get(entity_id)

    def dialogues(self, entity_id: str, *, unique: bool = False) -> List[dict]:
        if unique:
            return list(self._unique_dialogues.get(entity_id, []))
        return list(self._dialogues.get(entity_id, []))

    def unique_dialogue_count(self, entity_id: str) -> int:
        return len(self._unique_dialogues.get(entity_id, []))

    def entities_for_script(self, sid: int) -> List[EntityRef]:
        ids = set(self._scripts_to_entities.get(int(sid), []))
        return [r for r in self._entity_refs_all if r.entity_id in ids]

    def dialogue(self, entity_id: str, script_id: int, string_index: int) -> Optional[dict]:
        sid, si = int(script_id), int(string_index)
        for d in self._unique_dialogues.get(entity_id, []):
            if int(d.get("script_id", -1)) == sid and int(d.get("string_index", -1)) == si:
                return dict(d)
        return None

    def dialogue_contexts(self, script_id: int, string_index: int) -> List[dict]:
        return [dict(r) for r in self._contexts_by_dialogue.get((int(script_id), int(string_index)), [])]

    def dialogue_context_for_block(self, script_id: int, string_index: int) -> dict:
        rows = self.dialogue_contexts(script_id, string_index)
        unique_entities = {r.get("entity_id") for r in rows if r.get("entity_id")}
        return {
            "rows": rows,
            "single_entity": rows[0] if len(unique_entities) == 1 and rows else None,
            "ambiguous_speaker": len(unique_entities) > 1,
        }

    def stats(self) -> dict:
        return {
            "entities": self.entity_count,
            "with_dialogues": sum(1 for r in self._entity_refs_all if r.dialogue_count > 0),
            "schema": self.registry_raw.get("schema_version", "unknown"),
        }
