INSERT INTO `sprinternship-bld-2026.msa_dataset.msa_daily_queue`
  (
    msa_id,
    client_id,
    update_details,
    processed_at,
    status,
    customer_id,
    details
  )
WITH latest_msa AS (
  SELECT *
  FROM `sprinternship-bld-2026.msa_manager.msa_updates`
  WHERE distribution_date >= CURRENT_DATE()
    AND ARRAY_LENGTH(affected_services) > 0
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY msa_id
    ORDER BY sent_date DESC, distribution_date DESC, subject DESC
  ) = 1
),
service_matches AS (
  SELECT DISTINCT
    msa.msa_id,
    customer.client_id,
    msa.headline AS update_details,
    TIMESTAMP(msa.distribution_date) AS processed_at,
    'PENDING' AS status
  FROM latest_msa AS msa
  CROSS JOIN UNNEST(msa.affected_services) AS affected_service
  CROSS JOIN UNNEST(
    ARRAY_CONCAT([affected_service.name], affected_service.aliases)
  ) AS affected_term
  CROSS JOIN `sprinternship-bld-2026.msa_manager.customer_profiles` AS customer
  CROSS JOIN UNNEST(customer.active_services) AS customer_service
  WHERE REGEXP_REPLACE(
          LOWER(TRIM(customer_service)),
          r'[-\s]+',
          ' '
        ) = REGEXP_REPLACE(
          LOWER(TRIM(affected_term)),
          r'[-\s]+',
          ' '
        )
    AND NULLIF(TRIM(customer.client_id), '') IS NOT NULL
)
SELECT
  source.msa_id,
  source.client_id,
  source.update_details,
  source.processed_at,
  source.status,
  source.client_id AS customer_id,
  source.update_details AS details
FROM service_matches AS source
WHERE NOT EXISTS (
  SELECT 1
  FROM `sprinternship-bld-2026.msa_dataset.msa_daily_queue` AS existing
  WHERE TRIM(existing.msa_id) = TRIM(source.msa_id)
    AND TRIM(existing.client_id) = TRIM(source.client_id)
    AND DATE(existing.processed_at) = DATE(source.processed_at)
);
