"""The publisher's delivery instructions, pinned as tests.

Added images must be referenced as kerla_new_NN.png on the CDN base, numbered
continuously across a book (never restarting per chapter), and the English and
Malayalam renditions of a chapter must share one section structure.
"""

import asyncio

from services.cdn_uploader import push_images_to_cdn, rewrite_to_cdn
from services.html_generator import CDN_IMAGE_BASE, CDN_IMAGE_PREFIX, assign_cdn_names
from services.structure_compare import compare_structures, skeleton

FIG = "https://res.cloudinary.com/demo/image/upload/v1/document-correction/figures"


def _page(*imgs: str) -> str:
    body = "".join(f'<figure><img src="{src}" data-dcp-fig="1"></figure>' for src in imgs)
    return f"<html><body><p>intro</p>{body}</body></html>"


class TestCdnImageNaming:
    def test_names_are_sequential_in_document_order(self):
        mapping = []
        assign_cdn_names(_page(f"{FIG}/aaa.png", f"{FIG}/bbb.png"), mapping)
        assert [m["name"] for m in mapping] == [
            f"{CDN_IMAGE_PREFIX}01.png", f"{CDN_IMAGE_PREFIX}02.png"]
        assert mapping[0]["cdnUrl"] == CDN_IMAGE_BASE + mapping[0]["name"]

    def test_repeated_figure_keeps_one_name(self):
        mapping = []
        assign_cdn_names(_page(f"{FIG}/aaa.png", f"{FIG}/aaa.png"), mapping)
        assert len(mapping) == 1

    def test_numbering_continues_across_deliverables(self):
        # the complete rendition is named first; the corrected HTML shares the
        # map, so a figure both contain keeps its name and new ones continue
        mapping = []
        assign_cdn_names(_page(f"{FIG}/aaa.png", f"{FIG}/bbb.png"), mapping)
        assign_cdn_names(_page(f"{FIG}/aaa.png", f"{FIG}/ccc.png"), mapping)
        assert [m["name"] for m in mapping] == [
            f"{CDN_IMAGE_PREFIX}01.png", f"{CDN_IMAGE_PREFIX}02.png",
            f"{CDN_IMAGE_PREFIX}03.png"]

    def test_chapter_start_number_does_not_restart_the_book(self):
        # chapter 1 ended at 03 -> this chapter is processed with start=4
        mapping = []
        assign_cdn_names(_page(f"{FIG}/ddd.png"), mapping, start=4)
        assert mapping[0]["name"] == f"{CDN_IMAGE_PREFIX}04.png"

    def test_template_images_and_data_uris_are_left_alone(self):
        html = ('<html><body>'
                '<img src="https://cdn.example.com/site/banner.png">'
                '<img src="data:image/png;base64,AAAA" data-dcp-fig="1">'
                '</body></html>')
        mapping = []
        result = assign_cdn_names(html, mapping)
        assert mapping == []
        assert result == html

    def test_figures_folder_src_is_recognised_without_the_attribute(self):
        # corrected HTML built by the correction engine carries our hosted
        # sources but not the data attribute — the folder marker catches them
        html = f'<html><body><img src="{FIG}/eee.png"></body></html>'
        mapping = []
        result = assign_cdn_names(html, mapping)
        assert len(mapping) == 1
        assert f'data-cdn-name="{CDN_IMAGE_PREFIX}01.png"' in result


class _StubResponse:
    def __init__(self, content=b"", json_data=None):
        self.content = content
        self.text = ""
        self._json = json_data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _StubClient:
    """Mimics the publisher's upload service seen in its network traffic."""

    def __init__(self, fail=False):
        self.posted = []
        self.fail = fail

    async def get(self, url):
        return _StubResponse(content=b"png-bytes")

    async def post(self, url, files=None):
        if self.fail:
            raise RuntimeError("upload service down")
        name = files["files"][0]
        self.posted.append(name)
        return _StubResponse(json_data={"results": [{
            "filename": name, "status": "uploaded",
            "key": f"kerala_v2/html-images/{name}",
            "url": f"https://d1xu9delcvinxy.cloudfront.net/kerala_v2/html-images/{name}",
        }]})


