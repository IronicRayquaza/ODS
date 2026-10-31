"""Portal's file operations inside the Windows sandbox (windows/fs_op.py).

The AppContainer can reach the workspace and nothing above it, so these tests
hide every folder above a fake workspace the same way: it does not look like a
folder, and creating it is refused.
"""
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'windows'))
import fs_op  # noqa: E402


@pytest.fixture
def sandboxed(tmp_path, monkeypatch):
    workspace = tmp_path / 'portal' / 'workspace'
    workspace.mkdir(parents=True)
    real_isdir, real_mkdir = os.path.isdir, os.mkdir

    def inside(path):
        resolved = Path(os.path.abspath(path))
        return resolved == workspace or workspace in resolved.parents

    def isdir(path):
        return inside(path) and real_isdir(path)

    def mkdir(path, *args, **kwargs):
        if not inside(path):
            raise PermissionError(5, 'Access is denied', str(path))
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(fs_op.os.path, 'isdir', isdir)
    monkeypatch.setattr(fs_op.os, 'mkdir', mkdir)
    return workspace


def run(request, data=b''):
    out = io.BytesIO()
    fs_op.main(request, io.BytesIO(data), out)
    return out.getvalue()


def test_creating_the_workspace_itself_never_walks_above_it(sandboxed):
    run({'op': 'mkdirp', 'path': str(sandboxed)})
    assert sandboxed.is_dir()


def test_nested_folders_and_writes_are_created_from_the_workspace_down(sandboxed):
    run({'op': 'mkdirp', 'path': str(sandboxed / 'arcade' / 'assets')})
    run({'op': 'write', 'path': str(sandboxed / 'game' / 'src' / 'index.html')}, b'<h1>hi</h1>')
    assert (sandboxed / 'arcade' / 'assets').is_dir()
    assert (sandboxed / 'game' / 'src' / 'index.html').read_bytes() == b'<h1>hi</h1>'


def test_a_folder_outside_the_workspace_is_still_refused(sandboxed):
    with pytest.raises(PermissionError):
        run({'op': 'mkdirp', 'path': str(sandboxed.parent / 'elsewhere')})
    assert not (sandboxed.parent / 'elsewhere').exists()


def test_stat_reports_a_missing_path_as_null_and_a_file_as_json(sandboxed):
    assert run({'op': 'stat', 'path': str(sandboxed / 'nope')}) == b'null\n'
    (sandboxed / 'a.txt').write_bytes(b'abc')
    info = json.loads(run({'op': 'stat', 'path': str(sandboxed / 'a.txt')}))
    assert info['type'] == 'file' and info['size'] == 3
