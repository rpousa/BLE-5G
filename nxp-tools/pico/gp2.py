from machine import Pin
import time
for g in (1, 2):
    u = Pin(g, Pin.IN, Pin.PULL_UP); time.sleep_ms(10); a = u.value()
    d = Pin(g, Pin.IN, Pin.PULL_DOWN); time.sleep_ms(10); b = d.value()
    print('GP%d pull-up %d / pull-down %d -> %s' % (g, a, b, {(1,0):'FLOATING (no wire/contact)',(0,0):'driven low',(1,1):'driven high'}.get((a,b),'?')))