class TestCdnPush:
    def test_figures_are_uploaded_under_their_delivery_names(self):
        mapping = [{"name": "kerla_new_01.png", "src": f"{FIG}/aaa.png",
                    "cdnUrl": CDN_IMAGE_BASE + "kerla_new_01.png"}]
        client = _StubClient()
        pushed = asyncio.run(push_images_to_cdn(mapping, client=client))
        assert pushed == 1
        assert client.posted == ["kerla_new_01.png"]
        assert mapping[0]["cdnUploaded"] is True
        assert mapping[0]["cdnUrl"].endswith("/kerla_new_01.png")

    def test_already_pushed_entries_are_skipped(self):
        mapping = [{"name": "kerla_new_01.png", "src": f"{FIG}/aaa.png",
                    "cdnUploaded": True}]
        client = _StubClient()
        assert asyncio.run(push_images_to_cdn(mapping, client=client)) == 0
        assert client.posted == []

    def test_a_failed_upload_is_not_fatal_and_not_marked(self):
        mapping = [{"name": "kerla_new_01.png", "src": f"{FIG}/aaa.png"}]
        pushed = asyncio.run(push_images_to_cdn(mapping, client=_StubClient(fail=True)))
        assert pushed == 0
        assert not mapping[0].get("cdnUploaded")

    def test_rewrite_touches_only_confirmed_uploads(self):
        html = f'<img src="{FIG}/aaa.png"><img src="{FIG}/bbb.png">'
        mapping = [
            {"name": "kerla_new_01.png", "src": f"{FIG}/aaa.png",
             "cdnUrl": CDN_IMAGE_BASE + "kerla_new_01.png", "cdnUploaded": True},
            {"name": "kerla_new_02.png", "src": f"{FIG}/bbb.png",
             "cdnUrl": CDN_IMAGE_BASE + "kerla_new_02.png"},  # not uploaded
        ]
        result = rewrite_to_cdn(html, mapping)
        assert CDN_IMAGE_BASE + "kerla_new_01.png" in result
        assert f"{FIG}/bbb.png" in result       # kept: its CDN file doesn't exist


def _chapter(sections):
    nav = "".join(f'<a href="#s{i}">{i}</a>' for i in range(len(sections)))
    panels = []
    for i, (questions, images) in enumerate(sections):
        content = "<h2>title</h2>" + "".join(
            f"<li>{n}. question text</li>" for n in questions)
        content += '<img src="x.png">' * images
        panels.append(f'<div id="s{i}"><p>text</p>{content}</div>')
    return f"<html><body><nav>{nav}</nav>{''.join(panels)}</body></html>"


class TestLanguageStructureParity:
    def test_matching_structures_pass(self):
        english = _chapter([( [1, 2], 1 ), ( [1, 2, 3], 0 )])
        malayalam = _chapter([( [1, 2], 1 ), ( [1, 2, 3], 0 )])
        report = compare_structures(english, malayalam, "English", "Malayalam")
        assert report["match"] is True
        assert report["problems"] == []

    def test_section_count_mismatch_is_reported(self):
        report = compare_structures(
            _chapter([([1], 0), ([1], 0)]), _chapter([([1], 0)]),
            "English", "Malayalam")
        assert report["match"] is False
        assert any("section count differs" in p for p in report["problems"])

    def test_missing_question_is_reported(self):
        report = compare_structures(
            _chapter([([1, 2, 3], 0)]), _chapter([([1, 3], 0)]),
            "English", "Malayalam")
        assert any("only in English" in p for p in report["problems"])
        # and the gap in Malayalam's own 1..N sequence is flagged too
        assert any("missing from the sequence" in p for p in report["problems"])

    def test_missing_image_is_reported(self):
        report = compare_structures(
            _chapter([([1], 2)]), _chapter([([1], 1)]),
            "English", "Malayalam")
        assert any("image(s)" in p for p in report["problems"])

    def test_skeleton_handles_documents_without_tabs(self):
        plain = "<html><body><h2>One</h2><p>1. q</p></body></html>"
        result = skeleton(plain)
        assert result["sectionCount"] == 1
        assert result["sections"][0]["questionNumbers"] == [1]
