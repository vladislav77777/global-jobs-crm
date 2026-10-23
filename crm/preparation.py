"""Prepare local application drafts. No employer write or submission capability."""
import argparse
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path

from crm.collector import identity
from crm.schema import snapshot as legacy_snapshot, properties as ui_properties, is_english, OPTIONS


def cv_version(raw):
    return hashlib.sha256(raw).hexdigest()


def experience_years(start, as_of):
    """Conservative completed years; CV dates contain months, not exact days."""
    year, month = map(int, start.split("-"))
    months = (as_of.year - year) * 12 + as_of.month - month
    return max(0, months // 12)


def validate_bank(bank, raw_cv, confirmed_answers=None):
    bank = [dict(a, state=ui_properties({'Evidence status': a['state']}, english=False)['Состояние']) for a in bank]
    version = cv_version(raw_cv)
    cv = " ".join(raw_cv.decode("utf-8-sig").split())
    keys = set()
    for answer in bank:
        if answer["key"] in keys:
            raise ValueError("Duplicate answer key: " + answer["key"])
        keys.add(answer["key"])
        if answer["cv_sha256"] != version:
            raise ValueError("Answer bank belongs to a different CV version")
        if answer.get("auto_reuse"):
            if not answer.get("answer") or not answer.get("evidence"):
                raise ValueError("Reusable answers require a CV-backed answer and evidence")
            if answer["state"] == "Со слов кандидата":
                confirmation = (confirmed_answers or {}).get(answer["key"], {})
                if confirmation.get("answer") != answer["answer"] or confirmation.get("evidence") != answer["evidence"]:
                    raise ValueError("Candidate answer differs from recorded confirmation: " + answer["key"])
            elif answer["state"] == "Из CV":
                for quote in answer["evidence"]:
                    if " ".join(quote.split()) not in cv:
                        raise ValueError("Evidence is absent from current CV: " + answer["key"])
            else:
                raise ValueError("Unconfirmed answer cannot be reused")
    return {a["key"]: a for a in bank}


def bank_from_notion(snapshot):
    snapshot = legacy_snapshot(snapshot)
    if snapshot.get("has_more") is not False:
        raise ValueError("A complete answer-bank snapshot is required")
    def plain(value):
        value = html.unescape(re.sub(r"<br\s*/?>", "\n", value or ""))
        return re.sub(r"\\([\\*~`$\[\]<>{}|^_])", r"\1", value)

    return [{"key": plain(r["Ключ"]), "question": plain(r["Вопрос"]), "category": r["Категория"],
             "answer": plain(r.get("Ответ EN")), "state": r["Состояние"],
             "evidence": plain(r.get("Основание")).split("\n\n") if r["Состояние"] in {"Из CV", "Со слов кандидата"} else [],
             "auto_reuse": r["Состояние"] in {"Из CV", "Со слов кандидата"} and bool(r.get("Ответ EN")), "cv_sha256": r["Версия CV"]}
            for r in snapshot["results"]]


def plan_pack_links(packs, snapshot):
    """Reuse the draft already attached to a job; never create a second one on replay."""
    english = is_english(snapshot)
    snapshot = legacy_snapshot(snapshot)
    if snapshot.get("has_more") is not False:
        raise ValueError("A complete jobs snapshot is required")
    index = {}
    for row in snapshot["results"]:
        key = identity(row.get("Ссылка для отклика") or row.get("Вакансия компании") or "")
        if key in index:
            raise ValueError("Duplicate job identity in Notion")
        if key:
            index[key] = row
    result = []
    for pack in packs:
        row = index.get(pack["job_key"])
        if not row or row["url"] != pack["notion_job_url"]:
            raise ValueError("Draft does not match its current Notion job")
        prop = {"Подготовка": pack["status"]}
        if not row.get('Cover letter'):
            prop['Cover letter'] = pack['letter']
        resource = row.get('Пакет заявки') or pack.get('notion_preparation_url')
        if not row.get('Пакет заявки') and resource:
            prop['Пакет заявки'] = resource
        result.append({"job_url": row["url"], "pack_id": pack["pack_id"],
                       "action": "update_job", "page_url": row['url'],
                       "preparation_url": resource,
                       "properties": prop})
    if english:
        for item in result:
            item['properties'] = ui_properties(item['properties'])
    return result


def prepare(profile, bank, draft, raw_cv, as_of):
    version = cv_version(raw_cv)
    if profile["cv_sha256"] != version or draft["cv_sha256"] != version:
        raise ValueError("CV changed: rebuild profile and draft before reuse")
    answers = validate_bank(bank, raw_cv, profile.get("confirmed_answers"))
    if not identity(draft["job_url"]):
        raise ValueError("A valid job URL is required")
    letter = draft["letter"].strip()
    if not 3 <= len(re.findall(r"[.!?](?:\s|$)", letter)) <= 5 or len(letter.split()) > 100:
        raise ValueError("Use a short 3–5 sentence cover letter (at most 100 words)")
    if not draft.get("letter_evidence"):
        raise ValueError("Cover-letter factual claims require answer-bank references")
    for key in draft["letter_evidence"]:
        if key not in answers or not answers[key].get("auto_reuse"):
            raise ValueError("Cover letter references an unconfirmed fact: " + key)

    prepared = []
    form = draft.get("form")
    for field in (form or {}).get("fields", []):
        value, evidence = None, None
        binding = field.get("binding")
        status = "Нужно уточнить" if field.get("required") else "Оставить пустым"
        # No reliance on ATS default values, no implicit matching by label.
        if profile.get("policy", {}).get("skip_optional") and not field.get("required") and binding != "cover_letter":
            status = "Пропустить по настройке"
        elif field.get("kind") in {"consent", "demographic", "captcha"}:
            status = "Ручное действие" if field.get("required") else "Оставить пустым"
        elif binding == "cover_letter":
            if field.get("kind") == "file":
                letter_path = draft.get("letter_file")
                if letter_path and Path(letter_path).read_text(encoding="utf-8").strip() == letter:
                    value, evidence = letter_path, "Local cover-letter text attachment"
            else:
                value, evidence = letter, ", ".join(draft["letter_evidence"])
        elif binding == "resume_file":
            attachment = profile.get("resume_file")
            if attachment and Path(attachment["path"]).is_file():
                data = Path(attachment["path"]).read_bytes()
                if attachment["cv_sha256"] != version or cv_version(data) != attachment["file_sha256"]:
                    raise ValueError("Stale or altered CV attachment")
                value, evidence = attachment["path"], "CV export; local file only"
        elif binding == "java_years":
            value = experience_years(profile["java_start"], as_of)
            evidence = "CV: employment dates; completed years as of " + as_of.isoformat()
        elif binding and binding.startswith("answer:"):
            answer = answers.get(binding[7:])
            if answer and answer.get("auto_reuse"):
                value, evidence = answer["answer"], answer["key"]
        elif binding and binding.startswith("profile:"):
            fact = profile["fields"].get(binding[8:])
            if fact and fact.get("confirmed") and fact.get("evidence"):
                value, evidence = fact["value"], fact["evidence"]
        elif binding == "source" and draft.get("source_answer") in field.get("options", []):
            value, evidence = draft["source_answer"], "Saved source publication"
        if value is not None:
            if field.get("options") and value not in field["options"]:
                value, evidence, status = None, None, "Нет подходящего варианта"
            else:
                status = "Подготовлено локально"
        if field.get('kind') == 'file' and value:
            value = str(Path(value).resolve())
        prepared.append({**field, "value": value, "evidence": evidence, "status": status,
                         **({'file_sha256': cv_version(Path(value).read_bytes())} if field.get('kind') == 'file' and value else {})})

    blockers = [f["label"] for f in prepared if f.get("required") and f["value"] is None]
    blockers.extend((form or {}).get("blockers", []))
    if form is None:
        blockers.append("Форма ещё не проверена")
    profile_fingerprint = cv_version(json.dumps(profile, ensure_ascii=False, sort_keys=True).encode())
    result = {
        "job_key": identity(draft["job_url"]), "company": draft["company"], "role": draft["role"],
        "job_url": draft["job_url"], "notion_job_url": draft["notion_job_url"],
        "notion_preparation_url": draft.get("notion_preparation_url"),
        "cv_sha256": version, "profile_sha256": profile_fingerprint, "prepared_at": as_of.isoformat(), "letter": letter,
        "letter_evidence": draft["letter_evidence"], "form": form, "fields": prepared,
        "blockers": blockers, "submission_enabled": False, "employer_data_transmitted": False,
        "status": "Нужны данные" if form else "Письмо готово",
    }
    if form and not blockers:
        result["status"] = "Черновик готов"
    result["pack_id"] = hashlib.sha256((result["job_key"] + ":" + version).encode()).hexdigest()[:20]
    return result


def render_preview(packs):
    display = dict(OPTIONS, **{"Оставить пустым":"Leave blank", "Пропустить по настройке":"Skipped by policy", "Ручное действие":"Manual action", "Нет подходящего варианта":"No matching option", "Подготовлено локально":"Prepared locally", "нет подтверждённых данных":"No confirmed information", "Форма ещё не проверена":"Form not inspected yet"})
    esc = lambda v: html.escape(display.get(str(v), str(v)), quote=True)
    sections = []
    for pack in packs:
        fields = []
        for f in pack["fields"]:
            value = "" if f["value"] is None else str(f["value"])
            fields.append(f'<label>{esc(f["label"])} {"*" if f.get("required") else ""}'
                          f'<textarea readonly placeholder="{esc(f["status"])}">{esc(value)}</textarea>'
                          f'<small>{esc(f["status"])} · {esc(f.get("evidence") or "нет подтверждённых данных")}</small></label>')
        blockers = "".join(f"<li>{esc(b)}</li>" for b in pack["blockers"])
        sections.append(f'<section><h2>{esc(pack["company"])}</h2><p>{esc(pack["role"])}</p>'
                        f'<p class="letter">{esc(pack["letter"])}</p>'
                        f'<p><b>{esc(pack["status"])}</b> · CV {esc(pack["cv_sha256"][:12])}</p>'
                        f'<div class="fields">{"".join(fields)}</div><ul>{blockers}</ul>'
                        '<button type="button" disabled>Sending is off</button></section>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'connect-src \'none\'; form-action \'none\'; base-uri \'none\'">'
            '<title>Application drafts · Global Jobs</title><style>'
            'body{font:16px/1.5 system-ui;background:#f4f6f7;color:#172b35;margin:0;padding:32px;max-width:1080px;margin:auto}'
            'h1{font-size:30px}h2{margin-bottom:0}section{background:white;border:1px solid #dce3e7;border-radius:14px;padding:26px;margin:22px 0}'
            '.notice{border-left:4px solid #287664;padding:14px;background:#e6f1ed}.letter{font-size:17px;max-width:800px}'
            '.fields{display:grid;grid-template-columns:1fr 1fr;gap:18px}label{font-weight:600}textarea{display:block;box-sizing:border-box;'
            'width:100%;min-height:82px;border:1px solid #bacbd1;border-radius:6px;padding:10px;resize:vertical;font:14px/1.4 system-ui}'
            'small{font-weight:400;font-size:12px;color:#566c77;overflow-wrap:anywhere}li{color:#8a4f16}'
            'button{padding:12px 18px;border:0;border-radius:7px} @media(max-width:700px){.fields{grid-template-columns:1fr}body{padding:12px}}'
            '</style></head><body><h1>Application drafts</h1>'
            '<p class="notice">Local previews. No data is sent to employers. Unknown answers remain blank. '
            'Recheck the form and current CV before live filling.</p>' + "".join(sections) + '</body></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "bank", "drafts", "cv", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--jobs", help="Fresh complete Notion rows; plan links without executing writes")
    args = parser.parse_args()
    read = lambda name: json.loads(Path(getattr(args, name)).read_text(encoding="utf-8-sig"))
    profile, bank, drafts = read("profile"), read("bank"), read("drafts")
    if isinstance(bank, dict):
        bank = bank_from_notion(bank)
    raw_cv = Path(args.cv).read_bytes()
    packs = [prepare(profile, bank, draft, raw_cv, date.fromisoformat(args.today)) for draft in drafts]
    keys = [p["job_key"] for p in packs]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate job packs in batch")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "letters").mkdir(exist_ok=True)
    for draft, pack in zip(drafts, packs):
        path = (out / "letters" / (pack["pack_id"] + ".txt")).resolve()
        path.write_text(pack["letter"] + "\n", encoding="utf-8")
        draft["letter_file"] = str(path)
    packs = [prepare(profile, bank, draft, raw_cv, date.fromisoformat(args.today)) for draft in drafts]
    (out / "packs.json").write_text(json.dumps(packs, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "preview.html").write_text(render_preview(packs), encoding="utf-8")
    if args.jobs:
        plan = plan_pack_links(packs, read("jobs"))
        (out / "notion-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"packs": len(packs), "forms": sum(p["form"] is not None for p in packs), "submission_enabled": False}))


if __name__ == "__main__":
    main()
