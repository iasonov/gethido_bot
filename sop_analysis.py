from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Literal, TypedDict

import pandas as pd


Status = Literal["Требует внимания", "Есть риски", "Без риска"]
StrengthStatus = Literal["Выдающееся", "Сильная сторона"]

STATUS_ALARM: Status = "Требует внимания"
STATUS_RISK: Status = "Есть риски"
STATUS_OK: Status = "Без риска"
STATUS_OUTSTANDING: StrengthStatus = "Выдающееся"
STATUS_STRENGTH: StrengthStatus = "Сильная сторона"

STRENGTH_THRESHOLD = 4.5
OUTSTANDING_THRESHOLD = 4.7
POSITIVE_COMMENTS_COLUMN = "Позитивные стороны, отмеченные в комментариях"
TOP_PROGRAMS_LIMIT = 10
TOP_METRICS_LIMIT = 5

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
    course: str
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


class RowStrength(TypedDict):
    sheet: str
    program: str
    campus: str
    level: str
    course: str
    discipline: str
    activity: str
    teacher: str
    status: StrengthStatus
    metrics_at_least_4_5: list[MetricIssue]
    metrics_at_least_4_7: list[MetricIssue]
    max_score: float
    positive_comments: str


class ProgramStrengthSummary(TypedDict):
    program: str
    status: StrengthStatus
    outstanding_row_count: int
    strength_row_count: int
    highlight_count: int
    max_score: float
    top_metrics: list[MetricIssue]


class LineupMetricSummary(TypedDict):
    metric: str
    observation_count: int
    mean_score: float
    median_score: float
    share_below_4: float
    share_at_least_4_5: float
    share_at_least_4_7: float


class LineupProgramRank(TypedDict):
    program: str
    mean_score: float
    observation_count: int
    outstanding_metric_share: float
    risk_status: Status


class LineupSummary(TypedDict):
    program_count: int
    scored_row_count: int
    discipline_count: int
    programs_alarm: int
    programs_risk: int
    programs_ok: int
    outstanding_row_count: int
    strength_row_count: int
    metrics: list[LineupMetricSummary]
    top_programs_by_mean: list[LineupProgramRank]
    top_programs_by_outstanding_share: list[LineupProgramRank]


class SopAnalysisResult(TypedDict):
    source_file: str
    program_summaries: list[ProgramSummary]
    discipline_summaries: list[DisciplineSummary]
    row_issues: list[RowIssue]
    row_strengths: list[RowStrength]
    program_strengths: list[ProgramStrengthSummary]
    lineup_summary: LineupSummary


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


def normalize_course(value: Any) -> str:
    """Приводит курс к удобочитаемому строковому виду."""
    text = normalize_text(value)
    if text == "":
        return ""

    numeric_value = pd.to_numeric(text, errors="coerce")
    if pd.isna(numeric_value):
        return text

    numeric_course = float(numeric_value)
    if numeric_course.is_integer():
        return str(int(numeric_course))
    return str(numeric_course)


def build_program_key(program: str, level: str) -> str:
    """Формирует ключ программы с уровнем, чтобы различать одноименные программы."""
    normalized_program = normalize_text(program)
    normalized_level = normalize_level(level)
    if not normalized_level:
        return normalized_program
    return f"{normalized_program} / {normalized_level}"


def classify_metrics(metric_values: dict[str, float]) -> Status:
    """Определяет статус набора оценочных метрик по заданным порогам риска."""
    below_3_count = sum(1 for value in metric_values.values() if value < 3)
    below_4_count = sum(1 for value in metric_values.values() if value < 4)
    if below_3_count >= 1 or below_4_count >= 2:
        return STATUS_ALARM
    if below_4_count >= 1:
        return STATUS_RISK
    return STATUS_OK


def classify_strengths(metric_values: dict[str, float]) -> StrengthStatus | None:
    """Определяет статус сильных сторон строки по порогам 4.5 и 4.7."""
    outstanding_count = sum(1 for value in metric_values.values() if value >= OUTSTANDING_THRESHOLD)
    strength_count = sum(1 for value in metric_values.values() if value >= STRENGTH_THRESHOLD)
    if outstanding_count >= 1 or strength_count >= 2:
        return STATUS_OUTSTANDING
    if strength_count >= 1:
        return STATUS_STRENGTH
    return None


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


