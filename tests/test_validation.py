import unittest

from cts.validation import editor_to_raw, raw_to_editor


class FriendlyEditorTests(unittest.TestCase):
    def test_new_box_roundtrip(self):
        raw = "Primeira linha\nSegunda{BOX}Terceira{05}"
        friendly = raw_to_editor(raw)
        self.assertIn("{05 0C}", friendly)
        rebuilt = editor_to_raw(friendly)
        self.assertIn("{BOX}", rebuilt)

    def test_ff2a_stays_neutral(self):
        raw = "Hoje é de {FF 2A}.{05}"
        friendly = raw_to_editor(raw)
        self.assertIn("[FF 2A]", friendly)
        self.assertIn("{FF 2A}", editor_to_raw(friendly))


if __name__ == "__main__":
    unittest.main()
