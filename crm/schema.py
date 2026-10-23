"""English UI names with a lossless adapter for existing Russian workspaces."""
import copy

FIELDS = dict(zip(
    'Вакансия|Вакансия компании|Версия CV|Все источники|Дата вакансии работодателя|Дата ответа|Дата отклика|Дата поста источника|Дата следующего шага|Зарплата|Источник|Исходная публикация|Ключ дубликата|Комментарий к ответу|Компания|Контакт|Локация|Найдена|Нужно от меня|Обновлена|Опубликована|Ответ компании|Пакет заявки|Подготовка|Подтверждение отправки|Последний контакт|Последняя проверка|Почему подходит|Почта|Приоритет|Причина завершения|Причина отказа|Проверка ссылки|Результат ответа|Следующий шаг|Способ отклика|Ссылка для отклика|Ссылка на ответ|Стек|Страна|Уровень|Формат|Этап|Последний запуск|Проверка|Прогресс|Сбор|Вопрос|Категория|Ключ|Основание|Ответ EN|Состояние|Готовность|Действие|Лимит в день|Порядок|Режим'.split('|'),
    'Role|Employer job URL|CV version|All sources|Employer posting date|Response date|Applied date|Source posting date|Next action date|Salary|Source|Source URL|Deduplication key|Response notes|Company|Contact|Location|Found|Input needed|Updated|Published|Latest reply|Preparation resources|Preparation|Delivery receipt|Last contact|Last checked|Match notes|Email|Priority|Closure reason|Rejection reason|Link check|Response outcome|Next action|Application channel|Apply URL|Response URL|Stack|Country|Level|Work format|Stage|Last run|Run status|Progress|Collection|Question|Category|Key|Evidence|Answer|Evidence status|Connection status|Feature|Daily limit|Order|Enabled'.split('|')))
OPTIONS = dict(zip(
    'Не начата|Письмо готово|Нужны данные|Черновик готов|Высокий|Обычный|Низкий|Локация|Право на работу / виза|Опыт|Навыки / стек|Уровень|Зарплата|Язык|Вакансия закрыта|Другой кандидат|Другое|Не указана|Форма доступна|Описание доступно|Контакт из публикации|Недоступна|Не откликался|Ожидается ответ|Автоподтверждение|Вопросы|Приглашение|Отказ|Оффер|Форма компании|Сайт компании|Удалённо|Гибрид|Офис|Отправляется|Отклик отправлен|Переписка|Интервью|Нужно моё участие|Отправка не подтверждена|Новая|Готова к отклику|Подходит по CV|Закрыта|Пропущена|Успешно|Частично|Ошибка|Не проверен|Выключен|Включён|Профиль|Кейс|Условия|Из CV|Со слов кандидата|Не заявлять|Нужно уточнить|Не реализовано|Нет подключения|Не проверено|Готово|Выключено|Включено|Сайты компаний'.split('|'),
    'Not started|Letter ready|Input needed|Draft ready|High|Normal|Low|Location|Work authorization / visa|Experience|Skills / stack|Level|Salary|Language|Position closed|Another candidate|Other|Not specified|Form available|Description available|Contact in post|Unavailable|Not applied|Awaiting response|Acknowledgement|Questions|Interview invitation|Rejection|Offer|Employer form|Company website|Remote|Hybrid|Office|Submitting|Applied|Conversation|Interview|Needs attention|Delivery uncertain|New|CV match|CV match|Closed|Skipped|Success|Partial|Error|Not checked|Off|On|Profile|Example|Conditions|CV evidence|Candidate confirmed|Do not claim|Needs input|Not implemented|Not connected|Not verified|Connected|Off|On|Company websites'.split('|')))
# Distinguish a reviewed application from a technical CV match.
OPTIONS['Готова к отклику'] = 'Ready to apply'


def properties(values, english=True):
    values = dict(values)
    if 'Application draft' in values:
        values.setdefault('Preparation resources', values.pop('Application draft'))
    if 'Employer response' in values:
        values.setdefault('Latest reply', values.pop('Employer response'))
    fields = FIELDS if english else {v: k for k, v in FIELDS.items()}
    options = OPTIONS if english else {v: k for k, v in OPTIONS.items()}
    # Many old On/Off spellings share one English value; choose by property below.
    result = {}
    for key, value in values.items():
        original = key
        if key.startswith('date:'):
            _, name, suffix = key.split(':', 2)
            key = 'date:' + fields.get(name, name) + ':' + suffix
        else:
            key = fields.get(key, key)
        option_fields = {'Source', 'Stage', 'Preparation', 'Priority', 'Rejection reason', 'Link check',
                         'Response outcome', 'Application channel', 'Work format', 'Run status',
                         'Collection', 'Category', 'Evidence status', 'Connection status', 'Enabled', 'Name'}
        if (original if not english else key) in option_fields and isinstance(value, str):
            value = options.get(value, value)
        if not english and original == 'Collection':
            value = {'On': 'Включён', 'Off': 'Выключен'}.get(values[original], value)
        if not english and original == 'Enabled':
            value = {'On': 'Включено', 'Off': 'Выключено'}.get(values[original], value)
        result[key] = value
    return result


def snapshot(value):
    value = copy.deepcopy(value)
    value['results'] = [properties(r, english=False) for r in value['results']]
    return value


def is_english(value):
    return any('Stage' in r or 'Collection' in r for r in value['results'])
