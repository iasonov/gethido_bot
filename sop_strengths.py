from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import TypedDict

import pandas as pd

from sop_analysis import (
    SopAnalysisError,
    build_metric_values,
    build_program_key,
    normalize_course,
    normalize_text,
    read_sop_workbook,
    select_score_columns,
)


POSITIVE_SCORE: float = 4.0
MAX_STRENGTHS: int = 3
METRIC_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "Общее качество (усредненная оценка)": ("teaching", "качество преподавания"),
    "Ясность требований и обратной связи": ("requirements", "ясность требований и обратной связи"),
    "Ясность и последовательность изложения материала и инструкций": ("clarity", "ясность и последовательность объяснений преподавателей"),
    "Контакт и вежливость": ("communication", "контакт и вежливость преподавателей"),
    "Возможность внеаудиторного общения по учебным и научным вопросам": ("support", "возможность общения с преподавателями вне занятий по учебным и научным вопросам"),
    "Качество организации работ по ЭПП": ("organization", "организацию работы над практическими заданиями"),
    "Новизна содержания": ("content", "новизну содержания"),
    "Понимание целей и возможностей дальнейшего применения": ("application", "понятность целей обучения и возможностей применения знаний"),
    "Привлекательность и интересность": ("interest", "интерес к содержанию обучения"),
    "Ясность и последовательность изложения материала": ("clarity", "ясность и последовательность изложения в онлайн-курсах"),
    "Ясность требований, предъявляемых к студентам": ("requirements", "ясность требований в онлайн-курсах"),
    "Качество организации практики": ("organization", "качество организации практики"),
    "Организация процесса промежуточной аттестации по ЭПП": ("assessment", "организацию промежуточной аттестации по практической подготовке"),
    "Понимание того, как результат ЭПП будет использоваться в практических целях": ("application", "понятность практического применения результатов практической подготовки"),
    "Связь ЭПП с предыдущими знаниями по образовательной программе": ("connection", "связь практической подготовки с ранее полученными знаниями"),
}
METHOD: str = (
    "Отобраны до трёх разных сильных сторон со средней оценкой не ниже 4 из 5. "
    "Для каждого показателя отдельно на каждом листе сначала усредняются строки "
    "внутри пары курс–дисциплина, затем полученные оценки дисциплин. "
    "Это среднее оценок дисциплин, не среднее по студентам; число уникальных "
    "респондентов неизвестно. Тезисы относятся только к оценённым дисциплинам "
    "и указанному периоду СОП. Поле с позитивными комментариями сохранено "
    "для ручной обработки и не используется для автоматических тезисов."
)


class ScoreEvidence(TypedDict):
    program: str
    sheet: str
    source_row: int
    course: str
    discipline: str
    metric: str
    value: float


class PositiveComment(TypedDict):
    program: str
    sheet: str
    source_row: int
    course: str
    discipline: str
    text: str


class Strength(TypedDict):
    sheet: str
    metric: str
    aspect: str
    description: str
    score: float
    discipline_count: int
    row_count: int
    evidence: list[ScoreEvidence]


class ProgramStrengths(TypedDict):
    program: str
    consultation_text: str
    review_note: str
    strengths: list[Strength]


class StrengthsResult(TypedDict):
    source_file: str
    module_label: str
    method: str
    programs: list[ProgramStrengths]
    score_evidence: list[ScoreEvidence]
    positive_comments: list[PositiveComment]


def collect_strengths_data(frames: dict[str, pd.DataFrame]) -> tuple[list[str], list[ScoreEvidence], list[PositiveComment]]:
    """Collect all programs and traceable scores, including rows without risks."""
    programs: set[str] = set()
    evidence: list[ScoreEvidence] = []
    comments: list[PositiveComment] = []
    for sheet, frame in frames.items():
        score_columns: list[str] = select_score_columns(frame)
        for source_row, (_, row) in enumerate(frame.iterrows(), start=2):
            if pd.isna(row.get("Программа")) or not normalize_text(row.get("Программа")):
                raise SopAnalysisError(f"Missing program in sheet '{sheet}', Excel row {source_row}")
            level: str = "" if pd.isna(row.get("Уровень")) else normalize_text(row.get("Уровень"))
            program: str = build_program_key(normalize_text(row.get("Программа")), level)
            course: str = "" if pd.isna(row.get("Курс")) else normalize_course(row.get("Курс"))
            discipline: str = "" if pd.isna(row.get("Дисциплина")) else normalize_text(row.get("Дисциплина"))
            programs.add(program)
            for metric in METRIC_DESCRIPTIONS.keys() & set(frame.columns):
                raw_value = row.get(metric)
                if pd.notna(raw_value):
                    numeric_value = pd.to_numeric(raw_value, errors="coerce")
                    if pd.isna(numeric_value) or not 1 <= float(numeric_value) <= 5:
                        raise SopAnalysisError(
                            f"Invalid score in sheet '{sheet}', Excel row {source_row}, "
                            f"metric '{metric}': {raw_value!r}; expected 1–5 or an empty cell"
                        )
            values: dict[str, float] = build_metric_values(row, score_columns)
            for metric, value in values.items():
                if metric in METRIC_DESCRIPTIONS:
                    if not discipline:
                        raise SopAnalysisError(f"Missing discipline in sheet '{sheet}', Excel row {source_row}")
                    evidence.append({
                        "program": program, "sheet": sheet, "source_row": source_row,
                        "course": course, "discipline": discipline, "metric": metric, "value": value,
                    })
            raw_comment = row.get("Позитивные стороны, отмеченные в комментариях")
            if pd.notna(raw_comment):
                text: str = normalize_text(raw_comment)
                if text:
                    comments.append({
                        "program": program, "sheet": sheet, "source_row": source_row,
                        "course": course, "discipline": discipline, "text": text,
                    })
    if not programs:
        raise SopAnalysisError("SOP workbook contains no program rows")
    return sorted(programs), evidence, comments


