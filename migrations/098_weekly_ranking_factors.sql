-- Surface the real predictive factors on the weekly snapshot (rank moves to the validated
-- multi-factor composite; Quality/Momentum shown alongside Value/Surprise). FII/ownership is
-- demoted — it's the second-weakest predictor (IC +0.017) and already inside `composite`.
ALTER TABLE weekly_ranking ADD COLUMN composite REAL;
ALTER TABLE weekly_ranking ADD COLUMN quality REAL;
ALTER TABLE weekly_ranking ADD COLUMN momentum REAL;
