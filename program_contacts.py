from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import TypedDict

from sop_analysis import ProgramSummary, normalize_program_name, normalize_text


REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "program",
        "academic_lead_emails",
        "manager_emails",
        "extra_cc_emails",
        "program_aliases",
    }
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactError(ValueError):
    """Raised when contact data is missing or invalid."""


class ProgramContact(TypedDict):
    program: str
    academic_lead_emails: list[str]
    manager_emails: list[str]
    extra_cc_emails: list[str]
    program_aliases: list[str]


class ContactMatchResult(TypedDict):
    contacts_by_program: dict[str, ProgramContact]
    missing_programs: list[str]


def split_list_field(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;\n,]+", value)
    return [normalize_text(part) for part in parts if normalize_text(part)]


def split_aliases(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[|\n]+", value)
    return [normalize_text(part) for part in parts if normalize_text(part)]


def validate_email(email: str) -> None:
    if EMAIL_PATTERN.match(email) is None:
        raise ContactError(f"Invalid email address in contacts CSV: {email}")


def validate_contact(contact: ProgramContact) -> None:
    if not contact["program"]:
        raise ContactError("Contacts CSV contains a row without a program name")
    if not contact["academic_lead_emails"]:
        raise ContactError(f"Program '{contact['program']}' has no academic lead email")

    emails = [
        *contact["academic_lead_emails"],
        *contact["manager_emails"],
        *contact["extra_cc_emails"],
    ]
    for email in emails:
        validate_email(email)


def read_program_contacts(path: Path) -> list[ProgramContact]:
    if not path.exists():
        raise ContactError(f"Contacts CSV does not exist: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ContactError(f"Contacts CSV is empty: {path}")

        missing_columns = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing_columns:
            joined_columns = ", ".join(sorted(missing_columns))
            raise ContactError(f"Contacts CSV is missing required columns: {joined_columns}")

        contacts: list[ProgramContact] = []
        for row in reader:
            contact: ProgramContact = {
                "program": normalize_text(row["program"]),
                "academic_lead_emails": split_list_field(row["academic_lead_emails"]),
                "manager_emails": split_list_field(row["manager_emails"]),
                "extra_cc_emails": split_list_field(row["extra_cc_emails"]),
                "program_aliases": split_aliases(row["program_aliases"]),
            }
            validate_contact(contact)
            contacts.append(contact)

    return contacts


def build_contact_index(contacts: list[ProgramContact]) -> dict[str, ProgramContact]:
    index: dict[str, ProgramContact] = {}
    for contact in contacts:
        keys = [contact["program"], *contact["program_aliases"]]
        for key in keys:
            normalized_key = normalize_program_name(key)
            if not normalized_key:
                continue
            if normalized_key in index:
                raise ContactError(f"Duplicate contact alias in contacts CSV: {key}")
            index[normalized_key] = contact
    return index


def match_contacts(
    program_summaries: list[ProgramSummary],
    contacts: list[ProgramContact],
) -> ContactMatchResult:
    index = build_contact_index(contacts)
    contacts_by_program: dict[str, ProgramContact] = {}
    missing_programs: list[str] = []
    for summary in program_summaries:
        program = summary["program"]
        contact = index.get(normalize_program_name(program))
        if contact is None:
            missing_programs.append(program)
            continue
        contacts_by_program[program] = contact
    return {
        "contacts_by_program": contacts_by_program,
        "missing_programs": sorted(missing_programs),
    }


def assert_all_programs_have_contacts(match_result: ContactMatchResult) -> None:
    if match_result["missing_programs"]:
        joined_programs = "\n".join(f"- {program}" for program in match_result["missing_programs"])
        raise ContactError(f"Missing contacts for SOP programs:\n{joined_programs}")
