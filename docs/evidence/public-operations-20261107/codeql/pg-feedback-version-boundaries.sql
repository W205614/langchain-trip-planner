BEGIN;
CREATE TEMP TABLE public_ops_arithmetic_review(id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
INSERT INTO public_ops_arithmetic_review VALUES(1,1);
WITH changed AS (UPDATE public_ops_arithmetic_review SET version=version+1 WHERE id=1 AND version=2147483647 RETURNING version)
SELECT 'unmatched_max_header_rows='||count(*) FROM changed;
UPDATE public_ops_arithmetic_review SET version=2147483646 WHERE id=1;
WITH changed AS (UPDATE public_ops_arithmetic_review SET version=version+1 WHERE id=1 AND version=2147483646 RETURNING version)
SELECT 'largest_successful_java_operand=2147483646;next_version='||max(version)||';rows='||count(*) FROM changed;
DO $review$
BEGIN
  BEGIN
    UPDATE public_ops_arithmetic_review SET version=version+1 WHERE id=1 AND version=2147483647;
    RAISE EXCEPTION 'Expected PostgreSQL INTEGER overflow was not raised';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    RAISE NOTICE 'matching_max_version_SQLSTATE=22003';
  END;
END
$review$;
SELECT 'stored_version_after_rejected_update='||version FROM public_ops_arithmetic_review;
ROLLBACK;
