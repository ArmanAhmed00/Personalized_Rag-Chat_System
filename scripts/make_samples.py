"""Build the sample documents shipped with Outlawz (``backend/outlawz/samples``).

* ``Home Composting Guide.pdf``: a short original guide with headings, a table and numbers.
* ``The Lighthouse Keeper.epub``: a short original story in three chapters.
* ``lamp_sales_2024.csv`` and ``lamp_sales_2025.csv``: synthetic sales data with a few deliberate
  quirks (duplicates, outliers, gaps, a renamed product line, a column type change) so the
  overview and Compare have something to find.

The text was written for Outlawz and is dedicated to the public domain (CC0 1.0). The data is
random, generated from a fixed seed, so running this again gives the same files.

    python scripts/make_samples.py
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pymupdf

OUT = Path(__file__).resolve().parents[1] / "backend" / "outlawz" / "samples"

GUIDE = [
    (
        "Why compost at home",
        [
            "About a third of what a typical household throws away is food scraps and garden "
            "waste. Composting turns that into a dark, crumbly soil improver in three to twelve "
            "months, depending on how often the heap is turned.",
            "A home compost bin also cuts the weight of the rubbish bin, which matters where "
            "collection is charged by weight. Most households that start composting say the "
            "kitchen bin needs emptying about half as often.",
        ],
    ),
    (
        "Greens and browns",
        [
            "Compost needs two kinds of material. Greens are wet and rich in nitrogen: fruit and "
            "vegetable scraps, coffee grounds, tea leaves and fresh grass clippings. Browns are "
            "dry and rich in carbon: dry leaves, shredded cardboard, egg boxes and straw.",
            "A good working mix is roughly three parts browns to one part greens by volume. Too "
            "many greens make a wet, smelly heap; too many browns make a heap that barely warms "
            "up. The table below gives typical carbon-to-nitrogen ratios.",
        ],
    ),
    (
        "Keeping the heap healthy",
        [
            "Turn the heap every two to three weeks with a garden fork to let air in. A working "
            "heap feels like a wrung-out sponge: damp, but not dripping. In the middle of an "
            "active heap the temperature can reach 55 to 65 degrees Celsius, hot enough to kill "
            "most weed seeds.",
            "Never add meat, fish, dairy, cooked food with oil, or pet waste. They attract rats "
            "and can carry disease. Diseased plants and perennial weed roots are also best kept "
            "out of a cool home heap.",
        ],
    ),
    (
        "When it's ready",
        [
            "Finished compost is dark brown, smells of earth, and you can no longer recognise "
            "what went in, apart from the odd eggshell. Sieve out anything coarse and put it back "
            "in the bin to break down further.",
            "Spread a layer about 5 centimetres deep around shrubs and on vegetable beds in "
            "spring, or mix one part compost with three parts soil for planting pots.",
        ],
    ),
]
RATIOS = [
    ("Material", "Type", "C:N ratio"),
    ("Grass clippings", "Green", "20:1"),
    ("Vegetable scraps", "Green", "25:1"),
    ("Coffee grounds", "Green", "20:1"),
    ("Dry leaves", "Brown", "60:1"),
    ("Cardboard", "Brown", "350:1"),
    ("Straw", "Brown", "80:1"),
]

STORY = [
    (
        "The Last Lamp",
        [
            "Ines Calder took charge of the Farrow Point light on the first of March, 1951, the "
            "year the old keeper retired after thirty-one years. The tower stood forty-two metres "
            "above the rocks and its lamp could be seen twenty-three nautical miles out to sea.",
            "Her first job each evening was to wind the clockwork that turned the lens. It took "
            "four minutes of steady cranking and lasted the whole night, so long as nobody forgot.",
        ],
    ),
    (
        "The Storm of November",
        [
            "On the night of the eleventh of November, a gale broke two panes in the lantern "
            "room. Ines climbed the one hundred and eighteen steps six times before dawn, "
            "carrying canvas and tar to keep the wind off the flame.",
            "In the logbook she wrote only: Wind north-west, force ten. Two panes lost. Light "
            "kept. Three ships passed safely.",
        ],
    ),
    (
        "The Relief Boat",
        [
            "The relief boat came every fourteen days with paraffin, letters and fresh bread. "
            "When the sea was too rough to land, the crew threw the post up on a line and Ines "
            "hauled it in by hand.",
            "She kept the light for nineteen years, until the station was automated in 1970. "
            "Her logbooks are still read by visitors to the small museum at the foot of the tower.",
        ],
    ),
]


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Home Composting Guide", "author": "Outlawz samples (CC0)"})
    for number, (heading, paragraphs) in enumerate(GUIDE, start=1):
        page = doc.new_page()
        page.insert_text((72, 48), "Home Composting Guide", fontsize=9)
        page.insert_text((72, 100), heading, fontsize=20)
        y = 125
        for paragraph in paragraphs:
            rect = pymupdf.Rect(72, y, 523, y + 200)
            page.insert_textbox(rect, paragraph, fontsize=11.5, lineheight=1.35)
            y += 24 + 15 * (len(paragraph) // 78 + 1)
        if number == 2:
            top = y + 10
            xs = [72, 240, 360, 480]
            for r, row in enumerate(RATIOS):
                for c, cell in enumerate(row):
                    page.insert_text((xs[c] + 6, top + 18 + r * 24), cell, fontsize=10.5)
            for r in range(len(RATIOS) + 1):
                page.draw_line((72, top + r * 24), (480, top + r * 24))
            for x in xs:
                page.draw_line((x, top), (x, top + len(RATIOS) * 24))
        page.insert_text((290, 815), str(number), fontsize=9)
    doc.set_toc([[1, h, i] for i, (h, _) in enumerate(GUIDE, start=1)])
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def make_epub(path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine, nav = [], []
        for i, (title, paragraphs) in enumerate(STORY, start=1):
            manifest.append(
                f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<li><a href="c{i}.xhtml">{title}</a></li>')
            body = "".join(f"<p>{p}</p>" for p in paragraphs)
            zf.writestr(
                f"OEBPS/c{i}.xhtml",
                '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                f"<head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>",
            )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
            f'<nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?><package version="3.0" '
            'xmlns="http://www.idpf.org/2007/opf" unique-identifier="id"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">'
            "outlawz-sample-lighthouse</dc:identifier><dc:title>The Lighthouse Keeper</dc:title>"
            "<dc:creator>Outlawz samples</dc:creator>"
            "<dc:rights>CC0 1.0 (public domain)</dc:rights>"
            '<dc:language>en</dc:language><meta property="dcterms:modified">2026-01-01T00:00:00Z'
            f"</meta></metadata><manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}"
            "</spine></package>",
        )
    path.write_bytes(buf.getvalue())


PRICES = {
    "Desk Lamp": 39.0,
    "Floor Lamp": 89.0,
    "Bulb Pack": 12.5,
    "Lamp Shade": 24.0,
    "Smart Plug": 18.0,
}


def sales_rows(year: int, n: int, seed: int) -> tuple[list[str], list[list[str]]]:
    rng = np.random.default_rng(seed)
    regions = ["North", "South", "East", "West"] + (["Central"] if year == 2025 else [])
    region_p = [0.3, 0.25, 0.25, 0.2] if year == 2024 else [0.26, 0.22, 0.22, 0.18, 0.12]
    products = list(PRICES)
    if year == 2025:
        products = [p for p in products if p != "Smart Plug"] + ["Reading Lamp"]
    price_up = 1.0 if year == 2024 else 1.08
    channels = ["Online", "Store", "Partner"]
    header = ["order_id", "date", "region", "channel", "product", "units", "unit_price"]
    header += (
        ["discount_pct", "revenue", "returned", "rating"]
        if year == 2024
        else ["revenue", "returned", "rating", "coupon"]
    )
    rows = []
    start = date(year, 1, 1)
    for i in range(n):
        day = start + timedelta(days=int(rng.integers(0, 365)))
        region = str(rng.choice(regions, p=region_p))
        channel = str(rng.choice(channels, p=[0.5, 0.35, 0.15]))
        product = str(rng.choice(products))
        base = PRICES.get(product, 49.0)
        units = int(min(30, 1 + rng.poisson(3 if channel != "Partner" else 6)))
        if rng.random() < 0.006:
            units = int(rng.integers(80, 150))  # occasional bulk order (an outlier)
        price = round(base * price_up * (1 + rng.normal(0, 0.03)), 2)
        discount = int(rng.choice([0, 0, 0, 5, 10, 15]))
        revenue = round(units * price * (1 - discount / 100), 2)
        returned = "yes" if rng.random() < (0.06 if product != "Floor Lamp" else 0.11) else "no"
        rating = (
            ""
            if rng.random() < 0.15
            else str(int(rng.choice([1, 2, 3, 4, 5], p=[0.04, 0.06, 0.2, 0.4, 0.3])))
        )
        row = [
            f"S{str(year)[2:]}-{i + 1:04d}",
            day.isoformat(),
            region,
            channel,
            product,
            str(units),
            f"{price:.2f}",
        ]
        if year == 2024:
            row += [str(discount), f"{revenue:.2f}", returned, rating]
        else:
            if rating == "" and rng.random() < 0.5:
                rating = "n/a"  # written as text this year: the column's type changes
            coupon = (
                str(rng.choice(["SPRING10", "LAMPDAY", "WELCOME5"])) if rng.random() < 0.18 else ""
            )
            row += [f"{revenue:.2f}", returned, rating, coupon]
        rows.append(row)
    rows.sort(key=lambda r: r[1])
    for k in (17, 233, 401):  # a few exact duplicate rows to find
        rows.insert(k + 1, list(rows[k]))
    return header, rows


def make_csv(path: Path, year: int, n: int, seed: int) -> None:
    header, rows = sales_rows(year, n, seed)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


README = """# Sample documents

These files let a first-time user try every part of Outlawz in one click
("Try it with the sample documents").

| File | What it is |
|---|---|
| Home Composting Guide.pdf | A four-page guide with headings, a table and numbers |
| The Lighthouse Keeper.epub | A short story in three chapters |
| lamp_sales_2024.csv | 723 synthetic sales rows (with duplicates, outliers and gaps) |
| lamp_sales_2025.csv | The next year (new region, changed products, a type change), for Compare |

The guide and the story were written for Outlawz and are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). The sales data is random, made from
a fixed seed by `scripts/make_samples.py`, and describes no real company.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_pdf(OUT / "Home Composting Guide.pdf")
    make_epub(OUT / "The Lighthouse Keeper.epub")
    make_csv(OUT / "lamp_sales_2024.csv", 2024, 720, seed=2024)
    make_csv(OUT / "lamp_sales_2025.csv", 2025, 760, seed=2025)
    (OUT / "README.md").write_text(README, encoding="utf-8")
    for item in sorted(OUT.iterdir()):
        print(f"{item.name}: {item.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
