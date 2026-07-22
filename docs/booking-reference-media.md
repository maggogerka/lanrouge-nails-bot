# Референсные изображения записи v0.3.0

## Данные

`AppointmentReferenceMedia` хранит `appointment_id`, Telegram `file_id`, `file_unique_id`, тип, позицию, загрузившего пользователя, `created_at` и nullable `deleted_at`. Бинарные изображения, caption и полный клиентский комментарий в таблице не хранятся.

FK к Appointment и User используют `RESTRICT`. Позиция уникальна внутри Appointment. Активные изображения выдаются в стабильном порядке `position, id`.

## Черновик и транзакция

До подтверждения FSM Redis содержит не более `booking_reference_max_media` компактных элементов. Отмена очищает состояние. При подтверждении `BookingService` в одной UoW:

1. повторно валидирует клиента, услугу и окно;
2. создаёт Appointment;
3. создаёт reference rows с полученным appointment ID;
4. создаёт историю и notification jobs;
5. делает один commit.

При ошибке откатываются и Appointment, и reference rows.

## Telegram album

Одиночные фото добавляются сразу в черновик. Элементы одного `media_group_id` агрегируются с коротким неблокирующим debounce вне длительного handler sleep и сортируются по порядку Telegram updates. Завершение альбома не завершает FSM автоматически. Повторы по `file_unique_id` не увеличивают лимит.

## Владение и изменение

Клиент читает или редактирует референсы только своей записи и только до `start_at - booking_reference_edit_deadline_hours`. Администратор получает их через защищённый service use case. Callback ID не является подтверждением владения.

Soft delete сохраняет аудит и позиции. Референсы никогда не публикуются в Portfolio автоматически.

## Доставка мастеру

После commit handler отправляет альбом администраторам best-effort. Ошибка Telegram логируется безопасным кодом без полного file ID и не отменяет запись. Карточка Appointment остаётся повторно открываемым источником референсов.
