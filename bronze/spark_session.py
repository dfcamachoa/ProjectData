"""Spark session configured for Delta Lake.

Uses delta-spark's configure_spark_with_delta_pip so the Delta extension and
catalog are registered and the matching Delta JARs are pulled onto the classpath.
"""
from __future__ import annotations
import os
import sys

# 1. Forzar a que los procesos apunten al binario de Python de tu .venv
if ".venv" in sys.executable:
    # Ruta absoluta al ejecutable del entorno virtual actual
    current_python = sys.executable  
    
    os.environ["PYSPARK_PYTHON"] = current_python
    os.environ["PYSPARK_DRIVER_PYTHON"] = current_python

    # 2. Configurar el SPARK_HOME local (lo que ya tenías)
    venv_base = current_python.split("/bin/python")[0]
    os.environ["SPARK_HOME"] = f"{venv_base}/lib/python3.8/site-packages/pyspark"
    os.environ["PATH"] = f"{os.environ['SPARK_HOME']}/bin:" + os.environ["PATH"]

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
