from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from sop_analysis import SOP_SHEETS, SopAnalysisError, analyze_sop_workbook
from sop_cli import build_parser, run_command
from sop_strengths import (
    MAX_STRENGTHS,
    analyze_sop_strengths,
    build_consultation_text,
    collect_strengths_data,
    select_program_strengths,
    write_strengths_outputs,
)


def build_frames(rows: list[dict[str, object]]) -> dict[str, pd.DataFrame]:
    """Build one populated source sheet and empty required companion sheets."""
    return {
        sheet: pd.DataFrame(rows) if sheet == SOP_SHEETS[0] else pd.DataFrame(columns=["Программа", "Дисциплина"])
        for sheet in SOP_SHEETS
    }


def build_row(discipline: str, quality: float | None) -> dict[str, object]:
    """Return a source row with unrelated numeric and textual fields."""
    return {
        "Программа": "Финансы", "Уровень": "Магистратура", "Дисциплина": discipline, "Курс": 1,
        "Общее качество (усредненная оценка)": quality,
        "Количество студентов": 2, "Средний отклик": 1.0,
        "Сложность («1» – легко, «5» - сложно)": 5.0,
        "Позитивные стороны, отмеченные в комментариях": None,
        "Обобщение комментариев по данному онлайн-курсу": "Только отрицательный отзыв.",
    }


