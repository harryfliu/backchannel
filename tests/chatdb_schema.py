"""DDL for fixture databases that look like macOS 15's chat.db and AddressBook-v22.abcddb."""

CHAT_DB_SCHEMA = r"""
CREATE TABLE _SqliteDatabaseProperties (key TEXT, value TEXT, UNIQUE(key));
CREATE TABLE deleted_messages (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, guid TEXT NOT NULL);
CREATE TABLE chat_handle_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, handle_id INTEGER REFERENCES handle (ROWID) ON DELETE CASCADE, UNIQUE(chat_id, handle_id));
CREATE TABLE sync_deleted_messages (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, guid TEXT NOT NULL, recordID TEXT );
CREATE TABLE message_processing_task (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, guid TEXT NOT NULL, task_flags INTEGER NOT NULL );
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT, service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT, UNIQUE (id, service) );
CREATE TABLE sync_deleted_chats (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, guid TEXT NOT NULL, recordID TEXT,timestamp INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, attachment_id INTEGER REFERENCES attachment (ROWID) ON DELETE CASCADE, UNIQUE(message_id, attachment_id));
CREATE TABLE sync_deleted_attachments (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, guid TEXT NOT NULL, recordID TEXT );
CREATE TABLE kvtable (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, key TEXT UNIQUE NOT NULL, value BLOB NOT NULL);
CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
CREATE TABLE message (
  ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT, replace INTEGER DEFAULT 0,
  service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT, country TEXT, attributedBody BLOB,
  version INTEGER DEFAULT 0, type INTEGER DEFAULT 0, service TEXT, account TEXT, account_guid TEXT,
  error INTEGER DEFAULT 0, date INTEGER, date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER DEFAULT 0,
  is_finished INTEGER DEFAULT 0, is_emote INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0, is_empty INTEGER DEFAULT 0,
  is_delayed INTEGER DEFAULT 0, is_auto_reply INTEGER DEFAULT 0, is_prepared INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0,
  is_system_message INTEGER DEFAULT 0, is_sent INTEGER DEFAULT 0, has_dd_results INTEGER DEFAULT 0,
  is_service_message INTEGER DEFAULT 0, is_forward INTEGER DEFAULT 0, was_downgraded INTEGER DEFAULT 0,
  is_archive INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0, cache_roomnames TEXT,
  was_data_detected INTEGER DEFAULT 0, was_deduplicated INTEGER DEFAULT 0, is_audio_message INTEGER DEFAULT 0,
  is_played INTEGER DEFAULT 0, date_played INTEGER, item_type INTEGER DEFAULT 0, other_handle INTEGER DEFAULT 0,
  group_title TEXT, group_action_type INTEGER DEFAULT 0, share_status INTEGER DEFAULT 0,
  share_direction INTEGER DEFAULT 0, is_expirable INTEGER DEFAULT 0, expire_state INTEGER DEFAULT 0,
  message_action_type INTEGER DEFAULT 0, message_source INTEGER DEFAULT 0, associated_message_guid TEXT,
  associated_message_type INTEGER DEFAULT 0, balloon_bundle_id TEXT, payload_data BLOB,
  expressive_send_style_id TEXT, associated_message_range_location INTEGER DEFAULT 0,
  associated_message_range_length INTEGER DEFAULT 0, time_expressive_send_played INTEGER,
  message_summary_info BLOB, ck_sync_state INTEGER DEFAULT 0, ck_record_id TEXT, ck_record_change_tag TEXT,
  destination_caller_id TEXT, is_corrupt INTEGER DEFAULT 0, reply_to_guid TEXT, sort_id INTEGER,
  is_spam INTEGER DEFAULT 0, has_unseen_mention INTEGER DEFAULT 0, thread_originator_guid TEXT,
  thread_originator_part TEXT, syndication_ranges TEXT, synced_syndication_ranges TEXT,
  was_delivered_quietly INTEGER DEFAULT 0, did_notify_recipient INTEGER DEFAULT 0, date_retracted INTEGER,
  date_edited INTEGER, was_detonated INTEGER DEFAULT 0, part_count INTEGER, is_stewie INTEGER DEFAULT 0,
  is_sos INTEGER DEFAULT 0, is_critical INTEGER DEFAULT 0, bia_reference_id TEXT DEFAULT NULL,
  is_kt_verified INTEGER DEFAULT 0, fallback_hash TEXT DEFAULT NULL, associated_message_emoji TEXT DEFAULT NULL,
  is_pending_satellite_send INTEGER DEFAULT 0, needs_relay INTEGER DEFAULT 0, schedule_type INTEGER DEFAULT 0,
  schedule_state INTEGER DEFAULT 0, sent_or_received_off_grid INTEGER DEFAULT 0, date_recovered INTEGER DEFAULT 0,
  is_time_sensitive INTEGER DEFAULT 0, ck_chat_id TEXT
);
CREATE TABLE chat (
  ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER, state INTEGER,
  account_id TEXT, properties BLOB, chat_identifier TEXT, service_name TEXT, room_name TEXT, account_login TEXT,
  is_archived INTEGER DEFAULT 0, last_addressed_handle TEXT, display_name TEXT, group_id TEXT,
  is_filtered INTEGER DEFAULT 0, successful_query INTEGER, engram_id TEXT, server_change_token TEXT,
  ck_sync_state INTEGER DEFAULT 0, original_group_id TEXT, last_read_message_timestamp INTEGER DEFAULT 0,
  cloudkit_record_id TEXT, last_addressed_sim_id TEXT, is_blackholed INTEGER DEFAULT 0,
  syndication_date INTEGER DEFAULT 0, syndication_type INTEGER DEFAULT 0, is_recovered INTEGER DEFAULT 0,
  is_deleting_incoming_messages INTEGER DEFAULT 0, is_pending_review INTEGER DEFAULT 0
);
CREATE TABLE attachment (
  ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, created_date INTEGER DEFAULT 0,
  start_date INTEGER DEFAULT 0, filename TEXT, uti TEXT, mime_type TEXT, transfer_state INTEGER DEFAULT 0,
  is_outgoing INTEGER DEFAULT 0, user_info BLOB, transfer_name TEXT, total_bytes INTEGER DEFAULT 0,
  is_sticker INTEGER DEFAULT 0, sticker_user_info BLOB, attribution_info BLOB, hide_attachment INTEGER DEFAULT 0,
  ck_sync_state INTEGER DEFAULT 0, ck_server_change_token_blob BLOB, ck_record_id TEXT,
  original_guid TEXT UNIQUE NOT NULL, sr_ck_sync_state INTEGER DEFAULT 0, sr_ck_server_change_token_blob BLOB,
  sr_ck_record_id TEXT, is_commsafety_sensitive INTEGER DEFAULT 0, emoji_image_content_identifier TEXT DEFAULT NULL,
  emoji_image_short_description TEXT DEFAULT NULL, preview_generation_state INTEGER DEFAULT 0
);
CREATE TABLE chat_recoverable_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, delete_date INTEGER, ck_sync_state INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id), CHECK (delete_date != 0));
CREATE TABLE unsynced_removed_recoverable_messages (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, chat_guid TEXT NOT NULL, message_guid TEXT NOT NULL, part_index INTEGER);
CREATE TABLE recoverable_message_part (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, part_index INTEGER, delete_date INTEGER, part_text BLOB NOT NULL, ck_sync_state INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id, part_index), CHECK (delete_date != 0));
CREATE TABLE chat_service (service TEXT, chat INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, UNIQUE(service, chat));
CREATE TABLE chat_lookup (identifier TEXT NOT NULL, domain TEXT NOT NULL, chat INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, priority INTEGER DEFAULT 0, UNIQUE(identifier, domain, chat));
CREATE TABLE scheduled_messages_pending_cloudkit_delete (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT NOT NULL, recordID TEXT, UNIQUE (guid));

CREATE INDEX chat_handle_join_idx_handle_id ON chat_handle_join(handle_id);
CREATE INDEX message_attachment_join_idx_message_id ON message_attachment_join(message_id);
CREATE INDEX message_attachment_join_idx_attachment_id ON message_attachment_join(attachment_id);
CREATE INDEX chat_message_join_idx_message_id_only ON chat_message_join(message_id);
CREATE INDEX chat_message_join_idx_message_date_id_chat_id ON chat_message_join(chat_id, message_date, message_id);
CREATE INDEX message_idx_is_read ON message(is_read, is_from_me, item_type);
CREATE INDEX message_idx_failed ON message(is_finished, is_from_me, error);
CREATE INDEX message_idx_handle ON message(handle_id, date);
CREATE INDEX message_idx_handle_id ON message(handle_id);
CREATE INDEX message_idx_date ON message(date);
CREATE INDEX message_idx_associated_message2 ON message(associated_message_guid) WHERE associated_message_guid IS NOT NULL;
CREATE INDEX message_idx_thread_originator_guid ON message(thread_originator_guid);
CREATE INDEX message_idx_expire_state ON message(expire_state);
CREATE INDEX message_idx_other_handle ON message(other_handle);
CREATE INDEX message_idx_cache_has_attachments ON message(cache_has_attachments);
CREATE INDEX message_idx_was_downgraded ON message(was_downgraded);
CREATE INDEX message_idx_schedule_state ON message(schedule_state);
CREATE INDEX message_idx_undelivered_one_to_one_imessage ON message(cache_roomnames, service, is_sent, is_delivered, was_downgraded, item_type) WHERE cache_roomnames IS NULL AND service IN ('iMessage','RCS') AND is_sent = 1 AND is_delivered = 0 AND was_downgraded = 0 AND item_type == 0 AND schedule_type == 0;
CREATE INDEX chat_idx_chat_identifier ON chat(chat_identifier);
CREATE INDEX chat_idx_chat_identifier_service_name ON chat(chat_identifier, service_name);
CREATE INDEX chat_idx_chat_room_name_service_name ON chat(room_name, service_name);
CREATE INDEX chat_idx_is_archived ON chat(is_archived);
CREATE INDEX chat_idx_group_id ON chat(group_id);
CREATE INDEX attachment_idx_purged_attachments_v2 ON attachment(hide_attachment, ck_sync_state, transfer_state) WHERE hide_attachment=0 AND (ck_sync_state=1 OR ck_sync_state=4) AND transfer_state=0;
CREATE INDEX chat_lookup_idx_chat_identifier ON chat_lookup(chat);
"""

