-- The blast against the nudge test: volume, returns and opt-outs, from the governed campaign_metrics view.
SELECT
  `Campaign Type` AS campaign_type,
  MEASURE(`Pushes Delivered`) AS pushes_delivered,
  MEASURE(`Return Rate`) AS return_rate,
  MEASURE(`Opt-out Rate`) AS opt_out_rate,
  MEASURE(`Opt-outs`) AS opt_outs
FROM katie_febar_catalog.hearth_gold.campaign_metrics
GROUP BY ALL
ORDER BY campaign_type
