#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Средняя температура и число измерений по датчикам.

Логика (согласованная постановка):
  * CSV в кодировке UTF-8 с заголовком;
  * явная схема: sensor -> string, temperature -> double;
  * отбрасываются строки с null/пустым sensor и с null temperature
    (нечитаемое число при разборе CSV становится null);
  * отрицательные температуры валидны и не отбрасываются;
  * группировка по sensor: mean_temperature = среднее temperature,
    reading_count = число оставшихся строк;
  * запись Parquet в НОВОЕ место назначения, режим errorifexists;
  * пути задаются параметрами командной строки --readings и --output;
  * SparkSession останавливается в finally.

Пример запуска:
  spark-submit sensor_temperature_mean.py \
      --readings /path/to/readings.csv \
      --output /path/to/new_output_dir
"""

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Средняя температура и число измерений по датчикам (CSV -> Parquet)."
    )
    parser.add_argument(
        "--readings",
        required=True,
        help="Путь к входному CSV-файлу (UTF-8, с заголовком).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Путь к новому каталогу назначения для Parquet (должен не существовать).",
    )
    return parser.parse_args(argv)


def build_schema():
    """Явная схема входного CSV."""
    return StructType(
        [
            StructField("sensor", StringType(), True),
            StructField("temperature", DoubleType(), True),
        ]
    )


def main(argv=None):
    args = parse_args(argv)

    spark = (
        SparkSession.builder.appName("sensor-temperature-mean")
        .getOrCreate()
    )

    try:
        # 1. Чтение CSV с явной схемой.
        #    Режим PERMISSIVE (по умолчанию) превращает нечитаемые значения
        #    в null, поэтому нечитаемая temperature отсеется фильтром ниже.
        raw = (
            spark.read.option("header", True)
            .option("encoding", "UTF-8")
            .option("mode", "PERMISSIVE")
            .schema(build_schema())
            .csv(args.readings)
        )

        # 2. Очистка: только эти правила, без дополнительных условий.
        cleaned = raw.filter(
            F.col("sensor").isNotNull()
            & (F.trim(F.col("sensor")) != F.lit(""))
            & F.col("temperature").isNotNull()
        )

        # 3. Агрегация по датчику.
        result = cleaned.groupBy("sensor").agg(
            F.mean(F.col("temperature")).alias("mean_temperature"),
            F.count(F.lit(1)).alias("reading_count"),
        )

        # 4. Запись в новое место назначения; существующий путь — ошибка.
        result.write.mode("errorifexists").parquet(args.output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
