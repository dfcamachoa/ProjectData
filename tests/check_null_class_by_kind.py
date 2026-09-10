"""One-off diagnostic: real distribution of component_class=None rows by `kind`,
for Project B (PostProc) drawings, against real silver_components / gold_objects.
Run inside your Spark session (or adapt the read to however you load these tables).
"""
from pyspark.sql import functions as F

# Option A: against silver_components directly (source_format filter optional)
df = spark.table("silver.silver_components")
# df = df.where(F.col("source_format") == "POSTPROC")  # uncomment to scope to Project B only

dist = (
    df.groupBy("kind")
      .agg(
          F.count("*").alias("total_rows"),
          F.sum(F.when(F.col("component_class").isNull(), 1).otherwise(0)).alias("null_class_rows"),
      )
      .withColumn("pct_null", F.round(100 * F.col("null_class_rows") / F.col("total_rows"), 1))
      .orderBy(F.desc("null_class_rows"))
)
dist.show(20, False)

# Option B: same question against gold_objects (component kind only), if you'd rather
# check post-Gold-projection state (attrs is a map/struct column there):
# god = spark.table("gold.gold_objects").where("object_kind = 'component'")
# god.groupBy(F.col("attrs.kind")).agg(
#     F.count("*").alias("total_rows"),
#     F.sum(F.when(F.col("attrs.component_class").isNull(), 1).otherwise(0)).alias("null_class_rows"),
# ).show(20, False)
