"""Unit tests for configurable emulator paths."""

import os
import unittest
from unittest.mock import patch

from wosutil.config import _path_from_env


class TestPathFromEnvironment(unittest.TestCase):
    """Environment overrides are optional and normalized consistently."""

    def test_uses_default_when_environment_is_missing_or_blank(self):
        """An unset or blank variable keeps the default path."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WOSUTIL_TEST_PATH", None)
            self.assertEqual(_path_from_env("WOSUTIL_TEST_PATH", "C:/default/path"), os.path.normpath("C:/default/path"))
            os.environ["WOSUTIL_TEST_PATH"] = "  "
            self.assertEqual(_path_from_env("WOSUTIL_TEST_PATH", "C:/default/path"), os.path.normpath("C:/default/path"))

    def test_uses_normalized_environment_override(self):
        """A custom installation path is normalized before use."""
        with patch.dict(os.environ, {"WOSUTIL_TEST_PATH": "C:/custom/../emulator"}):
            self.assertEqual(_path_from_env("WOSUTIL_TEST_PATH", "C:/default/path"), os.path.normpath("C:/emulator"))


if __name__ == "__main__":
    unittest.main()
