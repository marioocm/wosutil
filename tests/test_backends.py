"""Unit tests for the emulator backend layer."""

import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.emulator import backends
from wosutil.emulator.emulator_manager import get_active_backend, set_active_backend

SAMPLE_CONF = """
bst.vkeyboard="0"
bst.enable_adb_access="1"
bst.instance.Nova32.display_name="Nougat"
bst.instance.Nova32.adb_port="5552"
bst.instance.Nova32.status.adb_port="5552"
bst.instance.Pie64.display_name="Mario"
bst.instance.Pie64.adb_port="5555"
bst.instance.Pie64.status.adb_port="5555"
"""


class TestConfParsing(unittest.TestCase):
    """Tests for bluestacks.conf parsing helpers."""

    def test_parse_bluestacks_conf(self):
        """Parsing flattens quoted key=value lines."""
        values = backends.parse_bluestacks_conf(tempfile_helper(SAMPLE_CONF))
        self.assertEqual(values["bst.enable_adb_access"], "1")
        self.assertEqual(values["bst.instance.Pie64.display_name"], "Mario")
        self.assertEqual(values["bst.instance.Pie64.status.adb_port"], "5555")

    def test_parse_missing_conf_returns_empty(self):
        """Parsing a missing file returns an empty dict."""
        self.assertEqual(backends.parse_bluestacks_conf("nonexistent_file.conf"), {})

    def test_list_instances_sorted_by_port(self):
        """Instances are returned ordered by ADB port with display names."""
        values = backends.parse_bluestacks_conf(tempfile_helper(SAMPLE_CONF))
        instances = backends.list_bluestacks_instances(values)
        self.assertEqual([i["name"] for i in instances], ["Nova32", "Pie64"])
        self.assertEqual(instances[0]["display_name"], "Nougat")
        self.assertEqual(instances[1]["display_name"], "Mario")
        self.assertEqual(instances[1]["adb_port"], "5555")

    def test_adb_access_flag(self):
        """The enable_adb_access flag is read correctly."""
        on = backends.parse_bluestacks_conf(tempfile_helper(SAMPLE_CONF))
        self.assertTrue(backends.get_bluestacks_adb_access(on))
        off = on.copy()
        off.pop("bst.enable_adb_access")
        self.assertFalse(backends.get_bluestacks_adb_access(off))

    def test_parse_devices_output(self):
        """Adb devices output is parsed into a {serial: state} dict."""
        stdout = "List of devices attached\n127.0.0.1:5555\tdevice\n127.0.0.1:5552\toffline\n"
        self.assertEqual(backends.parse_devices_output(stdout), {"127.0.0.1:5555": "device", "127.0.0.1:5552": "offline"})
        self.assertEqual(backends.parse_devices_output(""), {})


class TestMumuBackend(unittest.TestCase):
    """Regression tests for the MuMu backend internals."""

    def test_serial_formula(self):
        """MuMu serials keep the 16384 + 32*index formula."""
        backend = backends.MuMuBackend()
        self.assertEqual(backend.get_serial(0), "127.0.0.1:16384")
        self.assertEqual(backend.get_serial(1), "127.0.0.1:16416")

    def test_build_command_shape(self):
        """MuMu build_adb_command matches the original MuMuManager argv."""
        from wosutil.config import MUMU_MULTI_PLAYER_PATH

        backend = backends.MuMuBackend()
        command = backend.build_adb_command(["shell", "input", "tap", "100", "200"], 1)
        self.assertEqual(command, [MUMU_MULTI_PLAYER_PATH, "adb", "-v", "1", "shell", "input", "tap", "100", "200"])

    def test_no_adb_warnings(self):
        """MuMu produces no ADB access warnings."""
        self.assertEqual(backends.MuMuBackend().check_adb_access(), [])