ADDRESSBOOK_SCHEMA = r"""
CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER, Z_MAX INTEGER);
CREATE TABLE Z_METADATA (Z_VERSION INTEGER PRIMARY KEY, Z_UUID VARCHAR(255), Z_PLIST BLOB);
CREATE TABLE Z_MODELCACHE (Z_CONTENT BLOB);
CREATE TABLE ZABCDRECORD (
  Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCONTACTINDEX INTEGER, ZDISPLAYFLAGS INTEGER,
  ZIMAGEREFERENCE INTEGER, ZPRIVACYFLAGS INTEGER, ZSEARCHINDEX INTEGER, ZCONTAINER INTEGER, ZME INTEGER,
  ZCREATIONDATE TIMESTAMP, ZMODIFICATIONDATE TIMESTAMP, ZBIRTHDAY TIMESTAMP, ZFIRSTNAME VARCHAR,
  ZLASTNAME VARCHAR, ZMIDDLENAME VARCHAR, ZNICKNAME VARCHAR, ZORGANIZATION VARCHAR, ZDEPARTMENT VARCHAR,
  ZJOBTITLE VARCHAR, ZTITLE VARCHAR, ZSUFFIX VARCHAR, ZMAIDENNAME VARCHAR, ZNAME VARCHAR, ZNOTE VARCHAR,
  ZSORTINGFIRSTNAME VARCHAR, ZSORTINGLASTNAME VARCHAR, ZUNIQUEID VARCHAR, ZIDENTITYUNIQUEID VARCHAR,
  ZEXTERNALIDENTIFIER VARCHAR, ZTHUMBNAILIMAGEDATA BLOB, ZIMAGEDATA BLOB
);
CREATE TABLE ZABCDPHONENUMBER (
  Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISPRIMARY INTEGER, ZISPRIVATE INTEGER,
  ZORDERINGINDEX INTEGER, ZOWNER INTEGER, Z22_OWNER INTEGER, ZAREACODE VARCHAR, ZCOUNTRYCODE VARCHAR,
  ZEXTENSION VARCHAR, ZFULLNUMBER VARCHAR, ZLABEL VARCHAR, ZLASTFOURDIGITS VARCHAR, ZLOCALNUMBER VARCHAR,
  ZUNIQUEID VARCHAR
);
CREATE TABLE ZABCDEMAILADDRESS (
  Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISPRIMARY INTEGER, ZISPRIVATE INTEGER,
  ZORDERINGINDEX INTEGER, ZOWNER INTEGER, Z22_OWNER INTEGER, ZADDRESS VARCHAR, ZADDRESSNORMALIZED VARCHAR,
  ZLABEL VARCHAR, ZUNIQUEID VARCHAR
);
CREATE TABLE ZABCDPOSTALADDRESS (
  Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISPRIMARY INTEGER, ZORDERINGINDEX INTEGER,
  ZOWNER INTEGER, ZCITY VARCHAR, ZCOUNTRYNAME VARCHAR, ZLABEL VARCHAR, ZSTATE VARCHAR, ZSTREET VARCHAR,
  ZZIPCODE VARCHAR, ZUNIQUEID VARCHAR
);
CREATE TABLE ZABCDNOTE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCONTACT INTEGER, ZTEXT VARCHAR, ZRICHTEXTDATA BLOB);
CREATE INDEX ZABCDPHONENUMBER_ZOWNER_INDEX ON ZABCDPHONENUMBER (ZOWNER);
CREATE INDEX ZABCDEMAILADDRESS_ZOWNER_INDEX ON ZABCDEMAILADDRESS (ZOWNER);
CREATE INDEX ZABCDRECORD_ZUNIQUEID_INDEX ON ZABCDRECORD (ZUNIQUEID);
"""

