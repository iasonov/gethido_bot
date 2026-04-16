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
from pathlib import Path
from typing import TypedDict

from program_contacts import ProgramContact
from sop_analysis import DisciplineSummary, ProgramSummary, RowIssue


PERMANENT_CC_EMAILS: tuple[str, ...] = ("yremezova@hse.ru", "ekalyaeva@hse.ru")
DEFAULT_MODULE_LABEL = "3 модуль 2025/2026"


class EmailNotificationError(RuntimeError):
    """Raised when notification email generation or sending fails."""


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
    env_value = os.environ.get(name)
    if env_value is not None and env_value != "":
        return env_value
    secret_value = secret_values.get(name)
    if secret_value is not None and secret_value != "":
        return secret_value
    return fallback


def unique_emails(emails: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for email in emails:
        normalized = email.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(email.strip())
    return result


def get_program_disciplines(
    program: str,
    discipline_summaries: list[DisciplineSummary],
) -> list[DisciplineSummary]:
    disciplines = [
        discipline for discipline in discipline_summaries if discipline["program"] == program
    ]
    return sorted(disciplines, key=lambda item: (item["status"], item["min_score"]))


def get_program_row_issues(program: str, row_issues: list[RowIssue]) -> list[RowIssue]:
    issues = [issue for issue in row_issues if issue["program"] == program]
    return sorted(issues, key=lambda item: (item["status"], item["min_score"], item["discipline"]))


def format_metric_issues(issue: RowIssue) -> str:
    metrics = issue["metrics_below_3"] if issue["metrics_below_3"] else issue["metrics_below_4"]
    return "; ".join(f"{metric['metric']}: {metric['value']:.2f}" for metric in metrics)


def build_subject(program_summary: ProgramSummary, module_label: str) -> str:
    return f"[СОП] {program_summary['status']}: {program_summary['program']}, {module_label}"


def build_text_body(
    program_summary: ProgramSummary,
    disciplines: list[DisciplineSummary],
    row_issues: list[RowIssue],
    source_label: str,
    module_label: str,
) -> str:
    lines = [
        "Добрый день!",
        "",
        f"По итогам анализа студенческой оценки преподавания за {module_label} по программе "
        f"«{program_summary['program']}» просим обратить внимание на дисциплины:", #зафиксирован статус: {program_summary['status']}.
        ""
    ]

    for discipline in disciplines:
        lines.append(
            "- "
            f"{discipline['discipline']}: {discipline['status']}, "
            f"минимальная оценка {discipline['min_score']:.2f}"
        )

    lines.extend(["", "Детали по преподавателям, дисциплинам и метрикам:"])
    for issue in row_issues:
        teacher_text = f", преподаватель: {issue['teacher']}" if issue["teacher"] else ""
        lines.append(
            "- "
            f"{issue['discipline']} ({issue['sheet']}{teacher_text}): "
            f"{issue['status']}; {format_metric_issues(issue)}"
        )

    lines.extend(
        [
            ""#,
            # "На что обратить внимание:",
            # "- проверить дисциплины и преподавателей с оценками ниже порогов;",
            # "- обсудить причины низких оценок с командой программы;",
            # "- определить корректирующие действия по содержанию, коммуникации и организации курса.",
            # "",
            # f"Источник данных: {source_label}",
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
    discipline_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(discipline['discipline'])}</td>"
        f"<td>{html.escape(discipline['status'])}</td>"
        f"<td>{discipline['min_score']:.2f}</td>"
        "</tr>"
        for discipline in disciplines
    )
    issue_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(issue['discipline'])}</td>"
        f"<td>{html.escape(issue['teacher'])}</td>"
        f"<td>{html.escape(issue['sheet'])}</td>"
        f"<td>{html.escape(issue['status'])}</td>"
        f"<td>{html.escape(format_metric_issues(issue))}</td>"
        "</tr>"
        for issue in row_issues
    )

    # зафиксирован статус:       <strong>{html.escape(program_summary['status'])}</strong>
    return f"""
<html>
  <body>
    <p>Добрый день!</p>
    <p>
      По итогам анализа студенческой оценки преподавания за {html.escape(module_label)} по программе
      «{html.escape(program_summary['program'])}» видим моменты на которые просим обратить внимание.
    </p>
    <h3>Дисциплины, на которые стоит обратить внимание</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Дисциплина</th><th>Статус</th><th>Минимальная оценка</th></tr></thead>
      <tbody>{discipline_rows}</tbody>
    </table>
    <h3>Детали по преподавателям, дисциплинам и метрикам</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Дисциплина</th><th>Преподаватель</th><th>Источник</th><th>Статус</th><th>Метрики</th>
        </tr>
      </thead>
      <tbody>{issue_rows}</tbody>
    </table>
    <h3>На что обратить внимание</h3>
    <ul>
      <li>проверить дисциплины и преподавателей с оценками ниже порогов;</li>
      <li>обсудить причины низких оценок с командой программы;</li>
      <li>определить корректирующие действия по содержанию, коммуникации и организации курса.</li>
    </ul>
    <p>Источник данных: {html.escape(source_label)}</p>
  </body>
</html>
""".strip()


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

    return {
        "program": program_summary["program"],
        "status": program_summary["status"],
        "to": to_emails,
        "cc": cc_emails,
        "subject": subject,
        "message": message,
    }


def read_smtp_config() -> SmtpConfig:
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
    emails_dir = output_dir / "emails"
    emails_dir.mkdir(parents=True, exist_ok=True)
    for index, notification in enumerate(notifications, start=1):
        safe_program = "".join(
            character if character.isalnum() else "_" for character in notification["program"]
        )
        path = emails_dir / f"{index:03d}_{safe_program}.eml"
        path.write_bytes(bytes(notification["message"]))


def write_recipients_csv(
    notifications: list[ProgramNotification],
    output_dir: Path,
) -> None:
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