class StrengthsTests(unittest.TestCase):
    def test_all_rows_contribute_and_disciplines_have_equal_weight(self) -> None:
        """Many positive teacher rows must not hide a poorly rated discipline."""
        frames = build_frames([build_row("A", 5.0), build_row("A", 5.0), build_row("A", 5.0), build_row("B", 1.0)])
        original: pd.DataFrame = frames[SOP_SHEETS[0]].copy(deep=True)
        programs, evidence, comments = collect_strengths_data(frames)

        self.assertEqual(programs, ["Финансы / Магистратура"])
        self.assertEqual(len(evidence), 4)
        self.assertEqual(select_program_strengths(evidence), [])
        self.assertEqual(comments, [])
        pd.testing.assert_frame_equal(frames[SOP_SHEETS[0]], original)

    def test_threshold_uses_unrounded_scores_and_keeps_source_rows(self) -> None:
        """A displayed 4.00 is insufficient unless the actual average reaches four."""
        _, below, _ = collect_strengths_data(build_frames([build_row("A", 3.9999)]))
        self.assertEqual(select_program_strengths(below), [])

        _, boundary, _ = collect_strengths_data(build_frames([build_row("A", 4.0)]))
        strengths = select_program_strengths(boundary)
        self.assertEqual(strengths[0]["evidence"][0]["source_row"], 2)
        self.assertEqual(strengths[0]["row_count"], 1)
        self.assertIn("В дисциплине «A», курс 1", build_consultation_text(strengths))
        self.assertIn("4,00 из 5", build_consultation_text(strengths))

    def test_summary_limits_distinct_aspects(self) -> None:
        """Repeated metrics on different sheets cannot fill all three slots."""
        row = build_row("A", 4.1)
        row.update({
            "Новизна содержания": 4.2, "Контакт и вежливость": 4.9,
            "Ясность требований и обратной связи": 4.3, "Привлекательность и интересность": 4.4,
        })
        frames = build_frames([row])
        frames[SOP_SHEETS[1]] = pd.DataFrame([row])
        _, evidence, _ = collect_strengths_data(frames)
        strengths = select_program_strengths(evidence)

        self.assertEqual(len(strengths), MAX_STRENGTHS)
        self.assertEqual(len({item["aspect"] for item in strengths}), MAX_STRENGTHS)
        self.assertEqual(strengths[0]["metric"], "Контакт и вежливость")
        self.assertNotIn("Сложность", str(strengths))
        self.assertNotIn("Количество студентов", str(strengths))

    def test_invalid_scores_are_reported_instead_of_dropping_the_column(self) -> None:
        """Invalid data must not make a metric silently disappear."""
        for value in [6.0, 0.0, "broken"]:
            with self.subTest(value=value):
                row = build_row("A", 5.0)
                row["Общее качество (усредненная оценка)"] = value
                with self.assertRaisesRegex(SopAnalysisError, "Excel row 2"):
                    collect_strengths_data(build_frames([row]))

    def test_programs_without_scores_and_different_levels_are_retained(self) -> None:
        """Missing scores must not become praise or merge program levels."""
        missing = build_row("A", None)
        bachelor = build_row("A", 4.6)
        bachelor["Уровень"] = "bachelor"
        frames = build_frames([missing, bachelor])
        with patch("sop_strengths.read_sop_workbook", return_value=frames):
            result = analyze_sop_strengths(Path("source.xlsx"), "4 модуль 2025/2026")

        self.assertEqual(len(result["programs"]), 2)
        master = next(item for item in result["programs"] if "Магистратура" in item["program"])
        self.assertEqual(master["consultation_text"], "")
        self.assertTrue(master["review_note"])

    def test_positive_comments_remain_source_material(self) -> None:
        """Unstructured comments must not introduce unsupported marketing claims."""
        row = build_row("A", 4.2)
        row["Позитивные стороны, отмеченные в комментариях"] = "Полезные проекты. Но есть замечания."
        _, evidence, comments = collect_strengths_data(build_frames([row]))

        self.assertEqual(comments[0]["text"], row["Позитивные стороны, отмеченные в комментариях"])
        self.assertEqual(comments[0]["source_row"], 2)
        self.assertNotIn("проекты", build_consultation_text(select_program_strengths(evidence)))

    def test_missing_identifiers_and_empty_workbook_fail_explicitly(self) -> None:
        """Missing source identity must not create an ambiguous positive claim."""
        for field in ["Программа", "Дисциплина"]:
            row = build_row("A", 5.0)
            row[field] = None
            with self.subTest(field=field), self.assertRaisesRegex(SopAnalysisError, "Excel row 2"):
                collect_strengths_data(build_frames([row]))
        with self.assertRaisesRegex(SopAnalysisError, "no program rows"):
            collect_strengths_data(build_frames([]))

    def test_export_contains_every_program_and_machine_readable_evidence(self) -> None:
        """Safe programs absent from the risk export still receive consultation cards."""
        frames = build_frames([build_row("A", 4.8)])
        with tempfile.TemporaryDirectory() as directory:
            root: Path = Path(directory)
            source: Path = root / "sop.xlsx"
            with pd.ExcelWriter(source, engine="openpyxl") as writer:
                for sheet, frame in frames.items():
                    frame.to_excel(writer, sheet_name=sheet, index=False)
            self.assertEqual(analyze_sop_workbook(source)["program_summaries"], [])
            args = build_parser().parse_args([
                "strengths", "--sop-xlsx", str(source), "--out-dir", str(root / "export"),
                "--module-label", "4 модуль 2025/2026",
            ])
            with patch("sop_cli.read_smtp_config") as smtp:
                self.assertEqual(run_command(args), 0)
                smtp.assert_not_called()
            output: Path = root / "export"
            with (output / "consultation_briefs.csv").open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["Программа"], "Финансы / Магистратура")
            self.assertIn("4,80 из 5", rows[0]["Для консультирования"])
            payload = json.loads((output / "sop_strengths.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["score_evidence"][0]["source_row"], 2)
            self.assertEqual(len(list((output / "programs").glob("*.txt"))), 1)
            self.assertTrue((output / "positive_comments.csv").read_text(encoding="utf-8-sig").startswith("program,"))

    def test_export_without_positive_metrics_has_no_invented_text(self) -> None:
        """Explicit review notes accompany programs without supporting positive data."""
        with patch("sop_strengths.read_sop_workbook", return_value=build_frames([build_row("A", 3.0)])):
            result = analyze_sop_strengths(Path("source.xlsx"), "4 модуль 2025/2026")
        with tempfile.TemporaryDirectory() as directory:
            write_strengths_outputs(result, Path(directory))
            self.assertEqual(result["programs"][0]["consultation_text"], "")
            self.assertTrue(result["programs"][0]["review_note"])


if __name__ == "__main__":
    unittest.main()
