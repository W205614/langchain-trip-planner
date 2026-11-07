ALTER TABLE outbound_reservations
 ADD COLUMN supplier_input_tokens BIGINT CHECK(supplier_input_tokens>=0),
 ADD COLUMN supplier_output_tokens BIGINT CHECK(supplier_output_tokens>=0),
 ADD COLUMN supplier_reported_at TIMESTAMPTZ;
ALTER TABLE outbound_reservations ADD CONSTRAINT outbound_supplier_report_complete CHECK(
 (supplier_reported_at IS NULL AND supplier_input_tokens IS NULL AND supplier_output_tokens IS NULL)
 OR (supplier_reported_at IS NOT NULL AND supplier_input_tokens IS NOT NULL AND supplier_output_tokens IS NOT NULL)
);
