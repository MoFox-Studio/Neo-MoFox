-- Generated from GitHub 50da3262f01e3df8292677491e0b04dc940d4ff3:plugins/engram_memory/vnext/models.py
-- Exact SQLite DDL from the source branch; no private data.
BEGIN TRANSACTION;
CREATE TABLE engram_vnext_candidate (
	candidate_id VARCHAR(36) NOT NULL,
	status VARCHAR(10) NOT NULL,
	rough_title TEXT NOT NULL,
	rough_content TEXT NOT NULL,
	proposed_kind VARCHAR(14),
	observed_at VARCHAR(32) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	processing_session_id VARCHAR(36),
	last_error TEXT,
	PRIMARY KEY (candidate_id),
	CONSTRAINT engram_vnext_candidate_status CHECK (status IN ('PENDING', 'PROCESSING', 'DEFERRED', 'RESOLVED', 'FAILED')),
	CONSTRAINT engram_vnext_candidate_kind CHECK (proposed_kind IN ('EVENT', 'FACT', 'PREFERENCE', 'RELATIONSHIP', 'COMMITMENT', 'PERSONAL_STATE', 'SOCIAL_PATTERN')),
	FOREIGN KEY(processing_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id)
);
CREATE TABLE engram_vnext_candidate_action (
	action_id VARCHAR(36) NOT NULL,
	candidate_id VARCHAR(36) NOT NULL,
	sleep_session_id VARCHAR(36) NOT NULL,
	action_type VARCHAR(10) NOT NULL,
	result_revision_id VARCHAR(36),
	note TEXT,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (action_id),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	FOREIGN KEY(sleep_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id),
	CONSTRAINT engram_vnext_candidate_action_type CHECK (action_type IN ('CREATE_NEW', 'REINFORCE', 'REVISE', 'MERGE', 'RELATE', 'IGNORE', 'DEFER')),
	FOREIGN KEY(result_revision_id) REFERENCES engram_vnext_memory_revision (revision_id)
);
CREATE TABLE engram_vnext_candidate_action_target (
	action_id VARCHAR(36) NOT NULL,
	memory_id VARCHAR(36) NOT NULL,
	target_role VARCHAR(9) NOT NULL,
	PRIMARY KEY (action_id, memory_id, target_role),
	FOREIGN KEY(action_id) REFERENCES engram_vnext_candidate_action (action_id),
	FOREIGN KEY(memory_id) REFERENCES engram_vnext_memory (memory_id),
	CONSTRAINT engram_vnext_candidate_target_role CHECK (target_role IN ('SOURCE', 'TARGET', 'CANONICAL', 'RESULT'))
);
CREATE TABLE engram_vnext_candidate_encoder_cursor (
	stream_id TEXT NOT NULL,
	last_processed_message_time VARCHAR(32) NOT NULL,
	last_processed_message_id TEXT NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (stream_id)
);
CREATE TABLE engram_vnext_candidate_evidence (
	candidate_id VARCHAR(36) NOT NULL,
	evidence_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (candidate_id, evidence_id),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	FOREIGN KEY(evidence_id) REFERENCES engram_vnext_evidence (evidence_id)
);
CREATE TABLE engram_vnext_candidate_participant (
	participant_id VARCHAR(36) NOT NULL,
	candidate_id VARCHAR(36) NOT NULL,
	participant_kind VARCHAR(6) NOT NULL,
	person_id TEXT,
	label TEXT,
	PRIMARY KEY (participant_id),
	CONSTRAINT ck_engram_vnext_candidate_person_participant_id CHECK (participant_kind != 'PERSON' OR person_id IS NOT NULL),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	CONSTRAINT engram_vnext_candidate_participant_kind CHECK (participant_kind IN ('PERSON', 'SELF', 'OTHER'))
);
CREATE TABLE engram_vnext_candidate_subject (
	candidate_id VARCHAR(36) NOT NULL,
	subject_kind VARCHAR(7) NOT NULL,
	person_id TEXT,
	subject_key TEXT,
	subject_label TEXT,
	PRIMARY KEY (candidate_id),
	CONSTRAINT ck_engram_vnext_candidate_person_subject_id CHECK (subject_kind != 'PERSON' OR person_id IS NOT NULL),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	CONSTRAINT engram_vnext_candidate_subject_kind CHECK (subject_kind IN ('PERSON', 'GROUP', 'EVENT', 'TOPIC', 'SELF', 'WORLD', 'OTHER', 'UNKNOWN'))
);
CREATE TABLE engram_vnext_domain_operation (
	operation_key VARCHAR(128) NOT NULL,
	operation_type VARCHAR(32) NOT NULL,
	result_json JSON,
	created_at VARCHAR(32) NOT NULL,
	completed_at VARCHAR(32),
	PRIMARY KEY (operation_key)
);
CREATE TABLE engram_vnext_evidence (
	evidence_id VARCHAR(36) NOT NULL,
	source_type VARCHAR(13) NOT NULL,
	observed_at VARCHAR(32) NOT NULL,
	source_ref TEXT,
	note TEXT,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (evidence_id),
	CONSTRAINT engram_vnext_evidence_source_type CHECK (source_type IN ('MESSAGE_SET', 'ACTOR_WRITE', 'SYSTEM_EVENT', 'LEGACY_RECORD', 'ADMIN', 'EXTERNAL'))
);
CREATE TABLE engram_vnext_evidence_message_link (
	evidence_id VARCHAR(36) NOT NULL,
	stream_id TEXT NOT NULL,
	message_id TEXT NOT NULL,
	ordinal INTEGER NOT NULL,
	PRIMARY KEY (evidence_id, stream_id, message_id),
	CONSTRAINT ck_engram_vnext_evidence_ordinal CHECK (ordinal >= 0),
	FOREIGN KEY(evidence_id) REFERENCES engram_vnext_evidence (evidence_id)
);
CREATE TABLE engram_vnext_evidence_message_snapshot (
	stream_id TEXT NOT NULL,
	message_id TEXT NOT NULL,
	payload JSON NOT NULL,
	captured_at VARCHAR(32) NOT NULL,
	redacted_at VARCHAR(32),
	PRIMARY KEY (stream_id, message_id)
);
CREATE TABLE engram_vnext_memory (
	memory_id VARCHAR(36) NOT NULL,
	status VARCHAR(10) NOT NULL,
	current_revision_id VARCHAR(36) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	created_by_type VARCHAR(11) NOT NULL,
	last_experienced_at VARCHAR(32),
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (memory_id),
	CONSTRAINT engram_vnext_memory_status CHECK (status IN ('ACTIVE', 'MERGED', 'TOMBSTONED')),
	FOREIGN KEY(current_revision_id) REFERENCES engram_vnext_memory_revision (revision_id) DEFERRABLE INITIALLY DEFERRED,
	CONSTRAINT engram_vnext_memory_creator_type CHECK (created_by_type IN ('ACTOR', 'SLEEP_AGENT', 'SYSTEM', 'ADMIN', 'MIGRATION'))
);
CREATE TABLE engram_vnext_memory_event (
	event_id VARCHAR(36) NOT NULL,
	memory_id VARCHAR(36) NOT NULL,
	revision_id VARCHAR(36),
	event_type VARCHAR(18) NOT NULL,
	actor_type VARCHAR(11) NOT NULL,
	actor_ref TEXT,
	stream_id TEXT,
	occurred_at VARCHAR(32) NOT NULL,
	payload_json JSON,
	PRIMARY KEY (event_id),
	FOREIGN KEY(memory_id) REFERENCES engram_vnext_memory (memory_id),
	FOREIGN KEY(revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	CONSTRAINT engram_vnext_memory_event_type CHECK (event_type IN ('CREATED', 'REINFORCED', 'REVISED', 'ANCHOR_CHANGED', 'RELATED', 'RELATION_RETRACTED', 'MERGED', 'RECALLED', 'READ', 'FLASHBACK_EXPOSED', 'TOMBSTONED', 'RESTORED')),
	CONSTRAINT engram_vnext_memory_event_actor_type CHECK (actor_type IN ('ACTOR', 'SLEEP_AGENT', 'SYSTEM', 'ADMIN', 'MIGRATION'))
);
CREATE TABLE engram_vnext_memory_relation (
	relation_id VARCHAR(36) NOT NULL,
	source_memory_id VARCHAR(36) NOT NULL,
	target_memory_id VARCHAR(36) NOT NULL,
	relation_type VARCHAR(11) NOT NULL,
	reason TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	created_by_type VARCHAR(11) NOT NULL,
	retracted_at VARCHAR(32),
	retract_reason TEXT,
	PRIMARY KEY (relation_id),
	CONSTRAINT ck_engram_vnext_relation_not_self CHECK (source_memory_id != target_memory_id),
	FOREIGN KEY(source_memory_id) REFERENCES engram_vnext_memory (memory_id),
	FOREIGN KEY(target_memory_id) REFERENCES engram_vnext_memory (memory_id),
	CONSTRAINT engram_vnext_relation_type CHECK (relation_type IN ('CONTINUES', 'CAUSES', 'REINFORCES', 'CONTRADICTS', 'RELATED_TO', 'SUPERSEDES', 'MERGED_INTO')),
	CONSTRAINT engram_vnext_relation_creator_type CHECK (created_by_type IN ('ACTOR', 'SLEEP_AGENT', 'SYSTEM', 'ADMIN', 'MIGRATION'))
);
CREATE TABLE engram_vnext_memory_retrieval_entry (
	entry_id VARCHAR(36) NOT NULL,
	memory_id VARCHAR(36) NOT NULL,
	revision_id VARCHAR(36),
	entry_type VARCHAR(19) NOT NULL,
	text TEXT NOT NULL,
	content_hash TEXT NOT NULL,
	generator_version TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (entry_id),
	FOREIGN KEY(memory_id) REFERENCES engram_vnext_memory (memory_id),
	FOREIGN KEY(revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	CONSTRAINT engram_vnext_retrieval_entry_type CHECK (entry_type IN ('CURRENT_REVISION', 'HISTORICAL_REVISION', 'TAG', 'GENERATED_CUE'))
);
CREATE TABLE engram_vnext_memory_revision (
	revision_id VARCHAR(36) NOT NULL,
	memory_id VARCHAR(36) NOT NULL,
	revision_no INTEGER NOT NULL,
	parent_revision_id VARCHAR(36),
	title TEXT NOT NULL,
	content TEXT NOT NULL,
	memory_kind VARCHAR(14) NOT NULL,
	observed_at VARCHAR(32) NOT NULL,
	change_reason VARCHAR(19) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	created_by_type VARCHAR(11) NOT NULL,
	created_by_ref TEXT,
	PRIMARY KEY (revision_id),
	CONSTRAINT uq_engram_vnext_revision_no UNIQUE (memory_id, revision_no),
	CONSTRAINT ck_engram_vnext_revision_positive CHECK (revision_no >= 1),
	FOREIGN KEY(memory_id) REFERENCES engram_vnext_memory (memory_id),
	FOREIGN KEY(parent_revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	CONSTRAINT engram_vnext_memory_kind CHECK (memory_kind IN ('EVENT', 'FACT', 'PREFERENCE', 'RELATIONSHIP', 'COMMITMENT', 'PERSONAL_STATE', 'SOCIAL_PATTERN')),
	CONSTRAINT engram_vnext_revision_change_reason CHECK (change_reason IN ('INITIAL', 'DEVELOPMENT', 'CORRECTION', 'CLARIFICATION', 'NEW_EVIDENCE', 'EXPLICIT_CORRECTION', 'ADMIN_CORRECTION', 'MIGRATION_REWRITE')),
	CONSTRAINT engram_vnext_revision_creator_type CHECK (created_by_type IN ('ACTOR', 'SLEEP_AGENT', 'SYSTEM', 'ADMIN', 'MIGRATION'))
);
CREATE TABLE engram_vnext_memory_revision_participant (
	participant_id VARCHAR(36) NOT NULL,
	revision_id VARCHAR(36) NOT NULL,
	participant_kind VARCHAR(6) NOT NULL,
	person_id TEXT,
	label TEXT,
	PRIMARY KEY (participant_id),
	CONSTRAINT ck_engram_vnext_person_participant_id CHECK (participant_kind != 'PERSON' OR person_id IS NOT NULL),
	FOREIGN KEY(revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	CONSTRAINT engram_vnext_participant_kind CHECK (participant_kind IN ('PERSON', 'SELF', 'OTHER'))
);
CREATE TABLE engram_vnext_memory_revision_subject (
	revision_id VARCHAR(36) NOT NULL,
	subject_kind VARCHAR(7) NOT NULL,
	person_id TEXT,
	subject_key TEXT,
	subject_label TEXT,
	PRIMARY KEY (revision_id),
	CONSTRAINT ck_engram_vnext_person_subject_id CHECK (subject_kind != 'PERSON' OR person_id IS NOT NULL),
	FOREIGN KEY(revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	CONSTRAINT engram_vnext_subject_kind CHECK (subject_kind IN ('PERSON', 'GROUP', 'EVENT', 'TOPIC', 'SELF', 'WORLD', 'OTHER', 'UNKNOWN'))
);
CREATE TABLE engram_vnext_persona_update_log (
	update_id VARCHAR(36) NOT NULL,
	person_id TEXT NOT NULL,
	sleep_session_id VARCHAR(36),
	old_content_hash TEXT NOT NULL,
	new_content_hash TEXT NOT NULL,
	generator_version TEXT,
	revision_no INTEGER,
	impression_text TEXT,
	seen_revision_ids JSON,
	reason TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (update_id),
	CONSTRAINT ck_engram_vnext_persona_revision_positive CHECK (revision_no >= 1),
	FOREIGN KEY(sleep_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id)
);
CREATE TABLE engram_vnext_persona_update_memory (
	update_id VARCHAR(36) NOT NULL,
	memory_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (update_id, memory_id),
	FOREIGN KEY(update_id) REFERENCES engram_vnext_persona_update_log (update_id),
	FOREIGN KEY(memory_id) REFERENCES engram_vnext_memory (memory_id)
);
CREATE TABLE engram_vnext_revision_evidence (
	revision_id VARCHAR(36) NOT NULL,
	evidence_id VARCHAR(36) NOT NULL,
	linked_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (revision_id, evidence_id),
	FOREIGN KEY(revision_id) REFERENCES engram_vnext_memory_revision (revision_id),
	FOREIGN KEY(evidence_id) REFERENCES engram_vnext_evidence (evidence_id)
);
CREATE TABLE engram_vnext_schema_version (
	schema_key VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	applied_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (schema_key)
);
CREATE TABLE engram_vnext_sleep_action_operation (
	operation_key VARCHAR(128) NOT NULL,
	candidate_id VARCHAR(36) NOT NULL,
	sleep_session_id VARCHAR(36) NOT NULL,
	action_type VARCHAR(10) NOT NULL,
	intent_json JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	result_json JSON,
	error TEXT,
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (operation_key),
	CONSTRAINT ck_engram_vnext_sleep_operation_status CHECK (status IN ('PREPARED', 'EXECUTING', 'COMPLETED', 'FAILED')),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	FOREIGN KEY(sleep_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id),
	CONSTRAINT engram_vnext_sleep_operation_action_type CHECK (action_type IN ('CREATE_NEW', 'REINFORCE', 'REVISE', 'MERGE', 'RELATE', 'IGNORE', 'DEFER'))
);
CREATE TABLE engram_vnext_sleep_action_plan (
	plan_key VARCHAR(128) NOT NULL,
	candidate_id VARCHAR(36) NOT NULL,
	sleep_session_id VARCHAR(36) NOT NULL,
	intents_json JSON NOT NULL,
	operation_keys_json JSON NOT NULL,
	action_count INTEGER NOT NULL,
	next_action_index INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	target_memory_ids_json JSON NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (plan_key),
	CONSTRAINT ck_engram_vnext_sleep_plan_status CHECK (status IN ('PREPARED', 'EXECUTING', 'COMPLETED', 'FAILED')),
	CONSTRAINT ck_engram_vnext_sleep_plan_cursor CHECK (action_count >= 0 AND next_action_index >= 0 AND next_action_index <= action_count),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	FOREIGN KEY(sleep_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id)
);
CREATE TABLE engram_vnext_sleep_session (
	sleep_session_id VARCHAR(36) NOT NULL,
	trigger_type VARCHAR(8) NOT NULL,
	status VARCHAR(9) NOT NULL,
	started_at VARCHAR(32) NOT NULL,
	finished_at VARCHAR(32),
	candidate_count INTEGER NOT NULL,
	model_id TEXT NOT NULL,
	prompt_version TEXT NOT NULL,
	error_summary TEXT,
	PRIMARY KEY (sleep_session_id),
	CONSTRAINT ck_engram_vnext_sleep_candidate_count CHECK (candidate_count >= 0),
	CONSTRAINT engram_vnext_sleep_trigger_type CHECK (trigger_type IN ('DAILY', 'PRESSURE', 'MANUAL', 'RECOVERY')),
	CONSTRAINT engram_vnext_sleep_session_status CHECK (status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED'))
);
CREATE TABLE engram_vnext_sleep_session_candidate (
	sleep_session_id VARCHAR(36) NOT NULL,
	candidate_id VARCHAR(36) NOT NULL,
	claimed_at VARCHAR(32) NOT NULL,
	released_at VARCHAR(32),
	outcome VARCHAR(8),
	PRIMARY KEY (sleep_session_id, candidate_id),
	FOREIGN KEY(sleep_session_id) REFERENCES engram_vnext_sleep_session (sleep_session_id),
	FOREIGN KEY(candidate_id) REFERENCES engram_vnext_candidate (candidate_id),
	CONSTRAINT engram_vnext_sleep_candidate_outcome CHECK (outcome IN ('RESOLVED', 'DEFERRED', 'FAILED'))
);
CREATE TABLE engram_vnext_vector_index_manifest (
	index_id VARCHAR(36) NOT NULL,
	index_version TEXT NOT NULL,
	embedding_model_id TEXT NOT NULL,
	embedding_dimension INTEGER NOT NULL,
	retrieval_schema_version TEXT NOT NULL,
	flashback_threshold FLOAT,
	created_at VARCHAR(32) NOT NULL,
	activated_at VARCHAR(32),
	status VARCHAR(8) NOT NULL,
	PRIMARY KEY (index_id),
	CONSTRAINT ck_engram_vnext_manifest_dimension CHECK (embedding_dimension > 0),
	CONSTRAINT engram_vnext_vector_index_status CHECK (status IN ('BUILDING', 'ACTIVE', 'RETIRED', 'FAILED'))
);
CREATE TABLE engram_vnext_vector_outbox (
	outbox_id VARCHAR(36) NOT NULL,
	object_type VARCHAR(15) NOT NULL,
	object_id VARCHAR(36) NOT NULL,
	operation VARCHAR(6) NOT NULL,
	content_hash TEXT NOT NULL,
	embedding_model_id TEXT NOT NULL,
	index_id VARCHAR(36),
	status VARCHAR(10) NOT NULL,
	attempt_count INTEGER NOT NULL,
	claim_token VARCHAR(36),
	last_error TEXT,
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (outbox_id),
	CONSTRAINT ck_engram_vnext_outbox_attempt_count CHECK (attempt_count >= 0),
	CONSTRAINT engram_vnext_outbox_object_type CHECK (object_type IN ('RETRIEVAL_ENTRY')),
	CONSTRAINT engram_vnext_outbox_operation CHECK (operation IN ('UPSERT', 'DELETE')),
	CONSTRAINT engram_vnext_outbox_status CHECK (status IN ('PENDING', 'PROCESSING', 'DONE', 'FAILED'))
);
CREATE INDEX idx_engram_vnext_memory_experienced ON engram_vnext_memory (last_experienced_at);
CREATE INDEX idx_engram_vnext_memory_status ON engram_vnext_memory (status);
CREATE INDEX idx_engram_vnext_revision_kind ON engram_vnext_memory_revision (memory_kind);
CREATE INDEX idx_engram_vnext_revision_memory ON engram_vnext_memory_revision (memory_id);
CREATE INDEX idx_engram_vnext_sleep_status ON engram_vnext_sleep_session (status, started_at);
CREATE INDEX idx_engram_vnext_domain_operation_type ON engram_vnext_domain_operation (operation_type, created_at);
CREATE INDEX idx_engram_vnext_outbox_index_status ON engram_vnext_vector_outbox (index_id, status, updated_at);
CREATE INDEX idx_engram_vnext_outbox_status ON engram_vnext_vector_outbox (status, updated_at);
CREATE UNIQUE INDEX uq_engram_vnext_manifest_active ON engram_vnext_vector_index_manifest (status) WHERE status = 'ACTIVE';
CREATE INDEX idx_engram_vnext_manifest_status ON engram_vnext_vector_index_manifest (status);
CREATE INDEX idx_engram_vnext_subject_person ON engram_vnext_memory_revision_subject (person_id);
CREATE INDEX idx_engram_vnext_participant_person ON engram_vnext_memory_revision_participant (person_id);
CREATE INDEX idx_engram_vnext_participant_revision ON engram_vnext_memory_revision_participant (revision_id);
CREATE INDEX idx_engram_vnext_evidence_message_stream ON engram_vnext_evidence_message_link (stream_id);
CREATE INDEX idx_engram_vnext_relation_target ON engram_vnext_memory_relation (target_memory_id);
CREATE INDEX idx_engram_vnext_relation_source ON engram_vnext_memory_relation (source_memory_id);
CREATE UNIQUE INDEX uq_engram_vnext_relation_active_symmetric ON engram_vnext_memory_relation (min(source_memory_id, target_memory_id), max(source_memory_id, target_memory_id), relation_type) WHERE retracted_at IS NULL AND relation_type IN ('CONTRADICTS', 'RELATED_TO');
CREATE UNIQUE INDEX uq_engram_vnext_relation_active_directed ON engram_vnext_memory_relation (source_memory_id, target_memory_id, relation_type) WHERE retracted_at IS NULL;
CREATE INDEX idx_engram_vnext_candidate_status ON engram_vnext_candidate (status, created_at);
CREATE INDEX idx_engram_vnext_event_memory ON engram_vnext_memory_event (memory_id, occurred_at);
CREATE INDEX idx_engram_vnext_event_type ON engram_vnext_memory_event (event_type, occurred_at);
CREATE UNIQUE INDEX uq_engram_vnext_persona_revision ON engram_vnext_persona_update_log (person_id, revision_no);
CREATE INDEX idx_engram_vnext_persona_log_person ON engram_vnext_persona_update_log (person_id, created_at);
CREATE INDEX idx_engram_vnext_retrieval_revision ON engram_vnext_memory_retrieval_entry (revision_id);
CREATE INDEX idx_engram_vnext_retrieval_type ON engram_vnext_memory_retrieval_entry (entry_type);
CREATE INDEX idx_engram_vnext_retrieval_memory ON engram_vnext_memory_retrieval_entry (memory_id);
CREATE INDEX idx_engram_vnext_candidate_participant ON engram_vnext_candidate_participant (candidate_id);
CREATE INDEX idx_engram_vnext_action_candidate ON engram_vnext_candidate_action (candidate_id);
CREATE INDEX idx_engram_vnext_sleep_operation_resume ON engram_vnext_sleep_action_operation (sleep_session_id, status, updated_at);
CREATE INDEX idx_engram_vnext_sleep_operation_candidate ON engram_vnext_sleep_action_operation (candidate_id, created_at);
CREATE INDEX idx_engram_vnext_sleep_plan_resume ON engram_vnext_sleep_action_plan (sleep_session_id, status, updated_at);
CREATE INDEX idx_engram_vnext_sleep_plan_candidate ON engram_vnext_sleep_action_plan (candidate_id, created_at);
COMMIT;
