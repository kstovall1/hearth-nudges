"""Generate Hearth's raw files.

full: simulate the whole window and replace everything in the landing Volume.
      The window is 60 days ending two days ago, unless --end-date says otherwise.
drip: continue from the saved state and append the next --days days, never past
      yesterday, so new files land for the pipeline to pick up.

After a full run, full-refresh the pipeline: the old files are gone.
"""

import argparse
import os
import shutil
from datetime import date, datetime, timedelta, timezone

from hearth_data import config as C
from hearth_data.simulate import DAY, Simulation


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--mode", choices=["full", "drip"], default="full")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--end-date", "--end_date", dest="end_date", default="", help="last day of a full run, YYYY-MM-DD")
    p.add_argument("--days", type=int, default=1, help="days to add in drip mode")
    args = p.parse_args(argv)

    today = datetime.now(timezone.utc).date()
    base = f"/Volumes/{args.catalog}/hearth_raw/generator"
    state = f"{base}/state/sim"
    if args.mode == "full":
        end = date.fromisoformat(args.end_date) if args.end_date else today - timedelta(days=2)
        sim = Simulation(args.seed, end - timedelta(days=C.N_DAYS - 1))
        n_days, write_mode, first_new_s = C.N_DAYS, "overwrite", None
    else:
        sim = Simulation.load(state)
        next_day = datetime.fromtimestamp(sim.t0 + sim.day * DAY, tz=timezone.utc).date()
        n_days = min(args.days, (today - timedelta(days=1) - next_day).days + 1)
        if n_days <= 0:
            print(f"Nothing to add: the data already reaches {next_day - timedelta(days=1)}.")
            return
        write_mode, first_new_s = "append", sim.t0 + sim.day * DAY

    first_day = datetime.fromtimestamp(sim.t0 + sim.day * DAY, tz=timezone.utc).date()
    print(f"{args.mode}: simulating {n_days} days from {first_day}, seed {sim.seed}")
    frames = sim.run(n_days)
    members = sim.members()
    frames["all_members"] = members
    frames["members"] = members if first_new_s is None else members[members["signup_s"] >= first_new_s]
    for name, df in frames.items():
        print(f"  {name}: {len(df):,} rows")

    run_dir = f"{base}/runs/latest"
    shutil.rmtree(run_dir, ignore_errors=True)
    os.makedirs(run_dir)
    inputs = {}
    for name, df in frames.items():
        inputs[name] = f"{run_dir}/{name}.parquet"
        df.to_parquet(inputs[name], index=False)

    from pyspark.sql import SparkSession

    from hearth_data.write import write_all

    spark = SparkSession.builder.getOrCreate()
    write_all(spark, args.catalog, inputs, len(members), sim.t0, sim.seed, write_mode)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    sim.save(state)  # only once the files have landed, so a failed drip can simply rerun
    last_day = datetime.fromtimestamp(sim.t0 + sim.day * DAY - 1, tz=timezone.utc).date()
    print(f"Landed {first_day} to {last_day} in /Volumes/{args.catalog}/hearth_raw/landing")


if __name__ == "__main__":
    main()
