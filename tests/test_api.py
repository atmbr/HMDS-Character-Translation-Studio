import unittest
from types import SimpleNamespace

from cts.api import Workspace


class FakeModel:
    game_code = "ABCP"
    script_count = 1
    sha256 = "fake"
    source_path = None

    def __init__(self):
        self._strings = {0: ["Olá{05}"]}

    def parse_script(self, sid):
        return SimpleNamespace(valid=True, strings=self._strings[sid])

    def current_text(self, session, sid, idx):
        return session.string_edits.get(sid, {}).get(idx, self._strings[sid][idx])

    def set_string(self, session, sid, idx, text):
        session.string_edits.setdefault(sid, {})[idx] = text

    def is_script_write_protected(self, sid):
        return False


class APITests(unittest.TestCase):
    def test_read_and_write_dialogue_without_ui(self):
        ws = Workspace(FakeModel())
        before = ws.get_dialogue(0, 0)
        self.assertIn("Olá", before.current_text)
        after = ws.set_dialogue(0, 0, "Oi!", status="translated")
        self.assertIn("Oi!", after.current_text)
        self.assertEqual(after.status, "translated")


if __name__ == "__main__":
    unittest.main()
