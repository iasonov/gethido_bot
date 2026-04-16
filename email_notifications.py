from __future__ import annotations

import csv
import html
import json
import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr
from io import BytesIO
from pathlib import Path
from typing import TypedDict

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from program_contacts import ProgramContact
from sop_analysis import (
    STATUS_ALARM,
    STATUS_RISK,
    DisciplineSummary,
    ProgramSummary,
    RowIssue,
)


PERMANENT_CC_EMAILS: tuple[str, ...] = ("yremezova@hse.ru", "ekalyaeva@hse.ru")
DEFAULT_MODULE_LABEL = "3 модуль 2025/2026"
PDF_FONT_CANDIDATE_PATHS: tuple[Path, ...] = (
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\calibri.ttf"),
    Path(r"C:\Windows\Fonts\times.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
)
PDF_FONT_NAME = "SOPNotificationFont"


class EmailNotificationError(RuntimeError):
    """Ошибка, возникающая при проблемах в генерации или отправке писем."""


class SmtpConfig(TypedDict):
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str
    use_tls: bool
    attempts: int
    retry_delay_seconds: int


class ProgramNotification(TypedDict):
    program: str
    status: str
    to: list[str]
    cc: list[str]
    subject: str
    message: EmailMessage


def read_secret_values() -> dict[str, str]:
    """Читает SMTP-настройки из my_secrets.py, если файл доступен в проекте."""
    try:
        import my_secrets
    except ModuleNotFoundError:
        return {}

    values: dict[str, str] = {}
    for name in [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "SMTP_FROM_NAME",
        "SMTP_USE_TLS",
        "SMTP_ATTEMPTS",
        "SMTP_RETRY_DELAY_SECONDS",
    ]:
        value = getattr(my_secrets, name, None)
        if value is not None:
            values[name] = str(value)
    return values


def read_config_value(name: str, secret_values: dict[str, str]) -> str:
    """Возвращает обязательное значение настройки из окружения или my_secrets.py."""
    env_value = os.environ.get(name)
    if env_value is not None and env_value != "":
        return env_value
    secret_value = secret_values.get(name)
    if secret_value is not None and secret_value != "":
        return secret_value
    raise EmailNotificationError(f"Missing required SMTP configuration value: {name}")


def read_optional_config_value(
    name: str,
    secret_values: dict[str, str],
    fallback: str,
) -> str:
    """Возвращает необязательное значение настройки или переданное значение по умолчанию."""
    env_value = os.environ.get(name)
    if env_value is not None and env_value != "":
        return env_value
    secret_value = secret_values.get(name)
    if secret_value is not None and secret_value != "":
        return secret_value
    return fallback


def unique_emails(emails: list[str]) -> list[str]:
    """Удаляет дубли email без изменения первого встреченного написания адреса."""
    seen: set[str] = set()
    result: list[str] = []
    for email in emails:
        normalized = email.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(email.strip())
    return result


def severity_rank(status: str) -> int:
    """Возвращает числовое значение для сортировки по критичности ситуации."""
    if status == STATUS_ALARM:
        return 0
    if status == STATUS_RISK:
        return 1
    return 2


def get_program_disciplines(
    program: str,
    discipline_summaries: list[DisciplineSummary],
) -> list[DisciplineSummary]:
    """Возвращает проблемные дисциплины выбранной программы, отсортированные по риску."""
    disciplines = [
        discipline for discipline in discipline_summaries if discipline["program"] == program
    ]
    return sorted(disciplines, key=lambda item: (severity_rank(item["status"]), item["min_score"]))


def get_program_row_issues(program: str, row_issues: list[RowIssue]) -> list[RowIssue]:
    """Возвращает проблемные строки выбранной программы, отсортированные по риску."""
    issues = [issue for issue in row_issues if issue["program"] == program]
    return sorted(
        issues,
        key=lambda item: (
            severity_rank(item["status"]),
            item["min_score"],
            item["discipline"],
        ),
    )


def format_metric_issues(issue: RowIssue) -> str:
    """Форматирует список проблемных метрик строки для текста письма."""
    metrics = issue["metrics_below_3"] if issue["metrics_below_3"] else issue["metrics_below_4"]
    return "; ".join(f"{metric['metric']}: {metric['value']:.2f}" for metric in metrics)


def build_subject(program_summary: ProgramSummary, module_label: str) -> str:
    """Формирует тему письма по программе, статусу и периоду СОП."""
    return f"[СОП] {program_summary['status']}: {program_summary['program']}, {module_label}"


def build_attachment_file_name(program_name: str, module_label: str) -> str:
    """Создает безопасное название PDF-файла"""
    safe_program = "".join(character if character.isalnum() else "_" for character in program_name)
    safe_module_label = "".join(
        character if character.isalnum() else "_" for character in module_label
    )
    return f"{safe_program}_{safe_module_label}.pdf"


def select_disciplines_by_status(
    disciplines: list[DisciplineSummary],
    status: str,
) -> list[DisciplineSummary]:
    """Фильтрует дисциплины по статусу."""
    return [discipline for discipline in disciplines if discipline["status"] == status]


def build_discipline_row_html(discipline: DisciplineSummary) -> str:
    """Формирует одну строку HTML для таблицы дисциплин."""
    return (
        "<tr>"
        f"<td>{html.escape(discipline['discipline'])}</td>"
        f"<td>{discipline['min_score']:.2f}</td>"
        "</tr>"
    )


def build_issue_row_html(issue: RowIssue) -> str:
    """Формирует одну строку HTML для таблицы дисциплин в приложении."""
    return (
        "<tr>"
        f"<td>{html.escape(issue['discipline'])}</td>"
        f"<td>{html.escape(issue['teacher'])}</td>"
        f"<td>{html.escape(issue['status'])}</td>"
        f"<td>{html.escape(format_metric_issues(issue))}</td>"
        "</tr>"
    )


def build_text_discipline_section(
    title: str,
    explanation: str,
    disciplines: list[DisciplineSummary],
) -> list[str]:
    """Создает одну текстовую секцию с заголовком, пояснение и буллетами."""
    lines: list[str] = [title, explanation]
    if disciplines:
        for discipline in disciplines:
            lines.append(
                f"- {discipline['discipline']}: минимальная оценка {discipline['min_score']:.2f}"
            )
    else:
        lines.append("- Нет дисциплин, которые соответствуют этому разделу.")
    lines.append("")
    return lines


def register_pdf_font() -> str:
    """Регистрирует кириллические шрифты для генерации PDF."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for font_path in PDF_FONT_CANDIDATE_PATHS:
        if font_path.exists():
            if PDF_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, str(font_path)))
            return PDF_FONT_NAME
    raise EmailNotificationError(
        "Unable to generate PDF attachment because no Cyrillic-capable font was found"
    )


def build_pdf_attachment(
    program_summary: ProgramSummary,
    module_label: str,
    row_issues: list[RowIssue],
) -> bytes:
    """Создает приложение PDF с детализацией по преподавателям и дисциплинам."""
    font_name = register_pdf_font()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SOPTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=16,
        leading=20,
        alignment=TA_LEFT,
        spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "SOPHeading",
        parent=styles["Heading3"],
        fontName=font_name,
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "SOPBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10,
        leading=13,
        spaceAfter=4,
    )
    table_style = ParagraphStyle(
        "SOPTable",
        parent=body_style,
        spaceAfter=0,
    )

    story: list[object] = [
        Paragraph(html.escape(program_summary["program"]), title_style),
        Paragraph(f"Период: {html.escape(module_label)}", body_style),
        Paragraph("Детали по преподавателям, дисциплинам и метрикам", heading_style),
    ]

    issue_rows: list[list[Paragraph]] = [
        [
            Paragraph(html.escape("Дисциплина"), table_style),
            Paragraph(html.escape("Преподаватель"), table_style),
            Paragraph(html.escape("Статус"), table_style),
            Paragraph(html.escape("Метрики"), table_style),
        ]
    ]
    for issue in row_issues:
        issue_rows.append(
            [
                Paragraph(html.escape(issue["discipline"]), table_style),
                Paragraph(html.escape(issue["teacher"]), table_style),
                Paragraph(html.escape(issue["status"]), table_style),
                Paragraph(html.escape(format_metric_issues(issue)), table_style),
            ]
        )

    issue_table = Table(issue_rows, colWidths=[45 * mm, 45 * mm, 28 * mm, 54 * mm], repeatRows=1)
    issue_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEADING", (0, 0), (-1, -1), 12),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    recommendation_items = ListFlowable(
        [
            ListItem(Paragraph("проверить дисциплины и преподавателей с оценками ниже порогов;", body_style)),
            ListItem(Paragraph("обсудить причины низких оценок с командой программы;", body_style)),
            ListItem(Paragraph("определить корректирующие действия по содержанию, коммуникации и организации дисциплины.", body_style)), # ,?
        ],
        bulletType="bullet",
        leftIndent=12,
    )

    story.extend(
        [
            Spacer(1, 4),
            issue_table,
            Paragraph("Как может выглядеть работа на основании обратной связи", heading_style),
            recommendation_items,
        ]
    )

    document.build(story)
    return buffer.getvalue()


def build_text_body(
    program_summary: ProgramSummary,
    disciplines: list[DisciplineSummary],
    row_issues: list[RowIssue],
    source_label: str,
    module_label: str,
) -> str:
    """Формирует текстовую версию письма-оповещения"""
    del source_label
    del row_issues

    attention_disciplines = select_disciplines_by_status(disciplines, STATUS_ALARM)
    risk_disciplines = select_disciplines_by_status(disciplines, STATUS_RISK)

    lines = [
        "Добрый день!",
        "",
        (
            f"По итогам анализа студенческой оценки преподавания за {module_label} "
            f"по программе «{program_summary['program']}» видим моменты, на которые просим обратить внимание."
        ),
        "",
    ]
    lines.extend(
        build_text_discipline_section(
            "Дисциплины, требующие внимания",
            "Хотя бы одна средняя оценка ниже 3 баллов или хотя бы две средних оценки ниже 4 баллов.",
            attention_disciplines,
        )
    )
    lines.extend(
        build_text_discipline_section(
            "Дисциплины, имеющие риски",
            "Хотя бы одна средняя оценка ниже 4 баллов.",
            risk_disciplines,
        )
    )
    lines.extend(
        [
            "Подробные сведения по преподавателям, дисциплинам и метрикам приложены в PDF-файле.",
            "",
        ]
    )
    return "\n".join(lines)


def build_html_body(
    program_summary: ProgramSummary,
    disciplines: list[DisciplineSummary],
    row_issues: list[RowIssue],
    source_label: str,
    module_label: str,
) -> str:
    """Создает HTML версию для оптравки по электронной почте."""
    del source_label

    attention_disciplines = select_disciplines_by_status(disciplines, STATUS_ALARM)
    risk_disciplines = select_disciplines_by_status(disciplines, STATUS_RISK)

    attention_rows = "\n".join(
        build_discipline_row_html(discipline) for discipline in attention_disciplines
    )
    risk_rows = "\n".join(build_discipline_row_html(discipline) for discipline in risk_disciplines)
    issue_rows = "\n".join(build_issue_row_html(issue) for issue in row_issues)

    if attention_rows == "":
        attention_rows = "<tr><td colspan=\"2\">Нет дисциплин для этого раздела.</td></tr>"
    if risk_rows == "":
        risk_rows = "<tr><td colspan=\"2\">Нет дисциплин для этого раздела.</td></tr>"
    if issue_rows == "":
        issue_rows = "<tr><td colspan=\"4\">Нет деталей по преподавателям и метрикам.</td></tr>"

    return f"""
<html>
  <body>
    <p>Добрый день!</p>
    <p>
      По итогам анализа студенческой оценки преподавания за {html.escape(module_label)} по программе
      «{html.escape(program_summary['program'])}» видим моменты, на которые просим обратить внимание.
    </p>
    <h3>Дисциплины, требующие внимания</h3>
    <p>Хотя бы одна средняя оценка ниже 3 баллов или хотя бы две средних оценки ниже 4 баллов.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Дисциплина</th><th>Минимальная оценка</th></tr></thead>
      <tbody>{attention_rows}</tbody>
    </table>
    <h3>Дисциплины, имеющие риски</h3>
    <p>Хотя бы одна средняя оценка ниже 4 баллов.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Дисциплина</th><th>Минимальная оценка</th></tr></thead>
      <tbody>{risk_rows}</tbody>
    </table>
    
    <p>Подробные сведения по преподавателям, дисциплинам и метрикам приложены в PDF-файле.</p>
  </body>
</html>
""".strip()
# <p>Детали по преподавателям, дисциплинам и метрикам представлены в приложении.</p>
# <h3>Детали по преподавателям, дисциплинам и метрикам</h3>
# <table border="1" cellpadding="6" cellspacing="0">
#   <thead>
#     <tr>
#       <th>Дисциплина</th><th>Преподаватель</th><th>Статус</th><th>Метрики</th>
#     </tr>
#   </thead>
#   <tbody>{issue_rows}</tbody>
# </table>
# <h3>Как может выглядеть работа на основании обратной связи</h3>
# <ul>
#   <li>проверить дисциплины и преподавателей с оценками ниже порогов;</li>
#   <li>обсудить причины низких оценок с командой программы;</li>
#   <li>определить корректирующие действия по содержанию, коммуникации и организации дисциплины.</li>
# </ul>
    

def build_program_notification(
    program_summary: ProgramSummary,
    contact: ProgramContact,
    discipline_summaries: list[DisciplineSummary],
    row_issues: list[RowIssue],
    source_label: str,
    module_label: str,
    from_email: str,
    from_name: str,
) -> ProgramNotification:
    """Создает письмо по одной программе, готовое к отправке."""
    to_emails = unique_emails(contact["academic_lead_emails"])
    if not to_emails:
        raise EmailNotificationError(
            f"Program '{program_summary['program']}' has no notification recipients"
        )

    cc_emails = unique_emails(
        [*PERMANENT_CC_EMAILS, *contact["manager_emails"], *contact["extra_cc_emails"]]
    )
    disciplines = get_program_disciplines(program_summary["program"], discipline_summaries)
    issues = get_program_row_issues(program_summary["program"], row_issues)
    subject = build_subject(program_summary, module_label)
    attachment_name = build_attachment_file_name(program_summary["program"], module_label)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((from_name, from_email))
    message["To"] = ", ".join(to_emails)
    message["Cc"] = ", ".join(cc_emails)
    message.set_content(
        build_text_body(program_summary, disciplines, issues, source_label, module_label)
    )
    message.add_alternative(
        build_html_body(program_summary, disciplines, issues, source_label, module_label),
        subtype="html",
    )
    message.add_attachment(
        build_pdf_attachment(program_summary, module_label, issues),
        maintype="application",
        subtype="pdf",
        filename=attachment_name,
    )

    return {
        "program": program_summary["program"],
        "status": program_summary["status"],
        "to": to_emails,
        "cc": cc_emails,
        "subject": subject,
        "message": message,
    }


def read_smtp_config() -> SmtpConfig:
    """Читает полную SMTP-конфигурацию из окружения или my_secrets.py."""
    secret_values = read_secret_values()

    return {
        "host": read_config_value("SMTP_HOST", secret_values),
        "port": int(read_config_value("SMTP_PORT", secret_values)),
        "username": read_config_value("SMTP_USERNAME", secret_values),
        "password": read_config_value("SMTP_PASSWORD", secret_values),
        "from_email": read_config_value("SMTP_FROM_EMAIL", secret_values),
        "from_name": read_optional_config_value("SMTP_FROM_NAME", secret_values, "Онлайн-кампус"),
        "use_tls": read_optional_config_value("SMTP_USE_TLS", secret_values, "true").lower()
        == "true",
        "attempts": int(read_optional_config_value("SMTP_ATTEMPTS", secret_values, "3")),
        "retry_delay_seconds": int(
            read_optional_config_value("SMTP_RETRY_DELAY_SECONDS", secret_values, "10")
        ),
    }


def send_email_message(notification: ProgramNotification, config: SmtpConfig) -> None:
    """Отправляет письмо через SMTP с повторными попытками при временных ошибках."""
    recipients = [*notification["to"], *notification["cc"]]
    last_error: Exception | None = None
    for attempt in range(1, config["attempts"] + 1):
        try:
            with smtplib.SMTP(config["host"], config["port"]) as smtp:
                smtp.ehlo()
                if config["use_tls"]:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(config["username"], config["password"])
                smtp.send_message(notification["message"], to_addrs=recipients)
                return
        except (smtplib.SMTPException, OSError) as error:
            last_error = error
            logging.warning(
                "smtp_send_failed",
                extra={
                    "program": notification["program"],
                    "attempt": attempt,
                    "attempts": config["attempts"],
                    "error": repr(error),
                },
            )
            if attempt < config["attempts"]:
                time.sleep(config["retry_delay_seconds"])

    raise EmailNotificationError(
        f"Failed to send SOP notification for '{notification['program']}': {last_error!r}"
    )


def write_notification_previews(
    notifications: list[ProgramNotification],
    output_dir: Path,
) -> None:
    """Сохраняет PDF и предпросмотр писем в формате .eml для ручной проверки."""
    emails_dir = output_dir / "emails"
    pdfs_dir = output_dir / "pdfs"
    emails_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir.mkdir(parents=True, exist_ok=True)

    for index, notification in enumerate(notifications, start=1):
        safe_program = "".join(
            character if character.isalnum() else "_" for character in notification["program"]
        )
        path = emails_dir / f"{index:03d}_{safe_program}.eml"
        path.write_bytes(bytes(notification["message"]))

        for attachment in notification["message"].iter_attachments():
            filename = attachment.get_filename()
            if filename is None:
                continue
            payload = attachment.get_payload(decode=True)
            if payload is None:
                continue
            (pdfs_dir / filename).write_bytes(payload)


def write_recipients_csv(
    notifications: list[ProgramNotification],
    output_dir: Path,
) -> None:
    """Сохраняет CSV-сводку получателей, тем и статусов писем."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "email_recipients.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["program", "to", "cc", "subject", "status"],
        )
        writer.writeheader()
        for notification in notifications:
            writer.writerow(
                {
                    "program": notification["program"],
                    "to": "; ".join(notification["to"]),
                    "cc": "; ".join(notification["cc"]),
                    "subject": notification["subject"],
                    "status": notification["status"],
                }
            )


def append_send_log(
    notification: ProgramNotification,
    log_path: Path,
    dry_run: bool,
) -> None:
    """Добавляет структурированную JSONL-запись о подготовке или отправке письма."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "program": notification["program"],
        "status": notification["status"],
        "to": notification["to"],
        "cc": notification["cc"],
        "subject": notification["subject"],
        "dry_run": dry_run,
    }
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
