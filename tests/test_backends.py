"""Unit tests for the emulator backend layer."""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

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


def vm_dir_helper(folders, names=None):
    """Create a temp MuMu vms dir holding the given instance folders.

    Args:
        folders (list): Instance folder names, e.g. ["MuMuPlayerGlobal-15.0-0"].
        names (dict, optional): playerName by folder.
    """
    directory = tempfile.mkdtemp(suffix=".vms")
    for folder in folders:
        configs = os.path.join(directory, folder, "configs")
        os.makedirs(configs, exist_ok=True)
        name = (names or {}).get(folder, "Instance")
        with open(os.path.join(configs, "extra_config.json"), "w", encoding="utf-8") as f:
            json.dump({"playerName": name}, f)
    return directory


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


class TestMumuBackend(unittest.TestCase):
    """Regression tests for the MuMu backend internals."""

    def test_serial_formula(self):
        """MuMu serials keep the 16384 + 32*index formula."""
        backend = backends.MuMuBackend()
        self.assertEqual(backend.get_serial(0), "127.0.0.1:16384")
        self.assertEqual(backend.get_serial(1), "127.0.0.1:16416")

    def test_build_command_shape(self):
        """MuMu adb commands target the instance serial directly."""
        from wosutil.config import MUMU_ADB_PATH

        backend = backends.MuMuBackend()
        serial = backend.get_serial(1)
        command = backend.adb.build_command(serial, ["shell", "input", "tap", "100", "200"])
        self.assertEqual(command, [MUMU_ADB_PATH, "-s", "127.0.0.1:16416", "shell", "input", "tap", "100", "200"])

    def test_no_adb_warnings(self):
        """MuMu produces no ADB access warnings."""
        self.assertEqual(backends.MuMuBackend().check_adb_access(), [])


class TestMumuDiscovery(unittest.TestCase):
    """MuMu instances are discovered from the vms folder."""

    def setUp(self):
        """Create a backend bound to a temp vms dir with two series."""
        self.vms = vm_dir_helper(["MuMuPlayerGlobal-15.0-0", "MuMuPlayerGlobal-12.0-1"])
        self.backend = backends.MuMuBackend(log_func=lambda *a, **k: None, instance_base_path=self.vms)

    def test_vm_name_matches_the_instance_folder(self):
        """The folder of the instance series is used as the VM name."""
        self.assertEqual(self.backend._vm_name(0), "MuMuPlayerGlobal-15.0-0")
        self.assertEqual(self.backend._vm_name(1), "MuMuPlayerGlobal-12.0-1")

    def test_vm_name_falls_back_when_the_folder_is_missing(self):
        """A missing folder falls back to the 12.0 series guess."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, instance_base_path="nonexistent_dir")
        self.assertEqual(backend._vm_name(2), "MuMuPlayerGlobal-12.0-2")

    def test_device_executable_uses_the_instance_series(self):
        """Each series resolves to its own nx_device shell next to vms."""
        expected = os.path.normpath(os.path.join(os.path.dirname(self.vms), "nx_device", "15.0", "shell", "MuMuNxDevice.exe"))
        self.assertEqual(self.backend._device_executable(0), expected)

    def test_device_argv_launches_the_vm_directly(self):
        """The launch argv targets the instance folder without the manager."""
        expected = [
            os.path.normpath(os.path.join(os.path.dirname(self.vms), "nx_device", "15.0", "shell", "MuMuNxDevice.exe")),
            "-v",
            "0",
            "--vm",
            "MuMuPlayerGlobal-15.0-0",
        ]
        self.assertEqual(self.backend._device_argv(0), expected)

    def test_get_instances_reads_folders_and_names(self):
        """Indices come from the folder names, names from extra_config.json."""
        vms = vm_dir_helper(
            ["MuMuPlayerGlobal-15.0-0", "MuMuPlayerGlobal-12.0-1", "not-an-instance"],
            {"MuMuPlayerGlobal-15.0-0": "Healer", "MuMuPlayerGlobal-12.0-1": "Antnee"},
        )
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, instance_base_path=vms)
        instances = backend.get_instances()

        self.assertEqual(instances, [{"index": 0, "name": "Healer"}, {"index": 1, "name": "Antnee"}])

    def test_get_instances_missing_dir_returns_empty(self):
        """A missing vms dir yields no instances."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, instance_base_path="nonexistent_dir")
        self.assertEqual(backend.get_instances(), [])

    def test_matches_device_and_hypervisor_processes(self):
        """The shell and the hypervisor both identify the instance."""
        with patch(
            "wosutil.emulator.backends._iter_processes",
            return_value=[
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "0", "--vm", "MuMuPlayerGlobal-15.0-0"]),
                (object(), "MuMuVMMHeadless.exe", ["MuMuVMMHeadless.exe", "--comment", "MuMuPlayerGlobal-15.0-0", "--startvm", "abc"]),
                (object(), "MuMuVMMHeadless.exe", ["MuMuVMMHeadless.exe", "--comment", "MuMuPlayerGlobal-12.0-10", "--startvm", "def"]),
            ],
        ):
            matches = self.backend._matching_processes(0)

        self.assertEqual(len(matches), 2)

    def test_other_instance_does_not_match(self):
        """Index 1 does not match a folder belonging to instance 10."""
        with patch(
            "wosutil.emulator.backends._iter_processes",
            return_value=[
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "10", "--vm", "MuMuPlayerGlobal-15.0-10"]),
            ],
        ):
            matches = self.backend._matching_processes(1)

        self.assertEqual(matches, [])


