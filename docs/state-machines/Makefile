
default: images

images: allocator.png boss.png code.png input.png key.png lister.png machines.png mailbox.png nameplate.png order.png receive.png send.png terminator.png

.PHONY: default images

%.png: %.dot
	dot -T png $< >$@
