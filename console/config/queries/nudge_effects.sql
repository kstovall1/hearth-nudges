-- What each nudge did in the randomized test, against its holdout, from the governed campaign_metrics view.
SELECT
  `Nudge Type` AS nudge_type,
  MEASURE(`Extra Returns vs Holdout`) AS extra_returns,
  MEASURE(`Uplift vs Holdout`) AS uplift_pts,
  MEASURE(`Opt-out Rate`) AS opt_out_rate,
  MEASURE(`Test Treated Decisions`) AS pushes_sent
FROM katie_febar_catalog.hearth_gold.campaign_metrics
WHERE `Campaign Type` = 'nudge_test'
GROUP BY ALL
ORDER BY extra_returns DESC