class TestBlueStacksBackend(unittest.TestCase):
    """Tests for BlueStacks backend instance mapping and commands."""

    def setUp(self):
        """Create a backend bound to a sample config file."""
        self.conf_path = tempfile_helper(SAMPLE_CONF)
        self.backend = backends.BlueStacksBackend(log_func=lambda *a, **k: None, conf_path=self.conf_path)

    def test_get_instances(self):
        """Instances map to stable integer indices and update the cache."""
        with patch("wosutil.emulator.backends.save_instance_cache") as mock_save:
            instances = self.backend.get_instances()
            self.assertEqual(instances, [{"index": 0, "name": "Nougat"}, {"index": 1, "name": "Mario"}])
            mock_save.assert_called_once_with(instances)

    def test_instances_refresh_rebuilds_index_map(self):
        """A config change is picked up on the next call, keeping stable slots."""
        with patch("wosutil.emulator.backends.save_instance_cache"):
            new_conf = SAMPLE_CONF + 'bst.instance.Rogue64.display_name="Paco"\nbst.instance.Rogue64.status.adb_port="5558"\n'
            with open(self.conf_path, "a", encoding="utf-8") as f:
                f.write(new_conf)
            instances = self.backend.get_instances()
            self.assertEqual([i["name"] for i in instances], ["Nougat", "Mario", "Paco"])
            self.assertEqual(instances[2]["index"], 2)
            self.assertEqual(self.backend.get_serial(2), "127.0.0.1:5558")

    def test_serial_mapping(self):
        """Serial mapping uses each BlueStacks instance ADB port."""
        self.assertEqual(self.backend.get_serial(0), "127.0.0.1:5552")
        self.assertEqual(self.backend.get_serial(1), "127.0.0.1:5555")

    def test_serial_out_of_range(self):
        """Unknown indices raise a ValueError."""
        with self.assertRaises(ValueError):
            self.backend.get_serial(2)

    def test_build_command(self):
        """BlueStacks commands use HD-Adb targeting the instance serial."""
        m = self.backend
        self.assertEqual(m.build_adb_command(["shell", "echo", "hi"], 1), [m.adb_path, "-s", "127.0.0.1:5555", "shell", "echo", "hi"])

    def test_check_adb_access_enabled(self):
        """No warnings when ADB access is enabled."""
        self.assertEqual(self.backend.check_adb_access(), [])

    def test_check_adb_access_disabled(self):
        """A warning is returned when ADB access is disabled."""
        conf = tempfile_helper(SAMPLE_CONF.replace('bst.enable_adb_access="1"', 'bst.enable_adb_access="0"'))
        backend = backends.BlueStacksBackend(conf_path=conf)
        warnings = backend.check_adb_access()
        self.assertEqual(len(warnings), 1)
        self.assertIn("Android Debug Bridge", warnings[0])


LDPLAYER_INSTANCE_0 = """
{
    "basicSettings.adbDebug": 1,
    "statusSettings.playerName": "Mario"
}
"""

LDPLAYER_INSTANCE_1 = """
{
    "basicSettings.adbDebug": 0,
    "statusSettings.playerName": "Krys"
}
"""


def ldplayer_config_dir_helper(files):
    """Create a temp LDPlayer config dir with the given {filename: content}."""
    directory = tempfile.mkdtemp(suffix=".ldconfig")
    for filename, content in files.items():
        with open(os.path.join(directory, filename), "w", encoding="utf-8") as f:
            f.write(content)
    return directory


class TestLDPlayerHelpers(unittest.TestCase):
    """Tests for LDPlayer instance config parsing."""

    def test_parse_missing_config_returns_empty(self):
        """Parsing a missing file returns an empty dict."""
        self.assertEqual(backends.parse_ldplayer_config("nonexistent_file.config"), {})

    def test_parse_invalid_config_returns_empty(self):
        """Parsing a malformed JSON file returns an empty dict."""
        path = tempfile_helper("{not valid json")
        self.assertEqual(backends.parse_ldplayer_config(path), {})

    def test_list_instances_from_config_dir(self):
        """Instances are parsed from leidianN.config files, sorted by index."""
        config_dir = ldplayer_config_dir_helper({"leidian1.config": LDPLAYER_INSTANCE_1, "leidian0.config": LDPLAYER_INSTANCE_0, "other.txt": ""})
        instances = backends.list_ldplayer_instances(config_dir)
        self.assertEqual([i["index"] for i in instances], [0, 1])
        self.assertEqual(instances[0]["display_name"], "Mario")
        self.assertTrue(instances[0]["adb_debug"])
        self.assertEqual(instances[1]["display_name"], "Krys")
        self.assertFalse(instances[1]["adb_debug"])

    def test_list_instances_missing_dir(self):
        """A missing config directory yields no instances."""
        self.assertEqual(backends.list_ldplayer_instances("nonexistent_dir"), [])


