import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from flightdeck.state import CorruptState, StateStore, UnsupportedSchema


class StateStoreTest(unittest.TestCase):
    def test_versioned_state_round_trip_appends_events_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore.new(mode="full")
            store.apply({"type": "assumption_added", "text": "Use stdlib"})
            store.save(path)

            loaded = StateStore.load(path)
            self.assertEqual(1, loaded.data["schema_version"])
            self.assertEqual("full", loaded.data["mode"])
            self.assertEqual(
                ["automatic_decision", "assumption_added"],
                [event["type"] for event in loaded.data["events"]],
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_json_and_schema_are_diagnosed_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not-json", encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(CorruptState):
                StateStore.load(path)
            self.assertEqual(before, path.read_bytes())

            path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            with self.assertRaises(UnsupportedSchema):
                StateStore.load(path)

    def test_unknown_phase_mode_and_depth_are_rejected(self):
        for field, value in (("phase", "unknown"), ("mode", "magic"), ("depth", "bottomless")):
            with self.subTest(field=field):
                data = StateStore.new().data
                data[field] = value
                with self.assertRaises(ValueError):
                    StateStore(data)

    def test_full_mode_records_automatic_decision(self):
        store = StateStore.new(mode="full")
        self.assertIn("Remaining decisions run automatically in full mode", store.data["assumptions"])
        self.assertEqual("automatic_decision", store.data["events"][0]["type"])

    def test_rejected_mode_event_is_atomic(self):
        store = StateStore.new()
        before = json.loads(json.dumps(store.data))
        with self.assertRaisesRegex(ValueError, "unknown mode"):
            store.apply({"type": "mode_change_requested", "mode": "unsafe"})
        self.assertEqual(before, store.data)

    def test_atomic_save_creates_temp_beside_target_and_replaces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            real_mkstemp = __import__("tempfile").mkstemp
            seen = {}

            def tracked_mkstemp(*args, **kwargs):
                descriptor, temporary = real_mkstemp(*args, **kwargs)
                seen["temporary"] = Path(temporary)
                return descriptor, temporary

            with mock.patch("flightdeck.state.tempfile.mkstemp", side_effect=tracked_mkstemp), \
                 mock.patch("flightdeck.state.os.replace", wraps=__import__("os").replace) as replaced:
                StateStore.new().save(path)
            self.assertEqual(path.parent, seen["temporary"].parent)
            replaced.assert_called_once_with(str(seen["temporary"]), path)
            self.assertFalse(seen["temporary"].exists())


if __name__ == "__main__":
    unittest.main()
