import tempfile
import unittest
from pathlib import Path

from program_contacts import (
    ContactError,
    assert_all_programs_have_contacts,
    match_contacts,
    read_program_contacts,
)


class ProgramContactsTests(unittest.TestCase):
    def test_exact_program_match(self) -> None:
        """Проверяет сопоставление контакта по точному названию программы."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "contacts.csv"
            path.write_text(
                "program,academic_lead_emails,manager_emails,extra_cc_emails,program_aliases\n"
                "Финансы,vsolodkov@hse.ru,uodintsova@hse.ru,,\n",
                encoding="utf-8",
            )

            contacts = read_program_contacts(path)
            result = match_contacts(
                [
                    {
                        "program": "Финансы",
                        "status": "Есть риски",
                        "alarm_discipline_count": 1,
                        "risk_discipline_count": 0,
                        "issue_count": 1,
                        "min_score": 2.9,
                    }
                ],
                contacts,
            )

        self.assertEqual(result["missing_programs"], [])
        self.assertIn("Финансы", result["contacts_by_program"])

    def test_alias_program_match(self) -> None:
        """Проверяет сопоставление контакта по алиасу программы."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "contacts.csv"
            path.write_text(
                "program,academic_lead_emails,manager_emails,extra_cc_emails,program_aliases\n"
                "Master of Finance,vsolodkov@hse.ru,uodintsova@hse.ru,,Финансы|Master of Finance\n",
                encoding="utf-8",
            )

            contacts = read_program_contacts(path)
            result = match_contacts(
                [
                    {
                        "program": "Финансы",
                        "status": "Есть риски",
                        "alarm_discipline_count": 1,
                        "risk_discipline_count": 0,
                        "issue_count": 1,
                        "min_score": 2.9,
                    }
                ],
                contacts,
            )

        self.assertEqual(result["contacts_by_program"]["Финансы"]["program"], "Master of Finance")

    def test_missing_program_raises_error(self) -> None:
        """Проверяет явную ошибку при отсутствии контакта программы."""
        match_result = {"contacts_by_program": {}, "missing_programs": ["Финансы"]}

        with self.assertRaises(ContactError):
            assert_all_programs_have_contacts(match_result)


if __name__ == "__main__":
    unittest.main()
