"""Expand the simulation into raw files with Spark.

Each entity lands in /Volumes/<catalog>/hearth_raw/landing/<entity>/dt=YYYY-MM-DD/,
in the format its source system would use. Sessions become individual app
events, members get fake PII, and the app events get the quirks the
pipeline's expectations should catch.
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from hearth_data import config as C

INTERACTIONS = ["profile_view", "like_sent", "match_created", "message_sent", "message_received", "like_received"]
TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"


def member_id(col):
    return F.concat(F.lit("m_"), F.lpad((F.col(col) + 1).cast("string"), 6, "0"))


def ts(col, jitter_seed=None):
    """Epoch seconds to a UTC timestamp, with random milliseconds."""
    ms = F.col(col) * 1000
    if jitter_seed is not None:
        ms = ms + F.floor(F.rand(jitter_seed) * 1000)
    return F.timestamp_millis(ms.cast("long"))


def dt(col):
    return F.date_format(F.col(col), "yyyy-MM-dd")


def add_pii(df: DataFrame, seed: int) -> DataFrame:
    @F.pandas_udf("struct<first_name:string,email:string,phone:string>")
    def pii(ids):
        import pandas as pd
        from faker import Faker

        fake = Faker("en_US")
        fake.seed_instance(seed * 1_000_003 + int(ids.iloc[0]))
        rows = []
        for i in ids:
            first, last = fake.first_name(), fake.last_name()
            domain = fake.random_element(["example.com", "example.net", "example.org"])  # reserved, never real
            rows.append({
                "first_name": first,
                "email": f"{first.lower()}.{last.lower()}{int(i) % 997}@{domain}",
                "phone": f"+1-{fake.random_int(201, 989)}-555-01{fake.random_int(0, 99):02d}",  # 555-01xx is fictional
            })
        return pd.DataFrame(rows)

    return df.withColumn("pii", pii(F.col("member_idx"))).select("*", "pii.*").drop("pii")


def members(spark, path, start_s, seed):
    df = spark.read.parquet(path)
    df = add_pii(df, seed)
    return df.select(
        member_id("member_idx").alias("member_id"),
        ts("signup_s").alias("signup_ts"),
        "first_name", "email", "phone", "age_band", "gender_identity", "seeking", "metro", "market_size",
        "timezone", "platform", "plan", "push_opt_in",
        ts("last_active_s").alias("last_active_ts"),
        # Members who joined before the window arrive in the first day's export
        F.date_format(F.timestamp_seconds(F.greatest(F.col("signup_s"), F.lit(start_s))), "yyyy-MM-dd").alias("dt"),
    )


def random_member(n_members, seed):
    """Another member, for likes, matches and messages. Pairs aren't reconciled across members."""
    idx = F.floor(F.rand(seed) * n_members)
    return F.to_json(F.struct(F.concat(F.lit("m_"), F.lpad((idx + 1).cast("string"), 6, "0")).alias("other_member_id")))


def app_events(spark, sessions_path, away_path, members_df, n_members, start_s, seed):
    s = spark.read.parquet(sessions_path)
    kinds = F.flatten(F.array(*[F.array_repeat(F.lit(k), F.col(k).cast("int")) for k in INTERACTIONS]))
    inner = s.select("*", F.posexplode(kinds).alias("pos", "event_type")).select(
        "session_idx", "member_idx", "event_type", (F.col("pos") + 1).alias("pos"), "start_s",
        (F.col("start_s") + F.floor(F.rand(seed) * F.col("duration_s"))).alias("t"),
        random_member(n_members, seed + 1).alias("properties"),
    )
    start = s.select(
        "session_idx", "member_idx", F.lit("session_start").alias("event_type"), F.lit(0).alias("pos"), "start_s",
        F.col("start_s").alias("t"), F.to_json(F.struct("source")).alias("properties"),
    )
    end = s.select(
        "session_idx", "member_idx", F.lit("session_end").alias("event_type"), F.lit(999).alias("pos"), "start_s",
        (F.col("start_s") + F.col("duration_s")).alias("t"), F.to_json(F.struct("duration_s")).alias("properties"),
    )
    in_session = start.unionByName(inner).unionByName(end).select(
        F.concat(F.lit("ev_s"), F.col("session_idx").cast("string"), F.lit("_"), F.lpad(F.col("pos").cast("string"), 3, "0"))
        .alias("event_id"),
        "member_idx", F.concat(F.lit("s"), F.col("session_idx").cast("string")).alias("session_id"),
        "t", "event_type", "properties", F.col("start_s").alias("batch_s"),
    )
    away = spark.read.parquet(away_path).select(
        F.concat(F.lit("ev_a"), F.col("away_idx").cast("string")).alias("event_id"),
        "member_idx", F.lit(None).cast("string").alias("session_id"), F.col("ts").alias("t"), "event_type",
        random_member(n_members, seed + 2).alias("properties"), F.col("ts").alias("batch_s"),
    )
    # The app uploads a session's events together, so they land in the folder of the day it started
    day = F.floor((F.col("batch_s") - F.lit(start_s)) / 86_400)
    ev = in_session.unionByName(away).join(members_df.select("member_idx", "platform"), "member_idx").select(
        "event_id", member_id("member_idx").alias("member_id"), "session_id",
        ts("t", seed + 3).alias("event_ts"), "event_type", "platform", "properties",
        # A new app release starts sending app_version partway through the window
        F.when(day >= C.APP_VERSION_FROM_DAY,
               F.when(F.col("platform") == "ios", F.lit("6.4.0")).otherwise(F.lit("6.4.1"))).alias("app_version"),
        F.date_format(F.timestamp_seconds("batch_s"), "yyyy-MM-dd").alias("dt"),
    )
    return add_bad_rows(ev, seed)


