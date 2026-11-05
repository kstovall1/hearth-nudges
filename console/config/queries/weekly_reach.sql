-- Weekly reach, opt-outs and push volume from the governed reach_metrics view.
SELECT
  `Week` AS week,
  MEASURE(`Reachable Members`) AS reachable_end,
  MEASURE(`Opt-outs`) AS opt_outs,
  MEASURE(`Blast Pushes`) AS blast_pushes,
  MEASURE(`Pushes Delivered`) AS pushes_delivered,
  MEASURE(`Pushes per Active Member`) AS pushes_per_active_member
FROM katie_febar_catalog.hearth_gold.reach_metrics
GROUP BY ALL
ORDER BY week
