import os
from unittest.mock import patch, MagicMock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.reveal_in_explorer import _resolve_output_path, post_reveal_in_explorer


_OUTPUT_DIR = os.path.abspath(os.path.join(os.path.sep, "comfy", "output"))


def _fake_output_dir(_type):
    return _OUTPUT_DIR


def test_resolves_filename_subfolder_and_type_into_an_absolute_path():
    path = _resolve_output_path(
        "filename=a.png&subfolder=sub&type=output",
        get_directory_by_type=_fake_output_dir,
    )
    assert path == os.path.join(_OUTPUT_DIR, "sub", "a.png")


def test_rejects_missing_filename():
    assert _resolve_output_path("subfolder=&type=output", get_directory_by_type=_fake_output_dir) is None


def test_rejects_path_traversal_in_filename():
    assert _resolve_output_path(
        "filename=..%2F..%2Fetc%2Fpasswd&type=output",
        get_directory_by_type=_fake_output_dir,
    ) is None


def test_rejects_subfolder_that_escapes_the_output_dir():
    assert _resolve_output_path(
        "filename=a.png&subfolder=..%2F..%2F..%2Fsecrets&type=output",
        get_directory_by_type=_fake_output_dir,
    ) is None


def test_unknown_type_returns_none():
    assert _resolve_output_path(
        "filename=a.png&type=bogus",
        get_directory_by_type=lambda _type: None,
    ) is None


class TestRevealInExplorerRoute(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_post("/smart_queue/reveal_in_explorer", post_reveal_in_explorer)
        return app

    @unittest_run_loop
    async def test_rejects_missing_thumbnail_path(self):
        resp = await self.client.post("/smart_queue/reveal_in_explorer", json={})
        assert resp.status == 400

    @unittest_run_loop
    @patch("backend.reveal_in_explorer._resolve_output_path")
    @patch("backend.reveal_in_explorer.os.path.isfile", return_value=False)
    async def test_reports_not_found_for_a_missing_file(self, mock_isfile, mock_resolve):
        mock_resolve.return_value = "/comfy/output/gone.png"
        resp = await self.client.post(
            "/smart_queue/reveal_in_explorer",
            json={"thumbnail_path": "filename=gone.png&type=output"},
        )
        assert resp.status == 404

    @unittest_run_loop
    @patch("backend.reveal_in_explorer._reveal")
    @patch("backend.reveal_in_explorer._resolve_output_path")
    @patch("backend.reveal_in_explorer.os.path.isfile", return_value=True)
    async def test_reveals_the_resolved_file(self, mock_isfile, mock_resolve, mock_reveal):
        mock_resolve.return_value = "/comfy/output/a.png"
        resp = await self.client.post(
            "/smart_queue/reveal_in_explorer",
            json={"thumbnail_path": "filename=a.png&type=output"},
        )
        body = await resp.json()
        assert body["ok"] is True
        mock_reveal.assert_called_once_with("/comfy/output/a.png")
