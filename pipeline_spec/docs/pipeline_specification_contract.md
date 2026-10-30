# Спецификация JSON-контракта конвейера данных (Pipeline Specification Contract)

## 1. Введение и назначение

Данный документ определяет единый формат JSON-контракта спецификации конвейера данных (пайплайна), используемый всеми компонентами системы **data-pipeline-analyst**:

1. **Аналитик (AI Analyst / LLM Agent)**: извлекает из запроса пользователя на естественном языке структурированное описание пайплайна (входы, этапы, операции, зависимости, ограничения, ожидаемый результат, модель состояний).
2. **Формальный верификатор (Formal Check / TLA+ / TLC)**: транслирует верификационную модель пайплайна в спецификацию TLA+, осуществляет проверку ацикличности (IsDAG), отсутствия взаимных блокировок (deadlock-free), инвариантов безопасности и достижимости целевых состояний.
3. **Генератор кода (PySpark Code Generator)**: выполняет детерминированную кодогенерацию скрипта на PySpark, опираясь на топологический порядок графа зависимостей (DAG), объявленные источники, трансформации DataFrame API и целевые хранилища.

---

## 2. Данные, извлекаемые Аналитиком из запроса

При анализе естественного языка аналитик обязан извлечь следующие ключевые блоки данных:

| Категория | Извлекаемые данные | Роль в системе |
|---|---|---|
| **Метаданные** | Название, назначение, идентификатор, теги | Идентификация и версионирование пайплайна |
| **Входы (Sources / Inputs)** | Источники данных (файлы, таблицы), форматы (CSV, Parquet, JSON, Delta), схемы колонок и типы, пути/URI, параметры чтения (разделители, заголовки) | Генерация `spark.read.*` в PySpark |
| **Этапы и зависимости (Steps & DAG)** | Последовательность шагов, идентификаторы, явные зависимости (`depends_on`), топология графа | Построение графа вычислений, валидация отсутствия циклов, порядок выполнения |
| **Операции (Operations)** | Действия над данными на каждом шаге: фильтрация, проекция, вычисление колонок, соединение (Join), агрегация с группировкой, дедупликация, сортировка | Генерация цепочек вызовов Spark DataFrame API |
| **Ограничения (Constraints)** | Правила качества данных (not_null, unique, range, in_set, regex) и бизнес-инварианты | Валидация датасетов перед/после шагов |
| **Ожидаемый результат (Outputs / Sinks)** | Выходные таблицы/файлы, целевые пути, формат хранения, режим записи (`overwrite`, `append`), ключи партиционирования | Генерация `df.write.*` в PySpark |
| **Верификационная модель (Formal Model)** | Переменные состояния пайплайна, начальные значения, переходы с guard-условиями и эффектами, темпоральные и инвариантные свойства | Компиляция в TLA+ модель и верификация TLC |

---

## 3. Архитектура контракта (Root Schema)

