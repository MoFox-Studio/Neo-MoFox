-- Generated from GitHub 0483c39ebcaf5c0b155ceb3a54af66190bed75ed:plugins/booku_memory/service/models.py
-- Exact SQLite DDL from the source branch; no private data.
BEGIN TRANSACTION;
CREATE TABLE booku_memory_records (
	memory_id TEXT NOT NULL,
	title TEXT NOT NULL,
	folder_id TEXT NOT NULL,
	bucket TEXT NOT NULL,
	content TEXT NOT NULL,
	source TEXT NOT NULL,
	memory_type TEXT NOT NULL,
	status TEXT NOT NULL,
	person_id TEXT,
	relation_memory_ids TEXT NOT NULL,
	relation_aliases TEXT NOT NULL,
	event_start_at FLOAT NOT NULL,
	event_end_at FLOAT NOT NULL,
	related_people TEXT NOT NULL,
	knowledge_type TEXT NOT NULL,
	address_or_coord TEXT NOT NULL,
	place_type TEXT NOT NULL,
	asset_type TEXT NOT NULL,
	disposition_status TEXT NOT NULL,
	procedure_type TEXT NOT NULL,
	novelty_energy FLOAT NOT NULL,
	is_archived INTEGER NOT NULL,
	is_deleted INTEGER NOT NULL,
	deleted_at FLOAT NOT NULL,
	created_at FLOAT NOT NULL,
	updated_at FLOAT NOT NULL,
	last_activated_at FLOAT NOT NULL,
	activation_count INTEGER NOT NULL,
	PRIMARY KEY (memory_id)
);
CREATE TABLE booku_memory_tags (
	id INTEGER NOT NULL,
	memory_id TEXT NOT NULL,
	tag_type TEXT NOT NULL,
	tag_value TEXT NOT NULL,
	PRIMARY KEY (id)
);
CREATE TABLE booku_temporary_memos (
	memo_id TEXT NOT NULL,
	stream_id TEXT NOT NULL,
	content TEXT NOT NULL,
	expires_at FLOAT NOT NULL,
	created_at FLOAT NOT NULL,
	updated_at FLOAT NOT NULL,
	PRIMARY KEY (memo_id)
);
CREATE INDEX idx_booku_memory_folder_bucket ON booku_memory_records (folder_id, bucket, is_archived);
CREATE INDEX idx_booku_memory_type_status ON booku_memory_records (memory_type, status, last_activated_at);
CREATE INDEX idx_booku_memory_person_id ON booku_memory_records (person_id);
CREATE INDEX idx_booku_memory_tags_memory ON booku_memory_tags (memory_id, tag_type);
CREATE INDEX idx_booku_temporary_memos_expires_at ON booku_temporary_memos (expires_at);
CREATE INDEX idx_booku_temporary_memos_stream_id ON booku_temporary_memos (stream_id);
CREATE INDEX idx_booku_temporary_memos_updated_at ON booku_temporary_memos (updated_at);
COMMIT;