def summarize_strength(sheet: str, metric: str, evidence: list[ScoreEvidence]) -> Strength:
    """Give each evaluated course–discipline pair equal weight within one metric."""
    disciplines: dict[tuple[str, str], list[float]] = {}
    for item in evidence:
        key: tuple[str, str] = (item["course"], item["discipline"])
        disciplines[key] = [*disciplines.get(key, []), item["value"]]
    aspect, description = METRIC_DESCRIPTIONS[metric]
    return {
        "sheet": sheet, "metric": metric, "aspect": aspect, "description": description,
        "score": mean(mean(values) for values in disciplines.values()),
        "discipline_count": len(disciplines), "row_count": len(evidence), "evidence": evidence,
    }


def select_program_strengths(evidence: list[ScoreEvidence]) -> list[Strength]:
    """Select distinct positive aspects using all scores, never only positive rows."""
    grouped: dict[tuple[str, str], list[ScoreEvidence]] = {}
    for item in evidence:
        key: tuple[str, str] = (item["sheet"], item["metric"])
        grouped[key] = [*grouped.get(key, []), item]
    candidates: list[Strength] = [summarize_strength(sheet, metric, items) for (sheet, metric), items in grouped.items()]
    ranked: list[Strength] = sorted(
        candidates, key=lambda item: (-item["score"], -item["discipline_count"], item["sheet"], item["metric"])
    )
    selected: list[Strength] = []
    aspects: set[str] = set()
    for item in ranked:
        if item["score"] >= POSITIVE_SCORE and item["aspect"] not in aspects:
            selected.append(item)
            aspects.add(item["aspect"])
        if len(selected) == MAX_STRENGTHS:
            break
    return selected


def build_consultation_text(strengths: list[Strength]) -> str:
    """Build at most three sentences, explicitly limiting claims to evaluated courses."""
    sentences: list[str] = []
    for strength in strengths:
        score: str = f"{strength['score']:.2f}".replace(".", ",")
        scope: str = "В оценённых дисциплинах"
        if strength["discipline_count"] == 1:
            item: ScoreEvidence = strength["evidence"][0]
            course: str = f", курс {item['course']}" if item["course"] else ""
            scope = f"В дисциплине «{item['discipline']}»{course}"
        sentences.append(
            f"{scope} студенты высоко оценивают {strength['description']} — {score} из 5 по СОП."
        )
    return " ".join(sentences)


def analyze_sop_strengths(path: Path, module_label: str) -> StrengthsResult:
    """Create positive consultation briefs without changing the risk analysis."""
    if not module_label.strip():
        raise SopAnalysisError("A non-empty SOP period label is required")
    programs, evidence, comments = collect_strengths_data(read_sop_workbook(path))
    summaries: list[ProgramStrengths] = []
    for program in programs:
        strengths: list[Strength] = select_program_strengths([item for item in evidence if item["program"] == program])
        summaries.append({
            "program": program, "consultation_text": build_consultation_text(strengths),
            "review_note": "" if strengths else "Нет показателей со средней оценкой не ниже 4 из 5; готовый тезис не сформирован.",
            "strengths": strengths,
        })
    return {
        "source_file": str(path), "module_label": module_label, "method": METHOD,
        "programs": summaries, "score_evidence": evidence, "positive_comments": comments,
    }


def write_strengths_outputs(result: StrengthsResult, output_dir: Path) -> None:
    """Write a consultation CSV, complete evidence and separate manager cards."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cards_dir: Path = output_dir / "programs"
    cards_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for index, program in enumerate(result["programs"], start=1):
        details: list[str] = [
            f"{item['description']}: {item['score']:.2f}/5; "
            f"дисциплин: {item['discipline_count']}; строк: {item['row_count']}; "
            f"лист «{item['sheet']}»; строки: {', '.join(str(row['source_row']) for row in item['evidence'])}"
            for item in program["strengths"]
        ]
        rows.append({
            "Программа": program["program"], "Период СОП": result["module_label"],
            "Для консультирования": program["consultation_text"], "Основания": "\n".join(details),
            "Примечание менеджеру": program["review_note"], "Источник": result["source_file"],
            "Методика": result["method"],
        })
        comments: list[str] = list(dict.fromkeys(
            f"{item['discipline']} (курс {item['course']}): {item['text']}"
            for item in result["positive_comments"] if item["program"] == program["program"]
        ))
        card: str = "\n\n".join([
            program["program"], result["module_label"], program["consultation_text"], program["review_note"],
            "Основания:\n" + "\n".join(details),
            "Позитивные комментарии из исходника для ручной обработки:\n" + "\n\n".join(comments),
            result["method"], f"Источник: {result['source_file']}",
        ])
        safe_program: str = "".join(character if character.isalnum() else "_" for character in program["program"])
        (cards_dir / f"{index:03d}_{safe_program}.txt").write_text(card, encoding="utf-8")
    pd.DataFrame(rows).to_csv(output_dir / "consultation_briefs.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(result["score_evidence"], columns=list(ScoreEvidence.__annotations__)).to_csv(
        output_dir / "score_evidence.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(result["positive_comments"], columns=list(PositiveComment.__annotations__)).to_csv(
        output_dir / "positive_comments.csv", index=False, encoding="utf-8-sig"
    )
    (output_dir / "sop_strengths.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