class TestBlueStacksBackend(unittest.TestCase):
    """Tests for BlueStacks backend instance mapping and commands."""

    def setUp(self):
        """Create a backend bound to a sample config file."""
        self.conf_path = tempfile_helper(SAMPLE_CONF)
        self.backend = backends.BlueStacksBackend(log_func=lambda *a, **k: None, conf_path=self.conf_path)

    def test_get_instances(self):
        """Instances map to stable integer indices."""
        instances = self.backend.get_instances()
        self.assertEqual(instances, [{"index": 0, "name": "Nougat"}, {"index": 1, "name": "Mario"}])

    def test_instances_refresh_rebuilds_index_map(self):
        """A config change is picked up on the next call, keeping stable slots."""
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
        serial = m.get_serial(1)
        self.assertEqual(m.adb.build_command(serial, ["shell", "echo", "hi"]), [m.adb_path, "-s", "127.0.0.1:5555", "shell", "echo", "hi"])

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

    def test_parse_non_object_config_returns_empty(self):
        """A valid JSON list is not a usable LDPlayer configuration."""
        path = tempfile_helper("[1, 2, 3]")
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
        """Instances map to stable integer indices."""
        instances = self.backend.get_instances()
        self.assertEqual(instances, [{"index": 0, "name": "Mario"}, {"index": 1, "name": "Krys"}])

    def test_instances_refresh_rebuilds_index_map(self):
        """A new config file is picked up on the next call, keeping stable slots."""
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
        """Only MuMu is detected when only its main executable exists."""

        def exists(path):
            return path == os.path.normpath(os.path.join(backends.MUMU_BASE_PATH, "MuMuNxMain.exe"))

        with patch.object(backends.os.path, "exists", side_effect=exists):
            self.assertEqual(backends.detect_installed_emulators(), [backends.EMULATOR_MUMU])

    def test_detect_installed_all(self):
        """All four emulators are listed when all paths exist."""
        with patch.object(backends.os.path, "exists", side_effect=lambda path: True):
            self.assertEqual(
                backends.detect_installed_emulators(),
                [backends.EMULATOR_MUMU, backends.EMULATOR_BLUESTACKS, backends.EMULATOR_LDPLAYER, backends.EMULATOR_WAYDROID],
            )

    def test_detect_installed_waydroid_only(self):
        """Waydroid is detected from its default instance configuration."""

        def exists(path):
            return path == os.path.join(backends.WAYDROID_BASE_DIR, "waydroid", "waydroid.cfg")

        with patch.object(backends.os.path, "exists", side_effect=exists):
            self.assertEqual(backends.detect_installed_emulators(), [backends.EMULATOR_WAYDROID])

    def test_detect_installed_ldplayer_only(self):
        """Only LDPlayer is detected when only its console exists."""

        def exists(path):
            return path == backends.LDPLAYER_CONSOLE_PATH

        with patch.object(backends.os.path, "exists", side_effect=exists):
            self.assertEqual(backends.detect_installed_emulators(), [backends.EMULATOR_LDPLAYER])

    def test_detect_installed_uses_custom_paths(self):
        """Detection checks the configured executable and config locations."""
        paths = {
            backends.EMULATOR_MUMU: {"base_path": "D:/MuMu", "instance_base_path": "D:/MuMu/vms"},
            backends.EMULATOR_BLUESTACKS: {"base_path": "E:/BlueStacks", "config_path": "E:/Data/bluestacks.conf"},
            backends.EMULATOR_LDPLAYER: {"base_path": "F:/LDPlayer", "instance_config_dir": "F:/LDPlayer/vms/config"},
        }
        existing = {
            os.path.normpath(os.path.join("D:/MuMu", "MuMuNxMain.exe")),
            os.path.normpath("E:/Data/bluestacks.conf"),
            os.path.normpath(os.path.join("F:/LDPlayer", "ldconsole.exe")),
        }
        with patch.object(backends.os.path, "exists", side_effect=lambda path: path in existing):
            self.assertEqual(
                backends.detect_installed_emulators(paths),
                [backends.EMULATOR_MUMU, backends.EMULATOR_BLUESTACKS, backends.EMULATOR_LDPLAYER],
            )

    def test_create_bluestacks(self):
        """Alternatively named backends are honored."""
        self.assertIsInstance(backends.create_backend(backends.EMULATOR_BLUESTACKS), backends.BlueStacksBackend)

    def test_create_ldplayer(self):
        """The LDPlayer backend is created by name."""
        self.assertIsInstance(backends.create_backend(backends.EMULATOR_LDPLAYER), backends.LDPlayerBackend)

    def test_create_mumu_default(self):
        """create_backend defaults to the MuMu backend."""
        self.assertIsInstance(backends.create_backend(), backends.MuMuBackend)

    def test_create_backend_uses_custom_paths(self):
        """Each backend receives the paths selected in Preferences."""
        waydroid_base = tempfile.mkdtemp(suffix=".waydroid")
        paths = {
            backends.EMULATOR_MUMU: {"base_path": "D:/MuMu", "instance_base_path": "D:/MuMu/vms"},
            backends.EMULATOR_BLUESTACKS: {"base_path": "E:/BlueStacks", "config_path": "E:/Data/bluestacks.conf"},
            backends.EMULATOR_LDPLAYER: {"base_path": "F:/LDPlayer", "instance_config_dir": "F:/LDPlayer/vms/config"},
            backends.EMULATOR_WAYDROID: {"instances_dir": waydroid_base, "adb_path": "/usr/bin/adb"},
        }

        mumu = backends.create_backend(backends.EMULATOR_MUMU, emulator_paths=paths)
        self.assertEqual(mumu.adb_path, os.path.normpath(os.path.join("D:/MuMu", "adb.exe")))
        self.assertEqual(mumu.instance_base_path, os.path.normpath("D:/MuMu/vms"))

        bluestacks = backends.create_backend(backends.EMULATOR_BLUESTACKS, emulator_paths=paths)
        self.assertEqual(bluestacks.conf_path, os.path.normpath("E:/Data/bluestacks.conf"))
        self.assertEqual(bluestacks.adb_path, os.path.normpath(os.path.join("E:/BlueStacks", "HD-Adb.exe")))
        self.assertEqual(bluestacks.player_path, os.path.normpath(os.path.join("E:/BlueStacks", "HD-Player.exe")))

        ldplayer = backends.create_backend(backends.EMULATOR_LDPLAYER, emulator_paths=paths)
        self.assertEqual(ldplayer.config_dir, os.path.normpath("F:/LDPlayer/vms/config"))
        self.assertEqual(ldplayer.adb_path, os.path.normpath(os.path.join("F:/LDPlayer", "adb.exe")))
        self.assertEqual(ldplayer.console_path, os.path.normpath(os.path.join("F:/LDPlayer", "ldconsole.exe")))

        waydroid = backends.create_backend(backends.EMULATOR_WAYDROID, emulator_paths=paths)
        self.assertEqual(waydroid.base_dir, os.path.normpath(waydroid_base))
        self.assertEqual(waydroid.adb_path, os.path.normpath("/usr/bin/adb"))


