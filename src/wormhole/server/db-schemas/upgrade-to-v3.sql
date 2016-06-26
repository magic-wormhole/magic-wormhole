DROP TABLE `nameplates`;
DROP TABLE `messages`;
DROP TABLE `mailboxes`;


-- Wormhole codes use a "nameplate": a short name which is only used to
-- reference a specific (long-named) mailbox. The codes only use numeric
-- nameplates, but the protocol and server allow can use arbitrary strings.
CREATE TABLE `nameplates`
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 `app_id` VARCHAR,
 `name` VARCHAR,
 `mailbox_id` VARCHAR REFERENCES `mailboxes`(`id`),
 `request_id` VARCHAR -- from 'allocate' message, for future deduplication
);
CREATE INDEX `nameplates_idx` ON `nameplates` (`app_id`, `name`);
CREATE INDEX `nameplates_mailbox_idx` ON `nameplates` (`app_id`, `mailbox_id`);
CREATE INDEX `nameplates_request_idx` ON `nameplates` (`app_id`, `request_id`);

CREATE TABLE `nameplate_sides`
(
 `nameplates_id` REFERENCES `nameplates`(`id`),
 `claimed` BOOLEAN, -- True after claim(), False after release()
 `side` VARCHAR,
 `added` INTEGER -- time when this side first claimed the nameplate
);


-- Clients exchange messages through a "mailbox", which has a long (randomly
-- unique) identifier and a queue of messages.
-- `id` is randomly-generated and unique across all apps.
CREATE TABLE `mailboxes`
(
 `app_id` VARCHAR,
 `id` VARCHAR PRIMARY KEY,
 `updated` INTEGER, -- time of last activity, used for pruning
 `for_nameplate` BOOLEAN -- allocated for a nameplate, not standalone
);
CREATE INDEX `mailboxes_idx` ON `mailboxes` (`app_id`, `id`);

CREATE TABLE `mailbox_sides`
(
 `mailbox_id` REFERENCES `mailboxes`(`id`),
 `opened` BOOLEAN, -- True after open(), False after close()
 `side` VARCHAR,
 `added` INTEGER, -- time when this side first opened the mailbox
 `mood` VARCHAR
);

CREATE TABLE `messages`
(
 `app_id` VARCHAR,
 `mailbox_id` VARCHAR,
 `side` VARCHAR,
 `phase` VARCHAR, -- numeric or string
 `body` VARCHAR,
 `server_rx` INTEGER,
 `msg_id` VARCHAR
);
CREATE INDEX `messages_idx` ON `messages` (`app_id`, `mailbox_id`);

ALTER TABLE `mailbox_usage` ADD COLUMN `for_nameplate` BOOLEAN;
CREATE INDEX `mailbox_usage_result_idx` ON `mailbox_usage` (`result`);
CREATE INDEX `transit_usage_result_idx` ON `transit_usage` (`result`);

DELETE FROM `version`;
INSERT INTO `version` (`version`) VALUES (3);
