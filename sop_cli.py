from __future__ import annotations

import argparse
import sys
from pathlib import Path

from email_notifications import (
    DEFAULT_MODULE_LABEL,
    ProgramNotification,
    append_send_log,
    build_program_notification,
    read_smtp_config,
    send_email_message,
    write_notification_previews,
    write_recipients_csv,
)
from program_contacts import assert_all_programs_have_contacts, match_contacts, read_program_contacts
from sop_analysis import SopAnalysisResult, analyze_sop_workbook, write_analysis_outputs


def parse_bool(value: str) -> bool:
    normalized_value = value.strip().lower()
    if normalized_value in {"true", "1", "yes", "y", "да"}:
        return True
    if normalized_value in {"false", "0", "no", "n", "нет"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sop_cli",
        description="Analyze SOP xlsx files and prepare email notifications.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("--sop-xlsx", required=True)
    preview_parser.add_argument("--contacts-csv", required=True)
    preview_parser.add_argument("--out-dir", required=True)
    preview_parser.add_argument("--module-label", required=False)
    preview_parser.add_argument("--from-email", required=False)
    preview_parser.add_argument("--from-name", required=False)

    send_parser = subparsers.add_parser("send")
    send_parser.add_argument("--sop-xlsx", required=True)
    send_parser.add_argument("--contacts-csv", required=True)
    send_parser.add_argument("--dry-run", type=parse_bool, required=True)
    send_parser.add_argument("--out-dir", required=False)
    send_parser.add_argument("--module-label", required=False)

    return parser


def resolve_optional_text(value: str | None, fallback: str) -> str:
    if value is None or value.strip() == "":
        return fallback
    return value


def build_notifications(
    sop_xlsx: Path,
    contacts_csv: Path,
    module_label: str,
    from_email: str,
    from_name: str,
) -> tuple[SopAnalysisResult, list[ProgramNotification]]:
    analysis_result = analyze_sop_workbook(sop_xlsx)
    contacts = read_program_contacts(contacts_csv)
    match_result = match_contacts(analysis_result["program_summaries"], contacts)
    assert_all_programs_have_contacts(match_result)

    notifications = [
        build_program_notification(
            program_summary,
            match_result["contacts_by_program"][program_summary["program"]],
            analysis_result["discipline_summaries"],
            analysis_result["row_issues"],
            str(sop_xlsx),
            module_label,
            from_email,
            from_name,
        )
        for program_summary in analysis_result["program_summaries"]
    ]
    return analysis_result, notifications


def run_preview(args: argparse.Namespace) -> int:
    module_label = resolve_optional_text(args.module_label, DEFAULT_MODULE_LABEL)
    from_email = resolve_optional_text(args.from_email, "no-reply@example.invalid")
    from_name = resolve_optional_text(args.from_name, "Онлайн-кампус")
    output_dir = Path(args.out_dir)
    analysis_result, notifications = build_notifications(
        Path(args.sop_xlsx),
        Path(args.contacts_csv),
        module_label,
        from_email,
        from_name,
    )
    write_analysis_outputs(analysis_result, output_dir)
    write_notification_previews(notifications, output_dir)
    write_recipients_csv(notifications, output_dir)
    print(f"Preview created: {output_dir}")
    return 0


def run_send(args: argparse.Namespace) -> int:
    module_label = resolve_optional_text(args.module_label, DEFAULT_MODULE_LABEL)
    output_dir = Path(resolve_optional_text(args.out_dir, "output/sop_send"))
    if args.dry_run:
        from_email = "no-reply@example.invalid"
        from_name = "Онлайн-кампус"
        smtp_config = None
    else:
        smtp_config = read_smtp_config()
        from_email = smtp_config["from_email"]
        from_name = smtp_config["from_name"]

    analysis_result, notifications = build_notifications(
        Path(args.sop_xlsx),
        Path(args.contacts_csv),
        module_label,
        from_email,
        from_name,
    )
    write_analysis_outputs(analysis_result, output_dir)
    write_notification_previews(notifications, output_dir)
    write_recipients_csv(notifications, output_dir)

    log_path = output_dir / "send_log.jsonl"
    for notification in notifications:
        if not args.dry_run:
            if smtp_config is None:
                raise RuntimeError("SMTP config must be loaded for non-dry-run sending")
            send_email_message(notification, smtp_config)
        append_send_log(notification, log_path, args.dry_run)

    print(f"Send flow completed. Dry run: {args.dry_run}. Output: {output_dir}")
    return 0


def run_command(args: argparse.Namespace) -> int:
    if args.command == "preview":
        return run_preview(args)
    if args.command == "send":
        return run_send(args)
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        args = parser.parse_args(["preview", "--sop-xlsx", "sop.xlsx", "--contacts-csv", "program_contacts.csv", "--out-dir", "output"]) #"--dry-run", "true",
    else:
        args = parser.parse_args(sys.argv[1:])
    exit_code = run_command(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
