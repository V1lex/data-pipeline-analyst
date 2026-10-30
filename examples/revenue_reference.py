"""MANUALLY WRITTEN reference, not an LLM generation result. Synthetic-data demo only."""

import argparse

from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, StringType, StructField, StructType


def run(spark, orders_path: str, prices_path: str, output_path: str) -> None:
    orders_schema = StructType(
        [
            StructField("product_id", StringType()),
            StructField("quantity", DecimalType(18, 2)),
            StructField("city", StringType()),
        ]
    )
    prices_schema = StructType(
        [
            StructField("product_id", StringType()),
            StructField("price", DecimalType(18, 2)),
        ]
    )
    orders = spark.read.option("header", True).schema(orders_schema).csv(orders_path)
    prices = spark.read.option("header", True).schema(prices_schema).csv(prices_path)

    # These explicit actions finish both validations before the dependent join/write.
    if (
        orders.filter(
            F.col("product_id").isNull()
            | (F.trim("product_id") == "")
            | F.col("quantity").isNull()
            | F.col("city").isNull()
            | (F.trim("city") == "")
        )
        .limit(1)
        .count()
    ):
        raise ValueError("Orders contain missing required fields")
    if (
        prices.filter(
            F.col("product_id").isNull() | (F.trim("product_id") == "") | F.col("price").isNull()
        )
        .limit(1)
        .count()
    ):
        raise ValueError("Prices contain missing required fields")
    if prices.groupBy("product_id").count().filter(F.col("count") > 1).limit(1).count():
        raise ValueError("Prices contain duplicate product_id values")

    revenue = (
        orders.join(prices, "product_id", "inner")
        .withColumn("line_revenue", F.col("quantity") * F.col("price"))
        .groupBy("city")
        .agg(F.sum("line_revenue").alias("revenue"))
    )
    revenue.write.mode("errorifexists").parquet(output_path)


def main():
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", required=True)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spark = SparkSession.builder.appName("ManualRevenueReference").getOrCreate()
    try:
        run(spark, args.orders, args.prices, args.output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