def build_metric_highlights(
    row: pd.Series,
    score_columns: list[str],
    threshold: float,
) -> list[MetricIssue]:
    """Возвращает список метрик строки, значения которых не ниже указанного порога."""
    highlights: list[MetricIssue] = []
    for column in score_columns:
        value = pd.to_numeric(row.get(column), errors="coerce")
        if pd.isna(value):
            continue
        numeric_value = float(value)
        if numeric_value >= threshold:
            highlights.append({"metric": column, "value": numeric_value})
    return highlights


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
        "course": normalize_course(row.get("Курс")),
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


def build_row_strength(sheet_name: str, row: pd.Series, score_columns: list[str]) -> RowStrength | None:
    """Строит описание сильной строки СОП или возвращает None, если хвалить нечего."""
    metric_values = build_metric_values(row, score_columns)
    if not metric_values:
        return None

    status = classify_strengths(metric_values)
    if status is None:
        return None

    return {
        "sheet": sheet_name,
        "program": build_program_key(row.get("Программа"), row.get("Уровень")),
        "campus": normalize_text(row.get("Кампус")),
        "level": normalize_text(row.get("Уровень")),
        "course": normalize_course(row.get("Курс")),
        "discipline": normalize_text(row.get("Дисциплина")),
        "activity": normalize_text(row.get("Вид занятий")),
        "teacher": normalize_text(row.get("ФИО преподавателя")),
        "status": status,
        "metrics_at_least_4_5": build_metric_highlights(row, score_columns, STRENGTH_THRESHOLD),
        "metrics_at_least_4_7": build_metric_highlights(row, score_columns, OUTSTANDING_THRESHOLD),
        "max_score": max(metric_values.values()),
        "positive_comments": normalize_text(row.get(POSITIVE_COMMENTS_COLUMN)),
    }


def collect_row_strengths(frames: dict[str, pd.DataFrame]) -> list[RowStrength]:
    """Собирает все сильные строки со всех анализируемых листов СОП."""
    strengths: list[RowStrength] = []
    for sheet_name, frame in frames.items():
        score_columns = select_score_columns(frame)
        for _, row in frame.iterrows():
            row_strength = build_row_strength(sheet_name, row, score_columns)
            if row_strength is not None:
                strengths.append(row_strength)
    return strengths


def strongest_status(statuses: list[Status]) -> Status:
    """Возвращает самый строгий статус из списка статусов."""
    if STATUS_ALARM in statuses:
        return STATUS_ALARM
    if STATUS_RISK in statuses:
        return STATUS_RISK
    return STATUS_OK


def summarize_disciplines(row_issues: list[RowIssue]) -> list[DisciplineSummary]:
    """Агрегирует проблемные строки до уровня дисциплин внутри программ."""
    grouped: dict[tuple[str, str, str], list[RowIssue]] = {}
    for issue in row_issues:
        key = (issue["program"], issue["course"], issue["discipline"])
        grouped[key] = [*grouped.get(key, []), issue]

    summaries: list[DisciplineSummary] = []
    for (program, course, discipline), issues in grouped.items():
        statuses = [issue["status"] for issue in issues]
        alarm_count = sum(1 for issue in issues if issue["status"] == STATUS_ALARM)
        risk_count = sum(1 for issue in issues if issue["status"] == STATUS_RISK)
        summaries.append(
            {
                "program": program,
                "course": course,
                "discipline": discipline,
                "status": strongest_status(statuses),
                "issue_count": len(issues),
                "alarm_issue_count": alarm_count,
                "risk_issue_count": risk_count,
                "min_score": min(issue["min_score"] for issue in issues),
            }
        )
    return sorted(summaries, key=lambda item: (item["program"], item["course"], item["discipline"]))


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


def _program_strength_status(outstanding_row_count: int, strength_row_count: int) -> StrengthStatus:
    """Агрегирует статус программы по числу выдающихся и сильных строк."""
    if outstanding_row_count >= 1 or strength_row_count >= 2:
        return STATUS_OUTSTANDING
    return STATUS_STRENGTH


def _top_metrics_for_program(strengths: list[RowStrength]) -> list[MetricIssue]:
    """Собирает лучшие значения метрик программы для продвижения."""
    best_by_metric: dict[str, float] = {}
    for strength in strengths:
        highlights = strength["metrics_at_least_4_7"] or strength["metrics_at_least_4_5"]
        for highlight in highlights:
            metric = highlight["metric"]
            current_best = best_by_metric.get(metric)
            if current_best is None or highlight["value"] > current_best:
                best_by_metric[metric] = highlight["value"]
    ranked = sorted(best_by_metric.items(), key=lambda item: (-item[1], item[0]))
    return [{"metric": metric, "value": value} for metric, value in ranked[:TOP_METRICS_LIMIT]]


