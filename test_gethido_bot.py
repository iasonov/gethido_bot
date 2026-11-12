import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gethido_bot as bot


class LoadProgramsTests(unittest.TestCase):
    def test_load_programs_skips_rows_without_tg_chat_id(self):
        csv_text = (
            "program;level;price;plan_state;plan_rus;plan_foreign;format;income_percent;"
            "program_bitrix;tg_chat_id;campus;start_year;partner;early_invitation\n"
            "Valid program;master;100;0;0;0;online;0;bitrix;-12345;Moscow;2026;Partner;yes\n"
            "Missing chat;master;100;0;0;0;online;0;bitrix;;Moscow;2026;Partner;no\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "programs.csv"
            csv_path.write_text(csv_text, encoding="utf-8")

            original_file = bot.PROGRAMS_CSV_FILE
            try:
                bot.PROGRAMS_CSV_FILE = str(csv_path)
                programs = bot.load_programs()
            finally:
                bot.PROGRAMS_CSV_FILE = original_file

        self.assertIsNotNone(programs)
        self.assertEqual(programs["program"].tolist(), ["Valid program"])
        self.assertEqual(programs["tg_chat_id"].tolist(), [-12345])


class ForwardMessageTests(unittest.TestCase):
    @patch("gethido_bot.time.sleep")
    @patch("gethido_bot.requests.post")
    def test_forward_message_retries_once_after_timeout(self, mock_post, mock_sleep):
        mock_response = unittest.mock.Mock(status_code=200)
        mock_post.side_effect = [bot.requests.exceptions.Timeout("boom"), mock_response]

        self.assertTrue(bot.forward_message(1, 2, 3))
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(bot.DELAY)

    @patch("gethido_bot.time.sleep")
    @patch("gethido_bot.requests.post")
    def test_forward_message_returns_false_after_two_timeouts(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            bot.requests.exceptions.Timeout("boom"),
            bot.requests.exceptions.Timeout("boom"),
        ]

        self.assertFalse(bot.forward_message(1, 2, 3))
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


if __name__ == "__main__":
    unittest.main()
