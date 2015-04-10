
-- note: anything which isn't an boolean, integer, or human-readable unicode
-- string, (i.e. binary strings) will be stored as hex

CREATE TABLE `version`
(
 `version` INTEGER -- contains one row, set to 1
);

CREATE TABLE `messages`
(
 `channel_id` INTEGER,
 `side` VARCHAR,
 `msgnum` VARCHAR, -- not numeric, more of a PAKE-phase indicator string
 `message` VARCHAR,
 `when` INTEGER
);
CREATE INDEX `lookup` ON `messages` (`channel_id`, `side`, `msgnum`);