def summarize_program_strengths(row_strengths: list[RowStrength]) -> list[ProgramStrengthSummary]:
    """Агрегирует сильные строки до уровня программы."""
    grouped: dict[str, list[RowStrength]] = {}
    for strength in row_strengths:
        program = strength["program"]
        grouped[program] = [*grouped.get(program, []), strength]

    summaries: list[ProgramStrengthSummary] = []
    for program, strengths in grouped.items():
        outstanding_row_count = sum(
            1 for strength in strengths if strength["status"] == STATUS_OUTSTANDING
        )
        strength_row_count = sum(1 for strength in strengths if strength["status"] == STATUS_STRENGTH)
        summaries.append(
            {
                "program": program,
                "status": _program_strength_status(outstanding_row_count, strength_row_count),
                "outstanding_row_count": outstanding_row_count,
                "strength_row_count": strength_row_count,
                "highlight_count": len(strengths),
                "max_score": max(strength["max_score"] for strength in strengths),
                "top_metrics": _top_metrics_for_program(strengths),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (item["status"] != STATUS_OUTSTANDING, -item["max_score"], item["program"]),
    )


def _round_score(value: float) -> float:
    """Округляет оценку до двух знаков после запятой."""
    return round(value, 2)


def _round_share(value: float) -> float:
    """Округляет долю до четырех знаков после запятой."""
    return round(value, 4)


def _summarize_metric_values(metric: str, values: list[float]) -> LineupMetricSummary:
    """Строит сводку по одному показателю СОП на всей линейке."""
    observation_count = len(values)
    series = pd.Series(values)
    return {
        "metric": metric,
        "observation_count": observation_count,
        "mean_score": _round_score(float(series.mean())),
        "median_score": _round_score(float(series.median())),
        "share_below_4": _round_share(sum(1 for value in values if value < 4) / observation_count),
        "share_at_least_4_5": _round_share(
            sum(1 for value in values if value >= STRENGTH_THRESHOLD) / observation_count
        ),
        "share_at_least_4_7": _round_share(
            sum(1 for value in values if value >= OUTSTANDING_THRESHOLD) / observation_count
        ),
    }


def summarize_lineup(
    frames: dict[str, pd.DataFrame],
    program_summaries: list[ProgramSummary],
    row_strengths: list[RowStrength],
) -> LineupSummary:
    """Строит сводку по всей линейке Вышки Онлайн: покрытие, риски и показатели СОП."""
    programs: set[str] = set()
    scored_row_count = 0
    disciplines: set[tuple[str, str, str]] = set()
    values_by_metric: dict[str, list[float]] = {}
    values_by_program: dict[str, list[float]] = {}

    for frame in frames.values():
        score_columns = select_score_columns(frame)
        for _, row in frame.iterrows():
            program = build_program_key(row.get("Программа"), row.get("Уровень"))
            if not program:
                continue
            programs.add(program)
            metric_values = build_metric_values(row, score_columns)
            if not metric_values:
                continue
            scored_row_count += 1
            disciplines.add((program, normalize_course(row.get("Курс")), normalize_text(row.get("Дисциплина"))))
            for metric, value in metric_values.items():
                values_by_metric.setdefault(metric, []).append(value)
                values_by_program.setdefault(program, []).append(value)

    risk_status_by_program = {
        summary["program"]: summary["status"] for summary in program_summaries
    }
    programs_alarm = sum(1 for status in risk_status_by_program.values() if status == STATUS_ALARM)
    programs_risk = sum(1 for status in risk_status_by_program.values() if status == STATUS_RISK)
    programs_ok = len(programs) - programs_alarm - programs_risk

    ranks: list[LineupProgramRank] = []
    for program, values in values_by_program.items():
        outstanding_count = sum(1 for value in values if value >= OUTSTANDING_THRESHOLD)
        ranks.append(
            {
                "program": program,
                "mean_score": _round_score(sum(values) / len(values)),
                "observation_count": len(values),
                "outstanding_metric_share": _round_share(outstanding_count / len(values)),
                "risk_status": risk_status_by_program.get(program, STATUS_OK),
            }
        )

    metrics = [
        _summarize_metric_values(metric, values)
        for metric, values in sorted(values_by_metric.items())
    ]
    return {
        "program_count": len(programs),
        "scored_row_count": scored_row_count,
        "discipline_count": len(disciplines),
        "programs_alarm": programs_alarm,
        "programs_risk": programs_risk,
        "programs_ok": programs_ok,
        "outstanding_row_count": sum(
            1 for strength in row_strengths if strength["status"] == STATUS_OUTSTANDING
        ),
        "strength_row_count": sum(
            1 for strength in row_strengths if strength["status"] == STATUS_STRENGTH
        ),
        "metrics": metrics,
        "top_programs_by_mean": sorted(
            ranks, key=lambda item: (-item["mean_score"], item["program"])
        )[:TOP_PROGRAMS_LIMIT],
        "top_programs_by_outstanding_share": sorted(
            ranks,
            key=lambda item: (-item["outstanding_metric_share"], -item["mean_score"], item["program"]),
        )[:TOP_PROGRAMS_LIMIT],
    }


def analyze_sop_workbook(path: Path) -> SopAnalysisResult:
    """Запускает полный анализ xlsx-файла СОП и возвращает структурированный результат."""
    frames = read_sop_workbook(path)
    row_issues = collect_row_issues(frames)
    discipline_summaries = summarize_disciplines(row_issues)
    program_summaries = summarize_programs(discipline_summaries, row_issues)
    row_strengths = collect_row_strengths(frames)
    program_strengths = summarize_program_strengths(row_strengths)
    lineup_summary = summarize_lineup(frames, program_summaries, row_strengths)
    return {
        "source_file": str(path),
        "program_summaries": program_summaries,
        "discipline_summaries": discipline_summaries,
        "row_issues": row_issues,
        "row_strengths": row_strengths,
        "program_strengths": program_strengths,
        "lineup_summary": lineup_summary,
    }


def _records_to_csv(records: list[Any], path: Path) -> None:
    """Сохраняет список записей в CSV, сериализуя вложенные списки и словари."""
    if not records:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")
        return
    frame = pd.DataFrame(records)
    for column in frame.columns:
        needs_json = frame[column].map(lambda value: isinstance(value, (list, dict))).any()
        if needs_json:
            frame[column] = frame[column].map(
                lambda value: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else value
            )
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _format_percent(share: float) -> str:
    """Форматирует долю как процент с одним знаком после запятой."""
    return f"{share * 100:.1f}%"


def _format_metric_list(metrics: list[MetricIssue]) -> str:
    """Собирает компактную строку из названий метрик и оценок."""
    if not metrics:
        return "—"
    return ", ".join(f"{item['metric']} ({item['value']:.2f})" for item in metrics)


def _format_strength_subject(strength: RowStrength) -> str:
    """Описывает дисциплину, преподавателя и вид занятий для карточки программы."""
    parts = [strength["discipline"] or "без названия"]
    if strength["course"]:
        parts.append(f"курс {strength['course']}")
    if strength["teacher"]:
        parts.append(strength["teacher"])
    if strength["activity"]:
        parts.append(strength["activity"])
    return ", ".join(parts)


def format_strengths_report(result: SopAnalysisResult) -> str:
    """Собирает markdown-отчёт: сводка по линейке и сильные стороны программ."""
    lineup = result["lineup_summary"]
    lines: list[str] = [
        "# СОП: сильные стороны и сводка по линейке Вышки Онлайн",
        "",
        f"Источник: `{result['source_file']}`",
        "",
        "## Сводка по линейке",
        "",
        f"- Программ: **{lineup['program_count']}**",
        f"- Строк с оценками: **{lineup['scored_row_count']}**",
        f"- Дисциплин: **{lineup['discipline_count']}**",
        (
            f"- Риски: требуют внимания — {lineup['programs_alarm']}, "
            f"есть риски — {lineup['programs_risk']}, без риска — {lineup['programs_ok']}"
        ),
        (
            f"- Сильные строки: выдающихся — {lineup['outstanding_row_count']}, "
            f"сильных сторон — {lineup['strength_row_count']}"
        ),
        "",
        "### Показатели СОП",
        "",
        "| Показатель | Наблюдений | Среднее | Медиана | < 4 | ≥ 4.5 | ≥ 4.7 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in lineup["metrics"]:
        lines.append(
            "| {metric} | {count} | {mean:.2f} | {median:.2f} | {below} | {strong} | {outstanding} |".format(
                metric=metric["metric"],
                count=metric["observation_count"],
                mean=metric["mean_score"],
                median=metric["median_score"],
                below=_format_percent(metric["share_below_4"]),
                strong=_format_percent(metric["share_at_least_4_5"]),
                outstanding=_format_percent(metric["share_at_least_4_7"]),
            )
        )

    lines.extend(["", "### Топ программ по средней оценке", ""])
    if lineup["top_programs_by_mean"]:
        for rank, item in enumerate(lineup["top_programs_by_mean"], start=1):
            lines.append(
                f"{rank}. **{item['program']}** — среднее {item['mean_score']:.2f}, "
                f"доля ≥ 4.7: {_format_percent(item['outstanding_metric_share'])}, "
                f"риск: {item['risk_status']}"
            )
    else:
        lines.append("Нет программ с числовыми оценками.")

    lines.extend(["", "### Топ программ по доле выдающихся оценок (≥ 4.7)", ""])
    if lineup["top_programs_by_outstanding_share"]:
        for rank, item in enumerate(lineup["top_programs_by_outstanding_share"], start=1):
            lines.append(
                f"{rank}. **{item['program']}** — доля ≥ 4.7: "
                f"{_format_percent(item['outstanding_metric_share'])}, "
                f"среднее {item['mean_score']:.2f}"
            )
    else:
        lines.append("Нет программ с числовыми оценками.")

    lines.extend(["", "## Программы: чем гордиться", ""])
    strengths_by_program: dict[str, list[RowStrength]] = {}
    for strength in result["row_strengths"]:
        program = strength["program"]
        strengths_by_program[program] = [*strengths_by_program.get(program, []), strength]

    if not result["program_strengths"]:
        lines.append("Сильных сторон по порогам 4.5 / 4.7 не найдено.")
    else:
        for summary in result["program_strengths"]:
            program_strengths = sorted(
                strengths_by_program.get(summary["program"], []),
                key=lambda item: (item["status"] != STATUS_OUTSTANDING, -item["max_score"]),
            )
            lines.extend(
                [
                    f"### {summary['program']}",
                    "",
                    (
                        f"{summary['status']}: выдающихся строк — {summary['outstanding_row_count']}, "
                        f"сильных — {summary['strength_row_count']}, "
                        f"максимум {summary['max_score']:.2f}."
                    ),
                    f"Опора для продвижения: {_format_metric_list(summary['top_metrics'])}",
                    "",
                ]
            )
            comments: list[str] = []
            for strength in program_strengths:
                highlight_metrics = strength["metrics_at_least_4_7"] or strength["metrics_at_least_4_5"]
                lines.append(
                    f"- **{strength['status']}** — {_format_strength_subject(strength)}: "
                    f"{_format_metric_list(highlight_metrics)}"
                )
                if strength["positive_comments"] and strength["positive_comments"] not in comments:
                    comments.append(strength["positive_comments"])
            if comments:
                lines.append("")
                lines.append("Комментарии студентов:")
                for comment in comments[:3]:
                    lines.append(f"- «{comment}»")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def print_strengths_report(result: SopAnalysisResult) -> None:
    """Печатает markdown-отчёт о сильных сторонах и сводке линейки в stdout."""
    print(format_strengths_report(result), end="")


def write_analysis_outputs(result: SopAnalysisResult, output_dir: Path) -> None:
    """Сохраняет результаты анализа в JSON, CSV и markdown-отчёт для коллег."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sop_analysis.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _records_to_csv(result["program_summaries"], output_dir / "program_summaries.csv")
    _records_to_csv(result["discipline_summaries"], output_dir / "discipline_summaries.csv")
    _records_to_csv(result["row_issues"], output_dir / "row_issues.csv")
    _records_to_csv(result["program_strengths"], output_dir / "program_strengths.csv")
    _records_to_csv(result["row_strengths"], output_dir / "row_strengths.csv")
    _records_to_csv(result["lineup_summary"]["metrics"], output_dir / "lineup_metrics.csv")
    report_path = output_dir / "sop_strengths_report.md"
    report_path.write_text(format_strengths_report(result), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Запускает анализ СОП, пишет файлы и печатает отчёт в терминал."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: python sop_analysis.py путь/к/sop.xlsx [папка_вывода]", file=sys.stderr)
        return 2
    source = Path(args[0])
    output_dir = Path(args[1]) if len(args) > 1 else source.parent / "sop_outputs"
    result = analyze_sop_workbook(source)
    write_analysis_outputs(result, output_dir)
    print_strengths_report(result)
    print(f"\nФайлы сохранены в {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