class TestProcessMatching(unittest.TestCase):
    """Process matching must select only the requested emulator instance."""

    def test_ldplayer_index_one_does_not_match_index_ten(self):
        """LDPlayer instance 1 is distinct from instance 10."""
        backend = backends.LDPlayerBackend.__new__(backends.LDPlayerBackend)
        backend._instances = [{"index": 1, "name": "leidian1", "display_name": "Instance 1"}]
        expected = object()
        equals_form = object()
        wrong_instance = object()
        with patch.object(
            backends,
            "_iter_processes",
            return_value=[
                (expected, "dnplayer.exe", ["dnplayer.exe", "--index", "1"]),
                (equals_form, "dnplayer.exe", ["dnplayer.exe", "--index=1"]),
                (wrong_instance, "dnplayer.exe", ["dnplayer.exe", "--index", "10"]),
            ],
        ):
            matches = backend._matching_processes(1)

        self.assertEqual(matches, [expected, equals_form])

    def test_bluestacks_instance_name_is_compared_as_an_argument(self):
        """A similarly named BlueStacks instance is not selected."""
        backend = backends.BlueStacksBackend.__new__(backends.BlueStacksBackend)
        backend._instances = [{"index": 0, "name": "Pie64", "display_name": "Mario", "adb_port": "5555"}]
        expected = object()
        wrong_instance = object()
        with patch.object(
            backends,
            "_iter_processes",
            return_value=[
                (expected, "HD-Player.exe", ["HD-Player.exe", "--instance", "Pie64"]),
                (wrong_instance, "HD-Player.exe", ["HD-Player.exe", "--instance", "Pie640"]),
            ],
        ):
            matches = backend._matching_processes(0)

        self.assertEqual(matches, [expected])


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


