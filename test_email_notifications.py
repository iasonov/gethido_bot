import smtplib
import unittest
from unittest.mock import Mock, patch

from email_notifications import (
    PERMANENT_CC_EMAILS,
    EmailNotificationError,
    build_program_notification,
    send_email_message,
)
from program_contacts import ProgramContact
from sop_analysis import DisciplineSummary, ProgramSummary, RowIssue


def build_program_summary() -> ProgramSummary:
    """Return a test program summary."""
    return {
        "program": "Финансы",
        "status": "Есть риски",
        "alarm_discipline_count": 1,
        "risk_discipline_count": 1,
        "issue_count": 2,
        "min_score": 2.5,
    }


def build_contact() -> ProgramContact:
    """Return test contact data."""
    return {
        "program": "Финансы",
        "academic_lead_emails": ["lead@hse.ru"],
        "manager_emails": ["manager@hse.ru"],
        "extra_cc_emails": ["extra@hse.ru"],
        "program_aliases": [],
    }


def build_discipline_summary() -> DisciplineSummary:
    """Return a test discipline summary for the attention block."""
    return {
        "program": "Финансы",
        "discipline": "Курс 1",
        "status": "Требует внимания",
        "issue_count": 1,
        "alarm_issue_count": 1,
        "risk_issue_count": 0,
        "min_score": 2.5,
    }


def build_risk_discipline_summary() -> DisciplineSummary:
    """Return a test discipline summary for the risk block."""
    return {
        "program": "Финансы",
        "discipline": "Курс 2",
        "status": "Есть риски",
        "issue_count": 1,
        "alarm_issue_count": 0,
        "risk_issue_count": 1,
        "min_score": 3.8,
    }


def build_row_issue() -> RowIssue:
    """Return a test SOP issue row."""
    return {
        "sheet": "Преподаватели на ОП",
        "program": "Финансы",
        "campus": "Москва",
        "level": "Магистратура",
        "course": "1",
        "discipline": "Курс 1",
        "activity": "Преподавание",
        "teacher": "Иванов Иван",
        "status": "Требует внимания",
        "metrics_below_3": [{"metric": "Общее качество", "value": 2.9}],
        "metrics_below_4": [{"metric": "Общее качество", "value": 2.9}],
        "min_score": 2.5,
    }


class EmailNotificationTests(unittest.TestCase):
    def test_recipients_include_to_managers_and_permanent_cc(self) -> None:
        """Recipients should include leads, managers, and permanent CC addresses."""
        notification = build_program_notification(
            build_program_summary(),
            build_contact(),
            [build_discipline_summary(), build_risk_discipline_summary()],
            [build_row_issue()],
            "source.xlsx",
            "3 модуль 2025/2026",
            "sender@hse.ru",
            "Sender",
        )

        self.assertEqual(notification["to"], ["lead@hse.ru"])
        self.assertIn("manager@hse.ru", notification["cc"])
        self.assertIn("extra@hse.ru", notification["cc"])
        for email in PERMANENT_CC_EMAILS:
            self.assertIn(email, notification["cc"])

        message = notification["message"]
        plain_body = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()

        self.assertIn("Дисциплины, требующие внимания", plain_body)
        self.assertIn("Дисциплины, имеющие риски", plain_body)
        self.assertIn("Подробные сведения по преподавателям, дисциплинам и метрикам приложены в PDF-файле.", plain_body)
        self.assertIn("<h3>Дисциплины, требующие внимания</h3>", html_body)
        self.assertIn("<h3>Дисциплины, имеющие риски</h3>", html_body)
        self.assertIn("<h3>Детали по преподавателям, дисциплинам и метрикам</h3>", html_body)

        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "Финансы_3_модуль_2025_2026.pdf")
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        attachment_payload = attachments[0].get_payload(decode=True)
        self.assertIsNotNone(attachment_payload)
        self.assertTrue(attachment_payload.startswith(b"%PDF-"))

    def test_empty_to_raises_error(self) -> None:
        """An empty recipient list should raise an explicit error."""
        contact = build_contact()
        contact["academic_lead_emails"] = []

        with self.assertRaises(EmailNotificationError):
            build_program_notification(
                build_program_summary(),
                contact,
                [build_discipline_summary(), build_risk_discipline_summary()],
                [build_row_issue()],
                "source.xlsx",
                "3 модуль 2025/2026",
                "sender@hse.ru",
                "Sender",
            )

    @patch("email_notifications.time.sleep")
    @patch("email_notifications.smtplib.SMTP")
    def test_smtp_retry_after_failure(self, mock_smtp: Mock, mock_sleep: Mock) -> None:
        """SMTP send should retry once after a temporary failure."""
        notification = build_program_notification(
            build_program_summary(),
            build_contact(),
            [build_discipline_summary(), build_risk_discipline_summary()],
            [build_row_issue()],
            "source.xlsx",
            "3 модуль 2025/2026",
            "sender@hse.ru",
            "Sender",
        )
        failing_smtp = Mock()
        failing_smtp.__enter__ = Mock(return_value=failing_smtp)
        failing_smtp.__exit__ = Mock(return_value=False)
        failing_smtp.send_message.side_effect = smtplib.SMTPServerDisconnected("lost")
        working_smtp = Mock()
        working_smtp.__enter__ = Mock(return_value=working_smtp)
        working_smtp.__exit__ = Mock(return_value=False)
        mock_smtp.side_effect = [failing_smtp, working_smtp]

        send_email_message(
            notification,
            {
                "host": "smtp.example.com",
                "port": 587,
                "username": "u",
                "password": "p",
                "from_email": "sender@hse.ru",
                "from_name": "Sender",
                "use_tls": False,
                "attempts": 2,
                "retry_delay_seconds": 1,
            },
        )

        self.assertEqual(mock_smtp.call_count, 2)
        mock_sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
