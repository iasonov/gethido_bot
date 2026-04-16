from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict

import pandas as pd


Status = Literal["Требует внимания", "Есть риски", "Без риска"]

STATUS_ALARM: Status = "Требует внимания"
STATUS_RISK: Status = "Есть риски"
STATUS_OK: Status = "Без риска"

SOP_SHEETS: tuple[str, ...] = (
    "Преподаватели на ОП",
    "Дисциплины(ЭПП) на ОП",
    "МООК на ОП",
    "Доп.инф. по ЭПП",
)

IDENTITY_COLUMNS: frozenset[str] = frozenset(
    {
        "Кампус",
        "Уровень",
        "Программа",
        "Курс",
        "Дисциплина",
        "Вид занятий",
        "ФИО преподавателя",
        "Количество студентов",
        "Средний отклик",
        "Позитивные стороны, отмеченные в комментариях",
        "Негативные стороны, отмеченные в комментариях",
        "Обобщение комментариев по данному онлайн-курсу",
    }
)


class SopAnalysisError(ValueError):
    """Raised when the SOP workbook cannot be analyzed safely."""


class MetricIssue(TypedDict):
    metric: str
    value: float


class RowIssue(TypedDict):
    sheet: str
    program: str
    campus: str
    level: str
    course: str
    discipline: str
    activity: str
    teacher: str
    status: Status
    metrics_below_3: list[MetricIssue]
    metrics_below_4: list[MetricIssue]
    min_score: float


class DisciplineSummary(TypedDict):
    program: str
    discipline: str
    status: Status
    issue_count: int
    alarm_issue_count: int
    risk_issue_count: int
    min_score: float


class ProgramSummary(TypedDict):
    program: str
    status: Status
    alarm_discipline_count: int
    risk_discipline_count: int
    issue_count: int
    min_score: float


class SopAnalysisResult(TypedDict):
    source_file: str
    program_summaries: list[ProgramSummary]
    discipline_summaries: list[DisciplineSummary]
    row_issues: list[RowIssue]


def normalize_text(value: Any) -> str:
    """Возвращает строковое значение без краевых пробелов и лишних пробельных символов."""
    if value is None:
        return ""
    text = str(value).strip()
    return re.sub(r"\s+", " ", text)


def normalize_program_name(value: str) -> str:
    """Нормализует название программы для устойчивого сопоставления ключей и алиасов."""
    text = normalize_text(value).lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[«»\"'`]", "", text)
    text = re.sub(r"\s*[-–—]\s*", "-", text)
    return text


def normalize_level(value: str) -> str:
    """Приводит уровень программы к единому русскому обозначению."""
    normalized_value = normalize_text(value).lower()
    if normalized_value in {"master", "магистратура", "маг"}:
        return "Магистратура"
    if normalized_value in {"bachelor", "бакалавриат", "бак"}:
        return "Бакалавриат"
    return normalize_text(value)


def strip_program_level_marker(program: str) -> str:
    """Удаляет уровень из названия программы перед добавлением стандартного ключа уровня."""
    normalized_program = normalize_text(program)
    normalized_program = re.sub(
        r"\s*[/\\]\s*(магистратура|бакалавриат)\s*$",
        "",
        normalized_program,
        flags=re.IGNORECASE,
    )
    normalized_program = re.sub(
        r"\s*\((магистратура|бакалавриат)\)\s*$",
        "",
        normalized_program,
        flags=re.IGNORECASE,
    )
    return normalize_text(normalized_program)


def build_program_key(program: str, level: str) -> str:
    """Формирует ключ программы с уровнем, чтобы различать одноименные программы."""
    normalized_program = strip_program_level_marker(program)
    normalized_level = normalize_level(level)
    if not normalized_level:
        return normalized_program
    return f"{normalized_program} / {normalized_level}"


def classify_metrics(metric_values: dict[str, float]) -> Status:
    """Определяет статус набора оценочных метрик по заданным порогам риска."""
    below_3_count = sum(1 for value in metric_values.values() if value < 3)
    below_4_count = sum(1 for value in metric_values.values() if value < 4)
    if below_3_count >= 1:
        return STATUS_ALARM
    if below_4_count >= 1:
        return STATUS_RISK
    return STATUS_OK


