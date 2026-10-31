"""The Windows preview service's snapshot endpoints (windows/preview_service.py).

The Portal workbench reads these through dashboard /pixel-preview/ -> edge
/preview/ -> this server, and checks them against the publish receipt.
"""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'windows'))
import preview_service  # noqa: E402


@pytest.fixture
def service(tmp_path):
    workspace, snapshots = tmp_path / 'workspace', tmp_path / 'snapshots'
    (workspace / 'game').mkdir(parents=True)
    snapshots.mkdir()
    server = preview_service.PreviewServer(0, str(snapshots))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield workspace, snapshots, server.server_port
    server.shutdown()
    server.server_close()


def publish(workspace, snapshots, port, files):
    for name, text in files.items():
        (workspace / 'game' / name).write_text(text, encoding='utf-8', newline='\n')
    return preview_service.publish({'schemaVersion': 1, 'action': 'publish', 'relativeDirectory': 'game'},
                                   str(workspace), str(snapshots), port)


def fetch(port, site_id, tail, method='GET'):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request(method, f'/{site_id}/{tail}', headers={'Host': f'{site_id}.localhost:{port}'})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_the_manifest_matches_the_publish_receipt_the_dashboard_checks(service):
    workspace, snapshots, port = service
    receipt = publish(workspace, snapshots, port, {'index.html': '<h1>0</h1>', 'game.js': 'let s = 0;\n'})
    status, headers, body = fetch(port, receipt['siteId'], '__ods_manifest__.json')
    manifest = json.loads(body)
    assert status == 200 and headers['Content-Type'] == 'application/json'
    assert manifest['siteId'] == receipt['siteId'] and manifest['sha256'] == receipt['sha256']
    assert manifest['bytes'] == receipt['bytes'] and len(manifest['files']) == receipt['files']
    assert {entry['path']: entry['sha256'] for entry in manifest['files']}['index.html'] == receipt['entrySha256']
    assert headers['X-Preview-SHA256'] == hashlib.sha256(body).hexdigest()


def test_the_view_is_the_entry_page_with_only_the_viewer_style_appended(service):
    workspace, snapshots, port = service
    receipt = publish(workspace, snapshots, port, {'index.html': '<!doctype html><h1>0</h1>'})
    _, _, entry = fetch(port, receipt['siteId'], '')
    status, headers, view = fetch(port, receipt['siteId'], '__ods_view__.html')
    assert status == 200 and view == entry + preview_service.VIEW_STYLE
    assert headers['Access-Control-Allow-Origin'] == '*' and headers['Cross-Origin-Resource-Policy'] == 'cross-origin'
    status, headers, body = fetch(port, receipt['siteId'], '__ods_view__.html', method='HEAD')
    assert status == 200 and body == b'' and headers['Content-Length'] == str(len(view))


def test_changes_compare_two_publications_line_by_line(service):
    workspace, snapshots, port = service
    first = publish(workspace, snapshots, port, {'index.html': '<h1>0</h1>\n', 'game.js': 'let s = 0;\n'})
    second = publish(workspace, snapshots, port, {'index.html': '<h1>0</h1>\n', 'game.js': 'let s = 1;\n'})
    _, _, body = fetch(port, first['siteId'], '__ods_changes__/initial.json')
    initial = json.loads(body)
    assert [change['change'] for change in initial['changes']] == ['published', 'published']
    _, _, body = fetch(port, second['siteId'], f"__ods_changes__/{first['siteId']}.json")
    changes = json.loads(body)
    assert changes['beforeSiteId'] == first['siteId'] and changes['sha256'] == second['sha256']
    (change,) = changes['changes']
    assert change['path'] == 'game.js' and change['change'] == 'modified'
    assert (change['additions'], change['deletions']) == (1, 1)
    assert [row['type'] for row in change['diff']] == ['remove', 'add']


@pytest.mark.parametrize('tail', ['__ods_source__/source-0123456789abcdef01234567.json', '__ods_other__.json',
                                  '__ods_changes__/nope.json', '__ods_manifest__.json?x=1'])
def test_other_reserved_names_and_queried_metadata_are_not_served(service, tail):
    workspace, snapshots, port = service
    receipt = publish(workspace, snapshots, port, {'index.html': '<h1>0</h1>'})
    assert fetch(port, receipt['siteId'], tail)[0] == 404


def test_a_snapshot_changed_on_disk_is_not_described(service):
    workspace, snapshots, port = service
    receipt = publish(workspace, snapshots, port, {'index.html': '<h1>0</h1>'})
    (snapshots / receipt['siteId'] / 'index.html').write_text('<h1>tampered</h1>', encoding='utf-8')
    assert fetch(port, receipt['siteId'], '__ods_manifest__.json')[0] == 404
