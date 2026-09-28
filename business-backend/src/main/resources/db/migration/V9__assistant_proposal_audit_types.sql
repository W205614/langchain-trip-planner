ALTER TABLE trip_record_versions
  DROP CONSTRAINT trip_record_versions_change_type_check;

ALTER TABLE trip_record_versions
  ADD CONSTRAINT trip_record_versions_change_type_check CHECK (change_type IN
    ('migration_baseline','manual_create','agent_create','copied_create','user_edit','reverify',
     'agent_revision','assistant_confirm','draft_apply','restore',
     'assistant_proposal','assistant_proposal_confirm','assistant_proposal_discard'));
