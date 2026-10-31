"""The command line Portal's Windows sandbox runs (windows/sandbox_run.py)."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'windows'))
import sandbox_run  # noqa: E402

FLAG = '--experimental-test-isolation=none'


@pytest.mark.parametrize('command, expected', [
    ('node --test', f'node {FLAG} --test'),
    ('node --test arcade.test.js', f'node {FLAG} --test arcade.test.js'),
    ('cd arcade && node --test arcade.test.js', f'cd arcade && node {FLAG} --test arcade.test.js'),
    ('node.exe --test --test-reporter=spec', f'node.exe {FLAG} --test --test-reporter=spec'),
    (r'C:\portal\node\node.exe --test a.js', rf'C:\portal\node\node.exe {FLAG} --test a.js'),
    ('node a.js && node --test b.js', f'node a.js && node {FLAG} --test b.js'),
])
def test_node_test_runs_in_process_so_it_cannot_hang_in_the_container(command, expected):
    assert sandbox_run.with_test_isolation(command) == expected


@pytest.mark.parametrize('command', [
    f'node {FLAG} --test a.js',
    'node --experimental-test-isolation=none --test',
    'node arcade.test.js',
    'node --test-name-pattern=x a.js',
    'nodemon --test a.js',
    'echo node --testing',
    'node a.js && echo --test',
])
def test_other_commands_are_left_alone(command):
    assert sandbox_run.with_test_isolation(command) == command


def test_each_action_takes_exactly_what_setup_and_uninstall_pass():
    # Uninstall runs teardown with only --profile and --workspace (live 2026-11-01:
    # it was refused for a missing --path and uninstall stopped half way).
    base = ['--profile', 'ODS.Portal.Sandbox.x', '--workspace', 'C:\\ws'] if os.name == 'nt' else ['--profile', 'p', '--workspace', '/ws']
    assert sandbox_run.parse(['teardown'] + base).action == 'teardown'
    assert sandbox_run.parse(['setup'] + base + ['--read', base[-1]]).read == [base[-1]]
    run = sandbox_run.parse(['run'] + base + ['--path', 'x', '--', 'node', '-v'])
    assert run.command == ['node', '-v']
    with pytest.raises(SystemExit):
        sandbox_run.parse(['run'] + base + ['--path', 'x'])


def test_the_command_line_still_passes_verbatim_to_cmd():
    assert sandbox_run.build_command_line('node -e "console.log(1)"') == 'cmd.exe /d /s /c "node -e "console.log(1)""'
