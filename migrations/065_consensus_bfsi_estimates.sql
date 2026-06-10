-- P6 (Week 17.5 S8): BFSI estimate lines + multi-source consensus.
--
-- For a bank the estimates that matter are NII and NIM, not PAT — SBI Q4 FY26
-- "beat" on PAT while NII printed ~6% below Street and the stock sold off.
-- Neither live source (Yahoo earningsTrend, Moneycontrol earning-forecast)
-- carries NII/NIM estimates, so these columns are populated via the manual /
-- CSV path (scripts/load_consensus.py) for bank results that matter.
ALTER TABLE consensus_estimates ADD COLUMN nii_est_cr REAL;
ALTER TABLE consensus_estimates ADD COLUMN nim_est_pct REAL;
