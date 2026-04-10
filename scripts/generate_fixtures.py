#!/usr/bin/env python3
"""Generate synthetic test fixture documents with ground-truth PII annotations.

Usage:
    python scripts/generate_fixtures.py

All paths are relative to the project root.  Faker is seeded with 42 for
reproducibility.
"""
from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field

from faker import Faker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

fake = Faker("en_US")
Faker.seed(42)

SEED = 42


@dataclass
class EntityCollector:
    """Accumulates text and records PII entity spans as they are inserted."""

    parts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    _offset: int = 0

    # -- building blocks -----------------------------------------------------

    def add(self, text: str) -> None:
        """Append plain (non-PII) text."""
        self.parts.append(text)
        self._offset += len(text)

    def add_entity(self, text: str, entity_type: str) -> None:
        """Append PII text and record the span."""
        start = self._offset
        end = start + len(text)
        # build a short context window (up to 20 chars each side)
        full = self.full_text()
        ctx_start = max(0, start - 20)
        ctx_end = min(len(full) + len(text), end + 20)
        # we haven't appended yet, so build context manually
        before = full[ctx_start:]
        after = ""  # will be filled later; we patch context at the end
        self.entities.append(
            {
                "entity_type": entity_type,
                "start": start,
                "end": end,
                "text": text,
                "context": "",  # patched after document is complete
            }
        )
        self.parts.append(text)
        self._offset += len(text)

    def full_text(self) -> str:
        return "".join(self.parts)

    def finalize(self) -> tuple[str, list[dict]]:
        """Return completed text and entities with context filled in."""
        text = self.full_text()
        for ent in self.entities:
            s = max(0, ent["start"] - 20)
            e = min(len(text), ent["end"] + 20)
            ent["context"] = text[s:e]
        return text, self.entities

    def expected_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entities:
            counts[e["entity_type"]] = counts.get(e["entity_type"], 0) + 1
        return dict(sorted(counts.items()))