Корневой объект спецификации — [`PipelineSpecification`](file:///home/andrey/MAI/practice/data-pipeline-analyst/pipeline_spec/models/pipeline.py):

```json
{
  "version": "1.0.0",
  "metadata": { ... },
  "sources": [ ... ],
  "steps": [ ... ],
  "sinks": [ ... ],
  "constraints": [ ... ],
  "formal_model": { ... }
}
```

### Поля корневого объекта:
* `version` (*string*, обязательное): версия спецификации по semver (текущая: `"1.0.0"`).
* `metadata` (*object*, обязательное): паспорт конвейера.
* `sources` (*array*, обязательное): список входных наборов данных.
* `steps` (*array*, обязательное, мин. 1): узлы графа обработки данных.
* `sinks` (*array*, опциональное): список выходных хранилищ/витрин.
* `constraints` (*array*, опциональное): список ограничений и проверок качества данных.
* `formal_model` (*object*, опциональное): формальная модель состояний для TLC.

---

## 4. Описание разделов спецификации

### 4.1. Метаданные (`metadata`)
* `id` (*string*): уникальный машинный идентификатор пайплайна (латиница, `_`, `-`).
* `name` (*string*): понятное человеку название.
* `description` (*string* | *null*): развернутое текстовое описание целей и логики.
* `author` (*string* | *null*): автор спецификации (например, `"Analyst Agent"`).
* `tags` (*array[string]*): теги для категоризации (`["sales", "daily", "gold"]`).

### 4.2. Входные датасеты (`sources`)
Каждый объект источника описывает датасет:
* `id` (*string*): уникальный ID датасета в рамках спецификации (например, `"raw_orders"`).
* `name` (*string*): читаемое название источника.
* `description` (*string* | *null*): описание источника.
* `format` (*string*): формат хранения:
  `"parquet"` | `"csv"` | `"json"` | `"delta"` | `"orc"` | `"avro"` | `"jdbc"`.
* `location` (*string*): путь к данным (S3/HDFS/локальный путь) или имя таблицы.
* `options` (*object*): параметры чтения (например, `{"header": "true", "delimiter": ","}`).
* `partition_columns` (*array[string]*): колонки партиционирования источника (если есть).
* `schema` (*object* | *null*): явное определение схемы данных:
  * `columns`: список объектов `{"name": "...", "data_type": "...", "nullable": bool, "description": "..."}`.
  * Поддерживаемые типы данных (`data_type`):
    `"string"`, `"integer"`, `"long"`, `"float"`, `"double"`, `"boolean"`, `"date"`, `"timestamp"`, `"decimal"`.

### 4.3. Выходные датасеты (`sinks`)
Каждый объект описывает целевое сохранение:
* `id` (*string*): уникальный ID синка (например, `"daily_sales_mart"`).
* `name` (*string*): читаемое название.
* `from_step` (*string*): ID шага пайплайна, результат которого записывается в этот синк.
* `format` (*string*): формат сохранения (`"parquet"`, `"delta"`, `"csv"`, и т.д.).
* `location` (*string*): целевой путь или таблица.
* `save_mode` (*string*): режим сохранения:
  `"overwrite"` | `"append"` | `"ignore"` | `"error_if_exists"`.
* `partition_by` (*array[string]*): список колонок для партиционирования директорий.
* `options` (*object*): параметры сохранения (например, `{"compression": "snappy"}`).

### 4.4. Этапы обработки (`steps`)
Шаг представляет собой узел DAG вычислений:
* `id` (*string*): уникальный ID шага (например, `"filter_valid_orders"`).
* `name` (*string*): название этапа.
* `description` (*string* | *null*): описание производимой логики.
* `inputs` (*array[string]*): список ID источников или предшествующих шагов, датафреймы которых подаются на вход данному шагу.
* `depends_on` (*array[string]*): явные зависимости для топологической сортировки. Если не указаны, автоматически заполняются из `inputs`.
* `operation` (*object*): выполняемая операция трансформации (дискриминируемый союз по полю `type`).

---

## 5. Поддерживаемые операции MVP (`operation`)

Операции отображаются в конкретные вызовы PySpark DataFrame API:

### 5.1. `read`
Чтение исходного набора данных:
```json
{
  "type": "read",
  "source_id": "raw_orders"
}
```
*PySpark эквивалент:*
```python
df_read_orders = spark.read.format(source.format).options(**source.options).load(source.location)
```

### 5.2. `filter`
Фильтрация строк датафрейма по предикату:
```json
{
  "type": "filter",
  "condition": {
    "and": [
      { "ne": [ {"col": "status"}, {"lit": "CANCELLED"} ] },
      { "gt": [ {"col": "amount"}, {"lit": 0.0} ] }
    ]
  }
}
```
*PySpark эквивалент:*
```python
df_filtered = df_in.filter((F.col("status") != "CANCELLED") & (F.col("amount") > 0.0))
```

### 5.3. `select`
Проекция и переименование колонок:
```json
{
  "type": "select",
  "columns": [
    { "expr": {"col": "order_id"}, "alias": "id" },
    { "expr": {"col": "amount"}, "alias": "gross_amount" }
  ]
}
```
*PySpark эквивалент:*
```python
df_selected = df_in.select(
    F.col("order_id").alias("id"),
    F.col("amount").alias("gross_amount")
)
```

### 5.4. `with_columns`
Добавление новых вычисляемых колонок или замена существующих:
```json
{
  "type": "with_columns",
  "columns": [
    {
      "name": "net_amount",
      "expr": {
        "mul": [
          {"col": "amount"},
          { "sub": [ {"lit": 1.0}, { "div": [ {"col": "discount_pct"}, {"lit": 100.0} ] } ] }
        ]
      }
    },
    {
      "name": "customer_tier_clean",
      "expr": {
        "func": "coalesce",
        "args": [ {"col": "customer_tier"}, {"lit": "REGULAR"} ]
      }
    }
  ]
}
```
*PySpark эквивалент:*
```python
df_with_cols = df_in.withColumn("net_amount", F.col("amount") * (F.lit(1.0) - (F.col("discount_pct") / F.lit(100.0)))) \
                    .withColumn("customer_tier_clean", F.coalesce(F.col("customer_tier"), F.lit("REGULAR")))
```

### 5.5. `join`
Соединение двух датафреймов:
```json
{
  "type": "join",
  "right_input": "filter_active_customers",
  "join_type": "left",
  "on": ["customer_id"]
}
```
*Поддерживаемые `join_type`:* `"inner"`, `"left"`, `"right"`, `"full"`, `"cross"`, `"left_semi"`, `"left_anti"`.  
*PySpark эквивалент:*
```python
df_joined = df_left.join(df_right, on=["customer_id"], how="left")
```

### 5.6. `aggregate`
Группировка с агрегатными функциями:
```json
{
  "type": "aggregate",
  "group_by": ["order_date", "country"],
  "aggregations": [
    { "agg_func": "count", "column": "order_id", "alias": "total_orders" },
    { "agg_func": "sum", "column": "net_amount", "alias": "total_revenue" },
    { "agg_func": "avg", "column": "net_amount", "alias": "avg_order_value" },
    { "agg_func": "count_distinct", "column": "customer_id", "alias": "unique_customers" }
  ]
}
```
*Поддерживаемые функции:* `"count"`, `"sum"`, `"avg"`, `"min"`, `"max"`, `"count_distinct"`, `"first"`, `"last"`.  
*PySpark эквивалент:*
```python
df_agg = df_in.groupBy("order_date", "country").agg(
    F.count("order_id").alias("total_orders"),
    F.sum("net_amount").alias("total_revenue"),
    F.avg("net_amount").alias("avg_order_value"),
    F.countDistinct("customer_id").alias("unique_customers")
)
```

### 5.7. `deduplicate`
Удаление дубликатов по подмножеству колонок:
```json
{
  "type": "deduplicate",
  "subset": ["order_id"],
  "keep": "first"
}
```
*PySpark эквивалент:*
```python
df_dedup = df_in.dropDuplicates(subset=["order_id"])
```

### 5.8. `sort`
Сортировка строк датафрейма:
```json
{
  "type": "sort",
  "order_by": [
    { "column": "order_date", "direction": "desc", "null_ordering": "nulls_last" },
    { "column": "total_revenue", "direction": "desc", "null_ordering": "nulls_last" }
  ]
}
```
*PySpark эквивалент:*
```python
df_sorted = df_in.orderBy(F.col("order_date").desc_nulls_last(), F.col("total_revenue").desc_nulls_last())
```

### 5.9. `union`
Объединение строк из нескольких датафреймов с одинаковой схемой:
```json
{
  "type": "union",
  "other_inputs": ["read_historical_orders"],
  "distinct": false
}
```
*PySpark эквивалент:*
```python
df_union = df_in.unionByName(df_other)
```

### 5.10. `write`
Запись результата в синк:
```json
{
  "type": "write",
  "sink_id": "daily_sales_mart"
}
```
*PySpark эквивалент:*
```python
df_in.write.format(sink.format).mode(sink.save_mode).partitionBy(*sink.partition_by).options(**sink.options).save(sink.location)
```

---

## 6. Каталог выражений (Expressions)

Выражения строятся рекурсивно в виде дерева (AST):

| Выражение | JSON-представление | PySpark эквивалент |
|---|---|---|
| Ссылка на колонку | `{"col": "amount"}` | `F.col("amount")` |
| Литерал | `{"lit": 100.0}` | `F.lit(100.0)` |
| Равенство | `{"eq": [A, B]}` | `A == B` |
| Неравенство | `{"ne": [A, B]}` | `A != B` |
| Сравнения | `{"lt": [A, B]}`, `{"le": [A, B]}`, `{"gt": [A, B]}`, `{"ge": [A, B]}` | `<`, `<=`, `>`, `>=` |
| Вхождение в список | `{"in": [A, ["VAL1", "VAL2"]]}` | `A.isin(["VAL1", "VAL2"])` |
| Диапазон | `{"between": [A, lower, upper]}` | `A.between(lower, upper)` |
| Проверка на NULL | `{"is_null": A}`, `{"is_not_null": A}` | `A.isNull()`, `A.isNotNull()` |
| Логические связки | `{"and": [A, B, ...]}` | `(A) & (B)` |
| | `{"or": [A, B, ...]}` | `(A) \| (B)` |
| | `{"not": [A]}` | `~(A)` |
| Арифметика | `{"add": [A, B]}`, `{"sub": [A, B]}`, `{"mul": [A, B]}`, `{"div": [A, B]}`, `{"mod": [A, B]}` | `A + B`, `A - B`, `A * B`, `A / B`, `A % B` |
| Приведение типов | `{"cast": [A, "integer"]}` | `A.cast("integer")` |
| Условное ветвление | `{"when": [{"condition": C1, "value": V1}], "otherwise": V2}` | `F.when(C1, V1).otherwise(V2)` |
| Встроенная функция | `{"func": "coalesce", "args": [A, B]}` | `F.coalesce(A, B)` |

---

## 7. Ограничения качества данных (`constraints`)

Каждое ограничение позволяет задать правило валидации:
* `id` (*string*): идентификатор правила (`"qc_not_null_order_id"`).
* `name` (*string*): название.
* `target_step` (*string*): шаг конвейера, на выходе которого проверяется правило.
* `columns` (*array[string]*): проверяемые колонки.
* `type` (*string*): тип проверки:
  * `"not_null"`: значение колонки не должно быть `NULL`.
  * `"unique"`: значения в колонке/кортеже колонок должны быть уникальны.
  * `"range"`: проверка диапазона значений (`params: {"min": 0, "max": 100}`).
  * `"in_set"`: значения должны принадлежать списку (`params: {"values": [...]}`).
  * `"regex_match"`: строка должна соответствовать регулярному выражению (`params: {"pattern": "..."}`).
  * `"custom_sql"`: пользовательское булево условие (`custom_condition`).
* `level` (*string*):
  * `"error"`: нарушение правила прерывает выполнение пайплайна с ошибкой.
  * `"warning"`: нарушение правила формирует предупреждение в журнал мониторинга.

---

## 8. Модель формальной верификации (`formal_model`)

Секция `formal_model` обеспечивает интеграцию с модулем формальной проверки **`formal_check`** (TLA+ / TLC) и содержит:

1. **`variables`**: переменные конечного автомата жизненного цикла конвейера (типы: `enum`, `boolean`, `integer`).
2. **`initial`**: предикат начального состояния переменных.
3. **`transitions`**: действия/шаги перехода автомата с `guard` (условием выполнения) и `effects` (список присваиваний `{"set": [var_name, expr]}`).
4. **`properties`**: свойства верификации:
   * `invariant`: свойство, которое обязано выполняться во всех достижимых состояниях.
   * `forbidden_state`: запрещенное состояние (компилируется как отрицание инварианта).
   * `deadlock_free`: условие отсутствия тупиков (`ENABLED Next \/ FinalCondition`).
   * `reachable`: темпоральное свойство достижимости целевого состояния (`~[](~Goal)`).

Спецификация преобразуется в валидный объект `formal_check.src.models.input.Input` через вызов метода:
```python
fc_input = spec.formal_model.to_formal_check_input()
```

---

## 9. Правила валидации графа (DAG Validation)

Спецификация автоматически валидирует целостность пайплайна:
1. **Уникальность идентификаторов**: все `source.id`, `sink.id`, `step.id` и `constraint.id` уникальны; коллизии между источниками и шагами запрещены.
2. **Корректность ссылок**:
   - Все значения `inputs` и `depends_on` каждого шага ссылаются на существующие источники или шаги.
   - `ReadOperation.source_id` ссылается на существующий источник.
   - `WriteOperation.sink_id` ссылается на существующий синк.
   - `SinkDataset.from_step` ссылается на существующий шаг.
   - `DataConstraint.target_step` ссылается на существующий шаг.
3. **Ацикличность DAG (No Cycles)**:
   - Шаги и их зависимости проверяются алгоритмом Кана (Kahn's algorithm). При обнаружении цикла выбрасывается ошибка `ValueError: Cyclic dependency detected among steps: [...]`.
   - Метод `spec.get_topological_order()` возвращает строго упорядоченный список идентификаторов шагов для кодогенератора.
