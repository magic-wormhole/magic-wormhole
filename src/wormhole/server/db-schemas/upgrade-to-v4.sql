
CREATE TABLE `nameplate_cooldown`
(
 `app_id` VARCHAR,
 `name` VARCHAR,
 `until` INTEGER -- no claims allowed until then
);
CREATE INDEX `nameplate_cooldown_idx` ON `nameplate_cooldown` (`app_id`, `name`);
CREATE INDEX `nameplate_cooldown_until_idx` ON `nameplate_cooldown` (`until`);

DELETE FROM `version`;
INSERT INTO `version` (`version`) VALUES (4);
