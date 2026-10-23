import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path

from crm.preparation import bank_from_notion, cv_version, experience_years, plan_pack_links, prepare, render_preview


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.cv = b"Example Engineer. Java from 02/2021. Payment reconciliation."
        self.version = cv_version(self.cv)
        self.profile = {"cv_sha256": self.version, "java_start": "2021-02", "fields": {
            "name": {"value": "Example Engineer", "confirmed": True, "evidence": "CV heading"}}}
        self.bank = [{"key": "payments", "question": "Experience?", "category": "Опыт",
                      "answer": "I worked on payment reconciliation.", "evidence": ["Payment reconciliation."],
                      "state": "Из CV", "auto_reuse": True, "cv_sha256": self.version}]
        self.draft = {"company": "Example", "role": "Java Engineer", "cv_sha256": self.version,
                      "job_url": "https://jobs.lever.co/example/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                      "notion_job_url": "https://app.notion.com/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                      "letter": "I’m applying for this role. I have Java backend experience. I worked on payment reconciliation.",
                      "letter_evidence": ["payments"], "form": {"fields": []}}

    def pack(self):
        return prepare(self.profile, self.bank, self.draft, self.cv, date(2026, 10, 23))

    def test_english_answer_metadata_is_reusable_without_changing_cv_evidence(self):
        self.bank[0]['state'] = 'CV evidence'
        self.bank[0]['category'] = 'Experience'
        self.assertEqual(self.pack()['letter'], self.draft['letter'])

    def test_unknown_legal_fields_and_ats_default_are_never_answers(self):
        self.draft["form"]["fields"] = [
            {"label": "Country", "binding": "profile:country", "required": True, "default": "United States"},
            {"label": "Work rights", "binding": "profile:rights", "required": True, "options": ["Yes", "No"]},
            {"label": "Go years", "binding": "answer:go", "required": True}]
        pack = self.pack()
        self.assertTrue(all(f["value"] is None for f in pack["fields"]))
        self.assertEqual(len(pack["blockers"]), 3)
        self.assertEqual(pack["status"], "Нужны данные")

    def test_privacy_and_demographics_are_not_inferred_even_from_binding(self):
        self.draft["form"]["fields"] = [{"label": "Agree", "binding": "profile:name", "kind": "consent", "required": True},
                                         {"label": "Gender", "binding": "profile:name", "kind": "demographic"}]
        self.assertTrue(all(f["value"] is None for f in self.pack()["fields"]))

    def test_completed_years_do_not_round_up(self):
        self.assertEqual(experience_years("2021-02", date(2026, 10, 23)), 5)
        self.assertEqual(experience_years("2021-02", date(2026, 1, 31)), 4)

    def test_candidate_facts_require_matching_recorded_confirmation(self):
        self.bank[0].update(state="Со слов кандидата", answer="I can start immediately.", evidence=["Candidate: immediately"])
        with self.assertRaisesRegex(ValueError, "recorded confirmation"):
            self.pack()
        self.profile["confirmed_answers"] = {"payments": {"answer": self.bank[0]["answer"], "evidence": self.bank[0]["evidence"]}}
        self.pack()
        self.bank[0]["answer"] = "I have ten years of Go."
        with self.assertRaisesRegex(ValueError, "recorded confirmation"):
            self.pack()

    def test_optional_fields_are_skipped_except_cover_letter(self):
        self.profile["policy"] = {"skip_optional": True}
        self.draft["form"]["fields"] = [{"label": "Optional name", "binding": "profile:name", "required": False},
            {"label": "Required name", "binding": "profile:name", "required": True},
            {"label": "Letter", "binding": "cover_letter", "required": False}]
        fields = self.pack()["fields"]
        self.assertIsNone(fields[0]["value"])
        self.assertEqual(fields[1]["value"], "Example Engineer")
        self.assertEqual(fields[2]["value"], self.draft["letter"])

    def test_changed_cv_invalidates_bank_and_profile(self):
        with self.assertRaisesRegex(ValueError, "CV changed"):
            prepare(self.profile, self.bank, self.draft, self.cv + b" Updated.", date.today())
        self.bank[0]["cv_sha256"] = "old"
        with self.assertRaisesRegex(ValueError, "different CV"):
            self.pack()

    def test_factual_answer_requires_current_verbatim_evidence(self):
        self.bank[0]["evidence"] = ["Five years of Go."]
        with self.assertRaisesRegex(ValueError, "Evidence is absent"):
            self.pack()

    def test_unconfirmed_answer_cannot_be_promoted_by_letter(self):
        self.bank[0].update(state="Нужно уточнить", auto_reuse=False)
        with self.assertRaisesRegex(ValueError, "unconfirmed fact"):
            self.pack()

    def test_altered_attachment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.pdf"
            path.write_bytes(b"original")
            self.profile["resume_file"] = {"path": str(path), "cv_sha256": self.version,
                                           "file_sha256": cv_version(b"original")}
            self.draft["form"]["fields"] = [{"label": "Resume", "binding": "resume_file", "required": True}]
            self.assertEqual(self.pack()["fields"][0]["value"], str(path))
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "altered CV"):
                self.pack()

    def test_unmatched_select_option_is_not_substituted(self):
        self.draft["form"]["fields"] = [{"label": "Choice", "binding": "profile:name", "required": True, "options": ["Other"]}]
        self.assertIsNone(self.pack()["fields"][0]["value"])
        self.assertIn("Choice", self.pack()["blockers"])

    def test_preview_has_no_submission_or_script_and_escapes_user_content(self):
        pack = self.pack()
        pack["company"] = '</textarea><script>fetch("https://example.org")</script>'
        rendered = render_preview([pack])
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<form", rendered)
        self.assertIn("form-action 'none'", rendered)
        self.assertIn("connect-src 'none'", rendered)
        self.assertIn('type="button" disabled', rendered)
        self.assertFalse(pack["submission_enabled"])

    def test_preparation_updates_same_job_and_preserves_manual_letter_and_state(self):
        pack = self.pack()
        snapshot = {"has_more": False, "results": [{"url": self.draft["notion_job_url"],
            "Ссылка для отклика": self.draft["job_url"] + "/apply", "Этап": "Интервью", "Ответ компании": "Keep me"}]}
        before = copy.deepcopy(snapshot)
        first = plan_pack_links([pack], snapshot)[0]
        self.assertEqual(first["action"], "update_job")
        self.assertEqual(first['page_url'], self.draft['notion_job_url'])
        self.assertEqual(first['properties']['Cover letter'], pack['letter'])
        self.assertEqual(snapshot, before)
        snapshot["results"][0]['Cover letter'] = 'My manual letter'
        replay = plan_pack_links([pack], snapshot)[0]
        self.assertEqual(replay["action"], "update_job")
        self.assertNotIn('Cover letter', replay['properties'])
        self.assertNotIn('Этап', replay['properties'])

    def test_preparation_link_is_backfilled_once_and_existing_draft_is_preserved(self):
        self.draft['notion_preparation_url'] = 'https://app.notion.com/p/11111111222243338444555555555555'
        pack = self.pack()
        snapshot = {'has_more':False,'results':[{'url':self.draft['notion_job_url'],
                    'Apply URL':self.draft['job_url'],'Stage':'Applied'}]}
        plan = plan_pack_links([pack],snapshot)[0]
        self.assertEqual(plan['properties']['Preparation resources'],self.draft['notion_preparation_url'])
        snapshot['results'][0]['Application draft'] = self.draft['notion_preparation_url']
        pack['notion_preparation_url'] = 'https://app.notion.com/p/22222222333344448555666666666666'
        replay = plan_pack_links([pack],snapshot)[0]
        self.assertEqual(replay['preparation_url'],self.draft['notion_preparation_url'])
        self.assertNotIn('Preparation resources',replay['properties'])

    def test_incomplete_notion_snapshots_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            plan_pack_links([self.pack()], {"has_more": True, "results": []})
        with self.assertRaisesRegex(ValueError, "complete"):
            bank_from_notion({"has_more": True, "results": []})

    def test_notion_rich_text_escaping_round_trips_evidence_and_answers(self):
        rows = {"has_more": False, "results": [{"Ключ": "x", "Вопрос": "Question", "Категория": "Кейс",
            "Ответ EN": r"Volume was \$12M.", "Состояние": "Из CV", "Основание": r"Role \| dates<br /><br />\$12M",
            "Версия CV": self.version}]}
        answer = bank_from_notion(rows)[0]
        self.assertEqual(answer["answer"], "Volume was $12M.")
        self.assertEqual(answer["evidence"], ["Role | dates", "$12M"])

    def test_cover_letter_file_is_prepared_only_if_exact_local_text_matches(self):
        self.draft["form"]["fields"] = [{"label": "Letter", "binding": "cover_letter", "kind": "file", "required": True}]
        self.assertIsNone(self.pack()["fields"][0]["value"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "letter.txt"
            path.write_text(self.draft["letter"], encoding="utf-8")
            self.draft["letter_file"] = str(path)
            self.assertEqual(self.pack()["fields"][0]["value"], str(path))
            path.write_text("Wrong company", encoding="utf-8")
            self.assertIsNone(self.pack()["fields"][0]["value"])


if __name__ == "__main__":
    unittest.main()
