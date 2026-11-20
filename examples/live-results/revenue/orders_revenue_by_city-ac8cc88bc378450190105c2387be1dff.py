#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выручка по городам.

Пайплайн:
  1. Читает orders.csv и prices.csv (UTF-8, с заголовками, явные схемы).
  2. Две независимые проверки входов:
       - все перечисленные поля не NULL;
       - строковые поля не пустые (после trim);
       - product_id в prices уникален.
     Обе проверки выполняются ДО джойна.
  3. При любой ошибке валидации скрипт останавливается, ничего не записывая.
  4. INNER JOIN orders/prices по product_id (заказ без цены исключается),
     line_revenue = quantity * price, SUM(line_revenue) по city -> revenue.
  5. Результат пишется в Parquet в НОВЫЙ каталог (mode=errorifexists, без перезаписи).

Запуск:
  spark-submit orders_revenue_by_city.py \
      --orders /path/to/orders.csv \
      --prices /path/to/prices.csv \
      --output /path/to/new_output_dir
"""

import argparse
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, StringType, StructField, StructType

# --- Явные схемы входных CSV -------------------------------------------------

ORDERS_SCHEMA = StructType([
    StructField("product_id", StringType(), True),
    StructField("quantity", DecimalType(18, 2), True),
    StructField("city", StringType(), True),
])

PRICES_SCHEMA = StructType([
    StructField("product_id", StringType(), True),
    StructField("price", DecimalType(18, 2), True),
])

# Обязательные поля: строковые проверяются на NULL и на пустоту после trim,
# десятичные — только на NULL.
ORDERS_STRING_FIELDS = ("product_id", "city")
ORDERS_DECIMAL_FIELDS = ("quantity",)
PRICES_STRING_FIELDS = ("product_id",)
PRICES_DECIMAL_FIELDS = ("price",)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Выручка по городам: INNER JOIN orders и prices по product_id, "
            "line_revenue = quantity * price, SUM по city."
        )
    )
    parser.add_argument(
        "--orders",
        required=True,
        help="Путь к CSV с заказами (UTF-8, с заголовками).",
    )
    parser.add_argument(
        "--prices",
        required=True,
        help="Путь к CSV с ценами (UTF-8, с заголовками).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Путь к НОВОМУ каталогу для Parquet-результата (перезапись запрещена).",
    )
    return parser.parse_args(argv)


def read_csv(spark, path, schema):
    """Читает CSV с заголовками в UTF-8 и явной схемой."""
    return (
        spark.read
        .option("header", True)
        .option("encoding", "UTF-8")
        .option("mode", "PERMISSIVE")
        .schema(schema)
        .csv(path)
    )


def field_errors(df, label, string_fields, decimal_fields):
    """
    Проверяет NULL/пустые значения одним агрегирующим запросом.
    Возвращает список описаний ошибок (пустой список — проверка пройдена).
    """
    aggs = []
    for name in string_fields:
        col = F.col(name)
        aggs.append(
            F.sum(F.when(col.isNull(), F.lit(1)).otherwise(F.lit(0))).alias("null__" + name)
        )
        aggs.append(
            F.sum(
                F.when(col.isNotNull() & (F.trim(col) == F.lit("")), F.lit(1))
                .otherwise(F.lit(0))
            ).alias("blank__" + name)
        )
    for name in decimal_fields:
        col = F.col(name)
        aggs.append(
            F.sum(F.when(col.isNull(), F.lit(1)).otherwise(F.lit(0))).alias("null__" + name)
        )

    row = df.agg(*aggs).first()

    errors = []
    for name in string_fields:
        nulls = int(row["null__" + name] or 0)
        if nulls:
            errors.append("%s.%s: NULL в %d строке(ах)" % (label, name, nulls))
        blanks = int(row["blank__" + name] or 0)
        if blanks:
            errors.append(
                "%s.%s: пустое значение (после trim) в %d строке(ах)" % (label, name, blanks)
            )
    for name in decimal_fields:
        nulls = int(row["null__" + name] or 0)
        if nulls:
            errors.append("%s.%s: NULL в %d строке(ах)" % (label, name, nulls))
    return errors


def duplicate_key_errors(df, key, label):
    """Проверяет уникальность ключа; возвращает список ошибок (пустой — ключ уникален)."""
    duplicates = df.groupBy(key).count().filter(F.col("count") > F.lit(1))
    groups = duplicates.count()
    if groups == 0:
        return []
    samples = [r[key] for r in duplicates.select(key).orderBy(key).limit(5).collect()]
    return [
        "%s.%s: %d повторяющихся значени(й) ключа, например: %s"
        % (label, key, groups, samples)
    ]


def main(argv=None):
    args = parse_args(argv)

    spark = (
        SparkSession.builder
        .appName("orders-revenue-by-city")
        .getOrCreate()
    )

    try:
        orders = read_csv(spark, args.orders, ORDERS_SCHEMA)
        prices = read_csv(spark, args.prices, PRICES_SCHEMA)

        # --- Шаг 1: обе независимые проверки завершаются ДО джойна -----------
        orders_errors = field_errors(
            orders, "orders", ORDERS_STRING_FIELDS, ORDERS_DECIMAL_FIELDS
        )
        prices_errors = field_errors(
            prices, "prices", PRICES_STRING_FIELDS, PRICES_DECIMAL_FIELDS
        )
        prices_errors.extend(duplicate_key_errors(prices, "product_id", "prices"))

        all_errors = orders_errors + prices_errors
        if all_errors:
            for message in all_errors:
                print("VALIDATION ERROR: " + message, file=sys.stderr)
            raise ValueError(
                "Валидация входных данных не пройдена; результат не записан."
            )

        # --- Шаг 2: INNER JOIN по product_id --------------------------------
        joined = orders.join(prices, on="product_id", how="inner")

        # --- Шаг 3: line_revenue и сумма по городам -------------------------
        revenue = (
            joined
            .withColumn("line_revenue", F.col("quantity") * F.col("price"))
            .groupBy("city")
            .agg(F.sum("line_revenue").alias("revenue"))
            .orderBy("city")
        )

        print("Результат (city, revenue):")
        revenue.show(20, truncate=False)

        # --- Шаг 4: запись только в новый каталог, без перезаписи ----------
        (
            revenue.write
            .mode("errorifexists")
            .format("parquet")
            .save(args.output)
        )
        print("Parquet сохранён в: %s" % args.output)

    finally:
        spark.stop()


if __name__ == "__main__":
    main()
