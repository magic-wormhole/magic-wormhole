
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
 -- phase="_allocate" and "_deallocate" are used internally
 `body` VARCHAR,
 `when` INTEGER
);
CREATE INDEX `messages_idx` ON `messages` (`appid`, `channelid`);

CREATE TABLE `usage`
(
 `started` INTEGER, -- seconds since epoch, rounded to one day
 `result` VARCHAR, -- happy, scary, lonely, errory, pruney
 -- "happy": both sides close with mood=happy
 -- "scary": any side closes with mood=scary (bad MAC, probably wrong pw)
 -- "lonely": any side closes with mood=lonely (no response from 2nd side)
 -- "errory": any side closes with mood=errory (other errors)
 -- "pruney": channels which get pruned for inactivity
 -- "crowded": three or more sides were involved
 `total_time` INTEGER, -- seconds from start to closed, or None
 `waiting_time` INTEGER -- seconds from start to 2nd side appearing, or None
);
CREATE INDEX `usage_idx` ON `usage` (`started`);
