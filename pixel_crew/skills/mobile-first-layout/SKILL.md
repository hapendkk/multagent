---
name: mobile-first-layout
description: Раскладка mobile-first без горизонтального скролла на 375 px
agents: [ui_generator]
product_types: [any]
always: true
triggers: [горизонтальн, скролл, overflow, шире экрана, вылез]
---
# Навык: mobile-first без переполнения
1. Базовые стили — для 375 px, широкие экраны через `@media (min-width: 768px)`.
2. Никаких фиксированных `width` в px больше 340; используй `width: 100%`, `max-width`, `minmax(0, 1fr)`.
3. Во flex/grid детям ставь `min-width: 0`; строки кнопок — `flex-wrap: wrap`.
4. Медиа и SVG: `max-width: 100%; height: auto;`. Длинные слова и ссылки: `overflow-wrap: anywhere;`.
5. Отрицательные margin и `100vw` не используй (дают скролл из-за полосы прокрутки).
6. Таблицы и широкие блоки оборачивай в контейнер с `overflow-x: auto`.
Проверка в голове: сумма ширин + padding + gap в строке ≤ 343 px (375 − 2×16).
