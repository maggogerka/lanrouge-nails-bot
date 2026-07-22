# Портфолио

## Возможности

Администратор открывает `/admin` → «🖼 Портфолио», создаёт draft, задаёт название и
описание, добавляет до 8 фотографий Telegram, теги и порядок, затем публикует работу.
Опубликованная работа доступна клиентам в «🖼 Работы мастера» и через deep link
`/start portfolio_<id>`.

Клиент может выбрать опубликованный дизайн как референс и перейти в обычный flow записи.
В `Appointment.design_reference_id` сохраняется ссылка; название отображается в
подтверждении. Сервис повторно проверяет статус portfolio item, поэтому draft и archive
нельзя привязать обходным callback.

## Жизненный цикл и данные

`draft → published → archived`. Опубликованные и исторически связанные работы физически
не удаляются. Хранятся Telegram `file_id`/`file_unique_id`, а не бинарные изображения.
Сортировка стабильна: `sort_order`, дата публикации, `id`.

Основные модули: `app/handlers/admin/portfolio.py`,
`app/handlers/client/portfolio.py`, `app/services/portfolio_service.py`,
`app/repositories/portfolio_repository.py`.
