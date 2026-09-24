"""Static Privacy Policy / Terms of Service pages.

Required by Google Play (Data Safety section + in-app link) for any app that
collects personal data. Served straight from the backend so both apps can
link to a stable URL without needing their own web hosting.

NOTE: the copy below is a functional placeholder covering what the apps
actually collect and do, written to satisfy the Play Store requirement that
the page exists and is accurate. It is not a substitute for a lawyer's
review before a real public launch.
"""

from __future__ import annotations

from django.http import HttpResponse, HttpResponseRedirect

# The maintained, up-to-date privacy policy text lives in this Google Doc
# (must stay shared as "Anyone with the link -> Viewer", otherwise Google
# Play review and real users hit a login wall instead of the policy text).
PRIVACY_POLICY_DOC_URL = (
    "https://docs.google.com/document/d/1fwxNry-tCW80S7M_WenaMlAyI_0xpd4Av9wHaLVSe0U/view"
)

_STYLE = """
<style>
  body { font-family: -apple-system, Roboto, Arial, sans-serif; max-width: 720px;
         margin: 0 auto; padding: 24px 20px 64px; line-height: 1.6; color: #0B1220; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  h2 { font-size: 18px; margin-top: 32px; }
  .updated { color: #6B7280; font-size: 14px; margin-bottom: 24px; }
  ul { padding-left: 20px; }
  li { margin-bottom: 6px; }
</style>
"""

_TERMS_HTML = f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Пользовательское соглашение — Opus Go / Opus Master</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
{_STYLE}</head>
<body>
<h1>Пользовательское соглашение</h1>
<p class="updated">Действует для приложений Opus Go (клиент) и Opus Master (мастер).</p>

<h2>1. Общие положения</h2>
<p>Opus Go и Opus Master — это платформа, соединяющая клиентов, которым нужны бытовые услуги (электрика, сантехника,
уборка и т.д.), с независимыми мастерами, предоставляющими эти услуги. Платформа не является работодателем мастеров
и не несёт ответственности за качество выполненных работ — мастера действуют как независимые исполнители.</p>

<h2>2. Регистрация</h2>
<p>Для использования приложения необходимо подтвердить номер телефона по SMS-коду. Мастера дополнительно проходят
верификацию личности и модерацию перед допуском к заказам.</p>

<h2>3. Оплата</h2>
<p>Оплата за выполненную работу производится клиентом мастеру напрямую — наличными или банковским переводом (P2P) по договорённости
сторон, после подтверждения выполнения заказа. Платформа не является стороной этого расчёта, не проводит и не контролирует
эти платежи и не удерживает комиссию с заказа.</p>

<h2>4. Ответственность</h2>
<p>Платформа прикладывает разумные усилия для проверки мастеров (модерация, верификация личности), но не гарантирует
качество, сроки или результат выполненных работ. Споры между клиентом и мастером решаются через поддержку приложения.</p>

<h2>5. Удаление аккаунта</h2>
<p>Вы можете в любой момент удалить свой аккаунт в приложении (Профиль → Удалить аккаунт). Подробности — в
<a href="/legal/privacy/">Политике конфиденциальности</a>.</p>

<h2>6. Контакты</h2>
<p>По всем вопросам: <b>conornapoli@gmail.com</b></p>
</body>
</html>
"""


def privacy_policy(request):
    return HttpResponseRedirect(PRIVACY_POLICY_DOC_URL)


def terms_of_service(request):
    return HttpResponse(_TERMS_HTML, content_type="text/html; charset=utf-8")
