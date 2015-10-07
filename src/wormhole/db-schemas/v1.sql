
-- note: anything which isn't an boolean, integer, or human-readable unicode
-- string, (i.e. binary strings) will be stored as hex

CREATE TABLE `version`
(
 `version` INTEGER -- contains one row, set to 1
);

CREATE TABLE `messages`
(
 `appid` VARCHAR,
 `channelid` INTEGER,
 `side` VARCHAR,
 `phase` VARCHAR, -- not numeric, more of a PAKE-phase indicator string
 `body` VARCHAR,
 `when` INTEGER
);
CREATE INDEX `messages_idx` ON `messages` (`appid`, `channelid`);

CREATE TABLE `allocations`
(
 `appid` VARCHAR,
 `channelid` INTEGER,
 `side` VARCHAR
);
CREATE INDEX `allocations_idx` ON `allocations` (`channelid`);
