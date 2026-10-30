from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline_spec.models.enums import OperationType


@dataclass(frozen=True)
class ParameterRequirement:
    field_name: str
    description: str
    question: str
    default_suggestion: str
    validator: str | None = None  # Имя функции или типа проверки


@dataclass(frozen=True)
class OperationRule:
    operation_type: OperationType
    required_parameters: list[ParameterRequirement]
    general_question: str
    general_suggestion: str


OPERATION_RULES: dict[OperationType, OperationRule] = {
    OperationType.READ: OperationRule(
        operation_type=OperationType.READ,
        general_question="Укажите источник данных, формат хранения и путь/таблицу.",
        general_suggestion="Чтение из источника в формате Parquet или CSV",
        required_parameters=[
            ParameterRequirement(
                field_name="source_id",
                description="Идентификатор входного набора данных",
                question="Какой источник данных (таблица или путь к файлам) должен быть прочитан на этом этапе?",
                default_suggestion="raw_orders_source",
            ),
        ],
    ),
    OperationType.FILTER: OperationRule(
        operation_type=OperationType.FILTER,
        general_question="Укажите логическое условие для фильтрации строк.",
        general_suggestion="Исключить некорректные статусы или нулевые/отрицательные суммы",
        required_parameters=[
            ParameterRequirement(
                field_name="condition",
                description="Булево условие/предикат фильтрации",
                question="По какому конкретно условию необходимо отфильтровать строки (например: статус != 'CANCELLED' или amount > 0)?",
                default_suggestion="status != 'CANCELLED' AND amount > 0",
            ),
        ],
    ),
    OperationType.SELECT: OperationRule(
        operation_type=OperationType.SELECT,
        general_question="Укажите список колонок для проекции.",
        general_suggestion="Выбрать ключевые атрибуты и исключить служебные поля",
        required_parameters=[
            ParameterRequirement(
                field_name="columns",
                description="Список выбираемых колонок",
                question="Какие именно колонки необходимо оставить в результирующем наборе данных?",
                default_suggestion="Оставить основные бизнес-колонки (id, date, amount, status)",
            ),
        ],
    ),
    OperationType.WITH_COLUMNS: OperationRule(
        operation_type=OperationType.WITH_COLUMNS,
        general_question="Укажите формулы и правила вычисления новых колонок.",
        general_suggestion="Рассчитать итоговые суммы с учетом скидок/налогов",
        required_parameters=[
            ParameterRequirement(
                field_name="columns",
                description="Список вычисляемых колонок с формулами",
                question="Какова точная математическая или логическая формула для вычисления новой колонки (например: amount * (1 - discount))?",
                default_suggestion="net_amount = amount * (1 - discount_pct / 100)",
            ),
        ],
    ),
    OperationType.JOIN: OperationRule(
        operation_type=OperationType.JOIN,
        general_question="Укажите параметры объединения: второй датасет, ключи соединения и тип Join.",
        general_suggestion="Left join по первичному ключу",
        required_parameters=[
            ParameterRequirement(
                field_name="right_input",
                description="Второй датасет для соединения",
                question="С каким вторым набором данных необходимо выполнить соединение?",
                default_suggestion="Справочник клиентов или транзакций",
            ),
            ParameterRequirement(
                field_name="on",
                description="Ключи соединения колонок или булево условие",
                question="По какому общему ключу (колонкам) соединять таблицы (например: customer_id или order_id)?",
                default_suggestion="['customer_id']",
            ),
            ParameterRequirement(
                field_name="join_type",
                description="Тип соединения и правило обработки отсутствующих строк",
                question="Какой тип соединения применить и как обрабатывать строки без пары (left — сохранить все левые, inner — отбросить)?",
                default_suggestion="left (сохранить все записи левой таблицы)",
            ),
        ],
    ),
    OperationType.AGGREGATE: OperationRule(
        operation_type=OperationType.AGGREGATE,
        general_question="Укажите поля группировки и функции агрегации.",
        general_suggestion="Группировка по ключевым разрезам с подсчетом количества и суммы",
        required_parameters=[
            ParameterRequirement(
                field_name="group_by",
                description="Поля группировки данных",
                question="По каким разрезам/полям необходимо сгруппировать данные (например: город, дата, категория)?",
                default_suggestion="['city', 'order_date']",
            ),
            ParameterRequirement(
                field_name="aggregations",
                description="Список агрегатных функций (count, sum, avg и т.д.)",
                question="Какие метрики необходимо посчитать (например: count(order_id) для количества, sum(amount) для выручки)?",
                default_suggestion="total_count = count(*), total_amount = sum(amount)",
            ),
        ],
    ),
    OperationType.DEDUPLICATE: OperationRule(
        operation_type=OperationType.DEDUPLICATE,
        general_question="Укажите критерии дедупликации.",
        general_suggestion="Удаление дубликатов по первичному ключу",
        required_parameters=[
            ParameterRequirement(
                field_name="subset",
                description="Колонки для определения дубликатов (или null для всех колонок)",
                question="По какому подмножеству колонок выявлять дубликаты (или проверять совпадение по всей строке)?",
                default_suggestion="По первичному ключу записи",
            ),
            ParameterRequirement(
                field_name="keep",
                description="Какую из дублирующихся записей оставлять ('first' или 'last')",
                question="Какую версию строки сохранять при обнаружении дублей: первую ('first') или последнюю ('last')?",
                default_suggestion="first (первую встреченную строку)",
            ),
        ],
    ),
    OperationType.SORT: OperationRule(
        operation_type=OperationType.SORT,
        general_question="Укажите колонки и направление сортировки.",
        general_suggestion="Сортировка по дате или сумме по убыванию",
        required_parameters=[
            ParameterRequirement(
                field_name="order_by",
                description="Список полей сортировки с направлением (asc/desc)",
                question="По каким колонкам и в каком порядке (по возрастанию или по убыванию) отсортировать результат?",
                default_suggestion="Сортировка по убыванию даты ('desc')",
            ),
        ],
    ),
    OperationType.UNION: OperationRule(
        operation_type=OperationType.UNION,
        general_question="Укажите датасеты для объединения и режим уникальности.",
        general_suggestion="Объединение строк из нескольких источников",
        required_parameters=[
            ParameterRequirement(
                field_name="other_inputs",
                description="Наборы данных для вертикального объединения",
                question="С какими еще датасетами объединять строки текущей таблицы?",
                default_suggestion="Объединить с архивными данными",
            ),
            ParameterRequirement(
                field_name="distinct",
                description="Флаг удаления дубликатов (union distinct vs union all)",
                question="Нужно ли удалять дубликаты после объединения строк (union all или union distinct)?",
                default_suggestion="false (сохранить все строки без дополнительной дедупликации, union all)",
            ),
        ],
    ),
    OperationType.WRITE: OperationRule(
        operation_type=OperationType.WRITE,
        general_question="Укажите целевое хранилище для сохранения данных.",
        general_suggestion="Сохранение в витрину данных в формате Parquet/Delta",
        required_parameters=[
            ParameterRequirement(
                field_name="sink_id",
                description="Идентификатор целевого синка",
                question="В какую целевую таблицу или по какому пути сохранить итоговый результат?",
                default_suggestion="daily_mart_sink",
            ),
        ],
    ),
}
