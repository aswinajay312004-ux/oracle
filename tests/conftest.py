"""Test-wide settings. Uploads are parsed in-process for speed; tests named test_isolated_* in
tests/test_formats.py exercise the production path (a separate worker process with time and memory limits)."""
import os

os.environ.setdefault('POPPYS_INLINE_PARSE', '1')
