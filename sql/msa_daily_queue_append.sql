INSERT INTO `sprinternship-bld-2026.msa_manager.msa_daily_queue`
  (msa_id, customer_id, details, processed_at, status)
SELECT
  v.msa_id,
  v.client_id AS customer_id,
  v.update_details AS details,
  TIMESTAMP(MIN(v.processed_at)) AS processed_at,
  'PENDING' AS status
FROM `sprinternship-bld-2026.msa_manager.v_msa_daily_queue` v
WHERE NOT EXISTS (
  SELECT 1
  FROM `sprinternship-bld-2026.msa_manager.msa_daily_queue` q
  WHERE q.msa_id = v.msa_id
    AND q.customer_id = v.client_id
)
GROUP BY v.msa_id, v.client_id, v.update_details;
