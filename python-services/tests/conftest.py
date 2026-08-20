"""Shared fixtures. The PDF/HTML pair is generated once per session."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PDF_PATH = os.path.join(FIXTURES, "chapter.pdf")
HTML_PATH = os.path.join(FIXTURES, "chapter.html")


@pytest.fixture(scope="session", autouse=True)
def fixture_documents():
    """Build the sample chapter if it is not on disk yet."""
    if not (os.path.exists(PDF_PATH) and os.path.exists(HTML_PATH)):
        pytest.importorskip("reportlab", reason="reportlab is needed to build the fixtures")
        from tests.make_fixtures import main as build

        build()
    return PDF_PATH, HTML_PATH


@pytest.fixture(scope="session")
def pdf_analysis():
    from services.pdf_analyzer import PDFAnalyzer

    analyzer = PDFAnalyzer(path=PDF_PATH)
    analysis = analyzer.analyze()
    yield analyzer, analysis
    analyzer.close()


@pytest.fixture(scope="session")
async def html_analysis():
    from services.html_analyzer import HTMLAnalyzer

    analyzer = HTMLAnalyzer(path=HTML_PATH)
    analysis = await analyzer.analyze()
    return analyzer, analysis


@pytest.fixture
async def comparison(pdf_analysis, html_analysis):
    from services.comparison_engine import ComparisonEngine

    pdf_analyzer, pdf = pdf_analysis
    html_analyzer, html = html_analysis
    engine = ComparisonEngine(
        pdf, html, {**pdf_analyzer.pixel_cache, **html_analyzer.pixel_cache}
    )
    return engine, engine.generate_issues()