def add_bad_rows(ev: DataFrame, seed: int) -> DataFrame:
    """Copies of real events with the defects a mobile event stream really has."""
    dupes = ev.where(F.rand(seed + 10) < C.BAD_DUPLICATE)  # at-least-once delivery, same event_id
    no_member = ev.where(F.rand(seed + 11) < C.BAD_NULL_MEMBER).withColumn("member_id", F.lit(None).cast("string")) \
        .withColumn("event_id", F.concat("event_id", F.lit("n")))
    future = ev.where(F.rand(seed + 12) < C.BAD_FUTURE_TS).withColumn(
        "event_ts", F.col("event_ts") + F.make_interval(days=(F.floor(F.rand(seed + 13) * 730) + 365).cast("int"))
    ).withColumn("event_id", F.concat("event_id", F.lit("f")))  # device clock set years ahead
    unknown = ev.where(F.rand(seed + 14) < C.BAD_UNKNOWN_TYPE).withColumn(
        "event_type", F.element_at(F.array(*[F.lit(t) for t in C.UNKNOWN_EVENT_TYPES]),
                                   (F.floor(F.rand(seed + 15) * len(C.UNKNOWN_EVENT_TYPES)) + 1).cast("int")),
    ).withColumn("event_id", F.concat("event_id", F.lit("u")))
    return ev.unionByName(dupes).unionByName(no_member).unionByName(future).unionByName(unknown)


def decisions(spark, path):
    d = spark.read.parquet(path)
    return d.select(
        F.concat(F.lit("d"), F.lpad(F.col("decision_idx").cast("string"), 9, "0")).alias("decision_id"),
        member_id("member_idx").alias("member_id"),
        ts("decided_s").alias("decided_ts"), "campaign_id", "campaign_type", "nudge_type", "arm",
        F.when(F.col("send_s") >= 0, ts("send_s")).alias("scheduled_send_ts"),
        "copy_variant_id", "copy_text",
    ).withColumn("dt", dt("decided_ts"))


def notification_events(spark, path, seed):
    n = spark.read.parquet(path)
    ev = n.select(
        F.concat(F.lit("ne"), F.lpad(F.col("notification_idx").cast("string"), 9, "0")).alias("notification_event_id"),
        F.concat(F.lit("d"), F.lpad(F.col("decision_idx").cast("string"), 9, "0")).alias("decision_id"),
        member_id("member_idx").alias("member_id"), ts("ts", seed + 20).alias("event_ts"), "event_type",
    ).withColumn("dt", dt("event_ts"))
    return ev.unionByName(ev.where(F.rand(seed + 21) < C.BAD_DUPLICATE))  # providers retry callbacks


def subscription_events(spark, path):
    s = spark.read.parquet(path)
    return s.select(
        F.concat(F.lit("sub"), F.lpad(F.col("subscription_idx").cast("string"), 8, "0")).alias("subscription_event_id"),
        member_id("member_idx").alias("member_id"), ts("ts").alias("event_ts"), "event_type", "plan",
        F.col("price_usd").cast("decimal(6,2)").alias("price_usd"),
    ).withColumn("dt", dt("event_ts"))


def land(df: DataFrame, path: str, fmt: str, mode: str):
    w = df.repartition("dt").write.mode(mode).partitionBy("dt").option("timestampFormat", TS_FORMAT)
    if fmt == "csv":
        w.option("header", True).csv(path)
    else:
        w.option("ignoreNullFields", fmt == "json_sparse").json(path)


def write_all(spark: SparkSession, catalog: str, inputs: dict, n_members: int, start_s: int, seed: int, mode: str):
    """inputs maps each simulated frame name to its parquet path. mode is overwrite (full) or append (drip)."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    landing = f"/Volumes/{catalog}/hearth_raw/landing"
    m = members(spark, inputs["members"], start_s, seed)
    land(m, f"{landing}/members", "csv", mode)
    member_lookup = spark.read.parquet(inputs["all_members"])
    # Sparse JSON: null fields are left out, so app_version only appears once the new release ships
    land(app_events(spark, inputs["sessions"], inputs["away"], member_lookup, n_members, start_s, seed),
         f"{landing}/app_events", "json_sparse", mode)
    land(decisions(spark, inputs["decisions"]), f"{landing}/notification_decisions", "json", mode)
    land(notification_events(spark, inputs["notifications"], seed), f"{landing}/notification_events", "json", mode)
    land(subscription_events(spark, inputs["subscriptions"]), f"{landing}/subscription_events", "csv", mode)

    truth = spark.read.parquet(inputs["truth"]).join(
        member_lookup.select("member_idx", "preferred_hour_local"), "member_idx").select(
        F.concat(F.lit("d"), F.lpad(F.col("decision_idx").cast("string"), 9, "0")).alias("decision_id"),
        member_id("member_idx").alias("member_id"), "preferred_hour_local", "baseline_return_72h",
        *[f"true_uplift_{n}" for n in C.NUDGES],
    )
    truth.write.mode(mode).option("overwriteSchema", mode == "overwrite").saveAsTable(f"{catalog}.hearth_ml.generator_truth")