class TestQuietWindowHandling(unittest.TestCase):
    """Emulator windows must be minimized so they never steal the focus."""

    def test_mumu_start_minimizes_after_launch_and_when_running(self):
        """The window is minimized right after launch and again on success."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        with (
            patch("subprocess.Popen"),
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_is_instance_running", side_effect=[False, False, True]),
            patch.object(backends.MuMuBackend, "_instance_name", return_value="Healer"),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.minimize_windows_by_title") as mock_titles,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertTrue(backend.start_instance(0))

        # Right after launch + while booting + through the appearance grace period.
        self.assertEqual(mock_sweep.call_count, 4)
        self.assertEqual(mock_sweep.call_args.args[0], backends.MUMU_WINDOW_PROCESS_NAMES)
        # The instance display name (the window title) is matched too.
        self.assertEqual(mock_titles.call_count, 4)
        self.assertEqual(mock_titles.call_args.args[0], ("Healer",))

    def test_mumu_start_skips_minimizing_when_disabled(self):
        """The preference off disables every window manipulation."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        with (
            patch("subprocess.Popen"),
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_is_instance_running", side_effect=[False, True]),
            patch.object(backends.MuMuBackend, "_instance_name", return_value="Healer"),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.minimize_windows_by_title") as mock_titles,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=False),
        ):
            self.assertTrue(backend.start_instance(0))
        mock_sweep.assert_not_called()
        mock_titles.assert_not_called()

    def test_mumu_stop_minimizes_windows_after_close(self):
        """Any emulator window left behind is minimized after a close."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        with (
            patch.object(backends.MuMuBackend, "_matching_processes", side_effect=[[], [], [], [], [], []]),
            patch.object(backends.MuMuBackend, "_graceful_stop", return_value=False),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertTrue(backend.stop_instance(0))
        mock_sweep.assert_called_once_with(backends.MUMU_WINDOW_PROCESS_NAMES, backend.log)

    def test_mumu_stop_minimizes_windows_even_when_the_stop_fails(self):
        """The manager window is minimized even if the instance cannot be stopped."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        proc = Mock()
        with (
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_matching_processes", return_value=[proc]),
            patch.object(backends.MuMuBackend, "_graceful_stop", return_value=False),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertFalse(backend.stop_instance(0))
        mock_sweep.assert_called_once_with(backends.MUMU_WINDOW_PROCESS_NAMES, backend.log)

    def test_stop_terminates_processes_and_waits_short(self):
        """Every backend stops by terminating the instance processes fast."""
        backend = backends.BlueStacksBackend(log_func=lambda *a, **k: None, conf_path=tempfile_helper(SAMPLE_CONF))
        proc = Mock()
        with patch.object(backend, "_matching_processes", side_effect=[[proc], [proc], []]), patch.object(backend, "_graceful_stop", return_value=False):
            self.assertTrue(backend.stop_instance(0))
        proc.terminate.assert_called_once()

    def test_mumu_stop_terminates_only_shell_processes(self):
        """MuMu kills the instance shell but never the VM process."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        shell = Mock()
        vm = Mock()
        calls = {"count": 0}

        def matches(_index):
            calls["count"] += 1
            return [shell, vm] if calls["count"] == 1 else [vm]

        with (
            patch.object(backends.MuMuBackend, "_vm_name", return_value="MuMuPlayerGlobal-12.0-1"),
            patch.object(backends.MuMuBackend, "_matching_processes", side_effect=matches),
            patch.object(backends.MuMuBackend, "_graceful_stop", return_value=False),
            patch(
                "wosutil.emulator.backends._iter_processes",
                return_value=[
                    (shell, "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "1", "--vm", "MuMuPlayerGlobal-12.0-1"]),
                    (vm, "MuMuVMMHeadless.exe", ["MuMuVMMHeadless.exe", "--comment", "MuMuPlayerGlobal-12.0-1"]),
                ],
            ),
            patch("wosutil.emulator.backends.time.sleep"),
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=False),
        ):
            self.assertFalse(backend.stop_instance(1))
        shell.terminate.assert_called_once()
        vm.terminate.assert_not_called()
        vm.kill.assert_not_called()

    def test_mumu_graceful_stop_uses_manager_when_present(self):
        """MuMu asks MuMuManager to shut the VM down cleanly before killing."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, manager_path="C:\\MuMuManager.exe")
        with patch("wosutil.emulator.backends.run_process_robust") as mock_run, patch("wosutil.emulator.backends.os.path.exists", return_value=True):
            mock_run.return_value = Mock(returncode=0)
            self.assertTrue(backend._graceful_stop(1))
        mock_run.assert_called_once_with(["C:\\MuMuManager.exe", "control", "--vmindex", "1", "shutdown"], timeout=30)

    def test_mumu_graceful_stop_falls_back_when_manager_missing(self):
        """A missing MuMuManager lets the caller terminate processes directly."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, manager_path="C:\\does_not_exist\\MuMuManager.exe")
        self.assertFalse(backend._graceful_stop(1))

    def test_mumu_graceful_stop_retries_transient_failures(self):
        """A failed shutdown request is retried before giving up."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, manager_path="C:\\MuMuManager.exe")
        with patch("wosutil.emulator.backends.run_process_robust") as mock_run, patch("wosutil.emulator.backends.os.path.exists", return_value=True), patch("wosutil.emulator.backends.time.sleep"):
            mock_run.side_effect = [Mock(returncode=1), Mock(returncode=0)]
            self.assertTrue(backend._graceful_stop(1))
        self.assertEqual(mock_run.call_count, 2)

    def test_mumu_stop_repeats_shutdown_request_while_waiting(self):
        """The shutdown request is re-issued while the VM is still closing."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, manager_path="C:\\MuMuManager.exe")
        proc = Mock()
        processes = [[proc], [proc], [proc], [proc], [proc], [proc], [proc], [], []]
        with (
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_matching_processes", side_effect=processes),
            patch.object(backends.MuMuBackend, "_graceful_stop", return_value=True) as mock_graceful,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=False),
        ):
            self.assertTrue(backend.stop_instance(0))
        self.assertEqual(mock_graceful.call_count, 2)
        proc.terminate.assert_not_called()

    def test_mumu_stop_waits_for_graceful_shutdown_before_killing(self):
        """A graceful shutdown accepted by the manager is waited on, not killed."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None, manager_path="C:\\MuMuManager.exe")
        proc = Mock()
        with (
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_matching_processes", side_effect=[[proc], [proc], [], [], [], []]),
            patch.object(backends.MuMuBackend, "_graceful_stop", return_value=True),
        ):
            self.assertTrue(backend.stop_instance(0))
        proc.terminate.assert_not_called()

    def test_ldplayer_stop_requests_graceful_quit_then_terminates(self):
        """LDPlayer tries ldconsole quit before terminating dnplayer."""
        backend = backends.LDPlayerBackend(log_func=lambda *a, **k: None, config_dir=ldplayer_config_dir_helper({"leidian0.config": LDPLAYER_INSTANCE_0}))
        proc = Mock()
        with patch("wosutil.emulator.backends.run_process_robust", return_value=Mock(returncode=1)) as mock_run, patch.object(backend, "_matching_processes", side_effect=[[proc], [proc], []]):
            self.assertTrue(backend.stop_instance(0))
        mock_run.assert_called_once_with([backend.console_path, "quit", "--index", "0"], timeout=15)
        proc.terminate.assert_called_once()

    def test_ldplayer_stop_waits_for_an_accepted_quit(self):
        """A successful ldconsole quit is waited on instead of killing dnplayer."""
        backend = backends.LDPlayerBackend(log_func=lambda *a, **k: None, config_dir=ldplayer_config_dir_helper({"leidian0.config": LDPLAYER_INSTANCE_0}))
        proc = Mock()
        with (
            patch("wosutil.emulator.backends.run_process_robust", return_value=Mock(returncode=0)),
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backend, "_matching_processes", side_effect=[[proc], [proc], []]),
        ):
            self.assertTrue(backend.stop_instance(0))
        proc.terminate.assert_not_called()

    def test_bluestacks_start_minimizes_player_windows(self):
        """HD-Player windows are minimized on launch and on success."""
        backend = backends.BlueStacksBackend(log_func=lambda *a, **k: None, conf_path=tempfile_helper(SAMPLE_CONF))
        with (
            patch("subprocess.Popen"),
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backend, "_is_instance_running", side_effect=[False, False, True]),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.minimize_windows_by_title") as mock_titles,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertTrue(backend.start_instance(0))
        self.assertEqual(mock_sweep.call_count, 4)
        self.assertEqual(mock_sweep.call_args.args[0], backends.BLUESTACKS_WINDOW_PROCESS_NAMES)
        self.assertEqual(mock_titles.call_count, 4)
        self.assertEqual(mock_titles.call_args.args[0], ("Nougat",))

    def test_bluestacks_already_running_keeps_windows_background(self):
        """An already-running instance is minimized and watched as well."""
        backend = backends.BlueStacksBackend(log_func=lambda *a, **k: None, conf_path=tempfile_helper(SAMPLE_CONF))
        with (
            patch.object(backend, "_is_instance_running", return_value=True),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.minimize_windows_by_title") as mock_titles,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertTrue(backend.start_instance(0))
        mock_sweep.assert_called_once()
        mock_titles.assert_called_once()

    def test_ldplayer_start_minimizes_player_windows(self):
        """Dnplayer windows are minimized on launch and on success."""
        config_dir = ldplayer_config_dir_helper({"leidian0.config": LDPLAYER_INSTANCE_0})
        backend = backends.LDPlayerBackend(log_func=lambda *a, **k: None, config_dir=config_dir)
        with (
            patch("subprocess.Popen"),
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backend, "_is_instance_running", side_effect=[False, False, True]),
            patch("wosutil.emulator.backends.minimize_process_windows") as mock_sweep,
            patch("wosutil.emulator.backends.minimize_windows_by_title") as mock_titles,
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=True),
        ):
            self.assertTrue(backend.start_instance(0))
        self.assertEqual(mock_sweep.call_count, 4)
        self.assertEqual(mock_sweep.call_args.args[0], backends.LDPLAYER_WINDOW_PROCESS_NAMES)
        self.assertEqual(mock_titles.call_count, 4)
        self.assertEqual(mock_titles.call_args.args[0], ("Mario",))

    def test_startupinfo_asks_for_minimized_window(self):
        """The launched processes get a SW_SHOWMINNOACTIVE STARTUPINFO."""
        info = backends._minimized_startupinfo()
        if info is not None:  # Windows only
            import subprocess

            self.assertTrue(info.dwFlags & subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(info.wShowWindow, 7)

    def test_start_instance_launches_without_console_window(self):
        """Popen gets the detached flags (incl. CREATE_NO_WINDOW) + minimized info."""
        backend = backends.MuMuBackend(log_func=lambda *a, **k: None)
        sentinel_flags = 0x08000000 | 0x8 | 0x200
        sentinel_info = object()
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("wosutil.emulator.backends.time.sleep"),
            patch.object(backends.MuMuBackend, "_is_instance_running", side_effect=[False, True]),
            patch.object(backends.MuMuBackend, "_instance_name", return_value="Healer"),
            patch("wosutil.emulator.backends._detached_creation_flags", return_value=sentinel_flags),
            patch("wosutil.emulator.backends._minimized_startupinfo", return_value=sentinel_info),
            patch("wosutil.emulator.backends.minimize_process_windows"),
            patch("wosutil.emulator.backends.minimize_windows_by_title"),
            patch("wosutil.emulator.backends.start_minimized_enabled", return_value=False),
        ):
            self.assertTrue(backend.start_instance(0))
        _, kwargs = mock_popen.call_args
        self.assertEqual(kwargs.get("creationflags"), sentinel_flags)
        self.assertIs(kwargs.get("startupinfo"), sentinel_info)