# The three Chief tables Backchannel uses, as in chief/db/schema.sql.
CHIEF_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS open_loops (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  title             TEXT NOT NULL,
  owner             TEXT NOT NULL CHECK(owner IN ('harry', 'carmen', 'shared')),
  urgency           TEXT CHECK(urgency IN ('low', 'medium', 'high', 'critical')),
  category          TEXT,
  suggested_action  TEXT,
  autonomy_tier     TEXT NOT NULL DEFAULT 'ask' CHECK(autonomy_tier IN ('auto', 'draft', 'ask')),
  status            TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'done', 'dropped')),
  due_date          DATE,
  source_ref        TEXT,
  gcal_event_id     TEXT,
  calendar_status   TEXT,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  closed_at         TIMESTAMP,
  closed_reason     TEXT
);
CREATE TABLE IF NOT EXISTS people (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  relationship  TEXT,
  owner         TEXT,
  birthday      TEXT,
  anniversary   TEXT,
  notes         TEXT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS profile_facts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  owner       TEXT,
  category    TEXT,
  fact        TEXT NOT NULL,
  source      TEXT,
  source_ref  TEXT,
  superseded_by INTEGER REFERENCES profile_facts(id),
  captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_profile_owner    ON profile_facts(owner, category) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_profile_active   ON profile_facts(captured_at) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_loops_status      ON open_loops(status);
CREATE INDEX IF NOT EXISTS idx_people_birthday   ON people(birthday);
"""
