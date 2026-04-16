import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from sop_analysis import (
    RowIssue,
    STATUS_ALARM,
    STATUS_OK,
    STATUS_RISK,
    DisciplineSummary,
    analyze_sop_workbook,
    classify_metrics,
    select_score_columns,
    summarize_programs,
)


def append_rows(sheet: Worksheet, rows: list[list[object]]) -> None:
    for row in rows:
        sheet.append(row)


class SopAnalysisRuleTests(unittest.TestCase):
    def test_one_metric_below_three_requires_attention(self) -> None:
        status = classify_metrics({"Качество": 2.9})

        self.assertEqual(status, STATUS_ALARM)

    def test_more_than_one_metric_below_four_has_risks(self) -> None:
        status = classify_metrics({"Качество": 3.5, "Ясность": 3.7})

        self.assertEqual(status, STATUS_RISK)

    def test_single_metric_below_four_is_ok(self) -> None:
        status = classify_metrics({"Качество": 3.5, "Ясность": 4.2})

        self.assertEqual(status, STATUS_OK)

    def test_difficulty_column_is_excluded(self) -> None:
        frame = pd.DataFrame(
            {
                "Программа": ["A"],
                "Сложность («1» – легко, «5» - сложно)": [2.0],
                "Новизна содержания": [4.2],
            }
        )

        self.assertEqual(select_score_columns(frame), ["Новизна содержания"])

    def test_two_alarm_disciplines_raise_program_attention(self) -> None:
        discipline_summaries: list[DisciplineSummary] = [
                {
                    "program": "A",
                    "discipline": "D1",
                    "status": STATUS_ALARM,
                    "issue_count": 1,
                    "alarm_issue_count": 1,
                    "risk_issue_count": 0,
                    "min_score": 2.8,
                },
                {
                    "program": "A",
                    "discipline": "D2",
                    "status": STATUS_ALARM,
                    "issue_count": 1,
                    "alarm_issue_count": 1,
                    "risk_issue_count": 0,
                    "min_score": 2.7,
                },
            ]
        row_issues: list[RowIssue] = [
                {
                    "sheet": "S",
                    "program": "A",
                    "campus": "",
                    "level": "",
                    "course": "",
                    "discipline": "D1",
                    "activity": "",
                    "teacher": "",
                    "status": STATUS_ALARM,
                    "metrics_below_3": [{"metric": "M", "value": 2.8}],
                    "metrics_below_4": [{"metric": "M", "value": 2.8}],
                    "min_score": 2.8,
                },
                {
                    "sheet": "S",
                    "program": "A",
                    "campus": "",
                    "level": "",
                    "course": "",
                    "discipline": "D2",
                    "activity": "",
                    "teacher": "",
                    "status": STATUS_ALARM,
                    "metrics_below_3": [{"metric": "M", "value": 2.7}],
                    "metrics_below_4": [{"metric": "M", "value": 2.7}],
                    "min_score": 2.7,
                },
            ]
        summaries = summarize_programs(discipline_summaries, row_issues)

        self.assertEqual(summaries[0]["status"], STATUS_ALARM)

    def test_one_alarm_discipline_raises_program_risk(self) -> None:
        discipline_summaries: list[DisciplineSummary] = [
                {
                    "program": "A",
                    "discipline": "D1",
                    "status": STATUS_ALARM,
                    "issue_count": 1,
                    "alarm_issue_count": 1,
                    "risk_issue_count": 0,
                    "min_score": 2.8,
                }
            ]
        row_issues: list[RowIssue] = [
                {
                    "sheet": "S",
                    "program": "A",
                    "campus": "",
                    "level": "",
                    "course": "",
                    "discipline": "D1",
                    "activity": "",
                    "teacher": "",
                    "status": STATUS_ALARM,
                    "metrics_below_3": [{"metric": "M", "value": 2.8}],
                    "metrics_below_4": [{"metric": "M", "value": 2.8}],
                    "min_score": 2.8,
                }
            ]
        summaries = summarize_programs(discipline_summaries, row_issues)

        self.assertEqual(summaries[0]["status"], STATUS_RISK)


class SopWorkbookTests(unittest.TestCase):
    def test_analyze_small_workbook(self) -> None:
        headers = [
            "Кампус",
            "Уровень",
            "Программа",
            "Курс",
            "Дисциплина",
            "Вид занятий",
            "ФИО преподавателя",
            "Количество студентов",
            "Средний отклик",
            "Общее качество (усредненная оценка)",
            "Ясность требований и обратной связи",
            "Сложность («1» – легко, «5» - сложно)",
        ]
        rows = [
            headers,
            ["Москва", "Магистратура", "Финансы", 1, "Курс 1", "Преподавание", "Иванов", 10, 0.8, 2.9, 4.2, 1.0],
        ]
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet_name in [
            "Преподаватели на ОП",
            "Дисциплины(ЭПП) на ОП",
            "МООК на ОП",
            "Доп.инф. по ЭПП",
        ]:
            sheet = workbook.create_sheet(sheet_name)
            append_rows(sheet, rows)

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sop.xlsx"
            workbook.save(path)

            result = analyze_sop_workbook(path)

        self.assertEqual(result["program_summaries"][0]["program"], "Финансы")
        self.assertEqual(result["program_summaries"][0]["status"], STATUS_RISK)


if __name__ == "__main__":
    unittest.main()