def tempfile_helper(content):
    """Write ``content`` to a temporary file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".conf")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def waydroid_instance_helper(instance_ids=("default",), names=None):
    """Create a temporary Waydroid data tree and return (base_dir, names_path).

    Args:
        instance_ids (tuple): Internal instance IDs to create.
        names (dict, optional): Visible names stored in the names file.
    """
    base_dir = tempfile.mkdtemp(suffix=".waydroid")
    for instance_id in instance_ids:
        folder = "waydroid" if instance_id == "default" else f"waydroid.{instance_id}"
        directory = os.path.join(base_dir, folder)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "waydroid.cfg"), "w", encoding="utf-8") as config_file:
            config_file.write("[waydroid]\n")
    names_path = os.path.join(base_dir, "waydroid-instances.json")
    with open(names_path, "w", encoding="utf-8") as names_file:
        json.dump(names or {}, names_file)
    return base_dir, names_path


def waydroid_backend_helper(instance_ids=("default",), names=None, logs=None):
    """Build a Waydroid backend bound to a temporary instance tree.

    Args:
        instance_ids (tuple): Internal instance IDs to create.
        names (dict, optional): Visible names stored in the names file.
        logs (list, optional): Collects the ``(message, level)`` log lines.
    """
    base_dir, names_path = waydroid_instance_helper(instance_ids, names)
    lease_dir = os.path.join(base_dir, "misc")
    os.makedirs(lease_dir, exist_ok=True)
    log_func = None
    if logs is not None:

        def log_func(message, level="info"):
            """Collect one log line."""
            logs.append((message, level))

    return backends.WaydroidBackend(log_func=log_func, base_dir=base_dir, lease_dir=lease_dir, names_path=names_path)


class TestWaydroidDiscovery(unittest.TestCase):
    """Waydroid instances are discovered from their data directories."""

    def test_lists_instances_with_their_visible_names(self):
        """The default instance comes first and uses the names file."""
        backend = waydroid_backend_helper(("default", "alt"), {"default": {"name": "Mario"}, "alt": {"name": "Tokyo"}})
        self.assertEqual(backend.list_instances(), [{"index": 0, "name": "Mario"}, {"index": 1, "name": "Tokyo"}])

    def test_ignores_folders_that_are_not_instances(self):
        """Staging folders and folders without a config are not instances."""
        backend = waydroid_backend_helper(("default",), {"default": {"name": "Mario"}})
        os.makedirs(os.path.join(backend.base_dir, "waydroid-nvidia"))
        os.makedirs(os.path.join(backend.base_dir, "waydroid.beta"))
        self.assertEqual(backend.list_instances(), [{"index": 0, "name": "Mario"}])

    def test_serial_reads_the_lease_of_its_own_bridge(self):
        """Each instance reads its own dnsmasq lease file."""
        backend = waydroid_backend_helper(("default", "alt"))
        with open(os.path.join(backend.lease_dir, "dnsmasq.waydroid0.leases"), "w", encoding="utf-8") as leases:
            leases.write("1795696646 00:16:3e:f9:d3:03 192.168.240.112 WayDroid-x86-64-Device 01:00:16:3e:f9:d3:03\n")
        with open(os.path.join(backend.lease_dir, "dnsmasq.waydroid-alt.leases"), "w", encoding="utf-8") as leases:
            leases.write("1795696566 00:16:3e:f9:d3:03 192.168.243.112 WayDroid-x86-64-Device 01:00:16:3e:f9:d3:03\n")
        self.assertEqual(backend.get_serial(0), "192.168.240.112:5555")
        self.assertEqual(backend.get_serial(1), "192.168.243.112:5555")

    def test_parse_leases_uses_the_newest_address(self):
        """A stale lease line does not win over the current address."""
        fd, path = tempfile.mkstemp(suffix=".leases")
        with os.fdopen(fd, "w", encoding="utf-8") as leases:
            leases.write("1795696000 00:16:3e:f9:d3:03 192.168.240.50 WayDroid 01:00:16:3e:f9:d3:03\n")
            leases.write("1795696646 00:16:3e:f9:d3:03 192.168.240.112 WayDroid 01:00:16:3e:f9:d3:03\n")
        self.assertEqual(backends.parse_waydroid_leases(path), "192.168.240.112")
        self.assertEqual(backends.parse_waydroid_leases(os.path.join(tempfile.gettempdir(), "missing.leases")), "")


class TestWaydroidCommands(unittest.TestCase):
    """Waydroid sessions are addressed with the multi-instance wrapper."""

    def test_launch_argv_uses_gamescope_and_the_right_instance(self):
        """The default instance uses waydroid; others use waydroid-multi."""
        backend = waydroid_backend_helper(("default", "alt"))
        with patch.object(backends.shutil, "which", side_effect=lambda name: {"gamescope": "/usr/bin/gamescope", "waydroid-multi": "/usr/local/bin/waydroid-multi"}.get(name)):
            self.assertEqual(
                backend._launch_argv(0),
                [
                    "/usr/bin/gamescope",
                    "--expose-wayland",
                    "-W",
                    "576",
                    "-H",
                    "1024",
                    "-w",
                    "720",
                    "-h",
                    "1280",
                    "--",
                    "waydroid",
                    "session",
                    "start",
                ],
            )
            self.assertEqual(
                backend._launch_argv(1),
                [
                    "/usr/bin/gamescope",
                    "--expose-wayland",
                    "-W",
                    "576",
                    "-H",
                    "1024",
                    "-w",
                    "720",
                    "-h",
                    "1280",
                    "--",
                    "/usr/local/bin/waydroid-multi",
                    "--instance",
                    "alt",
                    "session",
                    "start",
                ],
            )

    def test_launch_falls_back_to_a_native_window_without_gamescope(self):
        """Without gamescope Waydroid opens its own window."""
        backend = waydroid_backend_helper(("default",))
        with patch.object(backends.shutil, "which", return_value=None):
            self.assertEqual(backend._launch_argv(0), ["waydroid", "session", "start"])

    def test_secondary_instance_requires_the_wrapper(self):
        """A missing wrapper is a clear failure for an additional instance."""
        backend = waydroid_backend_helper(("default", "alt"))
        with patch.object(backends.shutil, "which", return_value=None):
            self.assertEqual(backend._launch_argv(1), [])

    def test_matching_processes_select_only_their_instance(self):
        """Gamescope processes are matched by the wrapped command."""
        backend = waydroid_backend_helper(("default", "alt"))
        default_process = object()
        alt_process = object()
        decoy_process = object()
        watcher_process = object()
        processes = [
            (default_process, "gamescope", ["gamescope", "--expose-wayland", "--", "waydroid", "session", "start"]),
            (alt_process, "gamescope", ["gamescope", "--expose-wayland", "--", "/usr/local/bin/waydroid-multi", "--instance", "alt", "session", "start"]),
            (decoy_process, "gamescope", ["gamescope", "--", "steam", "session", "start"]),
            (watcher_process, "bash", ["bash", "-c", "waydroid-multi --instance alt session stop"]),
        ]
        with patch.object(backends, "_iter_processes", side_effect=lambda: iter(processes)):
            self.assertEqual(backend._matching_processes(0), [default_process])
            self.assertEqual(backend._matching_processes(1), [alt_process])

    def test_graceful_stop_uses_the_session_stop_of_the_instance(self):
        """Stopping the default instance calls the plain waydroid command."""
        backend = waydroid_backend_helper(("default",))
        with patch.object(backends, "run_process_robust", return_value=Mock(returncode=0)) as run:
            self.assertTrue(backend._graceful_stop(0))
        run.assert_called_once_with(["waydroid", "session", "stop"], timeout=30)

    def test_container_service_must_be_running_for_a_secondary_instance(self):
        """The tool reports the command instead of requesting root itself."""
        backend = waydroid_backend_helper(("default", "alt"), logs=[])
        with patch.object(backends.shutil, "which", return_value="/usr/bin/systemctl"), patch.object(backends, "run_process_robust", return_value=Mock(returncode=3)):
            self.assertFalse(backend._ensure_container_service(1))


class TestWaydroidStart(unittest.TestCase):
    """The start flow waits for Android before reporting success."""

    def _start_with(self, booted):
        """Run start_instance with the session process mocked."""
        logs = []
        backend = waydroid_backend_helper(("default",), logs=logs)
        with (
            tempfile.TemporaryDirectory() as log_dir,
            patch.object(backends, "LOG_DIR", log_dir),
            patch.object(backends.shutil, "which", return_value="/usr/bin/gamescope"),
            patch.object(backend, "_matching_processes", return_value=[]),
            patch.object(backend, "_session_running", return_value=False),
            patch.object(backend, "_ensure_container_service", return_value=True),
            patch.object(backend, "_wait_until_booted", return_value=booted) as wait,
            patch.object(backend, "_show_ui") as show_ui,
            patch.object(backend, "_place_window") as place_window,
            patch.object(backends.subprocess, "Popen") as popen,
        ):
            popen.return_value.pid = 4242
            popen.return_value.poll.return_value = None
            result = backend.start_instance(0)
        return result, wait, show_ui, place_window, logs

    def test_start_waits_for_boot_then_shows_the_ui_and_places_the_window(self):
        """A successful boot shows Android and moves the gamescope window."""
        result, wait, show_ui, place_window, _logs = self._start_with(booted=True)
        self.assertTrue(result)
        wait.assert_called_once()
        show_ui.assert_called_once_with(0)
        place_window.assert_called_once_with(0, 4242)

    def test_start_fails_when_android_does_not_boot(self):
        """A boot timeout is reported and the UI is never shown."""
        result, _wait, show_ui, _place_window, logs = self._start_with(booted=False)
        self.assertFalse(result)
        show_ui.assert_not_called()
        self.assertTrue(any("did not boot" in message for message, _level in logs))


if __name__ == "__main__":
    unittest.main()
