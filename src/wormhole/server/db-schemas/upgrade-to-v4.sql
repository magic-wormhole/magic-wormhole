ALTER TABLE `nameplates` ADD COLUMN `enumerable` BOOLEAN SET? True;
DELETE INDEX `nameplates_idx`;
CREATE INDEX `nameplates_idx` ON `nameplates` (`app_id`, `enumerable`, `name`);

DELETE FROM `version`;
INSERT INTO `version` (`version`) VALUES (4);
