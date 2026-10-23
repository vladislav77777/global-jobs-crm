import tempfile
import unittest
from datetime import date
from pathlib import Path
from crm.collector import RunState, build_plan, canonical_url, identity

TODAY = date(2026, 10, 23)
URL = "https://jobs.lever.co/example/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def candidate(source="HiringCafe", url=URL, posted="2026-10-20"):
    return {"reviewed": True, "direct_verified": True, "employer_open_verified": True, "posted": posted, "sources": [], "properties": {"Вакансия": "Senior Java Engineer", "Компания": "Example", "Источник": source, "Вакансия компании": url, "Ссылка для отклика": url + "/apply", "Исходная публикация": "https://hiringcafe.com/job/test-id", "Проверка ссылки": "Форма доступна"}}


def registry(value="Включён"):
    return {"has_more": False, "results": [{"Name": "HiringCafe", "Сбор": value}, {"Name": "LinkedIn · Jobs & Posts", "Сбор": "Включён"}]}


class CollectionTests(unittest.TestCase):
    def test_tracking_removed_but_job_query_kept(self):
        self.assertEqual(canonical_url("[job](https://example.com/a?gh_jid=7&utm_source=tg&lang=en#apply)"), "https://example.com/a?gh_jid=7&lang=en")

    def test_same_requisition_different_application_routes(self):
        self.assertEqual(identity(URL), identity(URL + "/apply?utm_source=linkedin"))
        self.assertEqual(identity("https://careers.adyen.com/de_DE/vacancies/7369512-java-software-engineer-payments"), identity("https://job-boards.greenhouse.io/adyen/jobs/7369512"))
        self.assertEqual(identity("https://careers.bol.com/en/jobs/old-slug/8788936002/"), identity("https://careers.bol.com/en/jobs/new-slug/8788936002/"))

    def test_cross_source_duplicates_in_one_batch(self):
        a, b = candidate(), candidate("LinkedIn Jobs")
        b["properties"]["Исходная публикация"] = "https://linkedin.com/jobs/view/123"
        plan = build_plan({"has_more": False, "results": []}, [a, b], registry(), TODAY)
        self.assertEqual(len(plan["create"]), 1)
        self.assertIn("linkedin.com", plan["create"][0]["properties"]["Все источники"])

    def test_revolut_slug_and_apply_link_share_one_requisition(self):
        base = 'https://www.revolut.com/careers/'
        key = '530d3bfd-bdca-4390-8f8a-bc88ddc1eb51'
        self.assertEqual(identity(base + 'position/software-engineer-java-' + key + '/'), identity(base + 'apply/' + key))
        self.assertEqual(identity(base + 'position/' + key), identity(base + 'apply/' + key))

    def test_restart_after_remote_write_does_not_create_again(self):
        c = candidate()
        first = build_plan({"has_more": False, "results": []}, [c], registry(), TODAY)
        saved = {**first["create"][0]["properties"], "url": "https://app.notion.com/p/saved", "Этап": "Интервью", "Ответ компании": "Interview tomorrow"}
        replay = build_plan({"has_more": False, "results": [saved]}, [c], registry(), TODAY)
        self.assertEqual(replay["create"], [])
        patch = replay["update"][0]["properties"]
        self.assertNotIn("Этап", patch)
        self.assertNotIn("Ответ компании", patch)

    def test_disabled_and_unknown_settings_fail_closed(self):
        for value in ("Выключен", "", "Yes"):
            plan = build_plan({"has_more": False, "results": []}, [candidate()], registry(value), TODAY)
            self.assertEqual(plan["create"], [])

    def test_older_website_listing_is_accepted_only_when_opening_is_verified(self):
        c = candidate(posted="2025-01-01")
        self.assertEqual(len(build_plan({"has_more": False, "results": []}, [c], registry(), TODAY)['create']), 1)
        c['employer_open_verified'] = False
        self.assertEqual(build_plan({"has_more": False, "results": []}, [c], registry(), TODAY)['skipped'][0]['reason'], 'employer_opening_unverified')

    def test_telegram_requires_known_date_and_has_a_hard_21_day_limit(self):
        sources = {'has_more': False, 'results': [{'Name': 'Telegram · Global Jobs', 'Сбор': 'Включён'}]}
        for posted, expected in [('2026-10-09', 1), ('2026-10-02', 1), ('2026-10-01', 0), (None, 0), ('2026-10-24', 0)]:
            with self.subTest(posted=posted):
                c = candidate(source='Telegram', posted=posted)
                self.assertEqual(len(build_plan({'has_more': False, 'results': []}, [c], sources, TODAY)['create']), expected)

    def test_incomplete_pagination_cannot_write(self):
        with self.assertRaises(ValueError):
            build_plan({"has_more": True, "results": []}, [candidate()], registry(), TODAY)

    def test_distinct_requisitions_not_merged_by_title(self):
        a, b = candidate(), candidate(url=URL.replace("aaaaaaaa", "ffffffff"))
        self.assertEqual(len(build_plan({"has_more": False, "results": []}, [a, b], registry(), TODAY)["create"]), 2)

    def test_shared_recruiter_contact_is_not_a_job_identity(self):
        self.assertEqual(identity("https://t.me/recruiter"), "")
        self.assertEqual(identity("mailto:jobs@example.com"), "")
        self.assertNotEqual(identity("https://t.me/channel/10"), identity("https://t.me/channel/11"))

    def test_lease_blocks_second_writer_and_partial_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.db"
            a, b = RunState(path), RunState(path)
            token = a.acquire()
            with self.assertRaises(RuntimeError):
                b.acquire()
            a.checkpoint(token, "HiringCafe", {"date": "2026-10-23"}, complete=False, writes_verified=True)
            self.assertIsNone(a.db.execute("SELECT cursor FROM checkpoint").fetchone())
            self.assertIsNotNone(a.db.execute("SELECT cursor FROM progress").fetchone())
            a.checkpoint(token, "HiringCafe", {"date": "2026-10-23"}, complete=True, writes_verified=True)
            self.assertIsNotNone(a.db.execute("SELECT cursor FROM checkpoint").fetchone())
            a.release(token)
            b.release(b.acquire())
            a.db.close(); b.db.close()

    def test_expired_lease_cannot_overwrite_new_owners_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.db"
            a, b = RunState(path), RunState(path)
            old = a.acquire(ttl=-1)
            new = b.acquire()
            with self.assertRaises(RuntimeError):
                a.checkpoint(old, "Telegram", {}, complete=True, writes_verified=True)
            a.release(old)
            b.checkpoint(new, "Telegram", {"message": 123}, complete=True, writes_verified=True)
            b.release(new)
            a.db.close(); b.db.close()


if __name__ == "__main__":
    unittest.main()
