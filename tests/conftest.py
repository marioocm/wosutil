"""Pytest configuration shared by the test suite."""

import os
import tempfile
from pathlib import Path

TEST_TEMP_DIR = Path(__file__).parent / ".tmp"
TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Some Windows environments restrict the user's system TEMP directory. Keep
# all test-generated files inside the writable project workspace instead.
os.environ["TMP"] = str(TEST_TEMP_DIR)
os.environ["TEMP"] = str(TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(TEST_TEMP_DIR)
tempfile.tempdir = str(TEST_TEMP_DIR)
