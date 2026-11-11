"""The synthetic benchmark: two PDFs, one CSV and 24 labelled questions.

Everything here is invented for testing (a fictional transit authority and library). Relevance is
defined by *gold phrases*: a passage is relevant to a question if it contains the question's gold
phrase. Answers are checked by gold answer text; data questions by the operation and the number.
The files are generated, so the benchmark is identical on every machine.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

Kind = Literal["fact", "refusal", "data"]

TRANSIT = [
    (
        "Fares and passes",
        "A single ride on any Harbor City bus or tram costs 2.50 dollars when paid with the "
        "Tidecard. Paying with cash costs 3.00 dollars and needs the exact amount. Transfers "
        "are free within 90 minutes of the first tap. A 7-day pass costs 24 dollars and a "
        "monthly pass costs 85 dollars. Riders aged 65 and over pay half fare with a Senior "
        "Tidecard. Children under 6 ride free with a paying adult.",
    ),
    (
        "Hours of service",
        "Buses run from 5:00 in the morning until 1:00 at night on weekdays. On weekends the "
        "first bus leaves at 6:30. The Night Owl route N1 runs every 40 minutes between "
        "midnight and 5:00, connecting the airport with the central station. Trams stop at "
        "midnight every day.",
    ),
    (
        "Bikes and luggage",
        "Each bus has a front rack that holds two bicycles. Folding bikes may be carried on "
        "board at any time if they are folded. Full-size bikes are allowed on trams outside "
        "rush hours, which are 7:00 to 9:30 and 16:30 to 18:30 on weekdays. Luggage must not "
        "block the aisle.",
    ),
    (
        "Lost and found",
        "Items left on a vehicle are kept at the central station office for 30 days. After "
        "that, unclaimed items are donated to charity. Riders can report a lost item online "
        "or by calling 555-0142 between 8:00 and 17:00.",
    ),
    (
        "Accessibility",
        "Every bus has a ramp and a kneeling feature. Tram platforms are level with the doors "
        "at all 38 stops. A free companion may travel with a rider who holds an Access "
        "Tidecard. Guide dogs and service animals are always welcome on board.",
    ),
]

LIBRARY = [
    (
        "Becoming a member",
        "Membership of the Greenfield Library is free for anyone who lives, works or studies "
        "in Greenfield. Bring a photo ID and proof of address to any branch. Members under 16 "
        "need a parent or guardian to sign the form.",
    ),
    (
        "Borrowing",
        "Adult members can borrow up to 12 items at a time. Books are lent for 21 days and "
        "DVDs for 7 days. A loan can be renewed twice, online or at the desk, unless another "
        "member has reserved the item. Reservations cost nothing.",
    ),
    (
        "Late returns",
        "The late fee is 0.20 per day for each book, up to a maximum of 6.00 per item. "
        "Children's books have no late fees. A member who owes more than 10.00 cannot borrow "
        "until the balance is paid.",
    ),
    (
        "Rooms and printing",
        "Study rooms can be booked for up to 3 hours a day, free of charge. Black-and-white "
        "printing costs 0.10 per page and colour printing costs 0.50 per page. The Maker Lab "
        "has two 3D printers that members can book after a 30-minute safety session.",
    ),
    (
        "Opening hours",
        "The Central branch is open from 9:00 to 20:00, Monday to Friday, and from 10:00 to "
        "17:00 on Saturday. All branches are closed on Sunday. The book drop outside the "
        "Central branch is open all the time.",
    ),
]


@dataclass
class Question:
    id: str
    kind: Kind
    question: str
    gold_phrase: str | None = None  # relevant passages contain this
    gold_answer: list[str] = field(default_factory=list)  # any of these in the answer = correct
    gold_op: str | None = None  # data questions
    gold_number: float | None = None


QUESTIONS: list[Question] = [
    Question(
        "t1",
        "fact",
        "How much does a single ride cost with the Tidecard?",
        "costs 2.50 dollars when paid with the Tidecard",
        ["2.50"],
    ),
    Question(
        "t2", "fact", "How long are transfers free?", "free within 90 minutes", ["90 minutes"]
    ),
    Question(
        "t3", "fact", "What does a monthly pass cost?", "monthly pass costs 85 dollars", ["85"]
    ),
    Question(
        "t4",
        "fact",
        "When does the first bus leave on weekends?",
        "first bus leaves at 6:30",
        ["6:30"],
    ),
    Question(
        "t5",
        "fact",
        "How often does the Night Owl route run?",
        "runs every 40 minutes",
        ["40 minutes"],
    ),
    Question(
        "t6", "fact", "How many bicycles fit on a bus rack?", "holds two bicycles", ["two", "2"]
    ),
    Question(
        "t7",
        "fact",
        "How long are lost items kept?",
        "kept at the central station office for 30 days",
        ["30 days"],
    ),
    Question("t8", "fact", "How many tram stops have level platforms?", "at all 38 stops", ["38"]),
    Question(
        "l1", "fact", "How many items can an adult member borrow?", "borrow up to 12 items", ["12"]
    ),
    Question("l2", "fact", "How long are DVDs lent for?", "DVDs for 7 days", ["7 days"]),
    Question(
        "l3",
        "fact",
        "What is the maximum late fee per item?",
        "maximum of 6.00 per item",
        ["6.00", "6"],
    ),
    Question(
        "l4",
        "fact",
        "How much does colour printing cost?",
        "colour printing costs 0.50 per page",
        ["0.50"],
    ),
    Question(
        "l5",
        "fact",
        "How long can a study room be booked?",
        "booked for up to 3 hours a day",
        ["3 hours"],
    ),
    Question(
        "l6",
        "fact",
        "What time does the Central branch close on Saturday?",
        "10:00 to 17:00 on Saturday",
        ["17:00"],
    ),
    Question("r1", "refusal", "How much does parking cost at the central station?"),
    Question("r2", "refusal", "Who is the mayor of Harbor City?"),
    Question("r3", "refusal", "Does the library lend laptops?"),
    Question("r4", "refusal", "What is the fine for eating on a tram?"),
    Question(
        "d1", "data", "What is the total number of riders on the Red line?", gold_op="aggregate"
    ),
    Question(
        "d2", "data", "Which line has the highest average on-time percentage?", gold_op="aggregate"
    ),
    Question("d3", "data", "How many riders were there in total in March?", gold_op="aggregate"),
    Question(
        "d4",
        "data",
        "What is the average number of riders per month on the Blue line?",
        gold_op="aggregate",
    ),
    Question(
        "d5",
        "data",
        "Is there a relationship between riders and on-time percentage?",
        gold_op="relationship",
    ),
    Question(
        "d6",
        "data",
        "Does the on-time percentage differ between bus and tram lines?",
        gold_op="compare_groups",
    ),
]

LINES = [
    ("Red", "tram"),
    ("Blue", "tram"),
    ("Green", "bus"),
    ("Orange", "bus"),
    ("N1", "bus"),
    ("Harbor", "tram"),
]


def ridership() -> pd.DataFrame:
    rows = []
    for m in range(1, 13):
        for i, (line, mode) in enumerate(LINES):
            riders = 20000 + 4000 * i + 600 * m + (i * 97 * m) % 1500
            on_time = round(96.0 - 1.5 * i - (m % 4) * 0.4 + (0.6 if mode == "tram" else -0.9), 1)
            rows.append(
                {
                    "month": f"2025-{m:02d}",
                    "line": line,
                    "mode": mode,
                    "riders": riders,
                    "on_time_pct": on_time,
                }
            )
    return pd.DataFrame(rows)


def _gold_numbers() -> None:
    frame = ridership()
    by_q = {q.id: q for q in QUESTIONS}
    by_q["d1"].gold_number = float(frame.loc[frame.line == "Red", "riders"].sum())
    by_q["d2"].gold_number = float(frame.groupby("line")["on_time_pct"].mean().max())
    by_q["d3"].gold_number = float(frame.loc[frame.month == "2025-03", "riders"].sum())
    by_q["d4"].gold_number = float(frame.loc[frame.line == "Blue", "riders"].mean())
    r = frame["riders"].corr(frame["on_time_pct"])
    by_q["d5"].gold_number = round(float(r), 2)
    tram = frame.loc[frame["mode"] == "tram", "on_time_pct"].mean()
    bus = frame.loc[frame["mode"] == "bus", "on_time_pct"].mean()
    by_q["d6"].gold_number = round(float(tram - bus), 2)


_gold_numbers()


def _pdf(title: str, sections: list[tuple[str, str]]) -> bytes:
    import pymupdf

    from outlawz.ingest.extractors.pdf import MUPDF_LOCK

    with MUPDF_LOCK:
        doc = pymupdf.open()
        doc.set_metadata({"title": title})
        for number, (heading, text) in enumerate(sections, start=1):
            page = doc.new_page()
            page.insert_text((72, 46), title, fontsize=9)
            page.insert_text((72, 100), heading, fontsize=18)
            page.insert_textbox(
                pymupdf.Rect(72, 120, 523, 700), text, fontsize=11.5, lineheight=1.35
            )
            page.insert_text((290, 815), str(number), fontsize=9)
        doc.set_toc([[1, h, i] for i, (h, _) in enumerate(sections, start=1)])
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return bytes(data)


FILES = {
    "harbor_city_transit_guide.pdf": lambda: _pdf("Harbor City Transit: Rider Guide", TRANSIT),
    "greenfield_library_handbook.pdf": lambda: _pdf("Greenfield Library: Member Handbook", LIBRARY),
    "transit_ridership.csv": lambda: ridership().to_csv(index=False).encode(),
}
CSV_NAME = "transit_ridership.csv"


# Bump when the benchmark's files or questions change, so old processed output isn't reused.
BENCH_VERSION = 1


def write_files(folder: Path) -> list[Path]:
    """Write the benchmark files into ``folder`` if they aren't there yet. (Generated PDFs embed
    dates and ids, so they're written once rather than compared.)"""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, make in FILES.items():
        path = folder / name
        if not path.exists():
            from outlawz.store.files import write_bytes_atomic

            write_bytes_atomic(path, make())
        paths.append(path)
    return paths


def csv_frame() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(ridership().to_csv(index=False)))