def is_score_column(column_name: str) -> bool:
    """Проверяет, может ли колонка содержать оценочную метрику СОП."""
    normalized_column = normalize_text(column_name)
    if normalized_column in IDENTITY_COLUMNS:
        return False
    if "Сложность" in normalized_column:
        return False
    if "комментар" in normalized_column.lower():
        return False
    return True


def select_score_columns(frame: pd.DataFrame) -> list[str]:
    """Выбирает из таблицы числовые колонки с оценками в диапазоне от 1 до 5."""
    score_columns: list[str] = []
    for column in frame.columns:
        column_name = normalize_text(column)
        if not is_score_column(column_name):
            continue
        numeric_values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if numeric_values.empty:
            continue
        values_inside_score_range = numeric_values.between(1, 5).all()
        if values_inside_score_range:
            score_columns.append(column_name)
    return score_columns


def read_sop_workbook(path: Path) -> dict[str, pd.DataFrame]:
    """Читает обязательные листы СОП из xlsx-файла и проверяет базовые колонки."""
    if not path.exists():
        raise SopAnalysisError(f"SOP workbook does not exist: {path}")

    workbook = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    missing_sheets = [sheet for sheet in SOP_SHEETS if sheet not in workbook]
    if missing_sheets:
        joined_sheets = ", ".join(missing_sheets)
        raise SopAnalysisError(f"SOP workbook is missing required sheets: {joined_sheets}")

    frames: dict[str, pd.DataFrame] = {}
    for sheet_name in SOP_SHEETS:
        frame = workbook[sheet_name].copy()
        frame.columns = [normalize_text(column) for column in frame.columns]
        if "Программа" not in frame.columns or "Дисциплина" not in frame.columns:
            raise SopAnalysisError(
                f"SOP sheet '{sheet_name}' must contain 'Программа' and 'Дисциплина' columns"
            )
        frames[sheet_name] = frame
    return frames


def build_metric_issues(row: pd.Series, score_columns: list[str], threshold: float) -> list[MetricIssue]:
    """Возвращает список метрик строки, значения которых ниже указанного порога."""
    issues: list[MetricIssue] = []
    for column in score_columns:
        value = pd.to_numeric(row.get(column), errors="coerce")
        if pd.isna(value):
            continue
        numeric_value = float(value)
        if numeric_value < threshold:
            issues.append({"metric": column, "value": numeric_value})
    return issues


def build_metric_values(row: pd.Series, score_columns: list[str]) -> dict[str, float]:
    """Собирает числовые оценочные метрики из строки в словарь."""
    metric_values: dict[str, float] = {}
    for column in score_columns:
        value = pd.to_numeric(row.get(column), errors="coerce")
        if pd.isna(value):
            continue
        metric_values[column] = float(value)
    return metric_values


def build_row_issue(sheet_name: str, row: pd.Series, score_columns: list[str]) -> RowIssue | None:
    """Строит описание проблемной строки СОП или возвращает None для безопасной строки."""
    metric_values = build_metric_values(row, score_columns)
    if not metric_values:
        return None

    status = classify_metrics(metric_values)
    if status == STATUS_OK:
        return None

    return {
        "sheet": sheet_name,
        "program": build_program_key(row.get("Программа"), row.get("Уровень")),
        "campus": normalize_text(row.get("Кампус")),
        "level": normalize_text(row.get("Уровень")),
        "course": normalize_text(row.get("Курс")),
        "discipline": normalize_text(row.get("Дисциплина")),
        "activity": normalize_text(row.get("Вид занятий")),
        "teacher": normalize_text(row.get("ФИО преподавателя")),
        "status": status,
        "metrics_below_3": build_metric_issues(row, score_columns, 3),
        "metrics_below_4": build_metric_issues(row, score_columns, 4),
        "min_score": min(metric_values.values()),
    }


def collect_row_issues(frames: dict[str, pd.DataFrame]) -> list[RowIssue]:
    """Собирает все проблемные строки со всех анализируемых листов СОП."""
    issues: list[RowIssue] = []
    for sheet_name, frame in frames.items():
        score_columns = select_score_columns(frame)
        for _, row in frame.iterrows():
            row_issue = build_row_issue(sheet_name, row, score_columns)
            if row_issue is not None:
                issues.append(row_issue)
    return issues