def _write(doc_path: str, text: str, entities: list[dict], ec: EntityCollector,
           provenance: str = "synthetic") -> None:
    """Write .txt and .json annotation side-car."""
    txt_path = FIXTURES / doc_path
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")

    json_path = txt_path.with_suffix(".json")
    annotation = {
        "document": txt_path.name,
        "provenance": provenance,
        "generator": "scripts/generate_fixtures.py",
        "seed": SEED,
        "entities": entities,
        "expected_counts": ec.expected_counts(),
    }
    json_path.write_text(json.dumps(annotation, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    logger.info("Wrote %s", txt_path.relative_to(ROOT))


# ---------------------------------------------------------------------------
# Document generators
# ---------------------------------------------------------------------------

def gen_nda_contract() -> None:
    ec = EntityCollector()

    # Pre-generate all PII values so we can reuse them
    party1 = fake.name()
    party2 = fake.name()
    company1 = fake.company()
    company2 = fake.company()
    addr1 = fake.address().replace("\n", ", ")
    addr2 = fake.address().replace("\n", ", ")
    eff_date = fake.date_between(start_date="-1y", end_date="today").strftime("%B %d, %Y")
    exp_date = fake.date_between(start_date="+1y", end_date="+3y").strftime("%B %d, %Y")
    email1 = fake.email()
    email2 = fake.email()
    witness_name = fake.name()

    ec.add("NON-DISCLOSURE AGREEMENT\n\nThis Non-Disclosure Agreement (the \"Agreement\") is entered into as of ")
    ec.add_entity(eff_date, "DATE_TIME")
    ec.add(" (the \"Effective Date\") by and between:\n\n")

    ec.add("Party A: ")
    ec.add_entity(party1, "PERSON")
    ec.add(", acting on behalf of ")
    ec.add_entity(company1, "ORGANIZATION")
    ec.add(", with principal offices located at ")
    ec.add_entity(addr1, "LOCATION")
    ec.add(", reachable at ")
    ec.add_entity(email1, "EMAIL_ADDRESS")
    ec.add(";\n\n")

    ec.add("Party B: ")
    ec.add_entity(party2, "PERSON")
    ec.add(", acting on behalf of ")
    ec.add_entity(company2, "ORGANIZATION")
    ec.add(", with principal offices located at ")
    ec.add_entity(addr2, "LOCATION")
    ec.add(", reachable at ")
    ec.add_entity(email2, "EMAIL_ADDRESS")
    ec.add(".\n\n")

    ec.add("WHEREAS, the parties wish to explore a potential business relationship ")
    ec.add("and, in connection therewith, may disclose to each other certain ")
    ec.add("confidential and proprietary information;\n\n")

    ec.add("NOW, THEREFORE, in consideration of the mutual covenants and agreements ")
    ec.add("set forth herein, and for other good and valuable consideration, the ")
    ec.add("receipt and sufficiency of which are hereby acknowledged, the parties ")
    ec.add("agree as follows:\n\n")

    ec.add("1. DEFINITION OF CONFIDENTIAL INFORMATION. \"Confidential Information\" ")
    ec.add("means any and all non-public, proprietary, or confidential information ")
    ec.add("disclosed by either party to the other party, whether orally, in writing, ")
    ec.add("electronically, or by any other means, including but not limited to: ")
    ec.add("trade secrets, business plans, financial data, customer lists, technical ")
    ec.add("specifications, software code, and marketing strategies.\n\n")

    ec.add("2. OBLIGATIONS. The receiving party shall: (a) hold the Confidential ")
    ec.add("Information in strict confidence; (b) not disclose the Confidential ")
    ec.add("Information to any third party without the prior written consent of the ")
    ec.add("disclosing party; (c) use the Confidential Information solely for the ")
    ec.add("purpose of evaluating the potential business relationship.\n\n")

    ec.add("3. TERM. This Agreement shall remain in effect until ")
    ec.add_entity(exp_date, "DATE_TIME")
    ec.add(", unless earlier terminated by either party upon thirty (30) days ")
    ec.add("written notice to the other party.\n\n")

    ec.add("4. GOVERNING LAW. This Agreement shall be governed by and construed in ")
    ec.add("accordance with the laws of the State of New York.\n\n")

    ec.add("IN WITNESS WHEREOF, the parties have executed this Agreement as of the ")
    ec.add("date first written above.\n\n")

    ec.add("_________________________\n")
    ec.add_entity(party1, "PERSON")
    ec.add("\nAuthorized Representative, ")
    ec.add_entity(company1, "ORGANIZATION")
    ec.add("\n\n_________________________\n")
    ec.add_entity(party2, "PERSON")
    ec.add("\nAuthorized Representative, ")
    ec.add_entity(company2, "ORGANIZATION")
    ec.add("\n\nWitness: ")
    ec.add_entity(witness_name, "PERSON")
    ec.add("\n")

    text, entities = ec.finalize()
    _write("legal/nda_contract.txt", text, entities, ec)


def gen_engagement_letter() -> None:
    ec = EntityCollector()

    attorney = fake.name()
    client = fake.name()
    firm = fake.company() + ", LLP"
    firm_addr = fake.address().replace("\n", ", ")
    client_addr = fake.address().replace("\n", ", ")
    phone = fake.phone_number()
    email_atty = fake.email()
    email_client = fake.email()
    letter_date = fake.date_between(start_date="-6m", end_date="today").strftime("%B %d, %Y")
    retainer = f"${fake.random_int(min=2000, max=15000):,}.00"
    hourly = f"${fake.random_int(min=250, max=750)}.00"

    ec.add_entity(firm, "ORGANIZATION")
    ec.add("\n")
    ec.add_entity(firm_addr, "LOCATION")
    ec.add("\nTel: ")
    ec.add_entity(phone, "PHONE_NUMBER")
    ec.add("\n\n")
    ec.add_entity(letter_date, "DATE_TIME")
    ec.add("\n\n")

    ec.add_entity(client, "PERSON")
    ec.add("\n")
    ec.add_entity(client_addr, "LOCATION")
    ec.add("\n\nDear ")
    ec.add_entity(client, "PERSON")
    ec.add(",\n\n")

    ec.add("Thank you for selecting ")
    ec.add_entity(firm, "ORGANIZATION")
    ec.add(" to represent you in connection with your business formation matter. ")
    ec.add("This letter sets forth the terms of our engagement.\n\n")

    ec.add("SCOPE OF REPRESENTATION: We will provide legal counsel regarding the ")
    ec.add("formation and structuring of your new business entity, including preparation ")
    ec.add("and filing of organizational documents, drafting of operating agreements, ")
    ec.add("and advising on regulatory compliance matters.\n\n")

    ec.add("ATTORNEY RESPONSIBLE: ")
    ec.add_entity(attorney, "PERSON")
    ec.add(" will be the primary attorney handling your matter. ")
    ec.add("You may reach ")
    ec.add_entity(attorney, "PERSON")
    ec.add(" directly at ")
    ec.add_entity(email_atty, "EMAIL_ADDRESS")
    ec.add(".\n\n")

    ec.add("FEES AND BILLING: Our standard hourly rate for this matter is ")
    ec.add(f"{hourly} per hour. We require an initial retainer of {retainer}, ")
    ec.add("which will be deposited into our client trust account and applied against ")
    ec.add("fees and costs as they are incurred. You will receive monthly invoices ")
    ec.add("detailing all work performed.\n\n")

    ec.add("COMMUNICATION: Please direct all correspondence to ")
    ec.add_entity(email_client, "EMAIL_ADDRESS")
    ec.add(" or to our office address listed above.\n\n")

    ec.add("Please sign and return a copy of this letter to confirm your agreement ")
    ec.add("to these terms.\n\n")

    ec.add("Sincerely,\n\n")
    ec.add_entity(attorney, "PERSON")
    ec.add("\nPartner, ")
    ec.add_entity(firm, "ORGANIZATION")
    ec.add("\n\nACCEPTED AND AGREED:\n\n_________________________\n")
    ec.add_entity(client, "PERSON")
    ec.add("\nDate: _________________\n")

    text, entities = ec.finalize()
    _write("legal/engagement_letter.txt", text, entities, ec)


def gen_bank_statement() -> None:
    ec = EntityCollector()

    holder = fake.name()
    addr = fake.address().replace("\n", ", ")
    acct = fake.bban()
    routing = fake.aba()
    stmt_date = fake.date_between(start_date="-2m", end_date="today")
    period_start = stmt_date.replace(day=1).strftime("%m/%d/%Y")
    period_end = stmt_date.strftime("%m/%d/%Y")
    bank_name = fake.company() + " National Bank"

    merchants = [fake.company() for _ in range(5)]
    amounts = [fake.pyfloat(min_value=10, max_value=2500, right_digits=2) for _ in range(5)]
    txn_dates = sorted(
        [fake.date_between_dates(date_start=stmt_date.replace(day=1), date_end=stmt_date)
         for _ in range(5)]
    )

    ec.add_entity(bank_name, "ORGANIZATION")
    ec.add("\nMONTHLY ACCOUNT STATEMENT\n\n")

    ec.add("Account Holder: ")
    ec.add_entity(holder, "PERSON")
    ec.add("\nMailing Address: ")
    ec.add_entity(addr, "LOCATION")
    ec.add("\nAccount Number: ")
    ec.add_entity(acct, "US_BANK_NUMBER")
    ec.add("\nRouting Number: ")
    ec.add_entity(routing, "US_BANK_NUMBER")
    ec.add("\nStatement Period: ")
    ec.add_entity(period_start, "DATE_TIME")
    ec.add(" - ")
    ec.add_entity(period_end, "DATE_TIME")
    ec.add("\n\n")

    balance = fake.pyfloat(min_value=5000, max_value=50000, right_digits=2)
    ec.add(f"Beginning Balance: ${balance:,.2f}\n\n")

    ec.add("TRANSACTION HISTORY\n")
    ec.add("-" * 70 + "\n")
    ec.add(f"{'Date':<14}{'Description':<36}{'Amount':>10}{'Balance':>10}\n")
    ec.add("-" * 70 + "\n")

    for i in range(5):
        d = txn_dates[i].strftime("%m/%d/%Y")
        sign = fake.random_element(["-", "+"])
        if sign == "-":
            balance -= amounts[i]
        else:
            balance += amounts[i]

        ec.add_entity(d, "DATE_TIME")
        ec.add("    ")
        ec.add_entity(merchants[i], "ORGANIZATION")
        padding = 36 - len(merchants[i])
        ec.add(" " * max(padding, 1))
        ec.add(f"{sign}${amounts[i]:>8,.2f}")
        ec.add(f"  ${balance:>9,.2f}\n")

    ec.add("-" * 70 + "\n")
    ec.add(f"Ending Balance: ${balance:,.2f}\n\n")

    ec.add("For questions about your account, please contact ")
    ec.add_entity(bank_name, "ORGANIZATION")
    ec.add(" customer service at 1-800-555-0199.\n")

    text, entities = ec.finalize()
    _write("financial/bank_statement.txt", text, entities, ec)


def gen_invoice() -> None:
    ec = EntityCollector()

    sender_co = fake.company()
    sender_person = fake.name()
    sender_addr = fake.address().replace("\n", ", ")
    sender_phone = fake.phone_number()
    sender_email = fake.email()

    receiver_co = fake.company()
    receiver_person = fake.name()
    receiver_addr = fake.address().replace("\n", ", ")

    inv_num = f"INV-{fake.random_int(min=10000, max=99999)}"
    inv_date = fake.date_between(start_date="-30d", end_date="today").strftime("%B %d, %Y")
    due_date = fake.date_between(start_date="+15d", end_date="+45d").strftime("%B %d, %Y")
    iban = fake.iban()

    ec.add("INVOICE\n\n")
    ec.add("From:\n")
    ec.add_entity(sender_co, "ORGANIZATION")
    ec.add("\nAttn: ")
    ec.add_entity(sender_person, "PERSON")
    ec.add("\n")
    ec.add_entity(sender_addr, "LOCATION")
    ec.add("\nPhone: ")
    ec.add_entity(sender_phone, "PHONE_NUMBER")
    ec.add("\nEmail: ")
    ec.add_entity(sender_email, "EMAIL_ADDRESS")
    ec.add("\n\nTo:\n")
    ec.add_entity(receiver_co, "ORGANIZATION")
    ec.add("\nAttn: ")
    ec.add_entity(receiver_person, "PERSON")
    ec.add("\n")
    ec.add_entity(receiver_addr, "LOCATION")
    ec.add("\n\n")

    ec.add("Invoice Number: ")
    ec.add(f"{inv_num}\n")
    ec.add("Invoice Date: ")
    ec.add_entity(inv_date, "DATE_TIME")
    ec.add("\nDue Date: ")
    ec.add_entity(due_date, "DATE_TIME")
    ec.add("\n\n")

    ec.add("ITEMS\n")
    ec.add("-" * 60 + "\n")
    ec.add(f"{'Description':<35}{'Qty':>5}{'Unit Price':>10}{'Total':>10}\n")
    ec.add("-" * 60 + "\n")

    items = [
        ("Consulting Services (40 hrs)", 40, 150.00),
        ("Project Management", 1, 2500.00),
        ("Travel Expenses", 1, 875.50),
    ]
    grand_total = 0.0
    for desc, qty, price in items:
        total = qty * price
        grand_total += total
        ec.add(f"{desc:<35}{qty:>5}{price:>10,.2f}{total:>10,.2f}\n")

    ec.add("-" * 60 + "\n")
    ec.add(f"{'TOTAL':>50}{grand_total:>10,.2f}\n\n")

    ec.add("PAYMENT INFORMATION\n")
    ec.add("Please remit payment to:\n")
    ec.add("Bank: First National Business Bank\n")
    ec.add("IBAN: ")
    ec.add_entity(iban, "IBAN_CODE")
    ec.add("\n\nPayment is due by ")
    ec.add_entity(due_date, "DATE_TIME")
    ec.add(". Late payments are subject to a 1.5% monthly finance charge.\n")

    text, entities = ec.finalize()
    _write("financial/invoice.txt", text, entities, ec)


def gen_discharge_summary() -> None:
    ec = EntityCollector()

    patient = fake.name()
    dob = fake.date_of_birth(minimum_age=25, maximum_age=80).strftime("%m/%d/%Y")
    ssn = fake.ssn()
    physician = "Dr. " + fake.name()
    hospital = fake.company() + " Medical Center"
    admit_date = fake.date_between(start_date="-30d", end_date="-7d").strftime("%m/%d/%Y")
    discharge_date = fake.date_between(start_date="-6d", end_date="today").strftime("%m/%d/%Y")
    patient_addr = fake.address().replace("\n", ", ")
    patient_phone = fake.phone_number()
    mrn = f"MRN-{fake.random_int(min=100000, max=999999)}"

    ec.add_entity(hospital, "ORGANIZATION")
    ec.add("\nDISCHARGE SUMMARY\n\n")

    ec.add("PATIENT INFORMATION\n")
    ec.add("Name: ")
    ec.add_entity(patient, "PERSON")
    ec.add("\nDate of Birth: ")
    ec.add_entity(dob, "DATE_TIME")
    ec.add("\nSocial Security Number: ")
    ec.add_entity(ssn, "US_SSN")
    ec.add("\nMedical Record Number: ")
    ec.add(f"{mrn}\n")
    ec.add("Address: ")
    ec.add_entity(patient_addr, "LOCATION")
    ec.add("\nPhone: ")
    ec.add_entity(patient_phone, "PHONE_NUMBER")
    ec.add("\n\n")

    ec.add("ATTENDING PHYSICIAN: ")
    ec.add_entity(physician, "PERSON")
    ec.add("\n\n")

    ec.add("ADMISSION DATE: ")
    ec.add_entity(admit_date, "DATE_TIME")
    ec.add("\nDISCHARGE DATE: ")
    ec.add_entity(discharge_date, "DATE_TIME")
    ec.add("\n\n")

    ec.add("CHIEF COMPLAINT: Patient presented with acute chest pain radiating to ")
    ec.add("the left arm, accompanied by shortness of breath and diaphoresis.\n\n")

    ec.add("HISTORY OF PRESENT ILLNESS: ")
    ec.add_entity(patient, "PERSON")
    ec.add(" is a ")
    age = fake.random_int(min=45, max=75)
    ec.add(f"{age}-year-old ")
    ec.add("male who presented to the emergency department with substernal chest ")
    ec.add("pain that began approximately three hours prior to arrival. The pain ")
    ec.add("was described as pressure-like, rated 8/10 in severity, and associated ")
    ec.add("with nausea and lightheadedness. The patient has a history of ")
    ec.add("hypertension, hyperlipidemia, and type 2 diabetes mellitus.\n\n")

    ec.add("HOSPITAL COURSE: The patient was admitted to the cardiac care unit. ")
    ec.add("Initial troponin levels were elevated. Cardiac catheterization revealed ")
    ec.add("a 90% occlusion of the left anterior descending artery. Percutaneous ")
    ec.add("coronary intervention was performed with placement of a drug-eluting ")
    ec.add("stent. The patient tolerated the procedure well and was monitored for ")
    ec.add("48 hours without complications.\n\n")

    ec.add("DISCHARGE MEDICATIONS:\n")
    ec.add("1. Aspirin 81 mg daily\n")
    ec.add("2. Clopidogrel 75 mg daily\n")
    ec.add("3. Atorvastatin 40 mg daily\n")
    ec.add("4. Metoprolol 25 mg twice daily\n")
    ec.add("5. Lisinopril 10 mg daily\n\n")

    ec.add("FOLLOW-UP: Patient to follow up with ")
    ec.add_entity(physician, "PERSON")
    ec.add(" in two weeks. Cardiac rehabilitation referral has been placed.\n\n")

    ec.add("Electronically signed by:\n")
    ec.add_entity(physician, "PERSON")
    ec.add("\n")
    ec.add_entity(hospital, "ORGANIZATION")
    ec.add("\n")

    text, entities = ec.finalize()
    _write("medical/discharge_summary.txt", text, entities, ec)


def gen_referral_letter() -> None:
    ec = EntityCollector()

    referring_dr = "Dr. " + fake.name()
    specialist = "Dr. " + fake.name()
    patient = fake.name()
    clinic1 = fake.company() + " Family Practice"
    clinic2 = fake.company() + " Cardiology Associates"
    clinic1_addr = fake.address().replace("\n", ", ")
    clinic2_addr = fake.address().replace("\n", ", ")
    phone1 = fake.phone_number()
    phone2 = fake.phone_number()
    ref_date = fake.date_between(start_date="-14d", end_date="today").strftime("%B %d, %Y")
    patient_dob = fake.date_of_birth(minimum_age=30, maximum_age=70).strftime("%m/%d/%Y")

    ec.add_entity(clinic1, "ORGANIZATION")
    ec.add("\n")
    ec.add_entity(clinic1_addr, "LOCATION")
    ec.add("\nPhone: ")
    ec.add_entity(phone1, "PHONE_NUMBER")
    ec.add("\n\n")
    ec.add_entity(ref_date, "DATE_TIME")
    ec.add("\n\n")

    ec.add_entity(specialist, "PERSON")
    ec.add("\n")
    ec.add_entity(clinic2, "ORGANIZATION")
    ec.add("\n")
    ec.add_entity(clinic2_addr, "LOCATION")
    ec.add("\nPhone: ")
    ec.add_entity(phone2, "PHONE_NUMBER")
    ec.add("\n\n")

    ec.add("Dear ")
    ec.add_entity(specialist, "PERSON")
    ec.add(",\n\n")

    ec.add("I am writing to refer my patient, ")
    ec.add_entity(patient, "PERSON")
    ec.add(" (Date of Birth: ")
    ec.add_entity(patient_dob, "DATE_TIME")
    ec.add("), for cardiology evaluation and management.\n\n")

    ec.add_entity(patient, "PERSON")
    ec.add(" has been under my care for the past three years. The patient recently ")
    ec.add("presented with complaints of intermittent palpitations and exercise ")
    ec.add("intolerance that have been worsening over the past two months. ")
    ec.add("Resting ECG performed in our office showed occasional premature ")
    ec.add("ventricular contractions. A 24-hour Holter monitor revealed ")
    ec.add("frequent PVCs with short runs of non-sustained ventricular ")
    ec.add("tachycardia.\n\n")

    ec.add("The patient's medical history includes well-controlled hypertension ")
    ec.add("managed with amlodipine 5 mg daily, and mild hyperlipidemia treated ")
    ec.add("with dietary modification. There is a family history of sudden cardiac ")
    ec.add("death in a first-degree relative.\n\n")

    ec.add("I would appreciate your evaluation of this patient and any ")
    ec.add("recommendations regarding further workup or treatment. Please do not ")
    ec.add("hesitate to contact me if you require any additional information.\n\n")

    ec.add("Sincerely,\n\n")
    ec.add_entity(referring_dr, "PERSON")
    ec.add("\n")
    ec.add_entity(clinic1, "ORGANIZATION")
    ec.add("\n")

    text, entities = ec.finalize()
    _write("medical/referral_letter.txt", text, entities, ec)


def gen_resume() -> None:
    ec = EntityCollector()

    person = fake.name()
    email = fake.email()
    phone = fake.phone_number()
    addr = fake.address().replace("\n", ", ")
    linkedin = f"linkedin.com/in/{person.lower().replace(' ', '-')}"

    company1 = fake.company()
    company2 = fake.company()
    university = fake.company() + " University"

    start1 = fake.date_between(start_date="-8y", end_date="-4y").strftime("%B %Y")
    end1 = fake.date_between(start_date="-4y", end_date="-2y").strftime("%B %Y")
    start2 = fake.date_between(start_date="-2y", end_date="-1y").strftime("%B %Y")
    grad_date = fake.date_between(start_date="-10y", end_date="-8y").strftime("%B %Y")

    ec.add_entity(person, "PERSON")
    ec.add("\n")
    ec.add_entity(addr, "LOCATION")
    ec.add("\n")
    ec.add_entity(email, "EMAIL_ADDRESS")
    ec.add(" | ")
    ec.add_entity(phone, "PHONE_NUMBER")
    ec.add(" | ")
    ec.add_entity(linkedin, "URL")
    ec.add("\n\n")

    ec.add("PROFESSIONAL SUMMARY\n")
    ec.add("Results-driven senior software engineer with over eight years of ")
    ec.add("experience designing and implementing scalable distributed systems. ")
    ec.add("Proven track record of leading cross-functional teams, reducing ")
    ec.add("system latency by 40%, and delivering high-availability platforms ")
    ec.add("serving millions of daily active users.\n\n")

    ec.add("EXPERIENCE\n\n")
    ec.add("Senior Software Engineer\n")
    ec.add_entity(company2, "ORGANIZATION")
    ec.add(" | ")
    ec.add_entity(start2, "DATE_TIME")
    ec.add(" - Present\n")
    ec.add("- Architected microservices platform handling 50,000 requests per second\n")
    ec.add("- Led migration from monolithic architecture to event-driven design\n")
    ec.add("- Mentored team of six junior engineers on distributed systems patterns\n")
    ec.add("- Implemented CI/CD pipeline reducing deployment time from hours to minutes\n\n")

    ec.add("Software Engineer\n")
    ec.add_entity(company1, "ORGANIZATION")
    ec.add(" | ")
    ec.add_entity(start1, "DATE_TIME")
    ec.add(" - ")
    ec.add_entity(end1, "DATE_TIME")
    ec.add("\n")
    ec.add("- Developed real-time data processing pipeline using Apache Kafka and Spark\n")
    ec.add("- Designed and maintained RESTful APIs serving 200+ internal consumers\n")
    ec.add("- Reduced database query latency by 60% through index optimization\n")
    ec.add("- Contributed to open-source monitoring tools adopted by 500+ organizations\n\n")

    ec.add("EDUCATION\n\n")
    ec.add("Bachelor of Science in Computer Science\n")
    ec.add_entity(university, "ORGANIZATION")
    ec.add(" | Graduated ")
    ec.add_entity(grad_date, "DATE_TIME")
    ec.add("\nDean's List, GPA: 3.8/4.0\n\n")

    ec.add("SKILLS\n")
    ec.add("Languages: Python, Go, Java, TypeScript\n")
    ec.add("Frameworks: Django, FastAPI, Spring Boot, React\n")
    ec.add("Infrastructure: AWS, Kubernetes, Docker, Terraform\n")
    ec.add("Databases: PostgreSQL, Redis, DynamoDB, Elasticsearch\n")

    text, entities = ec.finalize()
    _write("hr/resume.txt", text, entities, ec)


def gen_offer_letter() -> None:
    ec = EntityCollector()

    candidate = fake.name()
    hiring_mgr = fake.name()
    company = fake.company()
    company_addr = fake.address().replace("\n", ", ")
    candidate_addr = fake.address().replace("\n", ", ")
    phone = fake.phone_number()
    email_hr = fake.email()
    start_date = fake.date_between(start_date="+14d", end_date="+60d").strftime("%B %d, %Y")
    letter_date = fake.date_between(start_date="-7d", end_date="today").strftime("%B %d, %Y")
    salary = fake.random_int(min=85000, max=180000, step=5000)

    ec.add_entity(company, "ORGANIZATION")
    ec.add("\n")
    ec.add_entity(company_addr, "LOCATION")
    ec.add("\n\n")
    ec.add_entity(letter_date, "DATE_TIME")
    ec.add("\n\n")
    ec.add_entity(candidate, "PERSON")
    ec.add("\n")
    ec.add_entity(candidate_addr, "LOCATION")
    ec.add("\n\nDear ")
    ec.add_entity(candidate, "PERSON")
    ec.add(",\n\n")

    ec.add("On behalf of ")
    ec.add_entity(company, "ORGANIZATION")
    ec.add(", I am pleased to extend this offer of employment for the position of ")
    ec.add("Senior Data Engineer. We were impressed by your background and believe ")
    ec.add("you will make a valuable contribution to our team.\n\n")

    ec.add("POSITION DETAILS:\n")
    ec.add("Title: Senior Data Engineer\n")
    ec.add("Department: Engineering\n")
    ec.add("Reports To: ")
    ec.add_entity(hiring_mgr, "PERSON")
    ec.add(", VP of Engineering\n")
    ec.add("Start Date: ")
    ec.add_entity(start_date, "DATE_TIME")
    ec.add("\n")
    ec.add(f"Annual Salary: ${salary:,}\n\n")

    ec.add("BENEFITS: You will be eligible for our comprehensive benefits package, ")
    ec.add("including health, dental, and vision insurance; 401(k) with company ")
    ec.add("match; 20 days of paid time off; and an annual professional development ")
    ec.add("stipend.\n\n")

    ec.add("This offer is contingent upon successful completion of a background ")
    ec.add("check and verification of your eligibility to work in the United States.\n\n")

    ec.add("Please confirm your acceptance by signing below and returning this letter ")
    ec.add("by email to ")
    ec.add_entity(email_hr, "EMAIL_ADDRESS")
    ec.add(" or by calling ")
    ec.add_entity(phone, "PHONE_NUMBER")
    ec.add(".\n\n")

    ec.add("We look forward to welcoming you to the team.\n\n")
    ec.add("Sincerely,\n\n")
    ec.add_entity(hiring_mgr, "PERSON")
    ec.add("\nVP of Engineering, ")
    ec.add_entity(company, "ORGANIZATION")
    ec.add("\n\nACCEPTANCE:\n\n_________________________\n")
    ec.add_entity(candidate, "PERSON")
    ec.add("\nDate: _________________\n")

    text, entities = ec.finalize()
    _write("hr/offer_letter.txt", text, entities, ec)


def gen_attorney_client_email() -> None:
    ec = EntityCollector()

    attorney = fake.name()
    client = fake.name()
    opposing = fake.name()
    firm = fake.company() + " & Associates"
    email_atty = fake.email()
    email_client = fake.email()
    phone = fake.phone_number()
    case_ref = f"Case No. {fake.random_int(min=2020, max=2026)}-CV-{fake.random_int(min=1000, max=9999)}"
    email_date = fake.date_between(start_date="-7d", end_date="today").strftime("%B %d, %Y")
    depo_date = fake.date_between(start_date="+7d", end_date="+30d").strftime("%B %d, %Y")
    hearing_date = fake.date_between(start_date="+30d", end_date="+90d").strftime("%B %d, %Y")

    ec.add("From: ")
    ec.add_entity(attorney, "PERSON")
    ec.add(" <")
    ec.add_entity(email_atty, "EMAIL_ADDRESS")
    ec.add(">\n")
    ec.add("To: ")
    ec.add_entity(client, "PERSON")
    ec.add(" <")
    ec.add_entity(email_client, "EMAIL_ADDRESS")
    ec.add(">\n")
    ec.add("Date: ")
    ec.add_entity(email_date, "DATE_TIME")
    ec.add("\n")
    ec.add(f"Subject: Update on {case_ref} - ")
    ec.add_entity(client, "PERSON")
    ec.add(" v. ")
    ec.add_entity(opposing, "PERSON")
    ec.add("\n\n")

    ec.add("PRIVILEGED AND CONFIDENTIAL\nATTORNEY-CLIENT COMMUNICATION\n\n")

    ec.add("Dear ")
    ec.add_entity(client, "PERSON")
    ec.add(",\n\n")

    ec.add("I am writing to update you on the status of your case. Following our ")
    ec.add("conversation last week, I have taken the following steps:\n\n")

    ec.add("1. DISCOVERY: We have served interrogatories and document requests on ")
    ec.add_entity(opposing, "PERSON")
    ec.add("'s counsel. Responses are due within thirty days. We have also ")
    ec.add("scheduled a deposition of ")
    ec.add_entity(opposing, "PERSON")
    ec.add(" for ")
    ec.add_entity(depo_date, "DATE_TIME")
    ec.add(".\n\n")

    ec.add("2. MOTION PRACTICE: The court has set a hearing on our motion to compel ")
    ec.add("for ")
    ec.add_entity(hearing_date, "DATE_TIME")
    ec.add(". I am optimistic about our position given the defendant's failure to ")
    ec.add("produce the requested financial records.\n\n")

    ec.add("3. SETTLEMENT: Opposing counsel has indicated that their client may be ")
    ec.add("open to mediation. I recommend we explore this option, as it could ")
    ec.add("resolve the matter more efficiently and with less expense.\n\n")

    ec.add("Please review the enclosed documents and let me know if you have any ")
    ec.add("questions. You can reach me at ")
    ec.add_entity(phone, "PHONE_NUMBER")
    ec.add(" or ")
    ec.add_entity(email_atty, "EMAIL_ADDRESS")
    ec.add(".\n\n")

    ec.add("Best regards,\n\n")
    ec.add_entity(attorney, "PERSON")
    ec.add("\n")
    ec.add_entity(firm, "ORGANIZATION")
    ec.add("\n")

    text, entities = ec.finalize()
    _write("correspondence/attorney_client_email.txt", text, entities, ec)


def gen_internal_memo() -> None:
    ec = EntityCollector()

    sender = fake.name()
    recipient1 = fake.name()
    recipient2 = fake.name()
    recipient3 = fake.name()
    company = fake.company()
    memo_date = fake.date_between(start_date="-14d", end_date="today").strftime("%B %d, %Y")
    meeting_date = fake.date_between(start_date="+3d", end_date="+14d").strftime("%B %d, %Y")
    ext1 = str(fake.random_int(min=2000, max=9999))
    ext2 = str(fake.random_int(min=2000, max=9999))

    ec.add_entity(company, "ORGANIZATION")
    ec.add("\nINTERNAL MEMORANDUM\n")
    ec.add("CONFIDENTIAL\n\n")

    ec.add("TO: ")
    ec.add_entity(recipient1, "PERSON")
    ec.add(", Director of Operations\n    ")
    ec.add_entity(recipient2, "PERSON")
    ec.add(", Head of Product Development\n    ")
    ec.add_entity(recipient3, "PERSON")
    ec.add(", Chief Financial Officer\n")
    ec.add("FROM: ")
    ec.add_entity(sender, "PERSON")
    ec.add(", Chief Executive Officer\n")
    ec.add("DATE: ")
    ec.add_entity(memo_date, "DATE_TIME")
    ec.add("\n")
    ec.add("RE: Q2 Strategic Planning and Budget Allocation\n\n")

    ec.add("Team,\n\n")

    ec.add("As we approach the second quarter, I want to outline our strategic ")
    ec.add("priorities and discuss the preliminary budget allocations for each ")
    ec.add("department.\n\n")

    ec.add("PRIORITIES:\n")
    ec.add("1. The Operations team, led by ")
    ec.add_entity(recipient1, "PERSON")
    ec.add(", will focus on streamlining our supply chain processes and reducing ")
    ec.add("overhead costs by 15%. Please coordinate with the logistics division ")
    ec.add("to identify areas for improvement.\n\n")

    ec.add("2. Product Development, under ")
    ec.add_entity(recipient2, "PERSON")
    ec.add("'s leadership, should accelerate the timeline for the v3.0 platform ")
    ec.add("release. I am requesting a revised project plan by end of week.\n\n")

    ec.add("3. ")
    ec.add_entity(recipient3, "PERSON")
    ec.add(" will prepare a detailed financial forecast incorporating the revised ")
    ec.add("revenue projections we discussed in Monday's executive session.\n\n")

    ec.add("I have scheduled a follow-up meeting for ")
    ec.add_entity(meeting_date, "DATE_TIME")
    ec.add(" to review progress. ")
    ec.add_entity(recipient1, "PERSON")
    ec.add(", please reserve Conference Room A.\n\n")

    ec.add("If you have questions before then, reach me at ext. ")
    ec.add(f"{ext1}")
    ec.add(" or contact my assistant at ext. ")
    ec.add(f"{ext2}")
    ec.add(".\n\n")

    ec.add("Regards,\n\n")
    ec.add_entity(sender, "PERSON")
    ec.add("\nChief Executive Officer\n")
    ec.add_entity(company, "ORGANIZATION")
    ec.add("\n")

    text, entities = ec.finalize()
    _write("correspondence/internal_memo.txt", text, entities, ec)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger.info("Generating synthetic test fixtures (seed=42)...")

    gen_nda_contract()
    gen_engagement_letter()
    gen_bank_statement()
    gen_invoice()
    gen_discharge_summary()
    gen_referral_letter()
    gen_resume()
    gen_offer_letter()
    gen_attorney_client_email()
    gen_internal_memo()

    logger.info("Done. 10 documents + 10 annotation files generated.")


if __name__ == "__main__":
    main()
