"""Generate a small textbook-style PDF + HTML pair used by the local tests.

The HTML is deliberately defective: one figure is missing, one figure is a
different picture, a question is dropped, a heading level is wrong and two
sections are swapped — exactly the classes of problem Phase 2 must detect.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
WIDTH, HEIGHT = A4


def make_decoy(path: str) -> None:
    """A picture with no visual relationship to any real figure."""
    image = Image.new("RGB", (420, 320), (18, 24, 40))
    draw = ImageDraw.Draw(image)
    for i in range(60):
        x, y = (i * 47) % 400, (i * 83) % 300
        draw.pieslice([x, y, x + 90, y + 70], start=i * 6, end=i * 6 + 200,
                      fill=(220 - i * 3, 90 + i, 40 + i * 2))
    draw.text((20, 290), "Unrelated stock photo", fill="white")
    image.save(path)


def make_figure(path: str, seed: int, label: str) -> None:
    image = Image.new("RGB", (480, 300), "white")
    draw = ImageDraw.Draw(image)
    for i in range(8):
        offset = (i * 37 + seed * 11) % 200
        draw.rectangle([20 + offset, 30 + i * 25, 200 + offset, 60 + i * 25],
                       outline=(seed * 40 % 255, 60 + i * 20, 200 - i * 15), width=4)
        draw.ellipse([260 + (i * 13 + seed) % 90, 20 + i * 28, 340 + (i * 13) % 90, 90 + i * 28],
                     outline=(20 + i * 25, seed * 30 % 255, 120), width=3)
    draw.text((30, 270), label, fill="black")
    image.save(path)


def build_pdf(path: str, figures) -> None:
    pdf = canvas.Canvas(path, pagesize=A4)
    pdf.setTitle("Light - Reflection and Refraction")
    pdf.setAuthor("NCERT")

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(60, HEIGHT - 70, "Chapter 10  Light: Reflection and Refraction")
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(60, HEIGHT - 110, "10.1 Reflection of Light")
    pdf.setFont("Helvetica", 11)
    body = [
        "A highly polished surface, such as a mirror, reflects most of the light falling on it.",
        "The law of reflection states that the angle of incidence is equal to the angle of",
        "reflection, and that the incident ray, the reflected ray and the normal all lie in the",
        "same plane. These laws hold for every reflecting surface, plane or spherical.",
    ]
    y = HEIGHT - 135
    for line in body:
        pdf.drawString(60, y, line)
        y -= 16
    pdf.drawImage(ImageReader(figures[0]), 60, y - 210, width=300, height=190)
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(60, y - 224, "Figure 10.1 Reflection of light by a plane mirror")
    pdf.showPage()

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(60, HEIGHT - 70, "10.2 Spherical Mirrors")
    pdf.setFont("Helvetica", 11)
    body2 = [
        "The reflecting surface of a spherical mirror may be curved inwards or outwards.",
        "A spherical mirror whose reflecting surface is curved inwards is called a concave",
        "mirror, and one curved outwards is called a convex mirror.",
    ]
    y = HEIGHT - 100
    for line in body2:
        pdf.drawString(60, y, line)
        y -= 16
    pdf.drawImage(ImageReader(figures[1]), 60, y - 210, width=300, height=190)
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(60, y - 224, "Figure 10.2 Concave and convex mirrors")

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(60, y - 260, "10.3 Refraction of Light")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(60, y - 282, "Light bends while travelling from one medium into another. This")
    pdf.drawString(60, y - 298, "bending of light is called refraction of light.")
    pdf.drawImage(ImageReader(figures[2]), 60, y - 510, width=300, height=190)
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(60, y - 524, "Figure 10.3 Refraction through a glass slab")
    pdf.showPage()

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(60, HEIGHT - 70, "Exercises")
    pdf.setFont("Helvetica", 11)
    questions = [
        "1. Define the principal focus of a concave mirror.",
        "2. The radius of curvature of a spherical mirror is 20 cm. What is its focal length?",
        "3. Name a mirror that can give an erect and enlarged image of an object.",
        "4. Why do we prefer a convex mirror as a rear-view mirror in vehicles?",
        "5. State the laws of refraction of light and explain the refractive index.",
    ]
    y = HEIGHT - 105
    for question in questions:
        pdf.drawString(60, y, question)
        y -= 22
    pdf.showPage()
    pdf.save()


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Light - Reflection and Refraction</title>
<meta name="description" content="Chapter 10 for Class 10 Science">
<script type="application/json" id="chapter-data">{{"chapter": 10, "subject": "Science"}}</script>
</head>
<body>
<h1>Chapter 10 Light: Reflection and Refraction</h1>

<h2>10.1 Reflection of Light</h2>
<p>A highly polished surface, such as a mirror, reflects most of the light falling on it.
The law of reflection states that the angle of incidence is equal to the angle of reflection,
and that the incident ray, the reflected ray and the normal all lie in the same plane.
These laws hold for every reflecting surface, plane or spherical.</p>
<figure><img src="{fig1}" alt="Figure 10.1"><figcaption>Figure 10.1 Reflection of light by a plane mirror</figcaption></figure>

<h4>10.3 Refraction of Light</h4>
<p>Light bends while travelling from one medium into another. This bending of light is called
refraction of light.</p>
<figure><img src="{fig_wrong}" alt=""><figcaption>Figure 10.3 Refraction through a glass slab</figcaption></figure>

<h2>10.2 Spherical Mirrors</h2>
<p>The reflecting surface of a spherical mirror may be curved inwards or outwards. A spherical
mirror whose reflecting surface is curved inwards is called a concave mirror, and one curved
outwards is called a convex mirror.</p>

<h2>Exercises</h2>
<ol>
<li>Define the principal focus of a concave mirror.</li>
<li>The radius of curvature of a spherical mirror is 20 cm. What is its focal length?</li>
<li>Name a mirror that can give an erect and enlarged image of an object.</li>
<li>State the laws of refraction of light and explain the refractive index.</li>
</ol>
<p style="opacity:0.15">Sample copy - do not copy</p>
</body>
</html>
"""


def main() -> None:
    os.makedirs(FIXTURES, exist_ok=True)
    figures = []
    for index, label in enumerate(["Fig 10.1", "Fig 10.2", "Fig 10.3"]):
        path = os.path.join(FIXTURES, f"figure_{index + 1}.png")
        make_figure(path, index + 1, label)
        figures.append(path)
    decoy = os.path.join(FIXTURES, "figure_4.png")
    make_decoy(decoy)
    figures.append(decoy)

    pdf_path = os.path.join(FIXTURES, "chapter.pdf")
    build_pdf(pdf_path, figures)

    html_path = os.path.join(FIXTURES, "chapter.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(HTML_TEMPLATE.format(
            fig1=os.path.abspath(figures[0]).replace("\\", "/"),
            fig_wrong=os.path.abspath(figures[3]).replace("\\", "/"),
        ))
    print("wrote", pdf_path, "and", html_path)


if __name__ == "__main__":
    main()