def strongest_status(statuses: list[Status]) -> Status:
    """Возвращает самый строгий статус из списка статусов."""
    if STATUS_ALARM in statuses:
        return STATUS_ALARM
    if STATUS_RISK in statuses:
        return STATUS_RISK
    return STATUS_OK


def summarize_disciplines(row_issues: list[RowIssue]) -> list[DisciplineSummary]:
    """Агрегирует проблемные строки до уровня дисциплин внутри программ."""
    grouped: dict[tuple[str, str], list[RowIssue]] = {}
    for issue in row_issues:
        key = (issue["program"], issue["discipline"])
        grouped[key] = [*grouped.get(key, []), issue]

    summaries: list[DisciplineSummary] = []
    for (program, discipline), issues in grouped.items():
        statuses = [issue["status"] for issue in issues]
        alarm_count = sum(1 for issue in issues if issue["status"] == STATUS_ALARM)
        risk_count  = sum(1 for issue in issues if issue["status"] == STATUS_RISK)
        summaries.append(
            {
                "program": program,
                "discipline": discipline,
                "status": strongest_status(statuses),
                "issue_count": len(issues),
                "alarm_issue_count": alarm_count,
                "risk_issue_count": risk_count,
                "min_score": min(issue["min_score"] for issue in issues),
            }
        )
    return sorted(summaries, key=lambda item: (item["program"], item["discipline"]))


def summarize_programs(
    discipline_summaries: list[DisciplineSummary],
    row_issues: list[RowIssue],
) -> list[ProgramSummary]:
    """Агрегирует статусы дисциплин до уровня программы с учетом уровня обучения."""
    disciplines_by_program: dict[str, list[DisciplineSummary]] = {}
    for discipline in discipline_summaries:
        program = discipline["program"]
        disciplines_by_program[program] = [*disciplines_by_program.get(program, []), discipline]

    row_issues_by_program: dict[str, list[RowIssue]] = {}
    for issue in row_issues:
        program = issue["program"]
        row_issues_by_program[program] = [*row_issues_by_program.get(program, []), issue]

    summaries: list[ProgramSummary] = []
    for program, disciplines in disciplines_by_program.items():
        alarm_count = sum(1 for discipline in disciplines if discipline["status"] == STATUS_ALARM)
        risk_count  = sum(1 for discipline in disciplines if discipline["status"] == STATUS_RISK)
        status = STATUS_OK
        if alarm_count == 0 and risk_count == 1:
            status = STATUS_RISK
        elif alarm_count > 0 or risk_count > 1:
            status =  STATUS_ALARM

        program_row_issues = row_issues_by_program.get(program, [])
        summaries.append(
            {
                "program": program,
                "status": status,
                "alarm_discipline_count": alarm_count,
                "risk_discipline_count": risk_count,
                "issue_count": len(program_row_issues),
                "min_score": min(issue["min_score"] for issue in program_row_issues),
            }
        )
    return sorted(summaries, key=lambda item: (item["status"], item["program"]))


def analyze_sop_workbook(path: Path) -> SopAnalysisResult:
    """Запускает полный анализ xlsx-файла СОП и возвращает структурированный результат."""
    frames = read_sop_workbook(path)
    row_issues = collect_row_issues(frames)
    discipline_summaries = summarize_disciplines(row_issues)
    program_summaries = summarize_programs(discipline_summaries, row_issues)
    return {
        "source_file": str(path),
        "program_summaries": program_summaries,
        "discipline_summaries": discipline_summaries,
        "row_issues": row_issues,
    }


def write_analysis_outputs(result: SopAnalysisResult, output_dir: Path) -> None:
    """Сохраняет результаты анализа в JSON и CSV-файлы для проверки и истории."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sop_analysis.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(result["program_summaries"]).to_csv(
        output_dir / "program_summaries.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(result["discipline_summaries"]).to_csv(
        output_dir / "discipline_summaries.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(result["row_issues"]).to_csv(
        output_dir / "row_issues.csv",
        index=False,
        encoding="utf-8-sig",
    )
