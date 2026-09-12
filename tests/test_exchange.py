import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cts.team import (
    DIALOGUE_FORMAT, PACK_FORMAT,
    make_entry, write_dialogue_file, write_pack_file, read_team_package, import_entries,
)


class FakeSession:
    def __init__(self):
        self.string_edits = {}


class FakeProject:
    def __init__(self):
        self.statuses = {}

    @staticmethod
    def key(sid, idx):
        return f"{sid}:{idx}"

    def status(self, sid, idx, session):
        if self.key(sid, idx) in self.statuses:
            return self.statuses[self.key(sid, idx)]
        return "translated" if idx in session.string_edits.get(sid, {}) else "untranslated"

    def set_status(self, sid, idx, status):
        self.statuses[self.key(sid, idx)] = status


class FakeModel:
    game_code = "ABCP"
    script_count = 2
    sha256 = "basehash"

    def __init__(self):
        self._strings = {0: ["Original{05}", "Outra{05}"]}

    def parse_script(self, sid):
        return SimpleNamespace(valid=True, strings=self._strings[sid])

    def current_text(self, session, sid, idx):
        return session.string_edits.get(sid, {}).get(idx, self._strings[sid][idx])

    def set_string(self, session, sid, idx, text):
        session.string_edits.setdefault(sid, {})[idx] = text


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()

    def test_export_always_contains_current_text(self):
        session = FakeSession(); project = FakeProject()
        entry = make_entry(self.model, session, project, 0, 0, entity_name="Teste")
        self.assertTrue(entry["has_translation"])
        self.assertIn("Original", entry["translation"])

    def test_dialogue_and_pack_formats(self):
        session = FakeSession(); project = FakeProject()
        entry = make_entry(self.model, session, project, 0, 0)
        entry2 = make_entry(self.model, session, project, 0, 1)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "one.ctsdialogue"
            p = Path(td) / "many.ctspack"
            write_dialogue_file(d, self.model, entry)
            write_pack_file(p, self.model, [entry, entry2])
            self.assertEqual(read_team_package(d)["format"], DIALOGUE_FORMAT)
            self.assertEqual(read_team_package(p)["format"], PACK_FORMAT)
            self.assertEqual(len(read_team_package(d)["entries"]), 1)
            self.assertEqual(len(read_team_package(p)["entries"]), 2)

    def test_import_replaces_text_and_status(self):
        export_session = FakeSession(); export_project = FakeProject()
        self.model.set_string(export_session, 0, 0, "Importado{05}")
        export_project.set_status(0, 0, "reviewed")
        entry = make_entry(self.model, export_session, export_project, 0, 0)

        local_session = FakeSession(); local_project = FakeProject()
        self.model.set_string(local_session, 0, 0, "Local{05}")
        local_project.set_status(0, 0, "translated")
        result = import_entries(self.model, local_session, local_project, [entry], overwrite_conflicts=True)

        self.assertEqual(self.model.current_text(local_session, 0, 0), "Importado{05}")
        self.assertEqual(local_project.status(0, 0, local_session), "reviewed")
        self.assertEqual(result.applied, 1)
        self.assertEqual(result.text_changes, 1)
        self.assertEqual(result.status_changes, 1)


if __name__ == "__main__":
    unittest.main()

class ProtectedImportTests(unittest.TestCase):
    class ProtectedModel(FakeModel):
        script_count = 2
        def __init__(self):
            super().__init__()
            self._strings[1] = ["Protegido{05}"]
        def is_script_write_protected(self, sid):
            return int(sid) == 1
        def set_string(self, session, sid, idx, text):
            if self.is_script_write_protected(sid):
                raise RuntimeError("protected final record")
            return super().set_string(session, sid, idx, text)

    def test_protected_row_does_not_abort_batch(self):
        model = self.ProtectedModel()
        session = FakeSession(); project = FakeProject()
        good = {
            "script_id": 0, "string_index": 0,
            "original_sha256": __import__('hashlib').sha256("Original{05}".encode()).hexdigest(),
            "translation": "Bom{05}", "has_translation": True, "status": "translated",
        }
        bad = {
            "script_id": 1, "string_index": 0,
            "original_sha256": __import__('hashlib').sha256("Protegido{05}".encode()).hexdigest(),
            "translation": "Alterado{05}", "has_translation": True, "status": "translated",
        }
        result = import_entries(model, session, project, [bad, good], overwrite_conflicts=True)
        self.assertEqual(result.applied, 1)
        self.assertEqual(result.incompatible, 1)
        self.assertEqual(model.current_text(session, 0, 0), "Bom{05}")
        self.assertEqual(model.current_text(session, 1, 0), "Protegido{05}")
        self.assertTrue(result.skipped)
