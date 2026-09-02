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

# A system SPARK_HOME (e.g. /opt/spark) makes pyspark launch that install, whose
# classpath has NO Delta JARs -> "Cannot find catalog plugin ... DeltaCatalog".
# Drop it so pyspark uses the venv's own bundled Spark, and strip /opt/spark from
# sys.path so `import pyspark` resolves to the venv, not the system tree.
if os.environ.get("SPARK_HOME", "").startswith("/opt/spark"):
    os.environ.pop("SPARK_HOME", None)
sys.path[:] = [p for p in sys.path if "/opt/spark" not in p]

from pyspark.sql import SparkSession


# Repo root (…/ProjectData), so the metastore + warehouse are PINNED to fixed
# absolute paths regardless of the process's working directory. This is what makes
# a notebook session and a `python -m …` subprocess share ONE catalog + warehouse
# (spec §8.4). Override with env vars for a different location.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAREHOUSE_DIR = os.environ.get("PIDDATA_WAREHOUSE", os.path.join(_REPO, "spark-warehouse"))
METASTORE_DB = os.environ.get("PIDDATA_METASTORE_DB", os.path.join(_REPO, "metastore_db"))


def get_spark(
    app_name: str = "bronze-pid-ingestion",
    extra_conf: dict | None = None,
    enable_hive: bool = True,
) -> SparkSession:
    """Build a Delta-enabled SparkSession.

    enable_hive (spec §8.4): register named tables in the embedded Apache Derby
    Hive metastore, so a name like ``bronze.pid_documents`` resolves. The metastore
    (``metastore_db``) and warehouse (``spark-warehouse``) are PINNED to the repo
    root (see WAREHOUSE_DIR / METASTORE_DB) so every session — notebook or
    subprocess, any CWD — uses the SAME catalog. Still single-session (embedded
    Derby): don't run a `!` subprocess while a notebook session is live. Set
    enable_hive False for path-based-only use (no metastore).
    A modest Arrow batch size keeps per-task memory low with ~13 MB payloads.
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.driver.memory", "4g")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        # keep Arrow batches small so a task never buffers many large blobs (§8.2)
        .config("spark.sql.execution.arrow.maxRecordsPerBatch", "64")
        # PIN the warehouse so managed-table data always lands in one place (§8.4)
        .config("spark.sql.warehouse.dir", f"file:{WAREHOUSE_DIR}")
    )
    if enable_hive:
        builder = builder.enableHiveSupport().config(
            # spark.hadoop.* is forwarded to the Hive/Derby conf; the bare
            # javax.jdo.* key is NOT, so it must carry this prefix to take effect.
            "spark.hadoop.javax.jdo.option.ConnectionURL",
            f"jdbc:derby:;databaseName={METASTORE_DB};create=true",
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
