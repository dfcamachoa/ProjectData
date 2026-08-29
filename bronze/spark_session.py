"""Spark session configured for Delta Lake.

Uses delta-spark's configure_spark_with_delta_pip so the Delta extension and
catalog are registered and the matching Delta JARs are pulled onto the classpath.
"""
from __future__ import annotations
import os
import sys

# Use the active virtualenv interpreter for Spark worker/driver processes.
# Avoid forcing a stale SPARK_HOME built from a different Python version.
if sys.executable:
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    # Spark from the system install can still leak into PYTHONPATH and override the
    # venv-installed PySpark. Remove stale /opt/spark entries before importing.
    pythonpath = os.environ.get("PYTHONPATH", "")
    if pythonpath:
        cleaned = [
            p for p in pythonpath.split(os.pathsep)
            if p and "/opt/spark" not in p and p != "/opt/spark/python"
        ]
        if cleaned:
            os.environ["PYTHONPATH"] = os.pathsep.join(cleaned)
        else:
            os.environ.pop("PYTHONPATH", None)

# Keep system-wide Spark available if explicitly configured, but do not point at
# a Python-version-specific path in the repo venv.
from pyspark.sql import SparkSession


def get_spark(app_name: str = "bronze-pid-ingestion", extra_conf: dict | None = None) -> SparkSession:
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.driver.memory", "4g")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )
    for k, v in (extra_conf or {}).items():
        builder = builder.config(k, v)

    try:
        # Pulls the Delta JARs matching the installed delta-spark at runtime.
        from delta import configure_spark_with_delta_pip

        return configure_spark_with_delta_pip(builder).getOrCreate()
    except Exception:
        # If delta-spark's helper is unavailable, assume the Delta JARs are already
        # on the classpath (e.g. Databricks, or --packages supplied to spark-submit).
        return builder.getOrCreate()