class TestLDPlayerBackend(unittest.TestCase):
    """Tests for LDPlayer backend instance mapping and commands."""

    def setUp(self):
        """Create a backend bound to a temporary config directory."""
        self.config_dir = ldplayer_config_dir_helper({"leidian0.config": LDPLAYER_INSTANCE_0, "leidian1.config": LDPLAYER_INSTANCE_1})
        self.backend = backends.LDPlayerBackend(log_func=lambda *a, **k: None, config_dir=self.config_dir)

    def test_get_instances(self):
        """Instances map to stable integer indices and update the cache."""
        with patch("wosutil.emulator.backends.save_instance_cache") as mock_save:
            instances = self.backend.get_instances()
            self.assertEqual(instances, [{"index": 0, "name": "Mario"}, {"index": 1, "name": "Krys"}])
            mock_save.assert_called_once_with(instances)

    def test_instances_refresh_rebuilds_index_map(self):
        """A new config file is picked up on the next call, keeping stable slots."""
        with patch("wosutil.emulator.backends.save_instance_cache"):
            with open(os.path.join(self.config_dir, "leidian2.config"), "w", encoding="utf-8") as f:
                f.write('{"basicSettings.adbDebug": 1, "statusSettings.playerName": "Paco"}')
            instances = self.backend.get_instances()
            self.assertEqual([i["name"] for i in instances], ["Mario", "Krys", "Paco"])
            self.assertEqual(instances[2]["index"], 2)

    def test_serial_mapping(self):
        """Serials follow the emulator-5554 + 2*index LDPlayer scheme."""
        self.assertEqual(self.backend.get_serial(0), "emulator-5554")
        self.assertEqual(self.backend.get_serial(1), "emulator-5556")

    def test_serial_out_of_range(self):
        """Unknown indices raise a ValueError."""
        with self.assertRaises(ValueError):
            self.backend.get_serial(2)

    def test_build_command(self):
        """LDPlayer commands use its own adb targeting the instance serial."""
        m = self.backend
        self.assertEqual(m.build_adb_command(["shell", "echo", "hi"], 0), [m.adb_path, "-s", "emulator-5554", "shell", "echo", "hi"])

    def test_check_adb_access_enabled(self):
        """No warnings when ADB debugging is enabled everywhere."""
        config_dir = ldplayer_config_dir_helper({"leidian0.config": LDPLAYER_INSTANCE_0})
        self.assertEqual(backends.LDPlayerBackend(config_dir=config_dir).check_adb_access(), [])

    def test_check_adb_access_disabled(self):
        """A warning names the instances with ADB debugging disabled."""
        warnings = self.backend.check_adb_access()
        self.assertEqual(len(warnings), 1)
        self.assertIn("Krys", warnings[0])
        self.assertNotIn("Mario", warnings[0])


class TestBackendFactory(unittest.TestCase):
    """Tests for emulator detection and the backend factory."""

    def test_detect_installed_none(self):
        """Nothing installed yields an empty list."""
        with patch.object(backends.os.path, "exists", return_value=False):
            self.assertEqual(backends.detect_installed_emulators(), [])

    def test_detect_installed_mumu_only(self):
        """Only MuMu is detected when only its manager exists."""

        def exists(path):
            return path == backends.MUMU_MULTI_PLAYER_PATH

        with patch.object(backends.os.path, "exists", side_effect=exists):
            self.assertEqual(backends.detect_installed_emulators(), [backends.EMULATOR_MUMU])

    def test_detect_installed_all(self):
        """All three emulators are listed when all paths exist."""
        with patch.object(backends.os.path, "exists", side_effect=lambda path: True):
            self.assertEqual(
                backends.detect_installed_emulators(),
                [backends.EMULATOR_MUMU, backends.EMULATOR_BLUESTACKS, backends.EMULATOR_LDPLAYER],
            )

    def test_detect_installed_ldplayer_only(self):
        """Only LDPlayer is detected when only its console exists."""

        def exists(path):
            return path == backends.LDPLAYER_CONSOLE_PATH

        with patch.object(backends.os.path, "exists", side_effect=exists):
            self.assertEqual(backends.detect_installed_emulators(), [backends.EMULATOR_LDPLAYER])

    def test_create_bluestacks(self):
        """Alternatively named backends are honored."""
        self.assertIsInstance(backends.create_backend(backends.EMULATOR_BLUESTACKS), backends.BlueStacksBackend)

    def test_create_ldplayer(self):
        """The LDPlayer backend is created by name."""
        self.assertIsInstance(backends.create_backend(backends.EMULATOR_LDPLAYER), backends.LDPlayerBackend)

    def test_create_mumu_default(self):
        """create_backend defaults to the MuMu backend."""
        self.assertIsInstance(backends.create_backend(), backends.MuMuBackend)


class TestActiveBackend(unittest.TestCase):
    """Tests for the emulator_manager active backend delegation."""

    def tearDown(self):
        """Reset the global backend so later tests observe defaults."""
        set_active_backend(None)

    def test_default_forwards_to_mumu(self):
        """Without a registered backend the MuMu backend is used."""
        backend = get_active_backend()
        self.assertIsInstance(backend, backends.MuMuBackend)
        self.assertEqual(backend.get_serial(1), "127.0.0.1:16416")

    def test_registered_backend_is_returned(self):
        """set_active_backend sway the backend returned by get_active_backend."""
        fake = object()
        set_active_backend(fake)
        self.assertIs(get_active_backend(), fake)


def tempfile_helper(content):
    """Write ``content`` to a temporary file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".conf")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


if __name__ == "__main__":
    unittest.main()
