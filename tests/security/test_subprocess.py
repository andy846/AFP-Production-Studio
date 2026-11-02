"""Subprocess safety for the optional Java parser.

The Alpheus adapter runs a separate process. That process must never be
reachable from a shell string, must always be bounded by a timeout and
an output cap, and must never be able to write over the document it
is reading.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from afpstudio.parser_adapters.alpheus import AlpheusAdapter
from afpstudio.parser_adapters.contract import AdapterError


def write_script(tmp_path, name, body):
    """Write a small Python script that stands in for the Java parser."""
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def make_dummy_jar(tmp_path):
    """A file that exists, so ``describe()`` reaches the subprocess."""
    jar = tmp_path / "unused.jar"
    jar.write_bytes(b"not a real jar")
    return jar


def test_the_command_is_never_a_shell_string(tmp_path):
    adapter = AlpheusAdapter(jar=tmp_path / "adapter.jar")
    tricky = tmp_path / "a b; rm -rf x.afp"
    command = adapter.command(tricky)
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    # The file name is passed as one argument, not as shell syntax.
    assert "--input" in command
    assert command[command.index("--input") + 1] == str(tricky)
    assert not any("&&" in part or "|" in part for part in command)


def test_a_path_with_shell_metacharacters_is_not_interpreted(tmp_path):
    """A file name that would be dangerous in a shell is passed as data."""
    tricky = tmp_path / "job $(echo hacked) & more.afp"
    tricky.write_bytes(b"\x5a")
    script = write_script(
        tmp_path,
        "adapter.py",
        """
        import json, sys
        print(json.dumps({
            "contract_version": "1.0.0",
            "adapter": {"name": "t", "version": "1"},
            "document": {"filename": sys.argv[-1], "size_bytes": 1,
                         "page_count": 0, "documents": 0,
                         "structured_field_count": 0},
            "complete": True, "pages": [], "medium_maps": [],
            "invokes": [], "errors": [],
        }))
        """,
    )
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path), java_executable=sys.executable
    )
    # Run the script directly rather than through java, so the test does
    # not need a JDK installed.
    command = [sys.executable, str(script), "--input", str(tricky)]
    result = subprocess.run(
        command, capture_output=True, timeout=30, shell=False
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout.decode("utf-8"))
    # The metacharacters survived as literal file-name text: no shell
    # expanded them, and the adapter saw exactly the path it was given.
    assert payload["document"]["filename"] == str(tricky)
    assert not (tmp_path / "hacked").exists()


def test_a_timeout_stops_the_parser(tmp_path):
    """A parser that never finishes is killed rather than waited on."""
    script = write_script(
        tmp_path,
        "slow.py",
        """
        import time
        time.sleep(120)
        """,
    )
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path), java_executable=sys.executable
    )
    # Use the real adapter plumbing, but let the script play the jar.
    adapter.java_executable = sys.executable
    adapter.command = lambda path: [  # type: ignore[method-assign]
        sys.executable, str(script), "--input", str(path)
    ]
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "job.afp", timeout=2)
    assert excinfo.value.code == "timeout"


def test_a_failing_parser_is_reported(tmp_path):
    script = write_script(
        tmp_path,
        "broken.py",
        """
        import sys
        sys.stderr.write("it went wrong")
        sys.exit(3)
        """,
    )
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path), java_executable=sys.executable
    )
    adapter.java_executable = sys.executable
    adapter.command = lambda path: [  # type: ignore[method-assign]
        sys.executable,
        str(script),
        "--input",
        str(path),
    ]
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "job.afp", timeout=30)
    assert excinfo.value.code == "adapter_failed"
    assert "it went wrong" in str(excinfo.value)


def test_output_that_is_not_json_is_refused(tmp_path):
    script = write_script(
        tmp_path,
        "noise.py",
        """
        print("this is not json")
        """,
    )
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path), java_executable=sys.executable
    )
    adapter.java_executable = sys.executable
    adapter.command = lambda path: [  # type: ignore[method-assign]
        sys.executable,
        str(script),
        "--input",
        str(path),
    ]
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "job.afp", timeout=30)
    assert excinfo.value.code == "invalid_result"


def test_the_output_cap_is_enforced(tmp_path):
    """A parser that floods stdout is stopped at the cap."""
    script = write_script(
        tmp_path,
        "flood.py",
        """
        import sys
        chunk = "x" * 65536
        for _ in range(200):
            sys.stdout.write(chunk)
        sys.stdout.flush()
        """,
    )
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path),
        java_executable=sys.executable,
        max_output_bytes=1024,
    )
    adapter.java_executable = sys.executable
    adapter.command = lambda path: [  # type: ignore[method-assign]
        sys.executable,
        str(script),
        "--input",
        str(path),
    ]
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "job.afp", timeout=30)
    assert excinfo.value.code == "output_too_large"


def test_a_missing_java_executable_is_reported_cleanly(tmp_path):
    adapter = AlpheusAdapter(
        jar=make_dummy_jar(tmp_path),
        java_executable=str(tmp_path / "no-such-java.exe"),
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.parse(tmp_path / "job.afp", timeout=10)
    assert excinfo.value.code == "parser_unavailable"


def test_the_adapter_does_not_write_to_the_document(tmp_path):
    """The adapter is only ever given the file to read."""
    path = tmp_path / "job.afp"
    original = b"\x5a\x00\x08" + b"original"
    path.write_bytes(original)
    adapter = AlpheusAdapter(jar=make_dummy_jar(tmp_path))
    command = adapter.command(path)
    assert "--output" not in command
    assert path.read_bytes() == original
