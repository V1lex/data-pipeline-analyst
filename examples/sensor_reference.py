"""MANUALLY WRITTEN second reference. Null filtering is an explicit rule of this fixture."""

import argparse

from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType


def run(spark, readings_path: str, output_path: str) -> None:
    schema = StructType(
        [
            StructField("sensor", StringType()),
            StructField("temperature", DoubleType()),
        ]
    )
    readings = spark.read.option("header", True).schema(schema).csv(readings_path)
    cleaned = readings.filter(
        F.col("sensor").isNotNull() & (F.trim("sensor") != "") & F.col("temperature").isNotNull()
    )
    summary = cleaned.groupBy("sensor").agg(
        F.avg("temperature").alias("mean_temperature"), F.count("*").alias("reading_count")
    )
    summary.write.mode("errorifexists").parquet(output_path)


def main():
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readings", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spark = SparkSession.builder.appName("ManualSensorReference").getOrCreate()
    try:
        run(spark, args.readings, args.output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
