
default: images

images: boss.png code.png key.png machines.png mailbox.png nameplate.png nameplate_lister.png order.png receive.png send.png terminator.png

.PHONY: default images

%.png: %.dot
	dot -T png $< >$@
