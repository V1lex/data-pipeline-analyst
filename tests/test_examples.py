"""Explicit developer-only execution of reviewed MANUAL reference scripts, never LLM output."""

import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.spark,
    pytest.mark.skipif(
        os.getenv("RUN_SPARK_TESTS") != "1",
        reason="Set RUN_SPARK_TESTS=1 for explicit local Spark reference demonstrations",
    ),
]
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_reference(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def revenue():
    return load_reference("revenue_reference")


@pytest.fixture
def sensor():
    return load_reference("sensor_reference")


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["SPARK_LOCAL_IP"] = "127.0.0.1"
    session = (
        SparkSession.builder.master("local[1]")
        .appName("manual-reference-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.host", "127.0.0.1")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def test_manual_revenue_matches_hand_calculated_totals(revenue, spark, tmp_path):
    output = tmp_path / "revenue"
    revenue.run(
        spark, str(EXAMPLES / "data/orders.csv"), str(EXAMPLES / "data/prices.csv"), str(output)
    )
    actual = {row.city: row.revenue for row in spark.read.parquet(str(output)).collect()}
    assert actual == {"Москва": Decimal("300"), "Казань": Decimal("150")}


@pytest.mark.parametrize(
    ("orders", "prices"),
    [
        ("product_id,quantity,city\nA,,Москва\n", "product_id,price\nA,100\n"),
        ("product_id,quantity,city\nA,2,Москва\n", "product_id,price\nA,100\nA,101\n"),
        ("product_id,quantity,city\nA,2,Москва\n", "product_id,price\nA,\n"),
    ],
)
def test_manual_revenue_quality_failures_do_not_create_output(
    revenue, spark, tmp_path, orders, prices
):
    orders_path, prices_path, output = (
        tmp_path / "orders.csv",
        tmp_path / "prices.csv",
        tmp_path / "output",
    )
    orders_path.write_text(orders)
    prices_path.write_text(prices)
    with pytest.raises(ValueError):
        revenue.run(spark, str(orders_path), str(prices_path), str(output))
    assert not output.exists()


def test_manual_revenue_preserves_existing_output(revenue, spark, tmp_path):
    from pyspark.errors import AnalysisException

    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "do-not-overwrite.txt"
    sentinel.write_text("keep")
    with pytest.raises(AnalysisException):
        revenue.run(
            spark, str(EXAMPLES / "data/orders.csv"), str(EXAMPLES / "data/prices.csv"), str(output)
        )
    assert sentinel.read_text() == "keep"


def test_manual_sensor_example_filters_nulls_and_keeps_negative_values(sensor, spark, tmp_path):
    output = tmp_path / "sensor"
    sensor.run(spark, str(EXAMPLES / "data/readings.csv"), str(output))
    actual = {
        row.sensor: (row.mean_temperature, row.reading_count)
        for row in spark.read.parquet(str(output)).collect()
    }
    assert actual == {"north": (15.0, 2), "south": (-5.0, 1)}
